# SlideLink: Release checks for release.ps1, using the updater's own signature code.
#   python setup/check_release.py files <folder>   every release file has a valid .sig
#   python setup/check_release.py live <tag>        the published release on GitHub passes the updater's checks
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import updater  # noqa: E402


def files(folder):
    bad = []
    for f in sorted(os.listdir(folder)):
        if f.endswith((".sig", ".zip")):
            continue
        p = os.path.join(folder, f)
        try:
            ok = updater.verify(open(p, "rb").read(), open(p + ".sig", encoding="ascii").read())
        except OSError:
            ok = False
        print(("ok   " if ok else "BAD  ") + f)
        bad += [] if ok else [f]
    return not bad


def live(tag):
    rel = json.loads(updater._get(f"https://api.github.com/repos/{updater.REPO}/releases/tags/{tag}"))
    assets = {a["name"]: a for a in rel["assets"]}
    ok = True
    for name, a in assets.items():
        if name.endswith((".sig", ".zip")):
            continue
        good = updater._signed(assets, a, print) is not None
        print(("ok   " if good else "BAD  ") + name)
        ok &= good
    print(tag, "pre-release" if rel["prerelease"] else "stable")
    return ok


if __name__ == "__main__":
    mode, arg = sys.argv[1], sys.argv[2]
    sys.exit(0 if (files(arg) if mode == "files" else live(arg)) else 1)
