"""Watched folder 'Claude Presentations': decks, pages, PowerPoint and docs sync in by themselves."""
import datetime
import hashlib
import json
import os
import re
import shutil
import threading
import time

from core import MEDIA_DIR, PPT_EXT, WATCH_DIR
from importers import DOCS_DIR, DOC_FILES, claude_to_import, html_to_doc, pdf_to_doc, upload_to_op
from decks import library, store

ON_NOTIFY = None                           # set by the desktop app: tray notification (title, text)

# ---------------------------------------------------------------- watched folder: Claude Presentations
WATCH_FILES = {".html", ".htm"} | PPT_EXT | {".zip"}
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


# Reads a Claude Slides deck folder (deck.json, deck.css, slides/, assets/, source.json).
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


# Fingerprint of a folder (names, sizes, times): changes when any file changes.
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


# Background thread that imports everything saved in ~/Claude Presentations (and Docs/) automatically.
class Watcher(threading.Thread):
    # Creates the watched folder if needed.
    def __init__(self):
        super().__init__(daemon=True)
        os.makedirs(WATCH_DIR, exist_ok=True)
        self.pending = {}   # item -> signature from the previous scan (import only once it stopped changing)
        self.status = {"folder": WATCH_DIR, "last": None, "error": None, "count": 0}

    # Scans every 3 seconds; errors never stop the watcher.
    def run(self):
        while True:
            try:
                self.scan()
            except Exception as e:     # the watcher must never die
                self.status["error"] = str(e)[:200]
            time.sleep(3)

    # Finds new or changed items and imports them once they stopped changing.
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

    # Imports one deck folder, file or doc (updates the existing one instead of duplicating).
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
