# SlideLink · EN: Desktop auto-update: installs every new stable release from GitHub silently (never betas, never a downgrade).
# SlideLink · DE: Desktop-Auto-Update: installiert jede neue stabile Version von GitHub still (nie Betas, nie ein Downgrade).
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request

REPO = "Ghost-mhm/SlideLink"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"     # GitHub's "latest" = newest stable, pre-releases excluded
CHECK_EVERY = 6 * 3600
UA = {"User-Agent": "SlideLink-Updater", "Accept": "application/vnd.github+json"}


def parse(v):
    """'v1.2.1' / '1.2.1 beta' -> ((1, 2, 1), stable?) so that 1.2.1 beta < 1.2.1 < 1.2.2."""
    nums = tuple(int(n) for n in re.findall(r"\d+", (v or "").split("-")[0].split(" ")[0])[:3])
    stable = not re.search(r"beta|alpha|rc|pre|test", v or "", re.I)
    return nums + (0,) * (3 - len(nums)), int(stable)


def installed_version():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SlideLink") as k:
            return winreg.QueryValueEx(k, "DisplayVersion")[0], winreg.QueryValueEx(k, "InstallLocation")[0]
    except OSError:
        return None, None


def check(root, notify=None, busy=lambda: False, log=print):
    """One check. Returns the version it started installing, or None."""
    cur, loc = installed_version()
    if not cur or not loc or os.path.normcase(os.path.abspath(loc)) != os.path.normcase(os.path.abspath(root)):
        return None                                   # running from a source folder, not the installed app: never touch it
    with urllib.request.urlopen(urllib.request.Request(LATEST, headers=UA), timeout=20) as r:
        rel = json.load(r)
    tag = rel.get("tag_name", "")
    if rel.get("prerelease") or rel.get("draft") or parse(tag) <= parse(cur):
        return None
    asset = next((a for a in rel.get("assets", []) if re.match(r"SlideLink-Setup.*\.exe$", a["name"], re.I)), None)
    if not asset:
        return None
    while busy():                                     # never during a presentation
        time.sleep(60)
    exe = os.path.join(tempfile.gettempdir(), f"SlideLink-Setup-{re.sub(r'[^0-9A-Za-z.]', '', tag)}.exe")
    with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": UA["User-Agent"]}), timeout=120) as r, open(exe, "wb") as f:
        f.write(r.read())
    if os.path.getsize(exe) != asset.get("size", os.path.getsize(exe)):
        log("update: download incomplete"); return None
    appdata = os.environ.get("APPDATA", "")
    desktop = os.path.join(os.path.expanduser("~"), "Desktop", "SlideLink.lnk")
    startup = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup\SlideLink.lnk")
    args = [exe, "/S", "/Launch"] + ([] if os.path.exists(desktop) else ["/NoDesktop"]) + (["/Startup"] if os.path.exists(startup) else [])
    if notify:
        notify("Updating SlideLink", f"Installing version {tag.lstrip('v')} – SlideLink restarts in a moment.")
    log(f"update: {cur} -> {tag}")
    # detached: the installer stops this process and starts the new version
    subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, close_fds=True)   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    return tag


def run_forever(root, notify=None, busy=lambda: False, log=print):
    time.sleep(60)                                    # let SlideLink start first
    while True:
        try:
            if check(root, notify, busy, log):
                return
        except Exception as e:                        # offline, rate limit …: try again next time
            log(f"update check failed: {e}")
        time.sleep(CHECK_EVERY)
