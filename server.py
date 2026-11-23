# SlideLink server: HTTP API and live sync (SSE). The parts live in core, importers, devices, network, decks and watch.
# Standard library only.
"""SlideLink server - live-synced presentations for PC, Android and Claude.

Run:  python server.py        (then open http://localhost:8765)
Pure Python standard library, no installs needed.

Security model: the PC itself (localhost) is always trusted. Every other
device must be linked once with a 6-digit code shown on the PC; it then gets a
permanent token (stored as a cookie + in the browser) and stays linked until
it is unlinked from "Linked devices".
"""
import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import watch  # noqa: F401  (the desktop app sets server.watch.ON_NOTIFY)
from core import AUDIENCE, COOKIE, COOKIE_AGE, DATA_DIR, GROUP_COOKIE, GROUP_OPS, HOST, IMAGE_EXT, IMPORT_ORIGINS, KICK, LAYOUTS, LOCAL_HOSTS, MEDIA_DIR, MEDIA_PATH, PORT, REMOTE_ONLY, REMOTE_OPS, REPLACED, SLIDE_FIELDS, STATIC, STATUSES, THEMES, WATCH_DIR, make_slide, new_id
from importers import claude_to_import, upload_to_op
from devices import APP_DIR_PULLED, app_folder, app_latest, registry  # the first and third: for the desktop app
from network import LOCAL_BIND, RELAY, TLS, lan_ips, phone_urls, relay_status, set_relay, start_https, start_relay
from decks import e2e_server, library, pairing, sessions, store
from watch import watcher

ON_SHOW_PC = None                          # set by the desktop app: opens the presenter window on the PC
ON_OPEN_PC = None                          # set by the desktop app: opens the SlideLink app window on the PC
ON_CLOSE_PC = None                         # set by the desktop app: closes the SlideLink windows on the PC


# Starts the folder watcher (called by the desktop app and main()).
def start_background():
    start_relay()
    threading.Thread(target=start_https, args=(Handler,), daemon=True).start()
    if not watcher.is_alive():
        watcher.start()


# ---------------------------------------------------------------- HTTP
# The HTTP request handler: web UI, REST API, live events, uploads.
class Handler(BaseHTTPRequestHandler):
    server_version = "SlideLink/1.4.2"

    # Logs requests from phones (path only, no tokens); the PC itself stays quiet.
    def log_message(self, fmt, *args):
        # the PC itself stays quiet; phones/tablets are logged (path only, no tokens) to diagnose remote problems
        try:
            if self.client_address[0] in ("127.0.0.1", "::1"):
                return
            path = urlparse(self.path).path
            if path.startswith("/media/") or path.endswith((".png", ".svg", ".ico", ".webmanifest")):
                return
            linked = "linked" if registry.by_token(self._token()) else "not linked"
            print(f"  phone {self.client_address[0]} {self.command} {path} -> {args[1] if len(args) > 1 else '?'} ({linked})")
        except Exception:
            pass

    # ----- auth -----
    # True for the PC itself: 127.0.0.1 without proxy headers (Funnel/proxies never count as the PC).
    def _is_host(self):
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        # DNS rebinding: a website can point its own name at 127.0.0.1 and talk to SlideLink from the PC's browser.
        # Such requests carry the website's name as Host, so only the real local names count as the PC.
        host = (self.headers.get("Host") or "").lower()
        if host not in LOCAL_HOSTS:
            return False
        # A local proxy (the relay client, reverse proxies) forwards other devices from
        # 127.0.0.1 - those must still be linked.
        proxied = ("X-Forwarded-For", "Forwarded", "X-SlideLink-Relay", "X-Real-IP", "X-SlideLink-E2E")
        return not any(self.headers.get(h) for h in proxied)

    # The device token from the sl_token cookie.
    def _cookie_token(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        return c[COOKIE].value if COOKIE in c else None

    # The device token from cookie or X-SlideLink-Token header.
    def _token(self):
        return self._cookie_token() or self.headers.get("X-SlideLink-Token")

    # Who is asking: the PC (HOST), a linked device, or None.
    def _e2e_inner(self):
        """True for a request that e2e_session decrypted and runs here on behalf of a paired phone (loopback + secret)."""
        mark = self.headers.get("X-SlideLink-E2E")
        return bool(mark and sessions and self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
                    and secrets.compare_digest(mark, sessions.internal))

    def auth(self):
        if self.headers.get("X-SlideLink-E2E"):
            # the phone proved itself with its end-to-end key: it is that device, whatever token or cookie it sends
            if not self._e2e_inner():
                return None
            dev = sessions.device_for(self.headers.get("X-SlideLink-E2E-Device") or "")
            if dev:
                dev["last_seen"] = time.time()
            return dev
        if self._is_host():
            return HOST
        dev = registry.by_token(self._token())
        if dev:
            dev["last_seen"] = time.time()
            return dev
        # group member? (cookie sl_group or header X-SlideLink-Group)
        gtok = self.headers.get("X-SlideLink-Group")
        if not gtok:
            try:
                c = SimpleCookie(self.headers.get("Cookie") or "")
                gtok = c[GROUP_COOKIE].value if GROUP_COOKIE in c else None
            except CookieError:
                gtok = None
        with store.lock:
            return store.group_member(gtok)

    # Cookie header that stores (or clears) the device token for 10 years.
    @staticmethod
    def _set_cookie(token):
        if token is None:
            return ("Set-Cookie", f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        return ("Set-Cookie", f"{COOKIE}={token}; Max-Age={COOKIE_AGE}; Path=/; HttpOnly; SameSite=Lax")

    # ----- responses -----
    # Sends a JSON response.
    def _json(self, obj, code=200, headers=()):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # Sends a file (with the right content type; the APK as a download).
    def _file(self, path):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        self.send_response(200)
        if path.endswith(".apk"):   # the Android app, downloaded on the phone from SlideLink itself
            ctype = "application/vnd.android.package-archive"
            self.send_header("Content-Disposition", 'attachment; filename="SlideLink.apk"')
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # Reads and parses the JSON request body (with a size limit).
    def _body(self, limit=40_000_000):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # ----- routes -----
    # GET: web pages, static files, media, state, library, info, live events.
    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        if p in ("/", "/present", "/remote", "/edit", "/library", "/group"):
            return self._file(os.path.join(STATIC, "index.html"))
        if p == "/api/whoami":
            return self._whoami()
        if p == "/live":
            return self._file(os.path.join(STATIC, "live.html"))
        if p == "/events":
            qs = parse_qs(url.query)
            if (qs.get("role") or [""])[0] == "audience":
                if not secrets.compare_digest((qs.get("k") or [""])[0], store.audience_key()):
                    return self._json({"error": "This audience link is no longer valid."}, 403)
                return self._events(qs, AUDIENCE)
        if p.startswith("/api/") or p == "/events":
            dev = self.auth()
            if not dev:
                return self._json({"error": "This device is not linked", "linked": False}, 401)
            if dev.get("type") == "group" and p.startswith(("/api/library", "/api/info", "/api/watch")):
                return self._json({"error": "Group members control the presentation only."}, 403)
            if p == "/api/state":
                with store.lock:
                    return self._json(store.member_snapshot() if dev.get("type") == "group" else store.snapshot())
            if p == "/api/library":
                with store.lock:
                    return self._json({"active": store.deck["id"], "version": library.version,
                                       "decks": library.listing()})
            if p.startswith("/api/library/"):
                try:
                    return self._json(library.load(p.rsplit("/", 1)[1]))
                except ValueError as e:
                    return self._json({"error": str(e)}, 404)
            if p == "/api/watch":
                return self._json(watcher.status)
            if p == "/api/info":
                return self._json({"port": PORT, "ips": lan_ips(), "urls": phone_urls(), "relay": relay_status(), "tls": TLS, "layouts": LAYOUTS,
                                   "e2e": {"pub": e2e_server.b64(pairing.key.public)} if pairing else None,
                                   "statuses": STATUSES, "themes": THEMES})
            if p == "/events":
                return self._events(parse_qs(url.query), dev)
            return self._json({"error": "not found"}, 404)
        m = MEDIA_PATH.match(p)
        if m:
            return self._file(os.path.join(MEDIA_DIR, m.group(1), m.group(2)))
        if p in ("/SlideLink.apk", "/app-version.json"):
            return self._file(os.path.join(app_folder(), p.lstrip("/")))
        full = os.path.normpath(os.path.join(STATIC, p.lstrip("/")))
        if full.startswith(STATIC + os.sep) and os.path.isfile(full):
            return self._file(full)
        return self._json({"error": "not found"}, 404)

    # CORS headers for /api/import, only for claude.ai and the Chrome extension.
    def _cors_import(self):
        origin = self.headers.get("Origin") or ""
        if IMPORT_ORIGINS.match(origin):
            return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin"),
                    ("Access-Control-Allow-Methods", "POST, OPTIONS"),
                    ("Access-Control-Allow-Headers", "Content-Type"),
                    ("Access-Control-Allow-Private-Network", "true")]
        return None

    # CORS preflight for /api/import.
    def do_OPTIONS(self):
        cors = self._cors_import() if urlparse(self.path).path == "/api/import" else None
        self.send_response(204 if cors else 403)
        for k, v in cors or []:
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # CSRF protection: POSTs must come from SlideLink's own pages.
    def _same_origin(self):
        """Block other websites from driving SlideLink (CSRF): JSON only, and no foreign Origin."""
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == (self.headers.get("Host") or "")

    # POST: operations, linking, devices, uploads, imports, window control.
    def do_POST(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/import":
                # One-way import of a claude.ai Slides deck, sent from the user's own browser on this PC.
                cors = self._cors_import()
                if not cors or not self._is_host():
                    return self._json({"ok": False, "error": "forbidden"}, 403)
                body = self._body()
                if body.get("format") == "claude-raw":
                    body = claude_to_import(body)
                body["op"], body["by"] = "import_deck", "claude.ai sync"
                with store.lock:
                    result, _ = store.apply(body)
                store.publish()
                return self._json({"ok": True, "result": result}, headers=cors)
            if p == "/api/upload":
                return self._upload()
            if p == "/api/e2e/req":                       # encrypted request from a paired phone (binary, proven by its key)
                if not sessions:
                    return self._json({"ok": False, "error": "End-to-end is not available on this PC."}, 501)
                return sessions.handle(self)
            if not self._same_origin():
                return self._json({"ok": False, "error": "Cross-site request blocked"}, 403)
            if p == "/api/pair":
                return self._pair(self._body())
            if p == "/api/e2e/hello":
                return self._e2e_pair("hello", self._body())
            if p.startswith("/api/e2e/pair/"):
                return self._e2e_pair(p.rsplit("/", 1)[1], self._body())
            if p == "/api/group/join":
                body = self._body()
                name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:30]
                with store.lock:
                    g = store.group_active()
                    if not g or not secrets.compare_digest(str(body.get("k", "")), g["key"]):
                        return self._json({"ok": False, "error": "This group session has ended or the link is wrong."}, 403)
                    if not name:
                        return self._json({"ok": False, "error": "Please enter your name."}, 400)
                    mid, token = new_id(), secrets.token_urlsafe(24)
                    g["members"][mid] = {"name": name, "joined": time.time(),
                                         "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest()}
                    store.log(name, "joined the group")
                    age = max(60, int(g["expires"] - time.time()))
                store.publish()
                return self._json({"ok": True, "token": token, "name": name},
                                  headers=[("Set-Cookie", f"{GROUP_COOKIE}={token}; Max-Age={age}; Path=/; HttpOnly; SameSite=Lax")])
            if p == "/api/bye":
                # a window is closing: drop its live connection now (otherwise noticed only at the next ping)
                client = str(self._body().get("client", ""))[:16]
                if client:
                    with store.lock:
                        gone = [cid for cid in store.clients if cid.endswith(client)]
                        for cid in gone:
                            store.clients.pop(cid, None)
                    if gone:
                        store.publish()
                return self._json({"ok": True})
            if p == "/api/vote":
                # audience vote: the audience key is the ticket; one vote per viewer (can be changed while open)
                body = self._body()
                if not secrets.compare_digest(str(body.get("k", "")), store.audience_key()):
                    return self._json({"ok": False, "error": "This audience link is no longer valid."}, 403)
                voter = str(body.get("voter", ""))[:40]
                with store.lock:
                    poll = store.poll
                    if not poll or not poll["open"] or poll["id"] != body.get("poll") or not voter:
                        return self._json({"ok": False, "error": "This poll is closed."}, 400)
                    try:
                        choice = int(body.get("option"))
                    except (TypeError, ValueError):
                        choice = -1
                    if not 0 <= choice < len(poll["options"]):
                        return self._json({"ok": False, "error": "Unknown answer"}, 400)
                    poll["votes"][voter] = choice
                store.publish()
                return self._json({"ok": True})
            if p == "/api/clientlog":
                # script errors from a phone/tablet, so a black screen there leaves a trace on the PC
                msg = str(self._body().get("msg", ""))[:500].replace("\n", " ")
                print(f"  client error from {self.client_address[0]}: {msg}")
                return self._json({"ok": True})
            dev = self.auth()
            if not dev:
                return self._json({"ok": False, "error": "This device is not linked", "linked": False}, 401)
            body = self._body()
            if p == "/api/op":
                return self._op(body, dev)
            if p == "/api/relay":
                # the PC sets (or clears) its relay address: {"url": "https://…"} or {"url": ""}
                if dev is not HOST:
                    return self._json({"ok": False, "error": "Set the relay on the PC."}, 403)
                url = str(body.get("url") or "").strip().rstrip("/")
                if url and url != "off" and not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", url):
                    return self._json({"ok": False, "error": "Not a web address."}, 400)
                set_relay(url)
                return self._json({"ok": True, "relay": relay_status()})
            if p == "/api/watch/open":
                if dev is not HOST or os.name != "nt":
                    return self._json({"ok": False, "error": "Open the folder on the PC."}, 403)
                os.startfile(WATCH_DIR)
                return self._json({"ok": True})
            if p == "/api/devices/code":
                code, exp = registry.new_code()
                return self._json({"ok": True, "code": code, "expires_at": exp, "urls": phone_urls(), "relay": relay_status()})
            if p == "/api/devices/rename":
                d = registry.rename(body.get("id"), body.get("name"))
                store.publish()
                return self._json({"ok": True, "device": {"id": d["id"], "name": d["name"]}})
            if p == "/api/devices/unlink":
                return self._unlink(body, dev)
            return self._json({"error": "not found"}, 404)
        except (ValueError, TypeError, KeyError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)

    # Handles a file upload (PowerPoint, zip, html, json, pictures).
    def _upload(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != (self.headers.get("Host") or ""):
            return self._json({"ok": False, "error": "Cross-site request blocked"}, 403)
        dev = self.auth()
        if not dev:
            return self._json({"ok": False, "error": "This device is not linked", "linked": False}, 401)
        if dev is not HOST and dev.get("type") in REMOTE_ONLY:
            return self._json({"ok": False, "error": "Upload presentations on the PC."}, 403)
        qs = parse_qs(urlparse(self.path).query)
        name = (qs.get("name") or ["upload"])[0][:200]
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 300_000_000:
            raise ValueError("file is empty or larger than 300 MB")
        data = self.rfile.read(length)
        append_to = (qs.get("deck") or [None])[0]
        op = upload_to_op(name, data)
        by = "PC" if dev is HOST else dev["name"]
        with store.lock:
            if append_to and os.path.splitext(name.lower())[1] in IMAGE_EXT:   # more images for the same deck
                d = library.load(append_to)
                d["slides"].extend(dict(make_slide(**{k: v for k, v in sl.items() if k in SLIDE_FIELDS}), updated_by=by)
                                   for sl in op["slides"])
                library.save(d)
                if store.deck["id"] == d["id"]:
                    store.deck = d
                result = {"deck_id": d["id"], "slides": len(d["slides"])}
            else:
                op["by"] = by
                result, _ = store.apply(op)
        store.publish()
        print(f"  upload: {name} -> {result}")
        return self._json({"ok": True, "result": result})

    # Tells a client whether it is linked (and as which device).
    def _whoami(self):
        dev = self.auth()
        headers = []
        if dev and dev is not HOST and not self._cookie_token():
            headers.append(self._set_cookie(self.headers.get("X-SlideLink-Token")))  # restore lost cookie
        info = {"id": dev["id"], "name": dev["name"], "type": dev["type"]} if dev else None
        return self._json({"linked": bool(dev), "is_host": dev is HOST, "device": info}, headers=headers)

    # Links a device with the 6-digit code and sets its token cookie.
    def _pair(self, body):
        dev, token = registry.pair(body.get("code"), body.get("name"), body.get("type"))
        store.log(dev["name"], "was linked to this PC")
        store.publish()
        print(f"  Linked new device: {dev['name']}")
        return self._json({"ok": True, "token": token,
                           "device": {"id": dev["id"], "name": dev["name"], "type": dev["type"]}},
                          headers=[self._set_cookie(token)])

    # End-to-end pairing steps (start/reveal/status from the app, confirm from the PC itself).
    def _e2e_pair(self, step, body):
        if not pairing:
            return self._json({"ok": False, "error": "End-to-end pairing is not available on this PC."}, 501)
        if step == "start":
            return self._json(pairing.start(body))
        if step == "reveal":
            return self._json(pairing.reveal(body))
        if step == "status":
            return self._json(pairing.status(body))
        if step == "hello":
            return self._json(sessions.hello(body))
        if step == "confirm":
            if not self._is_host():
                return self._json({"ok": False, "error": "Only the PC itself can confirm a pairing."}, 403)
            dev = pairing.confirm(body.get("id"), bool(body.get("yes")))
            if dev:
                store.log(dev["name"], "was linked to this PC (end-to-end)")
                print(f"  Linked new device (end-to-end): {dev['name']}")
            store.publish()
            return self._json({"ok": True})
        return self._json({"ok": False, "error": "not found"}, 404)

    # Unlinks a device (not the PC) and disconnects it.
    def _unlink(self, body, dev):
        did = body.get("id")
        if did == "host":
            raise ValueError("The PC running SlideLink is always linked")
        d = registry.unlink(did)
        store.log(dev["name"], f"unlinked {d['name']}")
        store.kick(did)
        store.publish()
        headers = [self._set_cookie(None)] if dev.get("id") == did else []
        return self._json({"ok": True}, headers=headers)

    # Runs an operation; phones may only use remote operations.
    def _op(self, op, dev):
        if dev.get("type") == "group" and op.get("op") not in GROUP_OPS:
            return self._json({"ok": False, "error": "Group members control the presentation only."}, 403)
        if op.get("op") == "speaker_me":       # "I'm presenting" on a phone: that phone's name
            op = {"op": "speaker_set", "name": "PC" if dev is HOST else dev["name"]}
        if dev is not HOST:
            op["by"] = dev["name"]  # linked devices are shown by name in the activity feed
            if dev.get("type") in REMOTE_ONLY and op.get("op") not in REMOTE_OPS:
                return self._json({"ok": False, "error": "The phone is a remote control - edit presentations on the PC."}, 403)
        if op.get("op") == "pointer":
            store.pointer(op)
            return self._json({"ok": True})
        if op.get("op") in ("show_on_pc", "open_on_pc", "close_on_pc"):
            # windows on the PC, asked for from the phone: presenter (borderless fullscreen), app window, close all
            hook = {"show_on_pc": ON_SHOW_PC, "open_on_pc": ON_OPEN_PC, "close_on_pc": ON_CLOSE_PC}[op["op"]]
            if not hook:
                raise ValueError("Start the SlideLink desktop app on the PC to control its windows from here.")
            threading.Thread(target=hook, daemon=True).start()
            store.log(op.get("by") or "you", {"show_on_pc": "showed the presentation on the PC",
                                              "open_on_pc": "opened SlideLink on the PC",
                                              "close_on_pc": "closed the SlideLink windows on the PC"}[op["op"]])
            return self._json({"ok": True})
        with store.lock:
            result, deck_changed = store.apply(op)
            if deck_changed:
                store.save()
        store.publish(origin=op.get("origin"))
        return self._json({"ok": True, "result": result})

    # Server-Sent Events stream: sends the state on every change and a ping every 10 seconds.
    def _events(self, qs, dev):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = queue.Queue()
        client = (qs.get("client") or [""])[0][:16]
        cid = new_id() + client
        role = (qs.get("role") or ["viewer"])[0][:16]
        appv = (qs.get("appv") or [""])[0][:12]      # the phone app tells its version when it connects
        with store.lock:
            # The same page (same client id) or the same phone app reconnecting, e.g. after Wi-Fi -> mobile data:
            #     its old connection may still look open for minutes (the old network vanished without a goodbye) - drop it now.
            for old_id, c in list(store.clients.items()):
                same_page = client and c.get("client") == client and c["device"] == dev["id"]
                same_app = appv and dev is not HOST and c.get("app") and c["device"] == dev["id"] and c["role"] == role
                if same_page or same_app:
                    c["q"].put(REPLACED)
                    store.clients.pop(old_id, None)
            ip = self.client_address[0]
            via = ("pc" if dev is HOST else "e2e" if self._e2e_inner() else "relay" if self.headers.get("X-SlideLink-Relay")
                   else "internet" if ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") else "wifi")   # 127.0.0.1 + linked device = through a proxy
            store.clients[cid] = {"role": role, "device": dev["id"], "q": q, "via": via, "client": client, "app": bool(appv)}
            if appv and dev is not HOST and dev.get("app_version") != appv:
                dev["app_version"] = appv
                registry.save()
        store.publish()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            while True:
                try:
                    data = q.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b"event: ping\ndata: {}\n\n")
                else:
                    if data is REPLACED:
                        break
                    if data is KICK:
                        self.wfile.write(b"event: unlinked\ndata: {}\n\n")
                        self.wfile.flush()
                        break
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with store.lock:
                store.clients.pop(cid, None)
            if dev is not HOST:
                dev["last_seen"] = time.time()
                try:
                    registry.save()
                except (OSError, ValueError):
                    pass
            store.publish()


# Runs the server on its own (without the desktop app).
def main():
    start_background()
    server = ThreadingHTTPServer((LOCAL_BIND, PORT), Handler)    # plain http: this PC only
    server.daemon_threads = True
    print("\n  SlideLink is running\n")
    print(f"  PC editor     http://localhost:{PORT}/")
    print(f"  Presenter     http://localhost:{PORT}/present")
    for u in phone_urls():
        print(f"  Phone         {u['url']:<44} {u['label']}")
    if not RELAY["url"]:
        print("\n  Tip: set a relay (SLIDELINK_RELAY or relay.json) to use SlideLink from anywhere.")
    n = len(registry.items)
    print(f"\n  {n} linked device{'s' if n != 1 else ''}. Link more in the editor: ⚙ Linked devices.")
    print(f"  {len(library.ids())} presentations in {DATA_DIR}\n  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
