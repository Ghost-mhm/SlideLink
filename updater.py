# SlideLink: Desktop auto-update: installs every new stable release from GitHub silently (never betas, never a downgrade),
#               and pulls the newest phone app so the PC offers it to every phone.
import base64
import hashlib
import hmac
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
# Public half of the release signing key (setup/sign.ps1). Only files signed with its private half are installed,
#     so control over the GitHub account alone can't push software onto SlideLink PCs.
SIGN_E = 65537
SIGN_N = int("d5a87196820709343ef03b9767d65b40f6068b8447d56ac202edcabac0c0cae8612c5be5b704935a0190ca811379cd7ace3794865513ab31071266f5052238e319c2063c891e9f03ca478774edeedbb4099a3394fc3a0487f66f7e7057154b8a6ef11f6a1c01a16f37d0993bfa94ca838f8e205550c2b6f8524f0b449a4e2da6173501ef6ea9619ef2ffb3bf0b162fb496169cd6a1a409911fdf13d129e05a6830a620adde73df874da6fc43805dbc92ecf715810758219101b4d0c24ff1d08bca110c06d1c099c3cca0fbfe744020c205252237c0d79984606d6f81db0314f699c846cafa70a84101714683d478be57afd60310a188501d8997d3c3d6dcc0af56ec0ad717113a173509f5de792b300e060c9b7e32405185623cf5dca3af052a869333454459671d85ffd942bb49088d3814a5a2959095a35154256f9890b3288491d9b1189e9b43576275d6c652b6351a68bade575eae8135cfc0ce7979e0a46978f79f96bdce5f5d16b107ddfb28731002ccbd9baee37d0cf2679757fbc58d", 16)
UA = {"User-Agent": "SlideLink-Updater", "Accept": "application/vnd.github+json"}


def parse(v):
    """'v1.2.1' / '1.2.1 beta' -> ((1, 2, 1), stable?) so that 1.2.1 beta < 1.2.1 < 1.2.2."""
    nums = tuple(int(n) for n in re.findall(r"\d+", (v or "").split("-")[0].split(" ")[0])[:3])
    stable = not re.search(r"beta|alpha|rc|pre|test", v or "", re.I)
    return nums + (0,) * (3 - len(nums)), int(stable)


def _get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def verify(data, sig_b64, n=None, e=None):
    """RSA PKCS#1 v1.5 / SHA-256 signature check (stdlib only). True only for a valid signature."""
    n, e = n or SIGN_N, e or SIGN_E
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except (ValueError, AttributeError):
        return False
    k = (n.bit_length() + 7) // 8
    if len(sig) != k or int.from_bytes(sig, "big") >= n:
        return False
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    t = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(data).digest()
    return hmac.compare_digest(em, b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t)


def _signed(assets, asset, log, timeout=120):
    """Downloads an asset and its <name>.sig; returns the bytes only if size and signature are right."""
    sig = assets.get(asset["name"] + ".sig")
    if not sig:
        log(f"update: {asset['name']} has no signature, skipped"); return None
    data = _get(asset["browser_download_url"], timeout)
    if len(data) != asset.get("size", len(data)):
        log(f"update: {asset['name']} download incomplete"); return None
    if not verify(data, _get(sig["browser_download_url"]).decode("ascii", "replace")):
        log(f"update: {asset['name']} has a WRONG signature, not installed"); return None
    return data


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
    rel = json.loads(_get(LATEST, 20))
    tag = rel.get("tag_name", "")
    if rel.get("prerelease") or rel.get("draft") or parse(tag) <= parse(cur):
        return None
    assets = {a["name"]: a for a in rel.get("assets", [])}
    asset = next((a for n, a in assets.items() if re.match(r"SlideLink-Setup.*\.exe$", n, re.I)), None)
    if not asset:
        return None
    while busy():                                     # never during a presentation
        time.sleep(60)
    data = _signed(assets, asset, log)
    if data is None:
        return None
    exe = os.path.join(tempfile.mkdtemp(prefix="slidelink-update-"), f"SlideLink-Setup-{re.sub(r'[^0-9A-Za-z.]', '', tag)}.exe")
    with open(exe, "wb") as f:
        f.write(data)
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


# Phone app: pulls the newest Android app from GitHub releases so every phone is offered it by this PC.
def check_app(have, dest, betas=False, log=print):
    """have: versionCode this PC offers now. Saves a newer SlideLink-*.apk + app-version.json into dest.
    A release needs both assets; betas only if this SlideLink is a beta itself."""
    rels = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases?per_page=20"))
    best = None
    for rel in rels:
        if rel.get("draft") or (rel.get("prerelease") and not betas):
            continue
        assets = {a["name"]: a for a in rel.get("assets", [])}
        apk = next((a for n, a in assets.items() if re.match(r"SlideLink-[\d.]+\.apk$", n, re.I)), None)
        meta = assets.get("app-version.json")
        if not apk or not meta:
            continue
        raw = _signed(assets, meta, log, 30)
        if raw is None:
            continue
        info = json.loads(raw.decode("utf-8-sig"))
        code = int(info.get("versionCode", 0))
        if code > have and (not best or code > best[0]):
            best = (code, info, apk, assets)
    if not best:
        return None
    code, info, apk, assets = best
    os.makedirs(dest, exist_ok=True)
    part = os.path.join(dest, "SlideLink.apk.part")
    data = _signed(assets, apk, log)
    if data is None or not data.startswith(b"PK"):
        return None
    with open(part, "wb") as f:
        f.write(data)
    os.replace(part, os.path.join(dest, "SlideLink.apk"))      # apk first, then the version file that announces it
    info = {"versionCode": code, "versionName": str(info.get("versionName", "")), "url": "/SlideLink.apk",
            "notes": str(info.get("notes", ""))[:300]}
    with open(os.path.join(dest, "app-version.json"), "w", encoding="utf-8") as f:
        json.dump(info, f)
    log(f"phone app: pulled {info['versionName']} ({code}) from GitHub")
    return info["versionName"]


def run_forever(root, notify=None, busy=lambda: False, log=print, app=None):
    """app: (lambda: versionCode offered now, folder for pulled apps, betas?) to also keep the phone app current."""
    time.sleep(60)                                    # let SlideLink start first
    while True:
        if app:
            try:
                have, dest, betas = app()
                name = check_app(have, dest, betas, log)
                if name and notify:
                    notify("Phone app updated", f"SlideLink app {name} is ready – phones are offered it on their next connect.")
            except Exception as e:
                log(f"phone app check failed: {e}")
        try:
            if check(root, notify, busy, log):
                return
        except Exception as e:                        # offline, rate limit …: try again next time
            log(f"update check failed: {e}")
        time.sleep(CHECK_EVERY)
