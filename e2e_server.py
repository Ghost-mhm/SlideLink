# SlideLink · EN: End-to-end pairing on the PC: the phone app and the PC exchange identity keys through the (untrusted) relay and
#             the user compares a 6-digit number on both screens, like Bluetooth pairing. A relay in the middle can't make the
#             numbers match (commitment first, then reveal), so it can't swap keys unnoticed.
# SlideLink · DE: Ende-zu-Ende-Koppeln am PC: Handy-App und PC tauschen über das (nicht vertrauenswürdige) Relay ihre
#             Identitätsschlüssel, und man vergleicht eine 6-stellige Zahl auf beiden Bildschirmen, wie beim Bluetooth-Koppeln.
#
# Protocol (all binary values base64 in JSON):
#   1 phone -> PC  start  {code, name, type, phone_pub, phone_eph, commit=SHA256(phone_pub|phone_eph|phone_nonce)}
#                  PC checks the link code (not used up yet)  ->  {id, pc_pub, pc_eph, pc_nonce}
#   2 phone -> PC  reveal {id, phone_nonce}  PC checks the commitment; both now show SAS(transcript)
#   3 user clicks "Yes" on the PC (host only) -> the device is linked with phone_pub; its token is sealed with
#                  K = HKDF(ECDH(pc_eph, phone_eph), SHA256(transcript), "slidelink pair v1")
#   4 phone -> PC  status {id}  ->  {state, box}  (box = AES-GCM(K, nonce 0, {token, device}), aad = id)
#   transcript = "SL-PAIR-1" | pc_pub | pc_eph | pc_nonce | phone_pub | phone_eph | phone_nonce
import base64
import hashlib
import json
import os
import secrets
import threading
import time

import e2e

PAIR_TTL = 180          # seconds a pairing may take from start to "Yes"
MAX_PENDING = 3


def b64(b):
    return base64.b64encode(b).decode("ascii")


def unb64(s, size=None):
    try:
        b = base64.b64decode(str(s or ""), validate=True)
    except ValueError:
        raise ValueError("bad pairing data")
    if size is not None and len(b) != size:
        raise ValueError("bad pairing data")
    return b


def transcript(pc_pub, pc_eph, pc_nonce, phone_pub, phone_eph, phone_nonce):
    return b"SL-PAIR-1" + pc_pub + pc_eph + pc_nonce + phone_pub + phone_eph + phone_nonce


def sas(tr):
    """The 6-digit number both screens show."""
    return f"{int.from_bytes(hashlib.sha256(b'SL-SAS-1' + tr).digest()[:4], 'big') % 1_000_000:06d}"


def pair_key(shared, tr):
    return e2e.hkdf(shared, hashlib.sha256(tr).digest(), b"slidelink pair v1")


class Pairing:
    """Pairings in progress; the PC's identity key lives here too."""

    def __init__(self, key_path, registry, on_change=lambda: None):
        self.key = e2e.SigningKey.load_or_create(key_path)
        self.registry = registry
        self.on_change = on_change
        self.lock = threading.Lock()
        self.pending = {}

    def _clean(self):
        """Drops pairings that took too long or whose link code is no longer the current one (new code, used, burnt)."""
        now, code = time.time(), self.registry.code
        def stale(v):
            if v["state"] in ("failed", "rejected"):
                return now - v["t"] > 60           # kept a minute so the phone can say why
            return v["state"] != "done" and (now - v["t"] > PAIR_TTL or v["code"] != code)
        for pid in [p for p, v in self.pending.items() if stale(v)]:
            del self.pending[pid]

    def start(self, body):
        phone_pub, phone_eph = unb64(body.get("phone_pub"), 65), unb64(body.get("phone_eph"), 65)
        commit = unb64(body.get("commit"), 32)
        with self.registry.lock:
            self.registry.check_code(body.get("code"))        # right code needed; used up only on "Yes"
            code = self.registry.code
        with self.lock:
            self._clean()
            if sum(1 for v in self.pending.values() if v["state"] != "done") >= MAX_PENDING:
                raise ValueError("Too many pairings at once - try again in a minute.")
            eph = e2e.EcdhKey()
            pid = secrets.token_urlsafe(12)
            self.pending[pid] = {"t": time.time(), "code": code, "state": "committed", "name": str(body.get("name") or "Phone")[:40],
                                 "type": str(body.get("type") or "phone"), "phone_pub": phone_pub, "phone_eph": phone_eph,
                                 "commit": commit, "eph": eph, "nonce": os.urandom(32)}
            v = self.pending[pid]
            return {"ok": True, "id": pid, "pc_pub": b64(self.key.public), "pc_eph": b64(eph.public), "pc_nonce": b64(v["nonce"])}

    def reveal(self, body):
        phone_nonce = unb64(body.get("phone_nonce"), 32)
        with self.lock:
            v = self.pending.get(str(body.get("id")))
            if not v or v["state"] != "committed":
                raise ValueError("This pairing has ended - start again.")
            if not secrets.compare_digest(hashlib.sha256(v["phone_pub"] + v["phone_eph"] + phone_nonce).digest(), v["commit"]):
                v["state"] = "failed"
                raise ValueError("Pairing check failed - start again.")
            v["tr"] = transcript(self.key.public, v["eph"].public, v["nonce"], v["phone_pub"], v["phone_eph"], phone_nonce)
            v["sas"] = sas(v["tr"])
            v["state"] = "confirm"
        self.on_change()
        return {"ok": True}

    def confirm(self, pid, yes):
        """The user on the PC says the numbers match (yes) or not."""
        with self.lock:
            v = self.pending.get(str(pid))
            if not v or v["state"] != "confirm":
                raise ValueError("This pairing has ended.")
            if not yes:
                v["state"] = "rejected"
                with self.registry.lock:
                    self.registry.code = None     # numbers differed: maybe someone in the middle - the code is burnt
            else:
                with self.registry.lock:
                    if not self.registry.code:
                        v["state"] = "failed"
                        raise ValueError("The link code has expired - create a new one.")
                    dev, token = self.registry.add_device(v["name"], v["type"], e2e_pub=b64(v["phone_pub"]))
                k = pair_key(v["eph"].shared(v["phone_eph"]), v["tr"])
                payload = json.dumps({"token": token, "device": {"id": dev["id"], "name": dev["name"], "type": dev["type"]}}).encode()
                v["box"] = e2e.Aead(k).seal(bytes(12), payload, str(pid).encode())
                v["state"], v["device"] = "done", dev
        self.on_change()
        return v.get("device")

    def status(self, body):
        with self.lock:
            v = self.pending.get(str(body.get("id")))
            if not v:
                return {"ok": True, "state": "gone"}
            out = {"ok": True, "state": v["state"]}
            if v["state"] == "done":
                out["box"] = b64(v.pop("box"))          # handed out once
                del self.pending[str(body.get("id"))]
            return out

    def view(self):
        """For the PC screen: pairings waiting for the user's yes/no."""
        with self.lock:
            self._clean()
            return [{"id": p, "name": v["name"], "sas": v["sas"]} for p, v in self.pending.items() if v["state"] == "confirm"]
