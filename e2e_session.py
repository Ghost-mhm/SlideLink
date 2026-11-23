# SlideLink: Encrypted sessions between the paired phone app and the PC (end-to-end through the relay).
#   hello  phone -> PC  {device, phone_eph, ts, sig = phone_key.sign("SL-HELLO-1"|pc_pub|device|phone_eph|ts)}
#          PC -> phone  {sid, pc_eph, sig = pc_key.sign("SL-HELLO-PC-1"|hello_transcript|pc_eph|sid)}
#          k_up | k_down = HKDF(ECDH(pc_eph, phone_eph), SHA256(transcript), "slidelink session v1", 64)   (fresh per session)
#   request  POST /api/e2e/req   body = sid(16) | seq(8) | AES-GCM(k_up, nonce 0000|seq, aad "SL-REQ"|sid|seq,
#            plaintext = len(4) | {"m": method, "p": path, "h": headers} | body)
#   answer   a stream of frames  len(4) | ctr(8) | AES-GCM(k_down, nonce 0001|ctr, aad "SL-RES"|sid|seq|index(4),
#            plaintext = type(1) | data)   types: H = {"status", "headers"}, D = body bytes, E = end
#   The request runs inside SlideLink as that phone (same rules as always); the relay only sees opaque bytes.
#   Die Anfrage läuft in SlideLink als dieses Handy (dieselben Regeln wie immer); das Relay sieht nur undurchsichtige Bytes.
import hashlib
import http.client
import json
import os
import secrets
import struct
import threading
import time

import e2e
from e2e_server import b64, unb64

SESSION_IDLE = 24 * 3600
SESSIONS_PER_DEVICE = 8
MAX_ENVELOPE = 64 * 1024 * 1024
CLOCK_SKEW = 600
WINDOW = 4096                      # replay window: request numbers may arrive out of order, but never twice
DROP = {"host", "connection", "content-length", "transfer-encoding", "keep-alive", "upgrade", "te", "trailer",
        "origin", "referer", "x-forwarded-for", "forwarded", "x-real-ip", "x-slidelink-relay", "x-slidelink-e2e", "x-slidelink-e2e-device"}
METHODS = {"GET", "POST", "HEAD"}


def hello_transcript(pc_pub, device, phone_eph, ts):
    return b"SL-HELLO-1" + pc_pub + device.encode() + phone_eph + struct.pack(">Q", ts)


class Session:
    def __init__(self, sid, device, k_up, k_down):
        self.sid, self.device = sid, device
        self.up, self.down = e2e.Aead(k_up), e2e.Aead(k_down)
        self.lock = threading.Lock()
        self.max_seq, self.seen, self.ctr = -1, set(), 0
        self.t = time.time()

    def accept(self, seq):
        """Replay check: each request number once; very old ones (outside the window) are refused."""
        with self.lock:
            if seq in self.seen or seq <= self.max_seq - WINDOW:
                return False
            self.seen.add(seq)
            if seq > self.max_seq:
                self.max_seq = seq
                self.seen = {x for x in self.seen if x > seq - WINDOW}
            self.t = time.time()
            return True

    def frame(self, seq, index, kind, data=b""):
        with self.lock:
            self.ctr += 1
            ctr = self.ctr
        aad = b"SL-RES" + self.sid + struct.pack(">QI", seq, index)
        box = self.down.seal(b"\0\0\0\1" + struct.pack(">Q", ctr), kind + data, aad)
        return struct.pack(">IQ", len(box), ctr) + box


class Sessions:
    def __init__(self, pc_key, registry, port):
        self.key, self.registry, self.port = pc_key, registry, port
        self.lock = threading.Lock()
        self.items = {}
        self.internal = secrets.token_urlsafe(24)      # marks our own loopback requests; never leaves the PC

    def device_for(self, did):
        """The linked end-to-end device with this id, or None (unlinked = its sessions stop at once)."""
        try:
            d = self.registry.get(did)
        except ValueError:
            return None
        return d if d.get("e2e_pub") else None

    def hello(self, body):
        did = str(body.get("device") or "")[:32]
        phone_eph = unb64(body.get("phone_eph"), 65)
        sig = unb64(body.get("sig"), 64)
        try:
            ts = int(body.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0
        dev = self.device_for(did)
        tr = hello_transcript(self.key.public, did, phone_eph, ts)
        if not dev or abs(time.time() - ts) > CLOCK_SKEW or not e2e.verify(unb64(dev["e2e_pub"], 65), tr, sig):
            raise ValueError("Not paired end-to-end with this PC.")
        eph, sid = e2e.EcdhKey(), os.urandom(16)
        tr += eph.public + sid
        keys = e2e.hkdf(eph.shared(phone_eph), hashlib.sha256(tr).digest(), b"slidelink session v1", 64)
        with self.lock:
            now = time.time()
            for k in [k for k, s in self.items.items() if now - s.t > SESSION_IDLE]:
                del self.items[k]
            mine = sorted((s for s in self.items.values() if s.device == did), key=lambda s: s.t)
            for s in mine[:max(0, len(mine) - SESSIONS_PER_DEVICE + 1)]:
                del self.items[s.sid]
            self.items[sid] = Session(sid, did, keys[:32], keys[32:])
        return {"ok": True, "sid": b64(sid), "pc_eph": b64(eph.public), "sig": b64(self.key.sign(b"SL-HELLO-PC-1" + tr))}

    def handle(self, h):
        """POST /api/e2e/req on request handler h: decrypt, run inside SlideLink as the phone, stream the answer back encrypted."""
        n = int(h.headers.get("Content-Length") or 0)
        if not 24 + 16 <= n <= MAX_ENVELOPE:
            return h._json({"ok": False, "error": "bad request"}, 400)
        raw = h.rfile.read(n)
        sid, seqb = raw[:16], raw[16:24]
        seq = struct.unpack(">Q", seqb)[0]
        with self.lock:
            s = self.items.get(sid)
        if not s or not self.device_for(s.device):
            return h._json({"ok": False, "error": "no session", "e2e": "hello"}, 401)
        try:
            plain = s.up.open(b"\0\0\0\0" + seqb, raw[24:], b"SL-REQ" + sid + seqb)
            hl = struct.unpack(">I", plain[:4])[0]
            meta = json.loads(plain[4:4 + hl])
        except (e2e.CryptoError, ValueError, struct.error):
            return h._json({"ok": False, "error": "bad request"}, 400)
        if not s.accept(seq):
            return h._json({"ok": False, "error": "replayed request"}, 400)
        body = plain[4 + hl:]
        method, path = str(meta.get("m", "GET")).upper(), str(meta.get("p", "/"))
        if method not in METHODS or not path.startswith("/") or path.startswith(("/api/e2e/", "//")) or any(c in path for c in "\r\n "):
            return h._json({"ok": False, "error": "bad request"}, 400)
        headers = {str(k): str(v) for k, v in (meta.get("h") or {}).items()
                   if str(k).lower() not in DROP and not any(c in str(k) + str(v) for c in "\r\n")}
        headers.update({"Host": f"localhost:{self.port}", "X-SlideLink-E2E": self.internal, "X-SlideLink-E2E-Device": s.device})
        send_body = bool(body) or method == "POST"
        if send_body:
            headers["Content-Length"] = str(len(body))

        h.send_response(200)
        h.send_header("Content-Type", "application/octet-stream")
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Accel-Buffering", "no")
        h.end_headers()
        idx = 0

        def out(kind, data=b""):
            nonlocal idx
            h.wfile.write(s.frame(seq, idx, kind, data))
            h.wfile.flush()
            idx += 1

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=None)
        try:
            conn.request(method, path, body=body if send_body else None, headers=headers)
            r = conn.getresponse()
            keep = [[k, v] for k, v in r.getheaders() if k.lower() not in ("connection", "transfer-encoding", "content-length")]
            out(b"H", json.dumps({"status": r.status, "headers": keep}).encode())
            while True:
                chunk = r.read1(65536)
                if not chunk:
                    break
                out(b"D", chunk)
            out(b"E")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                       # the phone went away (e.g. closed the live stream)
        finally:
            conn.close()
