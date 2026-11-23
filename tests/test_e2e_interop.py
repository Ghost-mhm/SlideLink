# SlideLink: e2e.py must speak exactly the same crypto as Java/Android: ECDH, AES-GCM and ECDSA in both directions.
#             Needs a JDK (JAVA_HOME or the one android/build.ps1 uses); skipped if none is found.
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import e2e  # noqa: E402


def jdk_bin():
    for home in (os.environ.get("JAVA_HOME"), r"C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot"):
        if home and os.path.exists(os.path.join(home, "bin", "javac.exe")):
            return os.path.join(home, "bin")
    j = shutil.which("javac")
    return os.path.dirname(j) if j else None


@unittest.skipUnless(jdk_bin(), "no JDK found")
class JavaInterop(unittest.TestCase):
    def test_both_directions(self):
        bin_ = jdk_bin()
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([os.path.join(bin_, "javac.exe"), "-d", out, os.path.join(HERE, "interop", "E2eInterop.java")], check=True)
            ecdh, signer = e2e.EcdhKey(), e2e.SigningKey()
            key, nonce, nonce2, aad, msg = os.urandom(32), os.urandom(12), os.urandom(12), b"slidelink-e2e", b"pair me"
            box = e2e.Aead(key).seal(nonce, b"sealed by python", aad)
            sent = {"py_ecdh": ecdh.public, "gcm_key": key, "gcm_nonce": nonce, "gcm_nonce2": nonce2, "gcm_aad": aad,
                    "gcm_box": box, "py_sign_pub": signer.public, "py_sig": signer.sign(msg), "msg": msg}
            r = subprocess.run([os.path.join(bin_, "java.exe"), "-cp", out, "E2eInterop"], check=True, capture_output=True,
                               input="\n".join(f"{k} {v.hex()}" for k, v in sent.items()), text=True)
            got = {k: bytes.fromhex(v) for k, v in (line.split() for line in r.stdout.splitlines() if line.strip())}

        self.assertEqual(ecdh.shared(got["java_ecdh"]), got["java_shared"], "ECDH secret differs")
        self.assertEqual(got["java_opened"], b"sealed by python", "Java could not open Python's AES-GCM box")
        self.assertEqual(e2e.Aead(key).open(nonce2, got["java_box"], aad), b"sealed by java")
        self.assertEqual(got["java_verified"], b"\x01", "Java rejected Python's signature")
        self.assertTrue(e2e.verify(got["java_sign_pub"], msg, got["java_sig"]), "Python rejected Java's signature")


if __name__ == "__main__":
    unittest.main()
