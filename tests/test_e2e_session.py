# SlideLink · EN: End-to-end sessions against a real SlideLink server: requests, live stream, commands, and attacks
#             (replay, tampering, foreign keys, old clock, unlinked device, trying to become the PC).
# SlideLink · DE: Ende-zu-Ende-Sitzungen gegen einen echten SlideLink-Server: Anfragen, Live-Stream, Befehle und Angriffe.
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
if "server" not in sys.modules:
    os.environ.update({"SLIDELINK_DATA": tempfile.mkdtemp(prefix="slidelink-test-"), "SLIDELINK_PORT": "18765", "SLIDELINK_RELAY": "off"})
import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
import e2e  # noqa: E402
from e2e_client import Phone, _post  # noqa: E402

PORT = int(os.environ["SLIDELINK_PORT"])
B = f"http://127.0.0.1:{PORT}"
RELAY = {"X-SlideLink-Relay": "1"}        # as if it came through the relay


def ensure_server():
    try:
        urllib.request.urlopen(B + "/api/whoami", timeout=1)
    except Exception:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)


def paired_phone():
    ensure_server()
    p = Phone(B, RELAY)
    code, _ = server.registry.new_code()
    p.pair_start(code)
    pid = server.pairing.view()[-1]["id"]
    server.pairing.confirm(pid, True)
    p.pair_finish()
    return p


class Sessions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = paired_phone()
        st, _ = cls.p.hello()
        assert st == 200

    def test_1_get_and_post_as_that_phone(self):
        st, h, body = self.p.request("GET", "/api/whoami")
        self.assertEqual(st, 200)
        who = json.loads(body)
        self.assertTrue(who["linked"])
        self.assertFalse(who["is_host"], "an e2e phone must never be the PC")
        self.assertEqual(who["device"]["id"], self.p.device)
        st, _, body = self.p.request("GET", "/api/library")
        self.assertEqual(st, 200)
        self.assertIn("decks", json.loads(body))
        st, _, body = self.p.request("POST", "/api/op", json.dumps({"op": "next"}).encode(), {"Content-Type": "application/json"})
        self.assertIn(st, (200, 400), body)          # runs as a phone (remote op), not blocked as cross-site

    def test_2_static_files_and_big_answers(self):
        st, h, body = self.p.request("GET", "/remote.js")
        self.assertEqual(st, 200)
        with open(os.path.join(os.path.dirname(HERE), "static", "remote.js"), "rb") as f:
            self.assertEqual(body, f.read())
        st, h, body = self.p.request("GET", "/SlideLink.apk")
        if st == 200:
            self.assertTrue(body.startswith(b"PK"))

    def test_3_live_stream(self):
        resp, frames = self.p.stream("/events?role=remote&client=e2etest")
        kind, head = next(frames)
        self.assertEqual(kind, b"H")
        self.assertEqual(json.loads(head)["status"], 200)
        got = b""
        t = time.time()
        threading.Timer(0.5, server.store.publish).start()        # something changes on the PC
        for kind, data in frames:
            got += data
            if got.count(b"data:") >= 2 or time.time() - t > 8:
                break
        resp.close()
        self.assertGreaterEqual(got.count(b"data:"), 2, "live updates must arrive through the encrypted stream")
        time.sleep(0.3)
        vias = {c.get("via") for c in server.store.clients.values() if c["device"] == self.p.device}
        self.assertTrue(not vias or vias == {"e2e"}, vias)

    def test_4_replay_is_refused(self):
        seq, env = self.p.envelope("GET", "/api/whoami")
        st1, r1 = _post(B, "/api/e2e/req", env, "application/octet-stream", RELAY)
        st2, r2 = _post(B, "/api/e2e/req", env, "application/octet-stream", RELAY)
        self.assertEqual(st1, 200)
        self.assertEqual(st2, 400)
        self.assertIn(b"replayed", r2)

    def test_5_tampering_is_refused(self):
        seq, env = self.p.envelope("GET", "/api/whoami")
        bad = env[:-1] + bytes([env[-1] ^ 1])
        self.assertEqual(_post(B, "/api/e2e/req", bad, "application/octet-stream", RELAY)[0], 400)
        self.assertEqual(_post(B, "/api/e2e/req", b"x" * 100, "application/octet-stream", RELAY)[0], 401)
        self.assertEqual(_post(B, "/api/e2e/req", b"", "application/octet-stream", RELAY)[0], 400)

    def test_6_answers_cant_be_read_or_moved(self):
        """A relay can't read the answer, and can't hand the answer of one request to another."""
        seq, env = self.p.envelope("GET", "/api/whoami")
        st, resp = _post(B, "/api/e2e/req", env, "application/octet-stream", RELAY, stream=True)
        raw = resp.read()
        self.assertNotIn(b"linked", raw)
        self.assertNotIn(b"Test phone", raw)
        import io
        with self.assertRaises(e2e.CryptoError):
            list(self.p.frames(io.BytesIO(raw), seq + 1))

    def test_7_hello_attacks(self):
        q = Phone(B, RELAY)
        q.device, q.pc_pub = self.p.device, self.p.pc_pub
        self.assertEqual(q.hello()[0], 400, "someone else's key must not open a session for this phone")
        self.assertEqual(self.p.hello(ts=time.time() - 3600)[0], 400, "old hello (replayed) refused")
        st, _ = self.p.hello()
        self.assertEqual(st, 200)

    def test_8_cant_become_the_pc(self):
        st, _, body = self.p.request("POST", "/api/e2e/pair/confirm", b'{"id":"x","yes":true}', {"Content-Type": "application/json"})
        self.assertEqual(st, 400, "pairing/confirm can never be reached through a session")
        st, _, body = self.p.request("POST", "/api/relay", b'{"url":""}', {"Content-Type": "application/json"})
        self.assertIn(st, (401, 403), body)
        st, _, body = self.p.request("GET", "/api/whoami", headers={"Host": "localhost:%d" % PORT, "X-SlideLink-E2E": "guess"})
        self.assertFalse(json.loads(body)["is_host"])
        # forging the internal mark from outside (e.g. through the relay) gives nothing
        req = urllib.request.Request(B + "/api/whoami", headers={"X-SlideLink-E2E": "guess", "X-SlideLink-E2E-Device": self.p.device})
        self.assertFalse(json.loads(urllib.request.urlopen(req).read())["linked"])

    def test_9_unlinked_phone_is_out_at_once(self):
        p = paired_phone()
        p.hello()
        self.assertEqual(p.request("GET", "/api/whoami")[0], 200)
        server.registry.unlink(p.device)
        st, _, body = p.request("GET", "/api/whoami")
        self.assertEqual(st, 401)
        self.assertEqual(p.hello()[0], 400)

    def test_10_speed(self):
        t = time.time()
        for _ in range(30):
            self.p.request("GET", "/api/whoami")
        per = (time.time() - t) / 30 * 1000
        print(f"\n  encrypted request (local, no relay): {per:.1f} ms")
        self.assertLess(per, 100)


if __name__ == "__main__":
    unittest.main()
