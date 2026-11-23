"""How phones reach the PC: SlideLink Relay client, HTTPS for the home Wi-Fi, local addresses."""
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

from core import DATA_DIR, PORT

RELAY_FILE = os.path.join(DATA_DIR, "relay.json")
RELAY = {"url": "", "id": "", "key": "", "online": False}
# The relay every SlideLink uses out of the box (Cloudflare Worker from relay/worker). Empty until it is deployed.
DEFAULT_RELAY = "https://slidelink-relay.slidelink-app.workers.dev"


def _relay_config():
    try:
        with open(RELAY_FILE, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    changed = False
    if not cfg.get("id"):
        cfg["id"] = secrets.token_hex(6); changed = True        # address of this PC on the relay
    if not cfg.get("key"):
        cfg["key"] = secrets.token_urlsafe(24); changed = True   # proves to the relay that this is the PC
    url = (os.environ.get("SLIDELINK_RELAY") or cfg.get("url") or DEFAULT_RELAY).rstrip("/")
    if url == "off":
        url = ""
    if changed:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RELAY_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    RELAY.update(url=url, id=cfg["id"], key=cfg["key"])


def relay_status():
    """For the UI: is anywhere access on, and the phone address on the relay."""
    if not RELAY["url"]:
        return {"on": False, "online": False, "url": ""}
    return {"on": True, "online": RELAY["online"], "url": _relay_phone_url()}


def _relay_phone_url():
    """The phone address: own domain  https://{id}.domain  (own web origin per PC), else  <relay>/p/<id>."""
    if "{id}" in RELAY["url"]:
        return RELAY["url"].replace("{id}", RELAY["id"])
    return f"{RELAY['url']}/p/{RELAY['id']}"


def _relay_base():
    """Where the PC's permanent line goes (the domain itself, without a PC id)."""
    return RELAY["url"].replace("{id}.", "")


_relay_cancel = set()
_relay_pieces = {}                # rid -> pieces of a big upload, sent before its request


def _relay_handle(ws, job):
    """Answers one phone request through this server and streams the answer back over the relay line."""
    import base64
    import http.client
    rid = job["rid"]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=600)
        headers = dict(job.get("headers") or {})
        headers["X-Forwarded-For"] = job.get("client", "")      # never counts as the PC itself (see _is_local)
        headers["X-SlideLink-Relay"] = "1"
        body = b"".join(_relay_pieces.pop(rid, [])) if job.get("pieces") else base64.b64decode(job.get("body") or "")
        conn.request(job["method"], job["path"], body=body or None, headers=headers)
        resp = conn.getresponse()
        ws.send(json.dumps({"t": "head", "rid": rid, "status": resp.status, "headers": [[k, v] for k, v in resp.getheaders()]}))
        prefix = rid.encode()
        while rid not in _relay_cancel:
            chunk = resp.read1(65536) if job["method"] != "HEAD" else b""
            if not chunk:
                break
            ws.send(prefix + chunk)
        conn.close()
    except Exception as e:
        print(f"  relay request failed: {e}")
    finally:
        _relay_cancel.discard(rid)
        try:
            ws.send(json.dumps({"t": "end", "rid": rid}))
        except Exception:
            pass


def _relay_pinger(ws):
    while not ws.closed:
        time.sleep(20)
        try:
            ws.send('{"t":"ping"}')
        except Exception:
            ws.close()
            return


def _relay_line():
    """Keeps the permanent line to the relay; reconnects by itself (2 s, 4 s … up to 60 s)."""
    import wsmini
    wait = 2
    while True:
        url = RELAY["url"]
        if not url:
            RELAY["online"] = False
            time.sleep(3)
            continue
        ws = None
        try:
            wsurl = _relay_base().replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/_pc/{RELAY['id']}/ws"
            ws = wsmini.connect(wsurl, {"X-Relay-Key": RELAY["key"]})
            RELAY["online"], RELAY["_ws"], wait = True, ws, 2
            threading.Thread(target=_relay_pinger, args=(ws,), daemon=True).start()
            while RELAY["url"] == url:
                msg = ws.recv()
                if isinstance(msg, bytes):
                    rid = msg[:16].decode("ascii", "replace")
                    if len(_relay_pieces) < 64 or rid in _relay_pieces:
                        _relay_pieces.setdefault(rid, []).append(msg[16:])
                    continue
                m = json.loads(msg)
                if m.get("t") == "req":
                    threading.Thread(target=_relay_handle, args=(ws, m), daemon=True).start()
                elif m.get("t") == "cancel":
                    _relay_cancel.add(m.get("rid"))
        except Exception as e:
            if RELAY["online"]:
                print(f"  relay line lost: {e}")
        RELAY["online"] = False
        if ws:
            ws.close()
        time.sleep(wait)
        wait = min(wait * 2, 60)


# HTTPS for the home Wi-Fi: this PC's own certificate (made once by make_cert.ps1). Browsers would warn about it,
#     but the SlideLink app trusts exactly this certificate (its fingerprint comes with the PC's addresses).
HTTPS_PORT = PORT + 1
TLS_DIR = os.path.join(DATA_DIR, "tls")
TLS = {"on": False, "sha256": "", "port": HTTPS_PORT}


def start_https(handler):
    import ssl
    cert, key = os.path.join(TLS_DIR, "cert.pem"), os.path.join(TLS_DIR, "key.pem")
    try:
        if not (os.path.exists(cert) and os.path.exists(key)) and os.name == "nt":
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_cert.ps1")
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-Out", TLS_DIR],
                           capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        with open(cert, encoding="ascii") as f:
            TLS["sha256"] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(f.read())).hexdigest()
        httpd = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), handler)
        httpd.daemon_threads = True
        # the handshake runs in each request's own thread, so one slow phone can't hold up the others
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        TLS["on"] = True
        print(f"  https (Wi-Fi) on port {HTTPS_PORT}")
    except Exception as e:
        print(f"  https not available: {e}")


_relay_started = []


def start_relay():
    _relay_config()
    if not _relay_started:
        _relay_started.append(True)
        threading.Thread(target=_relay_line, daemon=True).start()
        if RELAY["url"]:
            print(f"  relay: {_relay_phone_url()}")


def set_relay(url):
    """Saves the relay address ("" = the built-in one, "off" = no relay) and switches right away."""
    try:
        with open(RELAY_FILE, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg["url"] = url
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RELAY_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    _relay_config()
    old = RELAY.pop("_ws", None)
    if old:
        old.close()                              # the line reconnects to the new address
    start_relay()


# True for addresses of the carrier-grade NAT range (100.64.0.0/10), which a phone can't use on Wi-Fi.
CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_cgnat(ip):
    try:
        return ipaddress.ip_address(ip) in CGNAT
    except ValueError:
        return False


# All IPv4 addresses of this PC.
def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the LAN interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


# Home-network addresses of this PC (no loopback, no carrier NAT).
def lan_ips():
    return [ip for ip in local_ips() if not _is_cgnat(ip)]


# Base address for the audience link: the relay (reachable from any network) if it is on.
def audience_base():
    return relay_status()["url"]


# Plain http (port 8765) is only for this PC's own window, Claude and the Chrome extension: phones use HTTPS.
LOCAL_BIND = "127.0.0.1"


def phone_urls():
    """Addresses a phone can use, best first."""
    urls = []
    r = relay_status()
    if r["on"]:
        urls.append({"url": r["url"], "label": "Anywhere – SlideLink Relay", "kind": "relay"})
    if TLS["on"]:                                # encrypted first (the app trusts this PC's own certificate)
        for ip in lan_ips():
            urls.append({"url": f"https://{ip}:{HTTPS_PORT}", "label": "Same Wi-Fi, encrypted (SlideLink app)", "kind": "lan"})
    # no plain http for phones: port 8765 only listens on the PC itself (127.0.0.1)
    return urls
