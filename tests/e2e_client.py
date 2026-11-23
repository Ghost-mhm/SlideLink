# SlideLink · EN: A reference client for end-to-end sessions (what the Android app does), used by the tests and live checks.
# SlideLink · DE: Ein Referenz-Client für Ende-zu-Ende-Sitzungen (was die Android-App macht), für Tests und Live-Prüfungen.
import base64
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import e2e  # noqa: E402
import e2e_server  # noqa: E402
import e2e_session  # noqa: E402

UA = "Mozilla/5.0 (Linux; Android 15) SlideLinkTest"


def b64(b):
    return base64.b64encode(b).decode()


def _post(base, path, data, ctype="application/json", extra=None, stream=False):
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": ctype, "User-Agent": UA, **(extra or {})})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return (r.status, r) if stream else (r.status, r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read() if not stream else e


class Phone:
    def __init__(self, base, extra_headers=None):
        self.base, self.extra = base, extra_headers or {}
        self.id_key, self.eph, self.nonce = e2e.SigningKey(), e2e.EcdhKey(), os.urandom(32)
        self.device = self.pc_pub = self.sid = None
        self.seq = 0

    # ---- pairing (step 2)
    def pair_start(self, code):
        commit = hashlib.sha256(self.id_key.public + self.eph.public + self.nonce).digest()
        st, r = _post(self.base, "/api/e2e/pair/start", json.dumps({"code": code, "name": "Test phone", "type": "phone",
                      "phone_pub": b64(self.id_key.public), "phone_eph": b64(self.eph.public), "commit": b64(commit)}).encode(), extra=self.extra)
        self.r = json.loads(r)
        self.tr = e2e_server.transcript(base64.b64decode(self.r["pc_pub"]), base64.b64decode(self.r["pc_eph"]),
                                        base64.b64decode(self.r["pc_nonce"]), self.id_key.public, self.eph.public, self.nonce)
        _post(self.base, "/api/e2e/pair/reveal", json.dumps({"id": self.r["id"], "phone_nonce": b64(self.nonce)}).encode(), extra=self.extra)
        return e2e_server.sas(self.tr)

    def pair_finish(self):
        st, r = _post(self.base, "/api/e2e/pair/status", json.dumps({"id": self.r["id"]}).encode(), extra=self.extra)
        s = json.loads(r)
        k = e2e_server.pair_key(self.eph.shared(base64.b64decode(self.r["pc_eph"])), self.tr)
        got = json.loads(e2e.Aead(k).open(bytes(12), base64.b64decode(s["box"]), self.r["id"].encode()))
        self.device, self.pc_pub = got["device"]["id"], base64.b64decode(self.r["pc_pub"])
        return got

    # ---- session (step 3)
    def hello(self, ts=None, sign_with=None):
        eph, ts = e2e.EcdhKey(), int(ts or time.time())
        tr = e2e_session.hello_transcript(self.pc_pub, self.device, eph.public, ts)
        st, r = _post(self.base, "/api/e2e/hello", json.dumps({"device": self.device, "phone_eph": b64(eph.public), "ts": ts,
                      "sig": b64((sign_with or self.id_key).sign(tr))}).encode(), extra=self.extra)
        if st != 200:
            return st, json.loads(r)
        j = json.loads(r)
        sid, pc_eph = base64.b64decode(j["sid"]), base64.b64decode(j["pc_eph"])
        tr += pc_eph + sid
        if not e2e.verify(self.pc_pub, b"SL-HELLO-PC-1" + tr, base64.b64decode(j["sig"])):
            raise AssertionError("PC signature wrong: not the paired PC")
        keys = e2e.hkdf(eph.shared(pc_eph), hashlib.sha256(tr).digest(), b"slidelink session v1", 64)
        self.sid, self.up, self.down = sid, e2e.Aead(keys[:32]), e2e.Aead(keys[32:])
        self.seq = 0
        return st, j

    def envelope(self, method, path, body=b"", headers=None, seq=None):
        if seq is None:
            self.seq += 1
            seq = self.seq
        meta = json.dumps({"m": method, "p": path, "h": headers or {}}).encode()
        seqb = struct.pack(">Q", seq)
        plain = struct.pack(">I", len(meta)) + meta + body
        return seq, self.sid + seqb + self.up.seal(b"\0\0\0\0" + seqb, plain, b"SL-REQ" + self.sid + seqb)

    def frames(self, resp, seq, limit=None):
        """Yields (type, data) of a response stream, checking every frame."""
        idx = 0
        while limit is None or idx < limit:
            head = resp.read(12)
            if len(head) < 12:
                return
            n, ctr = struct.unpack(">IQ", head)
            box = resp.read(n)
            aad = b"SL-RES" + self.sid + struct.pack(">QI", seq, idx)
            plain = self.down.open(b"\0\0\0\1" + struct.pack(">Q", ctr), box, aad)
            idx += 1
            yield plain[:1], plain[1:]
            if plain[:1] == b"E":
                return

    def request(self, method, path, body=b"", headers=None):
        """One encrypted request; returns (status, headers, body)."""
        seq, env = self.envelope(method, path, body, headers)
        st, resp = _post(self.base, "/api/e2e/req", env, "application/octet-stream", self.extra, stream=True)
        if st != 200:
            return st, {}, resp.read()
        status, hdrs, out, ended = None, {}, b"", False
        for kind, data in self.frames(resp, seq):
            if kind == b"H":
                h = json.loads(data)
                status, hdrs = h["status"], dict(h["headers"])
            elif kind == b"D":
                out += data
            elif kind == b"E":
                ended = True
        assert ended, "stream ended without the end frame"
        return status, hdrs, out

    def stream(self, path):
        seq, env = self.envelope("GET", path)
        st, resp = _post(self.base, "/api/e2e/req", env, "application/octet-stream", self.extra, stream=True)
        return resp, self.frames(resp, seq)
