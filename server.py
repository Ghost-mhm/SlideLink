"""SlideLink server - live-synced presentations for PC, Android and Claude.

Run:  python server.py        (then open http://localhost:8765)
Pure Python standard library, no installs needed.

Security model: the PC itself (localhost) is always trusted. Every other
device must be linked once with a 6-digit code shown on the PC; it then gets a
permanent token (stored as a cookie + in the browser) and stays linked until
it is unlinked from "Linked devices".
"""
import hashlib
import html as htmllib
import io
import json
import mimetypes
import datetime
import os
import queue
import ipaddress
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
import zipfile
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")


def _default_data_dir():
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SlideLink", "data")
    return os.path.join(os.path.expanduser("~"), ".slidelink")


DATA_DIR = os.environ.get("SLIDELINK_DATA") or _default_data_dir()
DECKS_DIR = os.path.join(DATA_DIR, "decks")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
LIBRARY_FILE = os.path.join(DATA_DIR, "library.json")
PORT = int(os.environ.get("SLIDELINK_PORT", "8765"))
os.makedirs(DECKS_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)

LAYOUTS = ["title", "content", "section", "two-column", "quote", "image", "html", "page", "doc"]
STATUSES = ["draft", "in-progress", "review", "done"]
THEMES = ["midnight", "paper", "ocean", "sunset"]
SLIDE_FIELDS = ["title", "content", "html", "notes", "layout", "status"]
DECK_ID = re.compile(r"^[a-z0-9]{6,32}$")
BUILD_ATTR = re.compile(r'data-build-(?:in|out)="([^"]*)"', re.I)
FRAGMENT = re.compile(r'class="[^"]*\bfragment\b', re.I)


def build_steps(slide):
    """How many clicks a designed slide needs before moving on (claude.ai build semantics):
    every build item without 'auto' starts a new click; each .fragment is one click."""
    html = (slide or {}).get("html") or ""
    if (slide or {}).get("layout") in ("page", "doc"):
        return 10_000          # an interactive page: every Next/Back goes to the page itself
    if (slide or {}).get("layout") != "html" or not html:
        return 0
    # items sharing an order number play together on one click; 'auto' items follow without a click
    items = []
    for m in BUILD_ATTR.finditer(html):
        parts = m.group(1).split()
        order = next((int(p) for p in parts if p.isdigit()), 0)
        items.append((order, "auto" in parts))
    items.sort(key=lambda x: x[0])
    clicks, last = 0, None
    for order, auto in items:
        if not auto and order != last:
            clicks += 1
            last = order
    return clicks + len(FRAGMENT.findall(html))


IMPORT_ORIGINS = re.compile(r"^(https://([a-z0-9-]+\.)*(claudeusercontent\.com|claude\.ai|claude\.site)|chrome-extension://[a-p]{32})$")
MEDIA_PATH = re.compile(r"^/media/([0-9a-f]{24})/([A-Za-z0-9._-]{1,80})$")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PPT_EXT = {".pptx", ".ppt", ".odp", ".ppsx", ".pps", ".potx"}


# ---------------------------------------------------------------- importing other formats
def _text(fragment):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_claude_slide(src):
    """Split a claude.ai slide file into (html without notes, speaker notes, title)."""
    notes = ""
    m = re.search(r"<aside[^>]*>([\s\S]*?)</aside>", src, re.I)
    if m:
        notes = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        src = src[:m.start()] + src[m.end():]
    title = ""
    for tag in ("h1", "h2", "h3", "p"):
        m = re.search(rf"<{tag}\b[^>]*>([\s\S]*?)</{tag}>", src, re.I)
        if m and _text(m.group(1)):
            title = _text(m.group(1))[:120]
            break
    return src.strip(), notes, title


def claude_to_import(raw):
    """Raw claude.ai deck {slug, url, index, css, slides:[{id, html}]} -> import_deck op."""
    idx = raw.get("index") or {}
    slides = []
    for sd in raw.get("slides") or []:
        body, notes, title = parse_claude_slide(str(sd.get("html") or ""))
        sid = re.sub(r"[^A-Za-z0-9_-]", "", str(sd.get("id") or ""))[:60]
        hidden = bool(re.search(r"<section\b[^>]*\shidden\b", body, re.I))
        slides.append({"id": ("c-" + sid) if sid else None, "layout": "html", "html": body, "notes": notes,
                       "title": title or sid, "status": "draft" if hidden else "done"})
    faces = [{"family": v.get("family", ""), "href": v["href"]}
             for v in (idx.get("faces") or {}).values() if isinstance(v, dict) and v.get("href")]
    return {"op": "import_deck", "title": idx.get("title") or "claude.ai presentation", "css": raw.get("css") or "",
            "faces": faces, "slides": slides,
            "source": {"kind": "claude-slides", "slug": str(raw.get("slug") or "")[:80], "url": str(raw.get("url") or "")}}


def html_to_import(text, title):
    """A single-file deck: <head> fonts/styles + one <section> per slide."""
    t = re.search(r"<title[^>]*>([\s\S]*?)</title>", text, re.I)
    faces = [{"family": "", "href": htmllib.unescape(h)}
             for h in re.findall(r'<link[^>]+href="(https://fonts\.googleapis\.com/[^"]+)"', text, re.I)]
    css = "\n".join(re.findall(r"<style[^>]*>([\s\S]*?)</style>", text, re.I))
    bs = re.search(r'<body[^>]*\sstyle="([^"]*)"', text, re.I)
    if bs:
        css += "\nbody{" + htmllib.unescape(bs.group(1)) + "}"
    # Only Claude's Slides format (sections with slide markers) is split into slides; any other page
    # (web page, app, quiz, scrolling document) stays one complete, untouched interactive page.
    is_deck = re.search(r"<section\b[^>]*\bdata-(?:transition|section|build-in)\b", text, re.I) or \
        re.search(r'<meta[^>]+name="slidelink"[^>]+content="deck"', text, re.I)
    secs = re.findall(r"<section\b[\s\S]*?</section>", text, re.I) if is_deck else []
    if not secs:
        # not a slide deck but an interactive page (quiz, app, own navigation): keep it exactly as it is
        return {"op": "import_deck", "title": _text(t.group(1)) if t else title, "source": None,
                "slides": [{"layout": "page", "html": text, "title": (_text(t.group(1)) if t else title)[:120],
                            "status": "done", "notes": "Interactive page: Next/Back on the phone or keyboard control it."}]}
    raw = {"index": {"title": _text(t.group(1)) if t else title}, "css": css,
           "slides": [{"id": f"s{i + 1}", "html": sec} for i, sec in enumerate(secs)]}
    op = claude_to_import(raw)
    op["faces"], op["source"] = faces, None
    return op


def zip_to_import(data, title):
    z = zipfile.ZipFile(io.BytesIO(data))
    infos = z.infolist()
    if len(infos) > 3000 or sum(i.file_size for i in infos) > 300_000_000:
        raise ValueError("zip file is too large")
    names = [i.filename for i in infos if not i.is_dir()]
    exp = [n for n in names if n.endswith("slidelink.json")]
    if exp:
        return json_to_import(z.read(exp[0]), title)
    decks = sorted((n for n in names if n.endswith("deck.json")), key=lambda n: n.count("/"))
    if not decks:
        htmls = [n for n in names if n.lower().endswith((".html", ".htm"))]
        if len(htmls) == 1:
            return html_to_import(z.read(htmls[0]).decode("utf-8", "replace"), title)
        raise ValueError("No deck.json found in the zip (expected a claude.ai deck folder)")
    base = decks[0][:-len("deck.json")]
    index = json.loads(z.read(decks[0]).decode("utf-8-sig"))
    css = ""
    for cand in [base + "deck.css"] + [base + sp.split("project/", 1)[-1] for sp in (index.get("format") or {}).get("shared", [])]:
        if cand in names:
            css = z.read(cand).decode("utf-8", "replace")
            break
    files = {n[len(base + "slides/"):-5]: n for n in names
             if n.startswith(base + "slides/") and n.endswith(".html") and "/" not in n[len(base + "slides/"):]}
    order = [sid for sid in index.get("order", []) if sid in files] + sorted(set(files) - set(index.get("order", [])))
    raw = {"index": index, "css": css, "slides": [{"id": sid, "html": z.read(files[sid]).decode("utf-8", "replace")} for sid in order]}
    if not raw["slides"]:
        raise ValueError("The zip has a deck.json but no slides/ folder")
    op = claude_to_import(raw)
    op["source"] = None
    if not index.get("title"):
        op["title"] = title
    return op


def json_to_import(data, title):
    d = json.loads(data.decode("utf-8-sig") if isinstance(data, bytes) else data)
    if isinstance(d, dict) and isinstance(d.get("slides"), list):
        return {"op": "import_deck", "title": d.get("title") or title, "theme": d.get("theme"), "css": d.get("css") or "",
                "faces": d.get("faces") or [], "slides": d["slides"], "source": None}
    raise ValueError("This JSON file is not a SlideLink presentation")


def _media_dir():
    tok = secrets.token_hex(12)
    path = os.path.join(MEDIA_DIR, tok)
    os.makedirs(path, exist_ok=True)
    return tok, path


def image_slide(tok, fname, title="", notes=""):
    return {"layout": "html", "title": title, "notes": notes, "status": "done",
            "html": f'<section style="background:#000;padding:0;display:flex;align-items:center;justify-content:center">'
                    f'<img src="/media/{tok}/{fname}" alt="" style="width:1920px;height:1080px;object-fit:contain"></section>'}


PPT_SCRIPT = r"""param([string]$src, [string]$out)
$ErrorActionPreference = 'Stop'
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open($src, -1, 0, 0)
$w = 1920; $h = [int](1920 * $pres.PageSetup.SlideHeight / $pres.PageSetup.SlideWidth)
$items = @()
foreach ($sl in $pres.Slides) {
  $f = ('{0:D3}.png' -f $sl.SlideIndex)
  $sl.Export((Join-Path $out $f), 'PNG', $w, $h)
  $title = ''; try { if ($sl.Shapes.HasTitle) { $title = $sl.Shapes.Title.TextFrame.TextRange.Text } } catch {}
  $notes = ''; try { foreach ($sh in $sl.NotesPage.Shapes) { if ($sh.PlaceholderFormat.Type -eq 2) { $notes = $sh.TextFrame.TextRange.Text } } } catch {}
  $items += [pscustomobject]@{ file = $f; title = $title; notes = $notes; hidden = [bool]$sl.SlideShowTransition.Hidden }
}
$pres.Close()
if ($pp.Presentations.Count -eq 0) { $pp.Quit() }
ConvertTo-Json -InputObject @($items) -Depth 3 | Set-Content -Encoding UTF8 (Join-Path $out 'slides.json')
"""


def powerpoint_to_import(data, ext, title):
    """Uses the installed PowerPoint to turn every slide into a full-quality picture (+ notes)."""
    if os.name != "nt":
        raise ValueError("PowerPoint import needs Windows with PowerPoint installed")
    tok, out = _media_dir()
    work = os.path.join(MEDIA_DIR, "_work_" + tok)
    os.makedirs(work, exist_ok=True)
    src = os.path.join(work, "upload" + ext)
    script = os.path.join(work, "export.ps1")
    try:
        with open(src, "wb") as f:
            f.write(data)
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(PPT_SCRIPT)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", script, src, out], capture_output=True, timeout=900, creationflags=NO_WINDOW)
        meta = os.path.join(out, "slides.json")
        if r.returncode or not os.path.exists(meta):
            err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise ValueError("PowerPoint could not convert the file" + (": " + err[0][:200] if err else ""))
        with open(meta, encoding="utf-8-sig") as f:
            items = json.load(f)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    slides = []
    for it in items if isinstance(items, list) else [items]:
        sl = image_slide(tok, it["file"], (it.get("title") or "").replace("\r", " ").strip()[:120],
                         (it.get("notes") or "").replace("\r", "\n").strip())
        if it.get("hidden"):
            sl["status"] = "draft"
        slides.append(sl)
    if not slides:
        raise ValueError("The presentation has no slides")
    return {"op": "import_deck", "title": title, "slides": slides, "source": None}


def upload_to_op(name, data):
    ext = os.path.splitext(name.lower())[1]
    title = os.path.splitext(os.path.basename(name))[0][:120] or "Upload"
    if ext in PPT_EXT:
        return powerpoint_to_import(data, ext, title)
    if ext == ".zip":
        return zip_to_import(data, title)
    if ext == ".json":
        return json_to_import(data, title)
    if ext in (".html", ".htm"):
        return html_to_import(data.decode("utf-8", "replace"), title)
    if ext in IMAGE_EXT:
        tok, out = _media_dir()
        fname = "image" + ext
        with open(os.path.join(out, fname), "wb") as f:
            f.write(data)
        return {"op": "import_deck", "title": title, "slides": [image_slide(tok, fname, title)], "source": None}
    raise ValueError(f"Unsupported file type '{ext or name}'. Use PowerPoint, a deck .zip, .html, .json or images.")
DEVICE_TYPES = ["phone", "tablet", "pc"]
CODE_TTL = 600            # link codes are valid for 10 minutes
COOKIE = "sl_token"
COOKIE_AGE = 10 * 365 * 24 * 3600
HOST = {"id": "host", "name": "This PC", "type": "host"}
REMOTE_ONLY = {"phone", "tablet"}          # these devices control presentations but never edit them
REMOTE_OPS = {"next", "prev", "goto", "blackout", "open_deck", "show_on_pc", "open_on_pc", "close_on_pc", "resync"}
ON_SHOW_PC = None                          # set by the desktop app: opens the presenter window on the PC
ON_OPEN_PC = None                          # set by the desktop app: opens the SlideLink app window on the PC
ON_CLOSE_PC = None                         # set by the desktop app: closes the SlideLink windows on the PC
PC_ROLES = {"library", "present", "edit"}  # SSE roles that are windows on the PC
ON_NOTIFY = None                           # set by the desktop app: tray notification (title, text)
WATCH_DIR = os.environ.get("SLIDELINK_WATCH") or os.path.join(os.path.expanduser("~"), "Claude Presentations")
KICK = object()           # queue sentinel: device was unlinked


def new_id():
    return uuid.uuid4().hex[:8]


def app_build():
    """Changes whenever a file of the app itself changes, so open windows know to reload."""
    try:
        return str(int(max(os.path.getmtime(os.path.join(STATIC, f)) for f in os.listdir(STATIC))))
    except (OSError, ValueError):
        return "0"


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def make_slide(**fields):
    s = {"id": new_id(), "title": "", "content": "", "notes": "", "layout": "content",
         "status": "draft", "updated_at": time.time(), "updated_by": "you"}
    for k in SLIDE_FIELDS:
        if k in fields and fields[k] is not None:
            s[k] = fields[k]
    return s


def sample_deck():
    return {
        "title": "My first SlideLink deck",
        "theme": "midnight",
        "slides": [
            make_slide(title="Welcome to SlideLink", layout="title", status="done",
                       content="Build, sync and present - on PC, phone and with Claude",
                       notes="Say hi. Mention that every device is live."),
            make_slide(title="How it works", status="review",
                       content="- Edit slides on your PC or phone\n- Changes appear everywhere instantly\n"
                               "- Claude can write and edit slides while you watch\n"
                               "  - its progress shows live on every device\n"
                               "- Use your phone as a remote to switch slides",
                       notes="Open the remote on the phone to demo."),
            make_slide(title="Two columns", layout="two-column", status="draft",
                       content="### PC\n- Editor\n- Presenter screen\n---\n### Android\n- Remote control\n- Speaker notes\n- Editing on the go"),
        ],
    }


# ---------------------------------------------------------------- devices
_old_devices = os.path.join(ROOT, "devices.json")
if os.path.exists(_old_devices) and not os.path.exists(DEVICES_FILE):
    shutil.copyfile(_old_devices, DEVICES_FILE)


class Registry:
    """Linked devices. Tokens are stored only as SHA-256 hashes."""

    def __init__(self):
        self.lock = threading.RLock()
        self.code = None
        self.code_exp = 0
        self.code_tries = 0
        try:
            with open(DEVICES_FILE, encoding="utf-8") as f:
                self.items = json.load(f).get("devices", [])
        except (FileNotFoundError, ValueError):
            self.items = []

    def save(self):
        with self.lock:
            write_json(DEVICES_FILE, {"devices": self.items})

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _clean_name(name, fallback="Phone"):
        name = " ".join(str(name or "").split())[:40]
        return name or fallback

    def by_token(self, token):
        if not token:
            return None
        h = self._hash(token)
        with self.lock:
            for d in self.items:
                if secrets.compare_digest(d["token_hash"], h):
                    return d
        return None

    def get(self, did):
        with self.lock:
            for d in self.items:
                if d["id"] == did:
                    return d
        raise ValueError("Unknown device")

    def new_code(self):
        with self.lock:
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.code_exp = time.time() + CODE_TTL
            self.code_tries = 0
            return self.code, self.code_exp

    def pair(self, code, name, kind):
        with self.lock:
            if not self.code or time.time() > self.code_exp:
                raise ValueError("No active link code. On the PC open ⚙ Linked devices → Link a new device.")
            if not secrets.compare_digest(str(code or "").strip().replace(" ", ""), self.code):
                self.code_tries += 1
                if self.code_tries >= 8:
                    self.code = None
                    raise ValueError("Too many wrong codes. Create a new code on the PC.")
                raise ValueError("Wrong code - check the number shown on the PC.")
            self.code = None  # one-time use
            token = secrets.token_urlsafe(32)
            now = time.time()
            dev = {"id": new_id(), "name": self._clean_name(name),
                   "type": kind if kind in DEVICE_TYPES else "phone",
                   "token_hash": self._hash(token), "created_at": now, "last_seen": now}
            self.items.append(dev)
            self.save()
            return dev, token

    def rename(self, did, name):
        with self.lock:
            d = self.get(did)
            d["name"] = self._clean_name(name, d["name"])
            self.save()
            return d

    def unlink(self, did):
        with self.lock:
            d = self.get(did)
            self.items.remove(d)
            self.save()
            return d

    def public(self, clients):
        online, via = {}, {}
        for c in clients.values():
            online.setdefault(c["device"], []).append(c["role"])
            via.setdefault(c["device"], set()).add(c.get("via", "wifi"))
        out = [{**HOST, "created_at": None, "last_seen": time.time(),
                "online": "host" in online, "roles": online.get("host", [])}]
        with self.lock:
            for d in self.items:
                out.append({"id": d["id"], "name": d["name"], "type": d["type"],
                            "created_at": d["created_at"], "last_seen": d["last_seen"],
                            "online": d["id"] in online, "roles": online.get(d["id"], []),
                            "via": sorted(via.get(d["id"], [])), "app_version": d.get("app_version", "")})
        return out


registry = Registry()


def app_latest():
    """Newest Android app version SlideLink offers (static/app-version.json), for the 'update your phone' popup."""
    try:
        with open(os.path.join(STATIC, "app-version.json"), encoding="utf-8-sig") as f:
            v = json.load(f)
        return {"versionName": str(v.get("versionName", "")), "versionCode": int(v.get("versionCode", 0)),
                "notes": str(v.get("notes", ""))[:300]}
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- library
class Library:
    """Every presentation is one JSON file in DECKS_DIR."""

    def __init__(self):
        self.lock = threading.RLock()
        self.version = 0
        self._summaries = {}  # id -> (mtime, summary)
        try:
            with open(LIBRARY_FILE, encoding="utf-8") as f:
                self.meta = json.load(f)
        except (FileNotFoundError, ValueError):
            self.meta = {}
        self._migrate_legacy()
        if not self.ids():
            self.create(**sample_deck())

    def _migrate_legacy(self):
        """Bring over deck.json from the first version (stored next to server.py)."""
        old_deck = os.path.join(ROOT, "deck.json")
        if os.path.exists(old_deck) and not self.ids():
            try:
                with open(old_deck, encoding="utf-8") as f:
                    d = json.load(f)
                self.create(title=d.get("title", "My first deck"), theme=d.get("theme", "midnight"),
                            slides=d.get("slides", []))
                os.replace(old_deck, old_deck + ".migrated")
            except (OSError, ValueError):
                pass

    def path(self, did):
        if not isinstance(did, str) or not DECK_ID.match(did):
            raise ValueError("Unknown presentation")
        return os.path.join(DECKS_DIR, did + ".json")

    def ids(self):
        return [f[:-5] for f in os.listdir(DECKS_DIR) if f.endswith(".json") and DECK_ID.match(f[:-5])]

    def load(self, did):
        try:
            with open(self.path(did), encoding="utf-8") as f:
                deck = json.load(f)
        except FileNotFoundError:
            raise ValueError("Unknown presentation")
        deck.setdefault("id", did)
        deck.setdefault("title", "Untitled")
        deck.setdefault("theme", "midnight")
        deck.setdefault("slides", [])
        return deck

    def save(self, deck):
        deck["updated_at"] = time.time()
        with self.lock:
            write_json(self.path(deck["id"]), deck)
            self.version += 1

    def create(self, title="Untitled presentation", theme="midnight", slides=None, **extra):
        now = time.time()
        deck = {"id": uuid.uuid4().hex[:12], "title": str(title or "Untitled presentation")[:200],
                "theme": theme if theme in THEMES else "midnight", "created_at": now, "updated_at": now,
                "slides": []}
        for k in ("css", "faces", "source", "kind", "date"):
            if extra.get(k) is not None:
                deck[k] = extra[k]
        for s in slides or []:
            fields = {k: s.get(k) for k in SLIDE_FIELDS if isinstance(s.get(k), str)}
            if fields.get("layout") not in LAYOUTS:
                fields.pop("layout", None)
            if fields.get("status") not in STATUSES:
                fields.pop("status", None)
            sl = make_slide(**fields)
            sl["updated_by"] = s.get("updated_by") or "import"
            if isinstance(s.get("id"), str) and re.match(r"^[A-Za-z0-9_-]{1,64}$", s["id"]):
                sl["id"] = s["id"]
            deck["slides"].append(sl)
        self.save(deck)
        return deck

    def delete(self, did):
        with self.lock:
            os.remove(self.path(did))
            self._summaries.pop(did, None)
            self.version += 1

    def set_active(self, did):
        self.meta["active"] = did
        write_json(LIBRARY_FILE, self.meta)

    def summary(self, did):
        p = self.path(did)
        mtime = os.path.getmtime(p)
        cached = self._summaries.get(did)
        if cached and cached[0] == mtime:
            return cached[1]
        d = self.load(did)
        counts = {}
        for s in d["slides"]:
            counts[s.get("status", "draft")] = counts.get(s.get("status", "draft"), 0) + 1
        summ = {"id": did, "title": d["title"], "theme": d["theme"], "slide_count": len(d["slides"]),
                "created_at": d.get("created_at"), "updated_at": d.get("updated_at"),
                "source": d.get("source"), "status_counts": counts,
                "kind": d.get("kind") or "deck", "date": d.get("date") or d.get("created_at"),
                "cover": d["slides"][0] if d["slides"] else None,
                "css": d.get("css", ""), "faces": d.get("faces", [])}
        self._summaries[did] = (mtime, summ)
        return summ

    def listing(self):
        out = []
        for did in self.ids():
            try:
                out.append(self.summary(did))
            except (OSError, ValueError):
                pass
        out.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
        return out


library = Library()


# ---------------------------------------------------------------- deck state
class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.clients = {}   # client id -> {"role", "device", "q"}
        self.version = 0
        self.present = {"index": 0, "step": 0, "blackout": False, "changed_at": time.time()}
        self.activity = []
        self.claude = {"text": "", "slide_id": None, "at": 0}
        try:
            self.deck = library.load(library.meta.get("active"))
        except ValueError:
            first = (library.listing() or [None])[0]
            self.deck = library.load(first["id"]) if first else library.create()
        library.set_active(self.deck["id"])

    def save(self):
        library.save(self.deck)

    def open_deck(self, did):
        self.deck = library.load(did)
        library.set_active(did)
        self.present.update(index=0, step=0, blackout=False, changed_at=time.time())

    def snapshot(self):
        roles = {}
        for c in self.clients.values():
            roles[c["role"]] = roles.get(c["role"], 0) + 1
        return {"version": self.version, "deck": self.deck, "present": self.present,
                "activity": self.activity[:30], "claude": self.claude, "devices": roles,
                "linked": registry.public(self.clients), "library_version": library.version,
                "build": app_build(),
                "app_latest": app_latest(),
                "pc_windows": {r: sum(1 for c in self.clients.values() if c["device"] == "host" and c["role"] == r)
                               for r in PC_ROLES},
                "server_time": time.time()}

    def publish(self, origin=None):
        with self.lock:
            self.version += 1
            data = json.dumps({"type": "state", "origin": origin, "state": self.snapshot()},
                              ensure_ascii=False)
            for c in list(self.clients.values()):
                c["q"].put(data)

    def kick(self, device_id):
        with self.lock:
            for c in self.clients.values():
                if c["device"] == device_id:
                    c["q"].put(KICK)

    def log(self, by, text, slide_id=None):
        self.activity.insert(0, {"at": time.time(), "by": by, "text": text, "slide_id": slide_id})
        del self.activity[50:]

    def _index(self, slide_id):
        for i, s in enumerate(self.deck["slides"]):
            if s["id"] == slide_id:
                return i
        raise ValueError(f"No slide with id '{slide_id}'")

    def _label(self, i):
        t = self.deck["slides"][i]["title"] or "untitled"
        return f"slide {i + 1} “{t[:40]}”"

    @staticmethod
    def _check(fields):
        if "layout" in fields and fields["layout"] not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        for k in ("title", "content", "notes"):
            if k in fields and not isinstance(fields[k], str):
                raise ValueError(f"{k} must be a string")

    def _clamp_present(self):
        n = len(self.deck["slides"])
        self.present["index"] = max(0, min(self.present["index"], max(n - 1, 0)))

    def apply(self, op):
        """Apply one operation. Returns (result, deck_changed)."""
        kind = op.get("op")
        by = op.get("by", "you")
        slides = self.deck["slides"]
        with self.lock:
            if kind == "add_slide":
                fields = {k: op.get(k) for k in SLIDE_FIELDS if op.get(k) is not None}
                self._check(fields)
                s = make_slide(**fields)
                s["updated_by"] = by
                pos = op.get("position")
                if op.get("after"):
                    pos = self._index(op["after"]) + 1
                pos = len(slides) if pos is None else max(0, min(int(pos), len(slides)))
                slides.insert(pos, s)
                if pos <= self.present["index"] and len(slides) > 1:
                    self.present["index"] += 1
                self.log(by, f"added {self._label(pos)}", s["id"])
                return {"slide_id": s["id"], "position": pos}, True

            if kind == "update_slide":
                i = self._index(op.get("id"))
                fields = {k: op[k] for k in SLIDE_FIELDS if k in op}
                self._check(fields)
                s = slides[i]
                old_status = s["status"]
                s.update(fields)
                s["updated_at"] = time.time()
                s["updated_by"] = by
                if by == "claude":
                    self.log(by, f"edited {self._label(i)} ({', '.join(fields) or 'nothing'})", s["id"])
                elif "status" in fields and fields["status"] != old_status:
                    self.log(by, f"marked {self._label(i)} as {fields['status']}", s["id"])
                return {"slide_id": s["id"]}, True

            if kind == "delete_slide":
                i = self._index(op.get("id"))
                label = self._label(i)
                slides.pop(i)
                if i < self.present["index"]:
                    self.present["index"] -= 1
                self._clamp_present()
                self.log(by, f"deleted {label}")
                return {"deleted": op.get("id")}, True

            if kind == "duplicate_slide":
                i = self._index(op.get("id"))
                s = dict(slides[i], id=new_id(), updated_at=time.time(), updated_by=by, status="draft")
                slides.insert(i + 1, s)
                self.log(by, f"duplicated {self._label(i)}", s["id"])
                return {"slide_id": s["id"], "position": i + 1}, True

            if kind == "move_slide":
                i = self._index(op.get("id"))
                to = max(0, min(int(op.get("to", i)), len(slides) - 1))
                current_id = slides[self.present["index"]]["id"] if slides else None
                s = slides.pop(i)
                slides.insert(to, s)
                if current_id:
                    self.present["index"] = self._index(current_id)
                self.log(by, f"moved “{s['title'][:40] or 'untitled'}” to position {to + 1}", s["id"])
                return {"slide_id": s["id"], "position": to}, True

            if kind == "set_deck":
                changed = []
                if isinstance(op.get("title"), str):
                    self.deck["title"] = op["title"]
                    changed.append("title")
                if op.get("theme") is not None:
                    if op["theme"] not in THEMES:
                        raise ValueError(f"theme must be one of {THEMES}")
                    self.deck["theme"] = op["theme"]
                    changed.append("theme")
                    self.log(by, f"switched theme to {op['theme']}")
                return {"changed": changed}, True

            if kind in ("next", "prev", "goto", "blackout"):
                n = len(slides)
                i = self.present["index"]
                step = self.present.get("step", 0)
                here = {"index": i, "step": step, "blackout": self.present["blackout"]}
                if kind in ("next", "prev") and "from_index" in op:
                    # the tap says where it started; if that's no longer true (a lost or doubled tap, or another
                    # device moved meanwhile) ignore it and answer with the real position instead
                    same_deck = op.get("from_deck") in (None, self.deck["id"])
                    if not same_deck or op.get("from_index") != i or (op.get("from_step") or 0) != step:
                        return {**here, "stale": True}, False
                if kind == "next":
                    if n and step < build_steps(slides[min(i, n - 1)]):
                        self.present["step"] = step + 1      # reveal the next build on this slide
                    elif i < n - 1:
                        self.present["index"], self.present["step"] = i + 1, 0
                elif kind == "prev":
                    if step > 0:
                        self.present["step"] = step - 1
                    elif i > 0:                               # back to the previous slide, fully built
                        self.present["index"] = i - 1
                        self.present["step"] = build_steps(slides[i - 1])
                elif kind == "goto":
                    self.present["index"] = int(op.get("index", 0))
                    self.present["step"] = 0
                if kind == "blackout":
                    on = op.get("on")
                    self.present["blackout"] = (not self.present["blackout"]) if on is None else bool(on)
                elif n:
                    self.present["blackout"] = False
                self._clamp_present()
                self.present["changed_at"] = time.time()
                return {"index": self.present["index"], "step": self.present.get("step", 0),
                        "blackout": self.present["blackout"]}, False

            if kind == "resync":   # every screen rebuilds its slide views from the current position
                self.present["resync"] = time.time()
                self.log(by, "resynced all screens")
                return {"index": self.present["index"], "step": self.present.get("step", 0),
                        "blackout": self.present["blackout"]}, False

            if kind == "open_deck":
                self.open_deck(op.get("deck_id"))
                self.log(by, f"opened \u201c{self.deck['title'][:60]}\u201d")
                return {"deck_id": self.deck["id"]}, False

            if kind == "create_deck":
                d = library.create(title=op.get("title") or "Untitled presentation", theme=op.get("theme") or "midnight")
                self.open_deck(d["id"])
                self.log(by, f"created presentation \u201c{d['title'][:60]}\u201d")
                return {"deck_id": d["id"]}, False

            if kind == "import_deck":
                slides_in = op.get("slides")
                if not isinstance(slides_in, list) or not slides_in:
                    raise ValueError("import needs a non-empty slides list")
                faces = op.get("faces") if isinstance(op.get("faces"), list) else []
                faces = [{"family": str(f.get("family", ""))[:80], "href": f["href"]} for f in faces
                         if isinstance(f, dict) and str(f.get("href", "")).startswith("https://fonts.googleapis.com/")][:8]
                src = op.get("source") if isinstance(op.get("source"), dict) else None
                if src:
                    src = {"kind": str(src.get("kind", ""))[:40], "url": str(src.get("url") or "")[:300],
                           "slug": str(src.get("slug") or "")[:80], "imported_at": time.time()}
                css = str(op.get("css") or "")[:400_000]
                digest = hashlib.sha256(json.dumps([op.get("title"), css, faces, slides_in], sort_keys=True,
                                                   ensure_ascii=False).encode("utf-8")).hexdigest()
                replace = None
                if src and (src["slug"] or src["url"]):  # the same claude.ai deck again: update it, don't duplicate
                    for summ in library.listing():
                        old = summ.get("source") or {}
                        if (src["slug"] and old.get("slug") == src["slug"]) or (src["url"] and old.get("url") == src["url"]):
                            replace = summ["id"]
                            break
                if replace:
                    old = library.load(replace)
                    osrc = old.get("source") or {}
                    if osrc.get("hash") == digest:
                        if any(op.get(k) and old.get(k) != op[k] for k in ("kind", "date")):
                            for k in ("kind", "date"):
                                if op.get(k):
                                    old[k] = op[k]
                            library.save(old)
                        return {"deck_id": replace, "slides": len(old["slides"]), "unchanged": True}, False
                    fresh = library.create(title=op.get("title"), slides=slides_in, css=css, faces=faces)
                    library.delete(fresh["id"])
                    old.update(title=fresh["title"], slides=fresh["slides"], css=css, faces=faces,
                               source={**osrc, **{k: v for k, v in src.items() if v}, "hash": digest})
                    for k in ("kind", "date"):
                        if op.get(k):
                            old[k] = op[k]
                    library.save(old)
                    if self.deck["id"] == replace:
                        self.deck = old
                        self._clamp_present()
                        self.present["step"] = 0
                    self.log(by, f"updated \u201c{old['title'][:60]}\u201d from claude.ai ({len(old['slides'])} slides)")
                    return {"deck_id": replace, "slides": len(old["slides"]), "updated": True}, False
                if src:
                    src["hash"] = digest
                d = library.create(title=op.get("title"), theme=op.get("theme") or "midnight", slides=slides_in,
                                   css=css, faces=faces, source=src,
                                   kind="doc" if op.get("kind") == "doc" else None,
                                   date=float(op["date"]) if isinstance(op.get("date"), (int, float)) else None)
                self.log(by, f"imported \u201c{d['title'][:60]}\u201d ({len(d['slides'])} slides)")
                return {"deck_id": d["id"], "slides": len(d["slides"]), "created": True}, False

            if kind == "duplicate_deck":
                src = library.load(op.get("deck_id"))
                d = library.create(title=src["title"] + " (copy)", theme=src["theme"], slides=src["slides"],
                                   css=src.get("css"), faces=src.get("faces"))
                return {"deck_id": d["id"]}, False

            if kind == "delete_deck":
                did = op.get("deck_id")
                title = library.load(did)["title"]
                library.delete(did)
                if self.deck["id"] == did:
                    rest = library.listing()
                    self.open_deck(rest[0]["id"] if rest else library.create()["id"])
                self.log(by, f"deleted presentation \u201c{title[:60]}\u201d")
                return {"deleted": did}, False

            if kind == "claude_status":
                text = str(op.get("text") or "")[:200]
                sid = op.get("slide_id")
                if sid:
                    self._index(sid)
                self.claude = {"text": text, "slide_id": sid, "at": time.time()}
                if text:
                    self.log("claude", text, sid)
                return {"ok": True}, False

            raise ValueError(f"Unknown op '{kind}'")


store = Store()


TAILNET = ipaddress.ip_network("100.64.0.0/10")   # Tailscale address range
_ts_cache = {"at": 0, "info": None}


def _is_tailnet(ip):
    try:
        return ipaddress.ip_address(ip) in TAILNET
    except ValueError:
        return False


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


def lan_ips():
    return [ip for ip in local_ips() if not _is_tailnet(ip)]


def tailscale():
    """Tailscale status for this PC (cached 20 s). Works without Tailscale installed."""
    if time.time() - _ts_cache["at"] < 20 and _ts_cache["info"] is not None:
        return _ts_cache["info"]
    info = {"installed": False, "running": False, "ip": None, "dns": None}
    exe = shutil.which("tailscale") or next(
        (p for p in (r"C:\Program Files\Tailscale\tailscale.exe","/usr/bin/tailscale",
                     "/Applications/Tailscale.app/Contents/MacOS/Tailscale") if os.path.exists(p)), None)
    if exe:
        info["installed"] = True
        try:
            out = subprocess.run([exe, "status", "--json"], capture_output=True, timeout=4,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            st = json.loads(out.stdout or b"{}")
            me = st.get("Self") or {}
            info["running"] = st.get("BackendState") == "Running"
            info["ip"] = next((ip for ip in me.get("TailscaleIPs") or [] if "." in ip), None)
            info["dns"] = (me.get("DNSName") or "").rstrip(".") or None
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if not info["ip"]:  # fall back to spotting a Tailscale address on a network adapter
        info["ip"] = next((ip for ip in local_ips() if _is_tailnet(ip)), None)
        info["running"] = info["running"] or bool(info["ip"])
    _ts_cache.update(at=time.time(), info=info)
    return info


def phone_urls():
    """Addresses a phone can use, best first."""
    urls = []
    ts = tailscale()
    if ts["running"] and ts["dns"]:
        urls.append({"url": f"http://{ts['dns']}:{PORT}", "label": "Anywhere – Tailscale (name never changes)", "kind": "tailscale"})
    if ts["running"] and ts["ip"]:
        urls.append({"url": f"http://{ts['ip']}:{PORT}", "label": "Anywhere – Tailscale", "kind": "tailscale"})
    for ip in lan_ips():
        urls.append({"url": f"http://{ip}:{PORT}", "label": "Same Wi-Fi only", "kind": "lan"})
    urls.append({"url": f"http://{socket.gethostname().lower()}.local:{PORT}", "label": "Same Wi-Fi only (if the IP changes)", "kind": "lan"})
    return urls


# ---------------------------------------------------------------- watched folder: Claude Presentations
WATCH_FILES = {".html", ".htm"} | PPT_EXT | {".zip"}
DOC_FILES = {".pdf", ".html", ".htm"}
DOCS_DIR = os.path.join(WATCH_DIR, "Docs")
PDF_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_pages.ps1")

DOC_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title><style>
html,body{{margin:0;background:#ececef}}
body{{padding:24px 0 40px}}
img{{display:block;width:min(96%,1200px);margin:0 auto 22px;background:#fff;box-shadow:0 2px 14px rgba(0,0,0,.18)}}
</style></head><body>{pages}</body></html>"""


def pdf_to_doc(path, title):
    """A PDF (e.g. a claude.ai Doc exported as PDF): every page rendered to a picture by Windows' own PDF engine,
    stacked on one scrolling page. Looks exactly like the original."""
    if os.name != "nt":
        raise ValueError("PDF docs need Windows")
    tok, out = _media_dir()
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", PDF_SCRIPT, path, out, "1600"], capture_output=True, timeout=600, creationflags=NO_WINDOW)
    pages = sorted(f for f in os.listdir(out) if f.startswith("page-") and f.endswith(".png"))
    if r.returncode or not pages:
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        shutil.rmtree(out, ignore_errors=True)
        raise ValueError("could not read the PDF" + (": " + err[0][:200] if err else ""))
    imgs = "".join(f'<img src="/media/{tok}/{f}" alt="Page {i + 1}">' for i, f in enumerate(pages))
    html = DOC_PAGE.format(title=htmllib.escape(title), pages=imgs)
    return {"op": "import_deck", "title": title, "kind": "doc", "source": None,
            "slides": [{"layout": "doc", "html": html, "title": title[:120], "status": "done",
                        "notes": f"Document, {len(pages)} page{'s' if len(pages) != 1 else ''}. Next/Back scroll one screen."}]}


def html_to_doc(text, title):
    t = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    title = (_text(t.group(1)) if t and _text(t.group(1)) else title)[:120]
    if "<html" not in text.lower():
        text = ('<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{max-width:860px;margin:40px auto;'
                'padding:0 28px;font:18px/1.6 system-ui,sans-serif;color:#1f1f1f;background:#fff}'
                'table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px 10px}</style></head><body>'
                + text + '</body></html>')
    return {"op": "import_deck", "title": title, "kind": "doc", "source": None,
            "slides": [{"layout": "page", "html": text, "title": title, "status": "done",
                        "notes": "Document. Next/Back scroll one screen."}]}


def item_date(path):
    """When a presentation/doc was made: "date" in source.json (claude.ai's own date) or the file's time."""
    src = os.path.join(path, "source.json") if os.path.isdir(path) else os.path.splitext(path)[0] + ".source.json"
    try:
        with open(src, encoding="utf-8-sig") as f:
            d = json.load(f).get("date")
        if d:
            return datetime.datetime.fromisoformat(str(d).replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        if os.path.isdir(path):
            return os.path.getmtime(os.path.join(path, "deck.json"))
        return os.path.getmtime(path)
    except OSError:
        return None


def folder_deck_raw(path):
    """A Claude Slides deck folder: deck.json + slides/<id>.html (+ deck.css, + source.json)."""
    with open(os.path.join(path, "deck.json"), encoding="utf-8-sig") as f:
        index = json.load(f)
    css = ""
    for cand in ["deck.css"] + [sp.split("project/", 1)[-1] for sp in (index.get("format") or {}).get("shared", [])]:
        cp = os.path.join(path, cand)
        if re.match(r"^[A-Za-z0-9_./-]+\.css$", cand) and ".." not in cand and os.path.isfile(cp):
            with open(cp, encoding="utf-8") as f:
                css = f.read()
            break
    sdir = os.path.join(path, "slides")
    files = {fn[:-5] for fn in os.listdir(sdir) if fn.endswith(".html")} if os.path.isdir(sdir) else set()
    order = [i for i in index.get("order", []) if i in files] + sorted(files - set(index.get("order", [])))
    slides = []
    for sid in order:
        with open(os.path.join(sdir, sid + ".html"), encoding="utf-8") as f:
            slides.append({"id": sid, "html": f.read()})
    src = {}
    sp = os.path.join(path, "source.json")
    if os.path.isfile(sp):
        with open(sp, encoding="utf-8") as f:
            src = json.load(f)
    # the deck's own pictures/fonts (e.g. project/assets/logo.png): serve them so slides stay lossless
    skip = {"deck.json", "deck.css", "source.json"}
    assets = []
    for root, _, fns in os.walk(path):
        for fn in fns:
            rel = os.path.relpath(os.path.join(root, fn), path).replace(os.sep, "/")
            if rel in skip or (rel.startswith("slides/") and rel.endswith(".html")):
                continue
            assets.append(rel)
    if assets:
        tok = hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:24]
        mdir = os.path.join(MEDIA_DIR, tok)
        os.makedirs(mdir, exist_ok=True)
        for rel in assets:
            flat = re.sub(r"[^A-Za-z0-9._-]", "_", rel)[:80]
            shutil.copyfile(os.path.join(path, rel), os.path.join(mdir, flat))
            for ref in ("project/" + rel, rel):
                css = css.replace(ref, f"/media/{tok}/{flat}")
                for sl in slides:
                    sl["html"] = sl["html"].replace('"' + ref, f'"/media/{tok}/{flat}').replace("(" + ref, f"(/media/{tok}/{flat}")
    return {"slug": src.get("slug") or "", "url": src.get("url") or "", "index": index, "css": css, "slides": slides}


def folder_signature(path):
    """Changes whenever any file of the deck folder changes (so edits re-sync)."""
    sig = []
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                st = os.stat(os.path.join(root, fn))
                sig.append((fn, st.st_size, int(st.st_mtime)))
            except OSError:
                pass
    return hashlib.sha256(json.dumps(sorted(sig)).encode()).hexdigest()


class Watcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        os.makedirs(WATCH_DIR, exist_ok=True)
        self.pending = {}   # item -> signature from the previous scan (import only once it stopped changing)
        self.status = {"folder": WATCH_DIR, "last": None, "error": None, "count": 0}

    def run(self):
        while True:
            try:
                self.scan()
            except Exception as e:     # the watcher must never die
                self.status["error"] = str(e)[:200]
            time.sleep(3)

    def scan(self):
        done = library.meta.setdefault("synced", {})
        items = []
        for name in os.listdir(WATCH_DIR):
            path = os.path.join(WATCH_DIR, name)
            if name.startswith((".", "~$")):
                continue
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, "deck.json")):
                items.append((path, "deck", folder_signature(path)))
            elif os.path.isfile(path) and os.path.splitext(name.lower())[1] in WATCH_FILES:
                st = os.stat(path)
                items.append((path, "file", f"{st.st_size}:{st.st_mtime}"))
        os.makedirs(DOCS_DIR, exist_ok=True)
        for name in os.listdir(DOCS_DIR):
            path = os.path.join(DOCS_DIR, name)
            if not name.startswith((".", "~$")) and os.path.isfile(path) and os.path.splitext(name.lower())[1] in DOC_FILES:
                st = os.stat(path)
                items.append((path, "doc", f"{st.st_size}:{st.st_mtime}"))
        self.status["count"] = len(items)
        for path, kind, sig in items:
            if done.get(path) == sig:
                continue
            if self.pending.get(path) != sig:      # still being written: wait for the next round
                self.pending[path] = sig
                continue
            self.pending.pop(path, None)
            self.sync(path, kind, sig)

    def sync(self, path, kind, sig):
        name = os.path.basename(path)
        done = library.meta["synced"]
        try:
            if kind == "deck":
                raw = folder_deck_raw(path)
                if not raw["slides"]:
                    raise ValueError("the deck folder has no slides/ yet")
                op = claude_to_import(raw)
                if not op["source"]["slug"]:
                    op["source"] = {"kind": "folder", "url": path}
            elif kind == "doc":
                title = os.path.splitext(name)[0]
                if name.lower().endswith(".pdf"):
                    op = pdf_to_doc(path, title)
                else:
                    with open(path, "rb") as f:
                        op = html_to_doc(f.read().decode("utf-8", "replace"), title)
                op["source"] = {"kind": "doc", "url": path}
            else:
                with open(path, "rb") as f:
                    op = upload_to_op(name, f.read())
                op["source"] = {"kind": "file", "url": path}
            date = item_date(path)
            if date:
                op["date"] = date
            op["by"] = "Claude Presentations"
            with store.lock:
                result, _ = store.apply(op)
            store.publish()
            done[path] = sig
            library.set_active(library.meta.get("active"))     # saves the sync list
            what = "updated" if result.get("updated") else "unchanged" if result.get("unchanged") else "added"
            self.status.update(last={"name": name, "what": what, "at": time.time()}, error=None)
            print(f"  sync: {name} -> {what}")
            if ON_NOTIFY and what != "unchanged":
                ON_NOTIFY("SlideLink", f"{name} {what}")
        except Exception as e:
            done[path] = sig                                      # retry only after the next change
            library.set_active(library.meta.get("active"))
            self.status.update(error=f"{name}: {e}"[:200])
            print(f"  sync failed: {name}: {e}")
            if ON_NOTIFY:
                ON_NOTIFY("SlideLink could not sync", f"{name}: {e}"[:200])


watcher = Watcher()


def start_background():
    if not watcher.is_alive():
        watcher.start()


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "SlideLink/1.1"

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
    def _is_host(self):
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        # A local proxy (tailscale serve/funnel, reverse proxies) forwards other devices from
        # 127.0.0.1 - those must still be linked.
        proxied = ("X-Forwarded-For", "Forwarded", "Tailscale-User-Login", "X-Real-IP")
        return not any(self.headers.get(h) for h in proxied)

    def _cookie_token(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        return c[COOKIE].value if COOKIE in c else None

    def _token(self):
        return self._cookie_token() or self.headers.get("X-SlideLink-Token")

    def auth(self):
        if self._is_host():
            return HOST
        dev = registry.by_token(self._token())
        if dev:
            dev["last_seen"] = time.time()
        return dev

    @staticmethod
    def _set_cookie(token):
        if token is None:
            return ("Set-Cookie", f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        return ("Set-Cookie", f"{COOKIE}={token}; Max-Age={COOKIE_AGE}; Path=/; HttpOnly; SameSite=Lax")

    # ----- responses -----
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

    def _body(self, limit=40_000_000):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # ----- routes -----
    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        if p in ("/", "/present", "/remote", "/edit", "/library"):
            return self._file(os.path.join(STATIC, "index.html"))
        if p == "/api/whoami":
            return self._whoami()
        if p.startswith("/api/") or p == "/events":
            dev = self.auth()
            if not dev:
                return self._json({"error": "This device is not linked", "linked": False}, 401)
            if p == "/api/state":
                with store.lock:
                    return self._json(store.snapshot())
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
                return self._json({"port": PORT, "ips": lan_ips(), "urls": phone_urls(), "tailscale": tailscale(), "layouts": LAYOUTS,
                                   "statuses": STATUSES, "themes": THEMES})
            if p == "/events":
                return self._events(parse_qs(url.query), dev)
            return self._json({"error": "not found"}, 404)
        m = MEDIA_PATH.match(p)
        if m:
            return self._file(os.path.join(MEDIA_DIR, m.group(1), m.group(2)))
        full = os.path.normpath(os.path.join(STATIC, p.lstrip("/")))
        if full.startswith(STATIC + os.sep) and os.path.isfile(full):
            return self._file(full)
        return self._json({"error": "not found"}, 404)

    def _cors_import(self):
        origin = self.headers.get("Origin") or ""
        if IMPORT_ORIGINS.match(origin):
            return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin"),
                    ("Access-Control-Allow-Methods", "POST, OPTIONS"),
                    ("Access-Control-Allow-Headers", "Content-Type"),
                    ("Access-Control-Allow-Private-Network", "true")]
        return None

    def do_OPTIONS(self):
        cors = self._cors_import() if urlparse(self.path).path == "/api/import" else None
        self.send_response(204 if cors else 403)
        for k, v in cors or []:
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _same_origin(self):
        """Block other websites from driving SlideLink (CSRF): JSON only, and no foreign Origin."""
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == (self.headers.get("Host") or "")

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
            if not self._same_origin():
                return self._json({"ok": False, "error": "Cross-site request blocked"}, 403)
            if p == "/api/pair":
                return self._pair(self._body())
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
            if p == "/api/watch/open":
                if dev is not HOST or os.name != "nt":
                    return self._json({"ok": False, "error": "Open the folder on the PC."}, 403)
                os.startfile(WATCH_DIR)
                return self._json({"ok": True})
            if p == "/api/devices/code":
                code, exp = registry.new_code()
                return self._json({"ok": True, "code": code, "expires_at": exp, "urls": phone_urls(), "tailscale": tailscale()})
            if p == "/api/devices/rename":
                d = registry.rename(body.get("id"), body.get("name"))
                store.publish()
                return self._json({"ok": True, "device": {"id": d["id"], "name": d["name"]}})
            if p == "/api/devices/unlink":
                return self._unlink(body, dev)
            return self._json({"error": "not found"}, 404)
        except (ValueError, TypeError, KeyError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)

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

    def _whoami(self):
        dev = self.auth()
        headers = []
        if dev and dev is not HOST and not self._cookie_token():
            headers.append(self._set_cookie(self.headers.get("X-SlideLink-Token")))  # restore lost cookie
        info = {"id": dev["id"], "name": dev["name"], "type": dev["type"]} if dev else None
        return self._json({"linked": bool(dev), "is_host": dev is HOST, "device": info}, headers=headers)

    def _pair(self, body):
        dev, token = registry.pair(body.get("code"), body.get("name"), body.get("type"))
        store.log(dev["name"], "was linked to this PC")
        store.publish()
        print(f"  Linked new device: {dev['name']}")
        return self._json({"ok": True, "token": token,
                           "device": {"id": dev["id"], "name": dev["name"], "type": dev["type"]}},
                          headers=[self._set_cookie(token)])

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

    def _op(self, op, dev):
        if dev is not HOST:
            op["by"] = dev["name"]  # linked devices are shown by name in the activity feed
            if dev.get("type") in REMOTE_ONLY and op.get("op") not in REMOTE_OPS:
                return self._json({"ok": False, "error": "The phone is a remote control - edit presentations on the PC."}, 403)
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

    def _events(self, qs, dev):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = queue.Queue()
        cid = new_id() + (qs.get("client") or [""])[0][:16]
        role = (qs.get("role") or ["viewer"])[0][:16]
        with store.lock:
            ip = self.client_address[0]
            via = ("pc" if dev is HOST else "tailscale" if _is_tailnet(ip)
                   else "internet" if ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") else "wifi")   # 127.0.0.1 + linked device = through the HTTPS funnel
            store.clients[cid] = {"role": role, "device": dev["id"], "q": q, "via": via}
            appv = (qs.get("appv") or [""])[0][:12]      # the phone app tells its version when it connects
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


def main():
    start_background()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("\n  SlideLink is running\n")
    print(f"  PC editor     http://localhost:{PORT}/")
    print(f"  Presenter     http://localhost:{PORT}/present")
    for u in phone_urls():
        print(f"  Phone         {u['url']:<44} {u['label']}")
    if not tailscale()["running"]:
        print("\n  Tip: install Tailscale on this PC and your phone to use SlideLink from anywhere.")
    n = len(registry.items)
    print(f"\n  {n} linked device{'s' if n != 1 else ''}. Link more in the editor: ⚙ Linked devices.")
    print(f"  {len(library.ids())} presentations in {DATA_DIR}\n  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
