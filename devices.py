"""Linked devices (link codes, hashed tokens) and the phone app files the PC hands out."""
import hashlib
import json
import os
import secrets
import shutil
import threading
import time

from core import CODE_TTL, DATA_DIR, DEVICES_FILE, DEVICE_TYPES, HOST, ROOT, STATIC, new_id, write_json

# ---------------------------------------------------------------- devices
_old_devices = os.path.join(ROOT, "devices.json")
if os.path.exists(_old_devices) and not os.path.exists(DEVICES_FILE):
    shutil.copyfile(_old_devices, DEVICES_FILE)


# Linked devices: one-time link codes, tokens (stored only as SHA-256 hashes), rename/unlink.
class Registry:
    """Linked devices. Tokens are stored only as SHA-256 hashes."""

    # Loads the device list from devices.json.
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

    # Saves the device list.
    def save(self):
        with self.lock:
            write_json(DEVICES_FILE, {"devices": self.items})

    # SHA-256 of a token: only this is stored, never the token itself.
    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # Shortens and cleans a device name typed by the user.
    @staticmethod
    def _clean_name(name, fallback="Phone"):
        name = " ".join(str(name or "").split())[:40]
        return name or fallback

    # Finds the device that belongs to a token (or None).
    def by_token(self, token):
        if not token:
            return None
        h = self._hash(token)
        with self.lock:
            for d in self.items:
                if secrets.compare_digest(d["token_hash"], h):
                    return d
        return None

    # Finds a device by its id.
    def get(self, did):
        with self.lock:
            for d in self.items:
                if d["id"] == did:
                    return d
        raise ValueError("Unknown device")

    # Creates a new 6-digit one-time link code (valid 10 minutes).
    def new_code(self):
        with self.lock:
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.code_exp = time.time() + CODE_TTL
            self.code_tries = 0
            return self.code, self.code_exp

    # Links a new device with the code; returns the device and its new token. 8 wrong tries kill the code.
    def pair(self, code, name, kind):
        with self.lock:
            self.check_code(code)
            return self.add_device(name, kind)

    # Checks the link code without using it up (wrong tries count; 8 kill the code). Call with the lock held.
    def check_code(self, code):
        if not self.code or time.time() > self.code_exp:
            raise ValueError("No active link code. On the PC open ⚙ Linked devices → Link a new device.")
        if not secrets.compare_digest(str(code or "").strip().replace(" ", ""), self.code):
            self.code_tries += 1
            if self.code_tries >= 8:
                self.code = None
                raise ValueError("Too many wrong codes. Create a new code on the PC.")
            raise ValueError("Wrong code - check the number shown on the PC.")

    # Uses up the code and links a new device; e2e_pub = the phone app's identity key (end-to-end pairing). Lock held.
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

    # Renames a linked device.
    def rename(self, did, name):
        with self.lock:
            d = self.get(did)
            d["name"] = self._clean_name(name, d["name"])
            self.save()
            return d

    # Removes a linked device (its token stops working at once).
    def unlink(self, did):
        with self.lock:
            d = self.get(did)
            self.items.remove(d)
            self.save()
            return d

    # The device list for the UI: online state, roles, connection type, app version (no secrets).
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


# Newest Android app version offered by this PC (static/app-version.json).
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
