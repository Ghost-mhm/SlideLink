"""Shared paths, constants and small helpers of the SlideLink server."""
import json
import os
import re
import subprocess
import time
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")


# Where presentations and devices are stored (%LOCALAPPDATA%\SlideLink\data or SLIDELINK_DATA).
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


# Number of clicks a slide needs before Next goes to the next slide (claude.ai build rules).
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



DEVICE_TYPES = ["phone", "tablet", "pc"]
CODE_TTL = 600            # link codes are valid for 10 minutes
COOKIE = "sl_token"
COOKIE_AGE = 10 * 365 * 24 * 3600
HOST = {"id": "host", "name": "This PC", "type": "host"}
REMOTE_ONLY = {"phone", "tablet"}          # these devices control presentations but never edit them
REMOTE_OPS = {"next", "prev", "goto", "blackout", "open_deck", "show_on_pc", "open_on_pc", "close_on_pc", "resync",
              "poll_start", "poll_show", "poll_end", "poll_clear", "speaker_me", "speaker_set", "audience_new", "join_qr",
              "group_start", "group_end", "group_kick", "group_qr", "set_lang", "pointer",
              "question_show", "question_dismiss", "questions_clear", "questions_open"}
LANGS = ("en", "de")        # interface languages (static/i18n.js); the admin picks one for every screen
# Group members (joined with the group link, not linked): control the talk only, never edit, switch decks or manage.
GROUP_OPS = {"next", "prev", "goto", "blackout", "resync", "poll_start", "poll_show", "poll_end", "poll_clear",
             "speaker_me", "speaker_set", "join_qr", "pointer",
             "question_show", "question_dismiss", "questions_clear", "questions_open"}
GROUP_COOKIE = "sl_group"
GROUP_HOURS = 4
# Viewers of the audience link: see the current slide live and vote in polls, nothing else.
AUDIENCE = {"id": "audience", "name": "Audience", "type": "audience"}
PC_ROLES = {"library", "present", "edit"}  # SSE roles that are windows on the PC
WATCH_DIR = os.environ.get("SLIDELINK_WATCH") or os.path.join(os.path.expanduser("~"), "Claude Presentations")
KICK = object()           # queue sentinel: device was unlinked
REPLACED = object()       # queue sentinel: the same page/app connected again (old connection is stale)


# A short random id for slides and devices.
def new_id():
    return uuid.uuid4().hex[:8]


# Version of the web files (newest change time): when it changes, open windows reload themselves.
def app_build():
    """Changes whenever a file of the app itself changes, so open windows know to reload."""
    try:
        return str(int(max(os.path.getmtime(os.path.join(STATIC, f)) for f in os.listdir(STATIC))))
    except (OSError, ValueError):
        return "0"


# Writes JSON atomically (temp file + replace), so a crash never leaves a broken file.
def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# Creates a new slide with defaults for missing fields.
def make_slide(**fields):
    s = {"id": new_id(), "title": "", "content": "", "notes": "", "layout": "content",
         "status": "draft", "updated_at": time.time(), "updated_by": "you"}
    for k in SLIDE_FIELDS:
        if k in fields and fields[k] is not None:
            s[k] = fields[k]
    return s


# The welcome presentation shown on the very first start.
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
