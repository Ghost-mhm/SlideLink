# SlideLink · EN: SlideLink server: HTTP API, live sync (SSE), library, devices, imports and folder watcher. Standard library only.
# SlideLink · DE: SlideLink-Server: HTTP-API, Live-Sync (SSE), Bibliothek, Geräte, Importe und Ordnerüberwachung. Nur Standardbibliothek.
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


# EN: Where presentations and devices are stored (%LOCALAPPDATA%\SlideLink\data or SLIDELINK_DATA).
# DE: Wo Präsentationen und Geräte gespeichert werden (%LOCALAPPDATA%\SlideLink\data oder SLIDELINK_DATA).
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


# EN: Number of clicks a slide needs before Next goes to the next slide (claude.ai build rules).
# DE: Anzahl der Klicks, die eine Folie braucht, bevor Weiter zur nächsten Folie geht (claude.ai-Build-Regeln).
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


# only the SlideLink Chrome extension may import (its service worker sends the deck); web pages never can,
# otherwise any published claude.ai artifact could slip decks into SlideLink or overwrite imported ones
IMPORT_ORIGINS = re.compile(r"^chrome-extension://[a-p]{32}$")
LOCAL_HOSTS = {f"{h}:{PORT}" for h in ("localhost", "127.0.0.1", "[::1]")}   # the PC's own names for this server
MEDIA_PATH = re.compile(r"^/media/([0-9a-f]{24})/([A-Za-z0-9._-]{1,80})$")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PPT_EXT = {".pptx", ".ppt", ".odp", ".ppsx", ".pps", ".potx"}


# ---------------------------------------------------------------- importing other formats
# EN: Plain text of an HTML fragment (tags removed, entities decoded).
# DE: Reiner Text eines HTML-Stücks (Tags entfernt, Entities aufgelöst).
def _text(fragment):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# EN: Splits one claude.ai slide file into its <section> HTML, title and speaker notes.
# DE: Zerlegt eine claude.ai-Foliendatei in <section>-HTML, Titel und Sprechernotizen.
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


# EN: Turns a raw claude.ai Slides deck (index + css + slides) into an import_deck operation.
# DE: Macht aus einem rohen claude.ai-Slides-Deck (Index + CSS + Folien) eine import_deck-Operation.
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


# EN: Imports an HTML file: a single-file deck becomes slides, any other page one interactive page.
# DE: Importiert eine HTML-Datei: Ein Ein-Datei-Deck wird zu Folien, jede andere Seite zu einer interaktiven Seite.
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


# EN: Imports a deck packed as .zip (claude.ai export or a folder with deck.json).
# DE: Importiert ein als .zip gepacktes Deck (claude.ai-Export oder Ordner mit deck.json).
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


# EN: Imports a SlideLink deck saved as JSON.
# DE: Importiert ein als JSON gespeichertes SlideLink-Deck.
def json_to_import(data, title):
    d = json.loads(data.decode("utf-8-sig") if isinstance(data, bytes) else data)
    if isinstance(d, dict) and isinstance(d.get("slides"), list):
        return {"op": "import_deck", "title": d.get("title") or title, "theme": d.get("theme"), "css": d.get("css") or "",
                "faces": d.get("faces") or [], "slides": d["slides"], "source": None}
    raise ValueError("This JSON file is not a SlideLink presentation")


# EN: Creates a new media folder with a random, unguessable name; returns (name, path).
# DE: Legt einen neuen Medienordner mit zufälligem, unerratbarem Namen an; gibt (Name, Pfad) zurück.
def _media_dir():
    tok = secrets.token_hex(12)
    path = os.path.join(MEDIA_DIR, tok)
    os.makedirs(path, exist_ok=True)
    return tok, path


# EN: A slide that shows one picture full-screen (used for PowerPoint and image imports).
# DE: Eine Folie, die ein Bild bildschirmfüllend zeigt (für PowerPoint- und Bild-Importe).
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


# EN: Lets the installed PowerPoint export every slide as a picture (with notes) and imports them.
# DE: Lässt das installierte PowerPoint jede Folie als Bild exportieren (mit Notizen) und importiert sie.
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


# EN: Chooses the right importer for an uploaded file by its extension.
# DE: Wählt anhand der Dateiendung den passenden Importer für eine hochgeladene Datei.
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
    if ext == ".pdf":                       # a PDF becomes a doc (every page shown, exactly like the original)
        tok, out = _media_dir()
        tmp = os.path.join(out, "upload.pdf")
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            return pdf_to_doc(tmp, title)
        finally:
            try:
                os.remove(tmp)
                os.rmdir(out)                   # pdf_to_doc keeps its pages in a media folder of its own
            except OSError:
                pass
    if ext in IMAGE_EXT:
        tok, out = _media_dir()
        fname = "image" + ext
        with open(os.path.join(out, fname), "wb") as f:
            f.write(data)
        return {"op": "import_deck", "title": title, "slides": [image_slide(tok, fname, title)], "source": None}
    raise ValueError(f"Unsupported file type '{ext or name}'. Use PowerPoint, a deck .zip, .html, .pdf, .json or images.")
DEVICE_TYPES = ["phone", "tablet", "pc"]
CODE_TTL = 600            # link codes are valid for 10 minutes
COOKIE = "sl_token"
COOKIE_AGE = 10 * 365 * 24 * 3600
HOST = {"id": "host", "name": "This PC", "type": "host"}
REMOTE_ONLY = {"phone", "tablet"}          # these devices control presentations but never edit them
REMOTE_OPS = {"next", "prev", "goto", "blackout", "open_deck", "show_on_pc", "open_on_pc", "close_on_pc", "resync",
              "poll_start", "poll_show", "poll_end", "poll_clear", "speaker_me", "speaker_set", "audience_new", "join_qr",
              "group_start", "group_end", "group_kick", "group_qr", "set_lang"}
LANGS = ("en", "de")        # interface languages (static/i18n.js); the admin picks one for every screen
# EN: Group members (joined with the group link, not linked): control the talk only, never edit, switch decks or manage.
# DE: Gruppenmitglieder (über den Gruppen-Link, nicht verknüpft): steuern nur den Vortrag, nie bearbeiten, Deck wechseln oder verwalten.
GROUP_OPS = {"next", "prev", "goto", "blackout", "resync", "poll_start", "poll_show", "poll_end", "poll_clear",
             "speaker_me", "speaker_set", "join_qr"}
GROUP_COOKIE = "sl_group"
GROUP_HOURS = 4
# EN: Viewers of the audience link: see the current slide live and vote in polls, nothing else.
# DE: Zuschauer über den Publikums-Link: sehen die aktuelle Folie live und stimmen ab, sonst nichts.
AUDIENCE = {"id": "audience", "name": "Audience", "type": "audience"}
ON_SHOW_PC = None                          # set by the desktop app: opens the presenter window on the PC
ON_OPEN_PC = None                          # set by the desktop app: opens the SlideLink app window on the PC
ON_CLOSE_PC = None                         # set by the desktop app: closes the SlideLink windows on the PC
PC_ROLES = {"library", "present", "edit"}  # SSE roles that are windows on the PC
ON_NOTIFY = None                           # set by the desktop app: tray notification (title, text)
WATCH_DIR = os.environ.get("SLIDELINK_WATCH") or os.path.join(os.path.expanduser("~"), "Claude Presentations")
KICK = object()           # queue sentinel: device was unlinked


# EN: A short random id for slides and devices.
# DE: Eine kurze Zufalls-ID für Folien und Geräte.
def new_id():
    return uuid.uuid4().hex[:8]


# EN: Version of the web files (newest change time): when it changes, open windows reload themselves.
# DE: Version der Web-Dateien (jüngste Änderungszeit): Ändert sie sich, laden sich offene Fenster neu.
def app_build():
    """Changes whenever a file of the app itself changes, so open windows know to reload."""
    try:
        return str(int(max(os.path.getmtime(os.path.join(STATIC, f)) for f in os.listdir(STATIC))))
    except (OSError, ValueError):
        return "0"


# EN: Writes JSON atomically (temp file + replace), so a crash never leaves a broken file.
# DE: Schreibt JSON atomar (Temp-Datei + Ersetzen), damit ein Absturz nie eine kaputte Datei hinterlässt.
def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# EN: Creates a new slide with defaults for missing fields.
# DE: Erzeugt eine neue Folie mit Standardwerten für fehlende Felder.
def make_slide(**fields):
    s = {"id": new_id(), "title": "", "content": "", "notes": "", "layout": "content",
         "status": "draft", "updated_at": time.time(), "updated_by": "you"}
    for k in SLIDE_FIELDS:
        if k in fields and fields[k] is not None:
            s[k] = fields[k]
    return s


# EN: The welcome presentation shown on the very first start.
# DE: Die Willkommens-Präsentation beim allerersten Start.
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


# EN: Linked devices: one-time link codes, tokens (stored only as SHA-256 hashes), rename/unlink.
# DE: Verknüpfte Geräte: einmalige Link-Codes, Tokens (nur als SHA-256-Hash gespeichert), Umbenennen/Entfernen.
class Registry:
    """Linked devices. Tokens are stored only as SHA-256 hashes."""

    # EN: Loads the device list from devices.json.
    # DE: Lädt die Geräteliste aus devices.json.
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

    # EN: Saves the device list.
    # DE: Speichert die Geräteliste.
    def save(self):
        with self.lock:
            write_json(DEVICES_FILE, {"devices": self.items})

    # EN: SHA-256 of a token: only this is stored, never the token itself.
    # DE: SHA-256 eines Tokens: Nur dieser wird gespeichert, nie das Token selbst.
    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # EN: Shortens and cleans a device name typed by the user.
    # DE: Kürzt und bereinigt einen vom Benutzer eingegebenen Gerätenamen.
    @staticmethod
    def _clean_name(name, fallback="Phone"):
        name = " ".join(str(name or "").split())[:40]
        return name or fallback

    # EN: Finds the device that belongs to a token (or None).
    # DE: Findet das Gerät zu einem Token (oder None).
    def by_token(self, token):
        if not token:
            return None
        h = self._hash(token)
        with self.lock:
            for d in self.items:
                if secrets.compare_digest(d["token_hash"], h):
                    return d
        return None

    # EN: Finds a device by its id.
    # DE: Findet ein Gerät anhand seiner ID.
    def get(self, did):
        with self.lock:
            for d in self.items:
                if d["id"] == did:
                    return d
        raise ValueError("Unknown device")

    # EN: Creates a new 6-digit one-time link code (valid 10 minutes).
    # DE: Erzeugt einen neuen 6-stelligen Einmal-Link-Code (10 Minuten gültig).
    def new_code(self):
        with self.lock:
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.code_exp = time.time() + CODE_TTL
            self.code_tries = 0
            return self.code, self.code_exp

    # EN: Links a new device with the code; returns the device and its new token. 8 wrong tries kill the code.
    # DE: Verknüpft ein neues Gerät mit dem Code; gibt Gerät und neues Token zurück. 8 Fehlversuche machen den Code ungültig.
    def pair(self, code, name, kind):
        with self.lock:
            self.check_code(code)
            return self.add_device(name, kind)

    # EN: Checks the link code without using it up (wrong tries count; 8 kill the code). Call with the lock held.
    # DE: Prüft den Link-Code, ohne ihn zu verbrauchen (Fehlversuche zählen; 8 machen ihn ungültig). Mit gehaltenem Lock aufrufen.
    def check_code(self, code):
        if not self.code or time.time() > self.code_exp:
            raise ValueError("No active link code. On the PC open ⚙ Linked devices → Link a new device.")
        if not secrets.compare_digest(str(code or "").strip().replace(" ", ""), self.code):
            self.code_tries += 1
            if self.code_tries >= 8:
                self.code = None
                raise ValueError("Too many wrong codes. Create a new code on the PC.")
            raise ValueError("Wrong code - check the number shown on the PC.")

    # EN: Uses up the code and links a new device; e2e_pub = the phone app's identity key (end-to-end pairing). Lock held.
    # DE: Verbraucht den Code und verknüpft ein neues Gerät; e2e_pub = Identitätsschlüssel der Handy-App. Mit gehaltenem Lock.
    def add_device(self, name, kind, e2e_pub=None):
        self.code = None  # one-time use
        token = secrets.token_urlsafe(32)
        now = time.time()
        dev = {"id": new_id(), "name": self._clean_name(name),
               "type": kind if kind in DEVICE_TYPES else "phone",
               "token_hash": self._hash(token), "created_at": now, "last_seen": now}
        if e2e_pub:
            dev["e2e_pub"] = e2e_pub
        self.items.append(dev)
        self.save()
        return dev, token

    # EN: Renames a linked device.
    # DE: Benennt ein verknüpftes Gerät um.
    def rename(self, did, name):
        with self.lock:
            d = self.get(did)
            d["name"] = self._clean_name(name, d["name"])
            self.save()
            return d

    # EN: Removes a linked device (its token stops working at once).
    # DE: Entfernt ein verknüpftes Gerät (sein Token funktioniert sofort nicht mehr).
    def unlink(self, did):
        with self.lock:
            d = self.get(did)
            self.items.remove(d)
            self.save()
            return d

    # EN: The device list for the UI: online state, roles, connection type, app version (no secrets).
    # DE: Die Geräteliste für die Oberfläche: online, Rollen, Verbindungsart, App-Version (keine Geheimnisse).
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
                            "via": sorted(via.get(d["id"], [])), "app_version": d.get("app_version", ""),
                            "e2e": bool(d.get("e2e_pub"))})
        return out


registry = Registry()


# EN: Newest Android app version offered by this PC (static/app-version.json).
# DE: Neueste Android-App-Version, die dieser PC anbietet (static/app-version.json).
APP_DIR_PULLED = os.path.join(DATA_DIR, "phone-app")   # newer app pulled from GitHub by updater.py


def _app_info(folder):
    try:
        with open(os.path.join(folder, "app-version.json"), encoding="utf-8-sig") as f:
            v = json.load(f)
        if not os.path.isfile(os.path.join(folder, "SlideLink.apk")):
            return None
        return {"versionName": str(v.get("versionName", "")), "versionCode": int(v.get("versionCode", 0)),
                "notes": str(v.get("notes", ""))[:300]}
    except (OSError, ValueError, AttributeError):
        return None


def app_folder():
    """Folder with the newest Android app: the one bundled with SlideLink or a newer one pulled from GitHub."""
    mine, pulled = _app_info(STATIC), _app_info(APP_DIR_PULLED)
    if pulled and (not mine or pulled["versionCode"] > mine["versionCode"]):
        return APP_DIR_PULLED
    return STATIC


def app_latest():
    """Newest Android app version SlideLink offers, for the 'update your phone' popup."""
    return _app_info(app_folder())


# ---------------------------------------------------------------- library
# EN: All presentations on disk (decks/*.json) with cached summaries for the library views.
# DE: Alle Präsentationen auf der Festplatte (decks/*.json) mit zwischengespeicherten Zusammenfassungen für die Bibliothek.
class Library:
    """Every presentation is one JSON file in DECKS_DIR."""

    # EN: Loads library.json and creates the welcome deck if the library is empty.
    # DE: Lädt library.json und legt das Willkommens-Deck an, wenn die Bibliothek leer ist.
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

    # EN: Moves a deck from the very first SlideLink version into the library.
    # DE: Übernimmt ein Deck aus der allerersten SlideLink-Version in die Bibliothek.
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

    # EN: File path of a deck.
    # DE: Dateipfad eines Decks.
    def path(self, did):
        if not isinstance(did, str) or not DECK_ID.match(did):
            raise ValueError("Unknown presentation")
        return os.path.join(DECKS_DIR, did + ".json")

    # EN: Ids of all decks.
    # DE: IDs aller Decks.
    def ids(self):
        return [f[:-5] for f in os.listdir(DECKS_DIR) if f.endswith(".json") and DECK_ID.match(f[:-5])]

    # EN: Loads one complete deck.
    # DE: Lädt ein vollständiges Deck.
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

    # EN: Saves a deck and bumps the library version (so all screens refresh).
    # DE: Speichert ein Deck und erhöht die Bibliotheksversion (damit alle Bildschirme aktualisieren).
    def save(self, deck):
        deck["updated_at"] = time.time()
        with self.lock:
            write_json(self.path(deck["id"]), deck)
            self.version += 1

    # EN: Creates a new deck (optionally with slides, CSS, fonts, source, kind and date).
    # DE: Erstellt ein neues Deck (optional mit Folien, CSS, Schriften, Quelle, Art und Datum).
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

    # EN: Deletes a deck.
    # DE: Löscht ein Deck.
    def delete(self, did):
        with self.lock:
            os.remove(self.path(did))
            self._summaries.pop(did, None)
            self.version += 1

    # EN: Remembers which deck is open (and saves the watcher list).
    # DE: Merkt sich, welches Deck offen ist (und speichert die Watcher-Liste).
    def set_active(self, did):
        self.meta["active"] = did
        write_json(LIBRARY_FILE, self.meta)

    # EN: Short summary of a deck for lists (title, cover, counts, kind, date), cached by file time.
    # DE: Kurze Zusammenfassung eines Decks für Listen (Titel, Cover, Zahlen, Art, Datum), zwischengespeichert nach Dateizeit.
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

    # EN: Summaries of all decks, newest first.
    # DE: Zusammenfassungen aller Decks, neueste zuerst.
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
# EN: The live state shared by all screens: open deck, presentation position, activity, Claude status, connected clients.
# DE: Der gemeinsame Live-Zustand aller Bildschirme: offenes Deck, Präsentationsposition, Aktivitäten, Claude-Status, verbundene Clients.
class Store:
    # EN: Opens the last active deck.
    # DE: Öffnet das zuletzt aktive Deck.
    def __init__(self):
        self.lock = threading.RLock()
        self.clients = {}   # client id -> {"role", "device", "q"}
        self.version = 0
        self.present = {"index": 0, "step": 0, "blackout": False, "changed_at": time.time()}
        self.activity = []
        self.claude = {"text": "", "slide_id": None, "at": 0}
        self.poll = None        # live poll: {id, question, options, votes {voter: option}, open, show}
        self.speaker = None     # who presents right now (several presenters): {"name", "at"}
        self.join_qr = False    # show the big audience QR code on the presentation screen
        self.group = None       # group session: {key, expires, members {id: {name, token_hash, joined}}}
        self.group_qr = False   # show the big group QR code on the presentation screen
        try:
            self.deck = library.load(library.meta.get("active"))
        except ValueError:
            first = (library.listing() or [None])[0]
            self.deck = library.load(first["id"]) if first else library.create()
        library.set_active(self.deck["id"])

    # EN: Saves the open deck.
    # DE: Speichert das offene Deck.
    def save(self):
        library.save(self.deck)

    # EN: Opens another deck on all screens, starting at slide 1.
    # DE: Öffnet ein anderes Deck auf allen Bildschirmen, beginnend bei Folie 1.
    def open_deck(self, did):
        self.deck = library.load(did)
        library.set_active(did)
        self.present.update(index=0, step=0, blackout=False, changed_at=time.time())

    # EN: The complete state as sent to every screen.
    # DE: Der komplette Zustand, wie er an jeden Bildschirm geschickt wird.
    def group_active(self):
        """The running group session (ends by itself after GROUP_HOURS)."""
        if self.group and time.time() > self.group["expires"]:
            self.group = None
            self.group_qr = False
        return self.group

    # EN: Finds the group member that belongs to a token (or None).
    # DE: Findet das Gruppenmitglied zu einem Token (oder None).
    def group_member(self, token):
        g = self.group_active()
        if not g or not token:
            return None
        h = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for mid, m in g["members"].items():
            if secrets.compare_digest(m["token_hash"], h):
                return {"id": "g:" + mid, "name": m["name"], "type": "group"}
        return None

    # EN: Group info for admins: link key, end time, members with online state.
    # DE: Gruppen-Infos für Admins: Link-Schlüssel, Endzeit, Mitglieder mit Online-Status.
    def group_admin_view(self):
        g = self.group_active()
        if not g:
            return {"active": False, "qr": False}
        online = {c["device"] for c in self.clients.values()}
        return {"active": True, "key": g["key"], "expires": g["expires"], "qr": self.group_qr,
                "members": [{"id": mid, "name": m["name"], "online": ("g:" + mid) in online} for mid, m in g["members"].items()]}

    def audience_key(self):
        """Secret part of the audience link (kept in library.json; a new one makes old links invalid)."""
        key = library.meta.get("audience_key")
        if not key:
            key = secrets.token_urlsafe(9)
            library.meta["audience_key"] = key
            library.set_active(library.meta.get("active"))
        return key

    # EN: Poll for everyone: question, options and vote counts (never who voted what).
    # DE: Umfrage für alle: Frage, Antworten und Stimmenzahlen (nie, wer was gewählt hat).
    def poll_public(self):
        if not self.poll:
            return None
        counts = [0] * len(self.poll["options"])
        for v in self.poll["votes"].values():
            counts[v] += 1
        return {"id": self.poll["id"], "question": self.poll["question"], "options": self.poll["options"],
                "counts": counts, "total": sum(counts), "open": self.poll["open"], "show": self.poll["show"]}

    # EN: The reduced state for the audience: slides without speaker notes, position, poll, speaker.
    # DE: Der reduzierte Zustand fürs Publikum: Folien ohne Sprechernotizen, Position, Umfrage, Vortragende.
    def public_snapshot(self):
        d = self.deck
        slides = [{k: s.get(k) for k in ("id", "title", "layout", "html", "content")} for s in d["slides"]]
        return {"deck": {"id": d["id"], "title": d["title"], "theme": d.get("theme"), "css": d.get("css", ""),
                         "faces": d.get("faces", []), "slides": slides},
                "present": self.present, "poll": self.poll_public(), "speaker": self.speaker,
                "lang": library.meta.get("lang", "en"), "build": app_build(), "server_time": time.time()}

    # EN: The state for group members: like the full state, but without linked devices and group admin data.
    # DE: Der Zustand für Gruppenmitglieder: wie der volle, aber ohne verknüpfte Geräte und Gruppen-Admin-Daten.
    def member_snapshot(self):
        s = self.snapshot()
        g = self.group_active()
        s["linked"] = []
        s["group"] = {"active": bool(g), "member": True,
                      "names": [m["name"] for m in g["members"].values()] if g else []}
        return s

    def snapshot(self):
        roles = {}
        for c in self.clients.values():
            roles[c["role"]] = roles.get(c["role"], 0) + 1
        return {"version": self.version, "deck": self.deck, "present": self.present,
                "activity": self.activity[:30], "claude": self.claude, "devices": roles,
                "linked": registry.public(self.clients), "library_version": library.version,
                "build": app_build(),
                "app_latest": app_latest(),
                "e2e_pair": pairing.view() if pairing else [],
                "poll": self.poll_public(), "speaker": self.speaker, "group": self.group_admin_view(),
                "lang": library.meta.get("lang", "en"),
                "audience": {"key": self.audience_key(), "url": audience_base(), "qr": self.join_qr,
                             "count": sum(1 for c in self.clients.values() if c["role"] == "audience")},
                "pc_windows": {r: sum(1 for c in self.clients.values() if c["device"] == "host" and c["role"] == r)
                               for r in PC_ROLES},
                "server_time": time.time()}

    # EN: Pushes the current state to every connected screen (SSE).
    # DE: Schickt den aktuellen Zustand an jeden verbundenen Bildschirm (SSE).
    def publish(self, origin=None):
        with self.lock:
            self.version += 1
            data = json.dumps({"type": "state", "origin": origin, "state": self.snapshot()},
                              ensure_ascii=False)
            public = member = None
            for c in list(self.clients.values()):
                if c["role"] == "audience":           # the audience gets the reduced state only
                    if public is None:
                        public = json.dumps({"type": "state", "state": self.public_snapshot()}, ensure_ascii=False)
                    c["q"].put(public)
                elif str(c["device"]).startswith("g:"):   # group members: no device list, no group admin data
                    if member is None:
                        member = json.dumps({"type": "state", "origin": origin, "state": self.member_snapshot()},
                                            ensure_ascii=False)
                    c["q"].put(member)
                else:
                    c["q"].put(data)

    # EN: Disconnects all live connections of an unlinked device.
    # DE: Trennt alle Live-Verbindungen eines entfernten Geräts.
    def kick(self, device_id):
        with self.lock:
            for c in self.clients.values():
                if c["device"] == device_id:
                    c["q"].put(KICK)

    # EN: Adds an entry to the activity feed.
    # DE: Fügt einen Eintrag zum Aktivitätsverlauf hinzu.
    def log(self, by, text, slide_id=None):
        self.activity.insert(0, {"at": time.time(), "by": by, "text": text, "slide_id": slide_id})
        del self.activity[50:]

    # EN: Position of a slide in the open deck (error if missing).
    # DE: Position einer Folie im offenen Deck (Fehler, wenn sie fehlt).
    def _index(self, slide_id):
        for i, s in enumerate(self.deck["slides"]):
            if s["id"] == slide_id:
                return i
        raise ValueError(f"No slide with id '{slide_id}'")

    # EN: Readable name of a slide for the activity feed.
    # DE: Lesbarer Name einer Folie für den Aktivitätsverlauf.
    def _label(self, i):
        t = self.deck["slides"][i]["title"] or "untitled"
        return f"slide {i + 1} “{t[:40]}”"

    # EN: Validates slide fields (layout, status, lengths).
    # DE: Prüft Folienfelder (Layout, Status, Längen).
    @staticmethod
    def _check(fields):
        if "layout" in fields and fields["layout"] not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        for k in ("title", "content", "notes"):
            if k in fields and not isinstance(fields[k], str):
                raise ValueError(f"{k} must be a string")

    # EN: Keeps the presentation position inside the deck after changes.
    # DE: Hält die Präsentationsposition nach Änderungen innerhalb des Decks.
    def _clamp_present(self):
        n = len(self.deck["slides"])
        self.present["index"] = max(0, min(self.present["index"], max(n - 1, 0)))

    # EN: Runs one operation (navigation, editing, decks, import, Claude status). Returns (result, deck changed).
    # DE: Führt eine Operation aus (Navigation, Bearbeiten, Decks, Import, Claude-Status). Gibt (Ergebnis, Deck geändert) zurück.
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

            # ---- live polls / several presenters / audience link (beta) ----
            if kind == "poll_start":
                question = str(op.get("question") or "").strip()[:200]
                options = [str(o).strip()[:80] for o in (op.get("options") or []) if str(o).strip()][:4]
                if not question or len(options) < 2:
                    raise ValueError("A poll needs a question and 2 to 4 answers")
                self.poll = {"id": new_id(), "question": question, "options": options, "votes": {},
                             "open": True, "show": False}
                self.log(by, f"started a poll: \u201c{question[:60]}\u201d")
                return {"poll": self.poll["id"]}, False
            if kind in ("poll_show", "poll_end", "poll_clear"):
                if kind == "poll_clear":
                    self.poll = None
                elif self.poll:
                    if kind == "poll_show":
                        self.poll["show"] = bool(op.get("on", True))
                    else:
                        self.poll["open"] = False
                return {}, False
            if kind == "speaker_set":
                name = str(op.get("name") or "").strip()[:40]
                self.speaker = {"name": name, "at": time.time()} if name else None
                if name:
                    self.log(by, f"{name} is presenting now")
                return {}, False
            if kind == "audience_new":
                library.meta["audience_key"] = secrets.token_urlsafe(9)
                library.set_active(library.meta.get("active"))
                for c in self.clients.values():       # old links stop working at once
                    if c["role"] == "audience":
                        c["q"].put(KICK)
                return {}, False
            if kind == "join_qr":
                self.join_qr = bool(op.get("on", True))
                return {}, False
            if kind == "group_start":
                if not self.group_active():
                    self.group = {"key": secrets.token_urlsafe(9), "expires": time.time() + GROUP_HOURS * 3600, "members": {}}
                    self.log(by, "started a group session")
                return {"key": self.group["key"]}, False
            if kind == "group_end":
                names = {m["name"] for m in self.group["members"].values()} if self.group else set()
                if self.group:
                    self.log(by, "ended the group session")
                self.group, self.group_qr = None, False
                if self.speaker and self.speaker.get("name") in names:   # a group member was presenting
                    self.speaker = None
                for c in self.clients.values():       # everyone in the group is out at once
                    if str(c["device"]).startswith("g:"):
                        c["q"].put(KICK)
                return {}, False
            if kind == "group_kick":
                g = self.group_active()
                mid = str(op.get("member") or "")
                if g and mid in g["members"]:
                    name = g["members"].pop(mid)["name"]
                    if self.speaker and self.speaker.get("name") == name:   # a removed person no longer presents
                        self.speaker = None
                    for c in self.clients.values():
                        if c["device"] == "g:" + mid:
                            c["q"].put(KICK)
                    self.log(by, f"removed {name} from the group")
                return {}, False
            if kind == "set_lang":
                lang = str(op.get("lang") or "")
                if lang not in LANGS:
                    raise ValueError("Unknown language")
                library.meta["lang"] = lang
                library.set_active(library.meta.get("active"))     # saves library.json
                return {"lang": lang}, False
            if kind == "group_qr":
                self.group_qr = bool(op.get("on", True)) and bool(self.group_active())
                return {}, False

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
                                   css=src.get("css"), faces=src.get("faces"), kind=src.get("kind"))
                self.log(by, f"duplicated \u201c{src['title'][:60]}\u201d")
                return {"deck_id": d["id"]}, False

            if kind == "edit_deck":
                # title and speaker notes of any deck, without opening it on all screens (library "Edit")
                did = op.get("deck_id")
                d = self.deck if self.deck["id"] == did else library.load(did)
                title = op.get("title")
                if isinstance(title, str) and title.strip():
                    d["title"] = title.strip()[:200]
                notes = op.get("notes") if isinstance(op.get("notes"), dict) else {}
                for s in d["slides"]:
                    if s["id"] in notes and isinstance(notes[s["id"]], str):
                        s["notes"] = notes[s["id"]][:20000]
                        s["updated_at"] = time.time()
                library.save(d)
                self.log(by, f"edited \u201c{d['title'][:60]}\u201d (title and notes)")
                return {"deck_id": did}, d is self.deck

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

# EN: End-to-end pairing (phone app <-> PC through the relay). If it can't start, everything else keeps working.
# DE: Ende-zu-Ende-Koppeln (Handy-App <-> PC über das Relay). Startet es nicht, läuft alles andere normal weiter.
try:
    import e2e_server
    pairing = e2e_server.Pairing(os.path.join(DATA_DIR, "e2e-key.bin"), registry, on_change=lambda: store.publish())
    import e2e_session
    sessions = e2e_session.Sessions(pairing.key, registry, PORT)
except Exception as e:          # e.g. not Windows
    print(f"  end-to-end pairing unavailable: {e}")
    pairing = sessions = None


# EN: SlideLink Relay client: the PC asks the relay for requests from phones (always outgoing, no open ports),
#     answers them through this server and streams the answer back. Ends when SlideLink ends.
# DE: SlideLink-Relay-Client: Der PC holt Anfragen von Handys beim Relay ab (immer ausgehend, keine offenen Ports),
#     beantwortet sie über diesen Server und streamt die Antwort zurück. Endet mit SlideLink.
RELAY_FILE = os.path.join(DATA_DIR, "relay.json")
RELAY = {"url": "", "id": "", "key": "", "online": False}
# EN: The relay every SlideLink uses out of the box (Cloudflare Worker from relay/worker). Empty until it is deployed.
# DE: Das Relay, das jedes SlideLink ab Werk nutzt (Cloudflare Worker aus relay/worker). Leer, bis es veröffentlicht ist.
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


# EN: HTTPS for the home Wi-Fi: this PC's own certificate (made once by make_cert.ps1). Browsers would warn about it,
#     but the SlideLink app trusts exactly this certificate (its fingerprint comes with the PC's addresses).
# DE: HTTPS fürs Heim-WLAN: das eigene Zertifikat dieses PCs (einmal von make_cert.ps1 erzeugt). Browser würden warnen,
#     die SlideLink-App vertraut aber genau diesem Zertifikat (sein Fingerabdruck kommt mit den Adressen des PCs).
HTTPS_PORT = PORT + 1
TLS_DIR = os.path.join(DATA_DIR, "tls")
TLS = {"on": False, "sha256": "", "port": HTTPS_PORT}


def start_https():
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
        httpd = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), Handler)
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


# EN: True for addresses of the carrier-grade NAT range (100.64.0.0/10), which a phone can't use on Wi-Fi.
# DE: True für Adressen aus dem Carrier-NAT-Bereich (100.64.0.0/10), die ein Handy im WLAN nicht nutzen kann.
CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_cgnat(ip):
    try:
        return ipaddress.ip_address(ip) in CGNAT
    except ValueError:
        return False


# EN: All IPv4 addresses of this PC.
# DE: Alle IPv4-Adressen dieses PCs.
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


# EN: Home-network addresses of this PC (no loopback, no carrier NAT).
# DE: Heimnetz-Adressen dieses PCs (kein Loopback, kein Carrier-NAT).
def lan_ips():
    return [ip for ip in local_ips() if not _is_cgnat(ip)]


# EN: Base address for the audience link: the relay (reachable from any network) if it is on.
# DE: Basisadresse für den Publikums-Link: das Relay (aus jedem Netz erreichbar), falls es an ist.
def audience_base():
    return relay_status()["url"]


# EN: Plain http (port 8765) is only for this PC's own window, Claude and the Chrome extension: phones use HTTPS.
# DE: Unverschlüsseltes http (Port 8765) nur für das eigene Fenster des PCs, Claude und die Chrome-Erweiterung: Handys nutzen HTTPS.
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


# EN: Imports a PDF doc: every page rendered to a picture, stacked as one scrolling document.
# DE: Importiert ein PDF-Doc: Jede Seite wird zum Bild und alles zu einem scrollbaren Dokument.
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


# EN: Imports an HTML doc as one scrolling page (adds a readable style if it has none).
# DE: Importiert ein HTML-Doc als eine scrollbare Seite (mit lesbarem Stil, falls keiner vorhanden ist).
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


# EN: Date of a presentation/doc: claude.ai's date from source.json, else the file time.
# DE: Datum einer Präsentation/eines Docs: claude.ai-Datum aus source.json, sonst die Dateizeit.
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


# EN: Reads a Claude Slides deck folder (deck.json, deck.css, slides/, assets/, source.json).
# DE: Liest einen Claude-Slides-Deck-Ordner (deck.json, deck.css, slides/, assets/, source.json).
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


# EN: Fingerprint of a folder (names, sizes, times): changes when any file changes.
# DE: Fingerabdruck eines Ordners (Namen, Größen, Zeiten): ändert sich, wenn sich eine Datei ändert.
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


# EN: Background thread that imports everything saved in ~/Claude Presentations (and Docs/) automatically.
# DE: Hintergrund-Thread, der alles automatisch importiert, was in ~/Claude Presentations (und Docs/) gespeichert wird.
class Watcher(threading.Thread):
    # EN: Creates the watched folder if needed.
    # DE: Legt den überwachten Ordner an, falls nötig.
    def __init__(self):
        super().__init__(daemon=True)
        os.makedirs(WATCH_DIR, exist_ok=True)
        self.pending = {}   # item -> signature from the previous scan (import only once it stopped changing)
        self.status = {"folder": WATCH_DIR, "last": None, "error": None, "count": 0}

    # EN: Scans every 3 seconds; errors never stop the watcher.
    # DE: Prüft alle 3 Sekunden; Fehler halten den Watcher nie an.
    def run(self):
        while True:
            try:
                self.scan()
            except Exception as e:     # the watcher must never die
                self.status["error"] = str(e)[:200]
            time.sleep(3)

    # EN: Finds new or changed items and imports them once they stopped changing.
    # DE: Findet neue oder geänderte Einträge und importiert sie, sobald sie sich nicht mehr ändern.
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

    # EN: Imports one deck folder, file or doc (updates the existing one instead of duplicating).
    # DE: Importiert einen Deck-Ordner, eine Datei oder ein Doc (aktualisiert das vorhandene, statt es zu verdoppeln).
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


# EN: Starts the folder watcher (called by the desktop app and main()).
# DE: Startet die Ordnerüberwachung (aufgerufen von der Desktop-App und main()).
def start_background():
    start_relay()
    threading.Thread(target=start_https, daemon=True).start()
    if not watcher.is_alive():
        watcher.start()


# ---------------------------------------------------------------- HTTP
# EN: The HTTP request handler: web UI, REST API, live events, uploads.
# DE: Der HTTP-Request-Handler: Web-Oberfläche, REST-API, Live-Events, Uploads.
class Handler(BaseHTTPRequestHandler):
    server_version = "SlideLink/1.4.0"

    # EN: Logs requests from phones (path only, no tokens); the PC itself stays quiet.
    # DE: Protokolliert Anfragen von Handys (nur Pfad, keine Tokens); der PC selbst bleibt still.
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
    # EN: True for the PC itself: 127.0.0.1 without proxy headers (Funnel/proxies never count as the PC).
    # DE: True für den PC selbst: 127.0.0.1 ohne Proxy-Header (Funnel/Proxys gelten nie als PC).
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

    # EN: The device token from the sl_token cookie.
    # DE: Das Geräte-Token aus dem Cookie sl_token.
    def _cookie_token(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        return c[COOKIE].value if COOKIE in c else None

    # EN: The device token from cookie or X-SlideLink-Token header.
    # DE: Das Geräte-Token aus Cookie oder Header X-SlideLink-Token.
    def _token(self):
        return self._cookie_token() or self.headers.get("X-SlideLink-Token")

    # EN: Who is asking: the PC (HOST), a linked device, or None.
    # DE: Wer fragt: der PC (HOST), ein verknüpftes Gerät oder None.
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

    # EN: Cookie header that stores (or clears) the device token for 10 years.
    # DE: Cookie-Header, der das Geräte-Token für 10 Jahre speichert (oder löscht).
    @staticmethod
    def _set_cookie(token):
        if token is None:
            return ("Set-Cookie", f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        return ("Set-Cookie", f"{COOKIE}={token}; Max-Age={COOKIE_AGE}; Path=/; HttpOnly; SameSite=Lax")

    # ----- responses -----
    # EN: Sends a JSON response.
    # DE: Sendet eine JSON-Antwort.
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

    # EN: Sends a file (with the right content type; the APK as a download).
    # DE: Sendet eine Datei (mit passendem Typ; die APK als Download).
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

    # EN: Reads and parses the JSON request body (with a size limit).
    # DE: Liest und parst den JSON-Body der Anfrage (mit Größenlimit).
    def _body(self, limit=40_000_000):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # ----- routes -----
    # EN: GET: web pages, static files, media, state, library, info, live events.
    # DE: GET: Webseiten, statische Dateien, Medien, Zustand, Bibliothek, Info, Live-Events.
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

    # EN: CORS headers for /api/import, only for claude.ai and the Chrome extension.
    # DE: CORS-Header für /api/import, nur für claude.ai und die Chrome-Erweiterung.
    def _cors_import(self):
        origin = self.headers.get("Origin") or ""
        if IMPORT_ORIGINS.match(origin):
            return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin"),
                    ("Access-Control-Allow-Methods", "POST, OPTIONS"),
                    ("Access-Control-Allow-Headers", "Content-Type"),
                    ("Access-Control-Allow-Private-Network", "true")]
        return None

    # EN: CORS preflight for /api/import.
    # DE: CORS-Vorabanfrage für /api/import.
    def do_OPTIONS(self):
        cors = self._cors_import() if urlparse(self.path).path == "/api/import" else None
        self.send_response(204 if cors else 403)
        for k, v in cors or []:
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # EN: CSRF protection: POSTs must come from SlideLink's own pages.
    # DE: CSRF-Schutz: POSTs müssen von SlideLinks eigenen Seiten kommen.
    def _same_origin(self):
        """Block other websites from driving SlideLink (CSRF): JSON only, and no foreign Origin."""
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == (self.headers.get("Host") or "")

    # EN: POST: operations, linking, devices, uploads, imports, window control.
    # DE: POST: Operationen, Verknüpfen, Geräte, Uploads, Importe, Fenstersteuerung.
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

    # EN: Handles a file upload (PowerPoint, zip, html, json, pictures).
    # DE: Verarbeitet einen Datei-Upload (PowerPoint, zip, html, json, Bilder).
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

    # EN: Tells a client whether it is linked (and as which device).
    # DE: Sagt einem Client, ob er verknüpft ist (und als welches Gerät).
    def _whoami(self):
        dev = self.auth()
        headers = []
        if dev and dev is not HOST and not self._cookie_token():
            headers.append(self._set_cookie(self.headers.get("X-SlideLink-Token")))  # restore lost cookie
        info = {"id": dev["id"], "name": dev["name"], "type": dev["type"]} if dev else None
        return self._json({"linked": bool(dev), "is_host": dev is HOST, "device": info}, headers=headers)

    # EN: Links a device with the 6-digit code and sets its token cookie.
    # DE: Verknüpft ein Gerät mit dem 6-stelligen Code und setzt sein Token-Cookie.
    def _pair(self, body):
        dev, token = registry.pair(body.get("code"), body.get("name"), body.get("type"))
        store.log(dev["name"], "was linked to this PC")
        store.publish()
        print(f"  Linked new device: {dev['name']}")
        return self._json({"ok": True, "token": token,
                           "device": {"id": dev["id"], "name": dev["name"], "type": dev["type"]}},
                          headers=[self._set_cookie(token)])

    # EN: End-to-end pairing steps (start/reveal/status from the app, confirm from the PC itself).
    # DE: Schritte des Ende-zu-Ende-Koppelns (start/reveal/status von der App, confirm nur vom PC selbst).
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

    # EN: Unlinks a device (not the PC) and disconnects it.
    # DE: Entfernt ein Gerät (nicht den PC) und trennt seine Verbindung.
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

    # EN: Runs an operation; phones may only use remote operations.
    # DE: Führt eine Operation aus; Handys dürfen nur Fernbedienungs-Operationen nutzen.
    def _op(self, op, dev):
        if dev.get("type") == "group" and op.get("op") not in GROUP_OPS:
            return self._json({"ok": False, "error": "Group members control the presentation only."}, 403)
        if op.get("op") == "speaker_me":       # "I'm presenting" on a phone: that phone's name
            op = {"op": "speaker_set", "name": "PC" if dev is HOST else dev["name"]}
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

    # EN: Server-Sent Events stream: sends the state on every change and a ping every 10 seconds.
    # DE: Server-Sent-Events-Stream: sendet den Zustand bei jeder Änderung und alle 10 Sekunden einen Ping.
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
            via = ("pc" if dev is HOST else "e2e" if self._e2e_inner() else "relay" if self.headers.get("X-SlideLink-Relay")
                   else "internet" if ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") else "wifi")   # 127.0.0.1 + linked device = through a proxy
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


# EN: Runs the server on its own (without the desktop app).
# DE: Startet nur den Server (ohne Desktop-App).
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
