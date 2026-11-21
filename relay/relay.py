# SlideLink · EN: SlideLink Relay, local test version. Same protocol as the Cloudflare Worker in relay/worker/.
# SlideLink · DE: SlideLink-Relay, lokale Testversion. Gleiches Protokoll wie der Cloudflare Worker in relay/worker/.
#
# The PC keeps ONE permanent WebSocket to the relay (outgoing, so routers, carrier NAT and school or hotel
# networks don't matter). Phones open  <relay>/p/<pc-id>  once; a cookie then routes all their requests to that PC.
# Requests go to the PC over the WebSocket, answers come back as a stream (SSE works).
#
#   relay -> PC   {"t":"req","rid":…,"method":…,"path":…,"headers":{…},"client":…,"body":<base64>}
#                 {"t":"cancel","rid":…}                       the phone left
#   PC -> relay   {"t":"head","rid":…,"status":200,"headers":[[k,v],…]}
#                 binary: 16 bytes rid + data                   any number of pieces
#                 {"t":"end","rid":…}
#                 {"t":"ping"}  ->  {"t":"pong"}
#
# Test locally:  python relay/relay.py --port 8780
import argparse
import hashlib
import json
import os
import queue
import secrets
import sys
import threading
import time
from base64 import b64encode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wsmini  # noqa: E402

HEAD_WAIT = 30          # seconds a phone waits for the PC to answer
CHUNK_WAIT = 120        # seconds between two pieces of a stream (SSE sends keep-alives)
MAX_BODY = 20 * 1024 * 1024
MAX_OPEN = 64           # open requests per PC
MAX_PER_MINUTE = 3000   # requests per PC and minute
MAX_PER_IP = 600        # requests per IP address and minute (all PCs)
PIECE = 512 * 1024      # big uploads go to the PC in pieces of this size
ips = {}                # ip -> [minute, count]
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host", "content-length"}
NULL_BODY = {101, 204, 205, 304}

lock = threading.Lock()
pcs = {}                # pc id -> {"key", "ws", "open": {rid: Pending}, "minute", "count"}


class Pending:
    def __init__(self):
        self.head = None
        self.head_ready = threading.Event()
        self.chunks = queue.Queue()


def _valid_id(pid):
    return 6 <= len(pid) <= 40 and pid.isalnum()


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SlideLinkRelay/1.3"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self):
        path = self.path
        if path == "/_relay/health":
            return self._send(200, b"ok")
        parts = path.split("?")[0].strip("/").split("/")
        if len(parts) == 3 and parts[0] == "_pc" and parts[2] == "ws":
            return self._pc_socket(parts[1])
        now = int(time.time() // 60)
        slot = ips.setdefault(self.client_address[0], [now, 0])
        if slot[0] != now:
            slot[:] = [now, 0]
        slot[1] += 1
        if slot[1] > MAX_PER_IP:
            return self._send(429, b"Too many requests from this address - try again in a minute.")
        current = None
        for c in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = c.strip().partition("=")
            if k == "sl_pc":
                current = v
        pid, set_cookie = current, None
        if path.startswith("/p/"):
            rest = path[3:]
            head, sep, tail = rest.partition("/")
            pid, _, query = head.partition("?")
            if not sep and query:
                tail = "?" + query
            confirmed = "sl_switch=1" in tail
            tail = tail.replace("?sl_switch=1&", "?").replace("?sl_switch=1", "").replace("&sl_switch=1", "")
            path = "/" + tail.lstrip("/") if tail else "/"
            if current and current != pid and not confirmed and self.command == "GET":
                return self._send(200, f"Open another SlideLink PC? <a href='/p/{pid}{'' if path == '/' else path}{'&' if '?' in path else '?'}sl_switch=1'>Continue</a>".encode(),
                                  {"Content-Type": "text/html; charset=utf-8"})
            set_cookie = f"sl_pc={pid}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
        if not pid or not _valid_id(pid):
            return self._send(404, "SlideLink Relay – open the link from your SlideLink PC (…/p/<id>).".encode())
        self._forward(pid, path, set_cookie)

    # the PC's permanent line
    def _pc_socket(self, pid):
        key = self.headers.get("X-Relay-Key") or ""
        if not _valid_id(pid) or len(key) < 16 or self.headers.get("Upgrade", "").lower() != "websocket":
            return self._send(403, b"no")
        h = hashlib.sha256(key.encode()).hexdigest()
        with lock:
            pc = pcs.get(pid)
            if pc and not secrets.compare_digest(pc["key"], h):
                return self._send(403, b"wrong key")
            if not pc:
                pc = pcs[pid] = {"key": h, "ws": None, "open": {}, "minute": 0, "count": 0}
            old = pc["ws"]
        if old:
            old.close()                                   # a new line replaces the old one
        ws = wsmini.accept(self)
        pc["ws"] = ws
        try:
            while True:
                msg = ws.recv()
                if isinstance(msg, bytes):
                    p = pc["open"].get(msg[:16].decode("ascii", "replace"))
                    if p:
                        p.chunks.put(msg[16:])
                    continue
                m = json.loads(msg)
                if m.get("t") == "ping":
                    ws.send('{"t":"pong"}')
                    continue
                p = pc["open"].get(m.get("rid", ""))
                if not p:
                    continue
                if m["t"] == "head":
                    p.head = (int(m["status"]), [tuple(x) for x in m["headers"]])
                    p.head_ready.set()
                elif m["t"] == "end":
                    p.chunks.put(None)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            if pc["ws"] is ws:
                pc["ws"] = None
                for p in list(pc["open"].values()):       # the PC left: end everything still open
                    if not p.head_ready.is_set():
                        p.head = (503, [("Content-Type", "text/plain")])
                        p.head_ready.set()
                    p.chunks.put(None)
            ws.close()

    # phone -> relay -> PC
    def _forward(self, pid, path, set_cookie):
        pc = pcs.get(pid)
        ws = pc and pc["ws"]
        cookie = {"Set-Cookie": set_cookie} if set_cookie else None
        if not ws:
            return self._send(503, "SlideLink: the PC is offline. Start SlideLink on the PC and try again.".encode(), cookie)
        now = int(time.time() // 60)
        if pc["minute"] != now:
            pc["minute"], pc["count"] = now, 0
        pc["count"] += 1
        if pc["count"] > MAX_PER_MINUTE or len(pc["open"]) >= MAX_OPEN:
            return self._send(429, b"Too many requests - try again in a moment.", cookie)
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._send(413, b"too large", cookie)
        body = self.rfile.read(n) if n else b""
        rid = secrets.token_hex(8)
        p = Pending()
        pc["open"][rid] = p
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() != "cookie"}
        headers["Host"] = self.headers.get("Host", "")
        own = []                                          # only this PC's own cookies (name__<pc-id>) reach it
        for c in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = c.strip().partition("=")
            if k.endswith(f"__{pid}"):
                own.append(f"{k[:-len(pid) - 2]}={v}")
        if own:
            headers["Cookie"] = "; ".join(own)
        try:
            big = len(body) > PIECE
            for i in range(0, len(body) if big else 0, PIECE):
                ws.send(rid.encode() + body[i:i + PIECE])
            ws.send(json.dumps({"t": "req", "rid": rid, "method": self.command, "path": path, "headers": headers,
                                "client": self.client_address[0], "body": "" if big else b64encode(body).decode(),
                                "pieces": big}))
            if not p.head_ready.wait(HEAD_WAIT):
                return self._send(504, b"SlideLink: the PC did not answer in time.", cookie)
            status, rh = p.head
            self.send_response(status)
            length = None
            for k, v in rh:
                if k.lower() in HOP - {"content-length"}:
                    continue
                if k.lower() == "content-length":
                    length = v
                if k.lower() == "set-cookie" and "=" in v:     # the PC's cookies get its id: other PCs never see them
                    n, _, rest = v.partition("=")
                    v = f"{n.strip()}__{pid}={rest}"
                self.send_header(k, v)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            if length is None and status not in NULL_BODY:
                self.send_header("Connection", "close")      # a stream (SSE): ends when the connection closes
                self.close_connection = True
            self.end_headers()
            self.wfile.flush()
            while True:
                try:
                    c = p.chunks.get(timeout=CHUNK_WAIT)
                except queue.Empty:
                    break
                if c is None:
                    break
                if self.command != "HEAD" and status not in NULL_BODY:
                    self.wfile.write(c)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            try:
                ws.send(json.dumps({"t": "cancel", "rid": rid}))   # the phone left: the PC stops this stream
            except (ConnectionError, OSError):
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            pc["open"].pop(rid, None)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _route


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8780)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Relay)
    srv.daemon_threads = True
    print(f"SlideLink Relay on port {a.port}")
    srv.serve_forever()
