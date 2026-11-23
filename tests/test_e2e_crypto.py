# SlideLink · EN: Tests for e2e.py: official test vectors, round trips, tampering. Run: python -m unittest discover tests
# SlideLink · DE: Tests für e2e.py: offizielle Testvektoren, Hin-und-zurück, Manipulation.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import e2e  # noqa: E402


class Vectors(unittest.TestCase):
    def test_gcm_nist_case_14(self):          # AES-256, zero key/IV, 16 zero bytes (GCM spec test case 14)
        a = e2e.Aead(bytes(32))
        out = a.seal(bytes(12), bytes(16))
        self.assertEqual(out.hex(), "cea7403d4d606b6e074ec5d3baf39d18" + "d0d1c8a799996bf0265b98b5d48ab919")
        self.assertEqual(a.open(bytes(12), out), bytes(16))

    def test_gcm_nist_case_13(self):          # AES-256, empty message: tag only
        self.assertEqual(e2e.Aead(bytes(32)).seal(bytes(12), b"").hex(), "530f8afbc74536b9a963b4f1c4cb738b")

    def test_hkdf_rfc5869_case_1(self):
        okm = e2e.hkdf(bytes([0x0b] * 22), bytes(range(13)), bytes(range(0xf0, 0xfa)), 42)
        self.assertEqual(okm.hex(), "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865")


class RoundTrips(unittest.TestCase):
    def test_ecdh_both_sides_agree(self):
        a, b = e2e.EcdhKey(), e2e.EcdhKey()
        self.assertEqual(len(a.public), 65)
        self.assertEqual(a.shared(b.public), b.shared(a.public))
        self.assertNotEqual(a.shared(b.public), e2e.EcdhKey().shared(b.public))

    def test_ecdh_rejects_bad_point(self):
        with self.assertRaises(e2e.CryptoError):
            e2e.EcdhKey().shared(b"\x04" + bytes(64))       # not on the curve

    def test_sign_verify(self):
        k = e2e.SigningKey()
        sig = k.sign(b"hello")
        self.assertTrue(e2e.verify(k.public, b"hello", sig))
        self.assertFalse(e2e.verify(k.public, b"hellO", sig))
        self.assertFalse(e2e.verify(e2e.SigningKey().public, b"hello", sig))
        self.assertFalse(e2e.verify(k.public, b"hello", sig[:-1] + bytes([sig[-1] ^ 1])))
        self.assertFalse(e2e.verify(k.public, b"hello", b"short"))

    def test_gcm_tamper_and_aad(self):
        a = e2e.Aead(os.urandom(32))
        n = os.urandom(12)
        box = a.seal(n, b"secret slide notes", b"header")
        self.assertEqual(a.open(n, box, b"header"), b"secret slide notes")
        for bad in (box[:-1] + bytes([box[-1] ^ 1]), bytes([box[0] ^ 1]) + box[1:]):
            with self.assertRaises(e2e.CryptoError):
                a.open(n, bad, b"header")
        with self.assertRaises(e2e.CryptoError):
            a.open(n, box, b"other header")
        with self.assertRaises(e2e.CryptoError):
            a.open(os.urandom(12), box, b"header")

    def test_gcm_large(self):
        a = e2e.Aead(os.urandom(32))
        data = os.urandom(3 * 1024 * 1024)
        self.assertEqual(a.open(bytes(12), a.seal(bytes(12), data)), data)

    def test_identity_key_saved_protected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pc-key.bin")
            k1 = e2e.SigningKey.load_or_create(p)
            k2 = e2e.SigningKey.load_or_create(p)
            self.assertEqual(k1.public, k2.public)
            with open(p, "rb") as f:
                raw = f.read()
            self.assertNotIn(k1.private_blob()[72:], raw)       # private part not stored in the clear
            self.assertTrue(e2e.verify(k1.public, b"x", k2.sign(b"x")))


if __name__ == "__main__":
    unittest.main()
