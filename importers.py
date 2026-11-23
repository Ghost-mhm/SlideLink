"""Turns other formats (Claude decks, HTML, zip, JSON, images, PowerPoint, PDF) into SlideLink imports."""
import html as htmllib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import zipfile

from core import IMAGE_EXT, MEDIA_DIR, NO_WINDOW, PPT_EXT, WATCH_DIR

# ---------------------------------------------------------------- importing other formats
# Plain text of an HTML fragment (tags removed, entities decoded).
def _text(fragment):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# Splits one claude.ai slide file into its <section> HTML, title and speaker notes.
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


# Turns a raw claude.ai Slides deck (index + css + slides) into an import_deck operation.
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


# Imports an HTML file: a single-file deck becomes slides, any other page one interactive page.
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


# Imports a deck packed as .zip (claude.ai export or a folder with deck.json).
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


# Imports a SlideLink deck saved as JSON.
def json_to_import(data, title):
    d = json.loads(data.decode("utf-8-sig") if isinstance(data, bytes) else data)
    if isinstance(d, dict) and isinstance(d.get("slides"), list):
        return {"op": "import_deck", "title": d.get("title") or title, "theme": d.get("theme"), "css": d.get("css") or "",
                "faces": d.get("faces") or [], "slides": d["slides"], "source": None}
    raise ValueError("This JSON file is not a SlideLink presentation")


# Creates a new media folder with a random, unguessable name; returns (name, path).
def _media_dir():
    tok = secrets.token_hex(12)
    path = os.path.join(MEDIA_DIR, tok)
    os.makedirs(path, exist_ok=True)
    return tok, path


# A slide that shows one picture full-screen (used for PowerPoint and image imports).
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


# Lets the installed PowerPoint export every slide as a picture (with notes) and imports them.
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


# Chooses the right importer for an uploaded file by its extension.
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

DOC_FILES = {".pdf", ".html", ".htm"}
DOCS_DIR = os.path.join(WATCH_DIR, "Docs")
PDF_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_pages.ps1")

DOC_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title><style>
html,body{{margin:0;background:#ececef}}
body{{padding:24px 0 40px}}
img{{display:block;width:min(96%,1200px);margin:0 auto 22px;background:#fff;box-shadow:0 2px 14px rgba(0,0,0,.18)}}
</style></head><body>{pages}</body></html>"""


# Imports a PDF doc: every page rendered to a picture, stacked as one scrolling document.
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


# Imports an HTML doc as one scrolling page (adds a readable style if it has none).
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


# Date of a presentation/doc: claude.ai's date from source.json, else the file time.
