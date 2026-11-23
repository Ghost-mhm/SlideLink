# SlideLink: Command-line import of a claude.ai deck folder into a running SlideLink.
"""Import a claude.ai "Slides" deck folder into SlideLink.

A deck folder looks like the deck's project files:
    <folder>/deck.json          (the deck index: title, order, faces, ...)
    <folder>/deck.css           (optional shared styles)
    <folder>/slides/<id>.html   (one <section> per slide, notes in <aside>)
    <folder>/source.txt         (optional: the claude.ai artifact URL)

Usage:  python import_claude_deck.py <folder> [<folder> ...]
SlideLink must be running. Re-importing the same deck replaces the old copy.
"""
import html
import json
import os
import re
import sys
import urllib.request

BASE = os.environ.get("SLIDELINK_URL", "http://localhost:8765").rstrip("/")


# Plain text of an HTML fragment.
def text_of(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# Reads one slide file: HTML, title and notes.
def parse_slide(src):
    notes = ""
    m = re.search(r"<aside[^>]*>([\s\S]*?)</aside>", src, re.I)
    if m:
        notes = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        src = src[:m.start()] + src[m.end():]
    title = ""
    for tag in ("h1", "h2", "h3", "p"):
        m = re.search(rf"<{tag}\b[^>]*>([\s\S]*?)</{tag}>", src, re.I)
        if m and text_of(m.group(1)):
            title = text_of(m.group(1))[:120]
            break
    return src.strip(), notes, title


# Reads a whole deck folder.
def load_folder(folder):
    with open(os.path.join(folder, "deck.json"), encoding="utf-8") as f:
        index = json.load(f)
    css = ""
    css_path = os.path.join(folder, "deck.css")
    if os.path.exists(css_path):
        with open(css_path, encoding="utf-8") as f:
            css = f.read()
    slide_dir = os.path.join(folder, "slides")
    files = {fn[:-5] for fn in os.listdir(slide_dir) if fn.endswith(".html")}
    order = [sid for sid in index.get("order", []) if sid in files]
    order += sorted(files - set(order))  # slides missing from the order are shown last, like on claude.ai
    slides = []
    for sid in order:
        with open(os.path.join(slide_dir, sid + ".html"), encoding="utf-8") as f:
            body, notes, title = parse_slide(f.read())
        hidden = bool(re.search(r"<section\b[^>]*\shidden\b", body, re.I))
        slides.append({"layout": "html", "html": body, "notes": notes, "title": title or sid,
                       "status": "draft" if hidden else "done"})
    faces = [{"family": v.get("family", ""), "href": v["href"]}
             for v in (index.get("faces") or {}).values() if isinstance(v, dict) and v.get("href")]
    source = None
    src_path = os.path.join(folder, "source.txt")
    if os.path.exists(src_path):
        with open(src_path, encoding="utf-8") as f:
            source = {"kind": "claude-slides", "url": f.read().strip()}
    return {"op": "import_deck", "title": index.get("title") or os.path.basename(folder), "css": css,
            "faces": faces, "slides": slides, "source": source, "by": "claude.ai import"}


# Sends the deck to SlideLink's import API.
def post(payload):
    req = urllib.request.Request(BASE + "/api/op", data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for folder in sys.argv[1:]:
        payload = load_folder(folder)
        res = post(payload)
        state = "updated" if res.get("result", {}).get("replaced") else "imported"
        print(f"{state}: {payload['title']} ({len(payload['slides'])} slides)" if res.get("ok") else f"FAILED {folder}: {res}")
