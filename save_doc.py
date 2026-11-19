"""Save a claude.ai Doc (PDF export) into ~/Claude Presentations/Docs so SlideLink shows it under Docs.

Usage:
    python save_doc.py <saved export result file> <artifact slug> <version> <date ISO> <doc title> [<tab name>]

The export result file is the JSON the docs connector's export tool returned (format pdf), as saved to disk.
A second, third ... tab of the same doc becomes "<doc title> (<tab name>)".
Records the version in "Claude Presentations/.pull-state.json" (key "<slug>" or "<slug>#<tab name>").
"""
import base64
import datetime
import json
import os
import re
import sys

ROOT = os.environ.get("SLIDELINK_WATCH") or os.path.join(os.path.expanduser("~"), "Claude Presentations")
DOCS = os.path.join(ROOT, "Docs")
STATE = os.path.join(ROOT, ".pull-state.json")


def main(result_file, slug, version, date, title, tab=""):
    with open(result_file, encoding="utf-8") as f:
        j = json.load(f)
    data = j.get("data") or {}
    if data.get("format") != "pdf" or not data.get("bytes_b64"):
        sys.exit("not a PDF export result")
    pdf = base64.b64decode(data["bytes_b64"])
    name = title + (f" ({tab})" if tab else "")
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip()[:110] or slug
    os.makedirs(DOCS, exist_ok=True)
    out = os.path.join(DOCS, name + ".pdf")
    tmp = out + ".partial"
    with open(tmp, "wb") as f:
        f.write(pdf)
    with open(os.path.join(DOCS, name + ".source.json"), "w", encoding="utf-8") as f:
        json.dump({"kind": "claude-doc", "slug": slug, "date": date}, f, indent=2)
    os.replace(tmp, out)
    try:
        t = datetime.datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
        os.utime(out, (t, t))
    except ValueError:
        pass
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    state[slug + (f"#{tab}" if tab else "")] = {"version": version, "title": name, "folder": "Docs/" + name + ".pdf"}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    print(f"saved doc: {name} ({len(pdf) // 1024} KB)")


if __name__ == "__main__":
    main(*sys.argv[1:])
