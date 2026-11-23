# SlideLink: A page or the phone app that reconnects (e.g. Wi-Fi -> mobile data) replaces its old live connection at once,
#             even if the old one still looks open (the old network vanished without a goodbye). Others are left alone.
import http.client
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_e2e_session import ensure_server, PORT, server  # noqa: E402  (same test server)


def open_stream(query, token):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("GET", "/events?" + query, headers={"X-SlideLink-Token": token, "X-SlideLink-Relay": "1"})
    r = c.getresponse()
    r.read1(64)                     # connected
    c._keep = r                     # the response owns the socket (HTTP/1.0): keep it, or the connection closes
    return c


def conns(device):
    time.sleep(0.3)
    return [v for v in server.store.clients.values() if v["device"] == device]


class Reconnect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_server()
        code, _ = server.registry.new_code()
        cls.dev, cls.token = server.registry.pair(code, "Reconnect phone", "phone")

    def test_same_page_replaces_old_connection(self):
        a = open_stream("role=remote&client=pageA", self.token)      # old network: never closed by us
        self.assertEqual(len(conns(self.dev["id"])), 1)
        b = open_stream("role=remote&client=pageA", self.token)      # same page, new network
        self.assertEqual(len(conns(self.dev["id"])), 1, "the stale connection must be gone at once")
        b.close(); a.close()

    def test_same_app_replaces_old_connection(self):
        a = open_stream("role=remote&client=x1&appv=1.17", self.token)
        b = open_stream("role=remote&client=x2&appv=1.17", self.token)   # the app opened a new page
        self.assertEqual(len(conns(self.dev["id"])), 1)
        b.close(); a.close()

    def test_two_different_pages_stay(self):
        a = open_stream("role=remote&client=pA", self.token)
        b = open_stream("role=library&client=pB", self.token)
        self.assertEqual(len(conns(self.dev["id"])), 2, "different pages (no app) must not kick each other")
        a.close(); b.close()

    def test_other_devices_untouched(self):
        code, _ = server.registry.new_code()
        other, tok2 = server.registry.pair(code, "Other phone", "phone")
        a = open_stream("role=remote&client=same&appv=1.17", self.token)
        b = open_stream("role=remote&client=same&appv=1.17", tok2)
        self.assertEqual(len(conns(self.dev["id"])), 1)
        self.assertEqual(len(conns(other["id"])), 1)
        a.close(); b.close()


if __name__ == "__main__":
    unittest.main()
