# SlideLink: Minimal WebSocket (RFC 6455) client and server, standard library only: the PC's permanent line to the relay.
import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, BINARY, CLOSE, PING, PONG = 1, 2, 8, 9, 10
MAX_MESSAGE = 32 * 1024 * 1024


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def _xor(data, mask):
    n = len(data)
    if not n:
        return data
    m = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")


class WebSocket:
    def __init__(self, sock, rfile, client):
        self.sock, self.rfile, self.client = sock, rfile, client     # clients mask their frames, servers don't
        self.lock = threading.Lock()
        self.closed = False

    def send(self, data):
        if isinstance(data, str):
            self._frame(TEXT, data.encode())
        else:
            self._frame(BINARY, bytes(data))

    def _frame(self, op, data):
        n = len(data)
        mbit = 0x80 if self.client else 0
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([mbit | n])
        elif n < 65536:
            head += bytes([mbit | 126]) + struct.pack("!H", n)
        else:
            head += bytes([mbit | 127]) + struct.pack("!Q", n)
        if self.client:
            mask = os.urandom(4)
            head += mask
            data = _xor(data, mask)
        with self.lock:
            if self.closed:
                raise ConnectionError("closed")
            self.sock.sendall(head + data)

    def _read(self, n):
        b = self.rfile.read(n)
        if len(b) < n:
            raise ConnectionError("connection lost")
        return b

    def recv(self):
        """Next text (str) or binary (bytes) message; answers pings; raises ConnectionError when closed."""
        parts, kind = [], None
        while True:
            b0, b1 = self._read(2)
            fin, op, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            if n > MAX_MESSAGE:
                raise ConnectionError("message too large")
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = _xor(data, mask)
            if op == CLOSE:
                self.close()
                raise ConnectionError("closed by peer")
            if op == PING:
                self._frame(PONG, data)
                continue
            if op == PONG:
                continue
            if op in (TEXT, BINARY):
                kind, parts = op, [data]
            else:                                   # continuation
                parts.append(data)
            if fin:
                msg = b"".join(parts)
                return msg.decode() if kind == TEXT else msg

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.sock.sendall(bytes([0x80 | CLOSE, 0x80 if self.client else 0]) + (os.urandom(4) if self.client else b""))
            except OSError:
                pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def connect(url, headers=None, timeout=15):
    """Opens ws:// or wss:// and returns a WebSocket."""
    u = urlparse(url)
    secure = u.scheme in ("wss", "https")
    port = u.port or (443 if secure else 80)
    sock = socket.create_connection((u.hostname, port), timeout=timeout)
    if secure:
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
    key = base64.b64encode(os.urandom(16)).decode()
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    host = u.hostname + (f":{u.port}" if u.port else "")
    lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13", "User-Agent: SlideLink"]
    lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    rfile = sock.makefile("rb")
    status = rfile.readline().decode("latin-1")
    got = {}
    while True:
        line = rfile.readline().decode("latin-1").strip()
        if not line:
            break
        k, _, v = line.partition(":")
        got[k.strip().lower()] = v.strip()
    if " 101 " not in status or got.get("sec-websocket-accept") != accept_key(key):
        sock.close()
        raise ConnectionError(f"relay refused: {status.strip()}")
    sock.settimeout(None)
    return WebSocket(sock, rfile, client=True)


def accept(handler):
    """Server side for a BaseHTTPRequestHandler: answers the upgrade and returns a WebSocket."""
    handler.send_response(101)
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept_key(handler.headers.get("Sec-WebSocket-Key", "")))
    handler.end_headers()
    handler.wfile.flush()
    handler.close_connection = True
    return WebSocket(handler.connection, handler.rfile, client=False)
