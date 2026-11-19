"""Assemble a claude.ai Slides deck from saved files into ~/Claude Presentations (SlideLink syncs it).

Usage (one JSON argument file):
    python save_deck.py job.json

job.json:
{
  "slug": "<artifact slug>", "url": "https://claude.ai/artifact/<id>", "version": "<artifact version>",
  "files": { "deck.json": "C:/.../saved.json", "deck.css": "C:/.../saved.css",
             "slides/cover.html": "C:/.../saved.html", "assets/logo.png": "C:/.../saved.png" }
}
The deck folder is built next to the target first and swapped in at once, so SlideLink never sees half a deck.
It also records the artifact version in "Claude Presentations/.pull-state.json" for the next pull.
"""
import json
import os
import re
import shutil
import sys

DEST = os.environ.get("SLIDELINK_WATCH") or os.path.join(os.path.expanduser("~"), "Claude Presentations")
STATE = os.path.join(DEST, ".pull-state.json")
SAFE = re.compile(r"^(deck\.json|deck\.css|slides/[A-Za-z0-9_-]{1,64}\.html|assets/[A-Za-z0-9_./-]{1,120})$")


def main(job_path):
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    files = job["files"]
    if "deck.json" not in files:
        sys.exit("deck.json missing")
    with open(files["deck.json"], encoding="utf-8-sig") as f:
        title = (json.load(f).get("title") or job["slug"]).strip()
    name = re.sub(r'[\\/:*?"<>|]', "", title).strip()[:80] or job["slug"]
    target = os.path.join(DEST, name)
    tmp = os.path.join(DEST, "." + name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, src in files.items():
        if not SAFE.match(rel) or ".." in rel:
            print("skipped unsafe path:", rel)
            continue
        out = os.path.join(tmp, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(src, out)
    with open(os.path.join(tmp, "source.json"), "w", encoding="utf-8") as f:
        json.dump({"kind": "claude-slides", "slug": job["slug"], "url": job.get("url", ""),
                   "date": job.get("date", "")}, f, indent=2)   # date = claude.ai's updated_at (sorting in SlideLink)
    old = target + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(target):
        os.replace(target, old)
    os.replace(tmp, target)
    shutil.rmtree(old, ignore_errors=True)
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    state[job["slug"]] = {"version": job.get("version", ""), "title": title, "folder": name}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    n = sum(1 for r in files if r.startswith("slides/"))
    print(f"saved: {name} ({n} slides)")


if __name__ == "__main__":
    main(sys.argv[1])
