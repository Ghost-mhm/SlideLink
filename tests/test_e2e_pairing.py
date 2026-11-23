# SlideLink: End-to-end pairing against a real SlideLink server (own port and data folder): a simulated phone app pairs,
#             a relay in the middle tries to swap keys, wrong codes, replays, and the old pairing must keep working.
import base64
import hashlib
import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_tmp = tempfile.mkdtemp(prefix="slidelink-test-")
os.environ.update({"SLIDELINK_DATA": _tmp, "SLIDELINK_PORT": "18765", "SLIDELINK_RELAY": "off"})
import e2e  # noqa: E402
import e2e_server  # noqa: E402
import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

B = "http://127.0.0.1:18765"
_httpd = ThreadingHTTPServer(("127.0.0.1", 18765), server.Handler)
_httpd.daemon_threads = True
threading.Thread(target=_httpd.serve_forever, daemon=True).start()


def call(path, body=None, host=None, headers=None, opener=None):
    """host=True: as the PC itself; otherwise as a phone coming through the relay."""
    h = {"Content-Type": "application/json", **(headers or {})}
    if not host:
        h["X-SlideLink-Relay"] = "1"          # proxy header: never treated as the PC
    req = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers=h,
                                 method="POST" if body is not None else "GET")
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def b64(b):
    return base64.b64encode(b).decode()


class Phone:
    """What the app will do (step 4 builds the same in Java)."""

    def __init__(self):
        self.id_key, self.eph, self.nonce = e2e.SigningKey(), e2e.EcdhKey(), os.urandom(32)

    def start(self, code, send=call):
        commit = hashlib.sha256(self.id_key.public + self.eph.public + self.nonce).digest()
        st, r = send("/api/e2e/pair/start", {"code": code, "name": "Test phone", "type": "phone",
                                             "phone_pub": b64(self.id_key.public), "phone_eph": b64(self.eph.public), "commit": b64(commit)})
        self.r = r
        return st, r

    def reveal(self, send=call):
        r = self.r
        self.tr = e2e_server.transcript(base64.b64decode(r["pc_pub"]), base64.b64decode(r["pc_eph"]), base64.b64decode(r["pc_nonce"]),
                                        self.id_key.public, self.eph.public, self.nonce)
        st, _ = send("/api/e2e/pair/reveal", {"id": r["id"], "phone_nonce": b64(self.nonce)})
        return st, e2e_server.sas(self.tr)

    def token(self, box):
        k = e2e_server.pair_key(self.eph.shared(base64.b64decode(self.r["pc_eph"])), self.tr)
        return json.loads(e2e.Aead(k).open(bytes(12), base64.b64decode(box), self.r["id"].encode()))


def new_code():
    code, _ = server.registry.new_code()
    return code


def pc_screen():
    return server.pairing.view()


class Pairing(unittest.TestCase):
    def test_1_happy_path(self):
        phone = Phone()
        st, r = phone.start(new_code())
        self.assertEqual(st, 200, r)
        self.assertEqual(base64.b64decode(r["pc_pub"]), server.pairing.key.public)
        st, phone_sas = phone.reveal()
        self.assertEqual(st, 200)
        shown = pc_screen()
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["sas"], phone_sas, "phone and PC must show the same number")
        self.assertEqual(call("/api/e2e/pair/status", {"id": r["id"]})[1]["state"], "confirm")
        # a phone can't press "Yes" for itself
        self.assertEqual(call("/api/e2e/pair/confirm", {"id": r["id"], "yes": True})[0], 403)
        self.assertEqual(call("/api/e2e/pair/confirm", {"id": r["id"], "yes": True}, host=True)[0], 200)
        st, s = call("/api/e2e/pair/status", {"id": r["id"]})
        self.assertEqual(s["state"], "done")
        got = phone.token(s["box"])
        dev = server.registry.by_token(got["token"])
        self.assertIsNotNone(dev, "token from the sealed box must work")
        self.assertEqual(dev["e2e_pub"], b64(phone.id_key.public))
        self.assertEqual(call("/api/e2e/pair/status", {"id": r["id"]})[1]["state"], "gone", "token is handed out once")
        # the token works for normal requests (old way, unchanged)
        st, w = call("/api/whoami", headers={"X-SlideLink-Token": got["token"]})
        self.assertTrue(w.get("linked"), w)
        self.assertTrue(any(d.get("e2e") for d in server.registry.public({})), "PC device list shows the lock")

    def test_2_relay_in_the_middle_is_noticed(self):
        """The relay pairs with the PC itself and fakes the PC towards the phone: the numbers differ."""
        code = new_code()
        phone, mitm_as_phone, mitm_as_pc = Phone(), Phone(), e2e_server.Pairing(os.path.join(_tmp, "evil.bin"), server.registry)
        # phone -> (relay pretends to be the PC) ; relay -> real PC (pretends to be the phone)
        st, r_real = mitm_as_phone.start(code)
        self.assertEqual(st, 200)
        commit = hashlib.sha256(phone.id_key.public + phone.eph.public + phone.nonce).digest()
        with server.registry.lock:
            pass
        fake = mitm_as_pc.start.__func__  # the relay runs its own pairing logic towards the phone
        fake_r = fake(mitm_as_pc, {"code": code, "name": "x", "phone_pub": b64(phone.id_key.public),
                                   "phone_eph": b64(phone.eph.public), "commit": b64(commit)})
        phone.r = fake_r
        _, phone_sas = phone.reveal(send=lambda p, b: (200, mitm_as_pc.reveal(b)))
        mitm_as_phone.reveal()
        pc_sas = pc_screen()[-1]["sas"]
        self.assertNotEqual(phone_sas, pc_sas, "a relay in the middle must not be able to make the numbers match")
        # the user sees different numbers and clicks "No": nothing linked, the code is burnt
        n = len(server.registry.items)
        call("/api/e2e/pair/confirm", {"id": r_real["id"], "yes": False}, host=True)
        self.assertEqual(len(server.registry.items), n)
        self.assertEqual(call("/api/e2e/pair/status", {"id": r_real["id"]})[1]["state"], "rejected")
        self.assertIsNone(server.registry.code, "the code is unusable after a 'No'")

    def test_3_wrong_code_and_tries(self):
        new_code()
        st, r = Phone().start("000000" if server.registry.code != "000000" else "111111")
        self.assertEqual(st, 400)
        self.assertIn("Wrong code", r["error"])
        self.assertEqual(Phone().start("x")[0], 400)

    def test_4_commitment_is_checked(self):
        phone = Phone()
        st, r = phone.start(new_code())
        phone.nonce = os.urandom(32)          # reveals a different nonce than committed to
        st, _ = phone.reveal()
        self.assertEqual(st, 400)
        self.assertEqual(call("/api/e2e/pair/status", {"id": r["id"]})[1]["state"], "failed")

    def test_5_bad_input(self):
        new_code()
        for body in ({}, {"code": "1", "phone_pub": "!!", "phone_eph": "", "commit": ""},
                     {"code": server.registry.code, "phone_pub": b64(b"\x04" + bytes(64)), "phone_eph": b64(bytes(65)), "commit": b64(bytes(32))}):
            st, r = call("/api/e2e/pair/start", body)
            self.assertIn(st, (200, 400))
        self.assertEqual(call("/api/e2e/pair/reveal", {"id": "nope", "phone_nonce": b64(bytes(32))})[0], 400)
        self.assertEqual(call("/api/e2e/pair/confirm", {"id": "nope", "yes": True}, host=True)[0], 400)
        self.assertEqual(call("/api/e2e/pair/status", {"id": "nope"})[1]["state"], "gone")

    def test_6_box_can_only_be_opened_by_the_phone(self):
        phone, other = Phone(), Phone()
        _, r = phone.start(new_code())
        phone.reveal()
        call("/api/e2e/pair/confirm", {"id": r["id"], "yes": True}, host=True)
        box = call("/api/e2e/pair/status", {"id": r["id"]})[1]["box"]
        other.r, other.tr = phone.r, phone.tr      # knows everything the relay saw, but not the phone's private key
        with self.assertRaises(e2e.CryptoError):
            other.token(box)

    def test_7_old_pairing_still_works(self):
        jar = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        st, r = call("/api/pair", {"code": new_code(), "name": "Old app", "type": "phone"}, opener=jar)
        self.assertEqual(st, 200, r)
        self.assertTrue(call("/api/whoami", opener=jar)[1].get("linked"))
        self.assertFalse(server.registry.by_token(r["token"]).get("e2e_pub"))

    def test_8_pc_info_has_key(self):
        st, info = call("/api/info", host=True)
        self.assertEqual(base64.b64decode(info["e2e"]["pub"]), server.pairing.key.public)


if __name__ == "__main__":
    unittest.main()
