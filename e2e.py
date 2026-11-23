# SlideLink: End-to-end crypto between phone app and PC, on the crypto built into Windows (CNG, bcrypt.dll) - no extra packages.
#             ECDH P-256 (key exchange), ECDSA P-256 (PC/phone identity), HKDF-SHA256, AES-256-GCM. Same building blocks as Android's.
import ctypes
import hashlib
import hmac
import os
import struct
import threading
from ctypes import POINTER, byref, c_ubyte, c_ulong, c_ulonglong, c_void_p, c_wchar_p, sizeof

_bc = ctypes.WinDLL("bcrypt.dll")
_crypt32 = ctypes.WinDLL("crypt32.dll")
_kernel32 = ctypes.WinDLL("kernel32.dll")

ECDH_PUBLIC_P256 = 0x314B4345      # 'ECK1'
ECDSA_PUBLIC_P256 = 0x31534345     # 'ECS1'


class CryptoError(Exception):
    pass


def _ok(status, what):
    if status != 0:
        raise CryptoError(f"{what} failed (0x{status & 0xFFFFFFFF:08x})")


_lock = threading.Lock()
_alg = {}


def _provider(name):
    """Opens a CNG algorithm provider once (they are thread-safe and slow to open)."""
    with _lock:
        if name not in _alg:
            h = c_void_p()
            _ok(_bc.BCryptOpenAlgorithmProvider(byref(h), c_wchar_p(name), None, 0), f"open {name}")
            if name == "AES":
                mode = "ChainingModeGCM\0".encode("utf-16-le")
                _ok(_bc.BCryptSetProperty(h, c_wchar_p("ChainingMode"), mode, len(mode), 0), "GCM mode")
            _alg[name] = h
        return _alg[name]


class _Key:
    """A CNG key handle that is destroyed with the object."""

    def __init__(self, handle):
        self.h = handle

    def __del__(self):
        try:
            if self.h:
                _bc.BCryptDestroyKey(self.h)
        except Exception:
            pass


def _export(key, blob_type):
    n = c_ulong()
    _ok(_bc.BCryptExportKey(key.h, None, c_wchar_p(blob_type), None, 0, byref(n), 0), "export size")
    buf = (c_ubyte * n.value)()
    _ok(_bc.BCryptExportKey(key.h, None, c_wchar_p(blob_type), buf, n, byref(n), 0), "export")
    return bytes(buf[:n.value])


def _import(alg, blob_type, blob):
    h = c_void_p()
    b = (c_ubyte * len(blob)).from_buffer_copy(blob)
    _ok(_bc.BCryptImportKeyPair(_provider(alg), None, c_wchar_p(blob_type), byref(h), b, len(blob), 0), "import key")
    return _Key(h)


def _generate(alg):
    h = c_void_p()
    _ok(_bc.BCryptGenerateKeyPair(_provider(alg), byref(h), 256, 0), "generate key")
    _ok(_bc.BCryptFinalizeKeyPair(h, 0), "finalize key")
    return _Key(h)


def _pub_blob(magic, point):
    """CNG public blob from an uncompressed point 04||X||Y."""
    if len(point) != 65 or point[0] != 4:
        raise CryptoError("bad public key")
    return struct.pack("<II", magic, 32) + point[1:]


def _point(blob):
    return b"\x04" + blob[8:72]


# ------------------------------------------------------------------ key exchange (one-time keys per session)
class EcdhKey:
    """A P-256 key pair for one key exchange."""

    def __init__(self):
        self._k = _generate("ECDH_P256")
        self.public = _point(_export(self._k, "ECCPUBLICBLOB"))     # 65 bytes, 04||X||Y (same as Android/Java)

    def shared(self, peer_public):
        """The raw ECDH secret (X coordinate, big-endian, 32 bytes) with the peer's public key."""
        peer = _import("ECDH_P256", "ECCPUBLICBLOB", _pub_blob(ECDH_PUBLIC_P256, peer_public))
        s = c_void_p()
        _ok(_bc.BCryptSecretAgreement(self._k.h, peer.h, byref(s), 0), "secret agreement")
        try:
            out = (c_ubyte * 32)()
            n = c_ulong()
            _ok(_bc.BCryptDeriveKey(s, c_wchar_p("TRUNCATE"), None, out, 32, byref(n), 0), "derive")
            return bytes(out[:n.value])[::-1]     # CNG hands the raw secret out little-endian
        finally:
            _bc.BCryptDestroySecret(s)


# ------------------------------------------------------------------ identity (long-lived signing keys)
class SigningKey:
    """A long-lived P-256 identity key (ECDSA). Stored encrypted for this Windows user (DPAPI)."""

    def __init__(self, private_blob=None):
        self._k = _import("ECDSA_P256", "ECCPRIVATEBLOB", private_blob) if private_blob else _generate("ECDSA_P256")
        self.public = _point(_export(self._k, "ECCPUBLICBLOB"))

    def private_blob(self):
        return _export(self._k, "ECCPRIVATEBLOB")

    def sign(self, data):
        """Signature r||s (64 bytes) over SHA-256(data)."""
        digest = (c_ubyte * 32).from_buffer_copy(hashlib.sha256(data).digest())
        sig = (c_ubyte * 64)()
        n = c_ulong()
        _ok(_bc.BCryptSignHash(self._k.h, None, digest, 32, sig, 64, byref(n), 0), "sign")
        return bytes(sig[:n.value])

    @classmethod
    def load_or_create(cls, path):
        """Loads the key from path (DPAPI-protected) or creates and saves a new one."""
        try:
            with open(path, "rb") as f:
                return cls(dpapi_unprotect(f.read()))
        except FileNotFoundError:
            pass
        key = cls()
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(dpapi_protect(key.private_blob()))
        os.replace(tmp, path)
        return key


def verify(public, data, signature):
    """True if signature (r||s, 64 bytes) over SHA-256(data) belongs to public (04||X||Y)."""
    if len(signature) != 64:
        return False
    try:
        key = _import("ECDSA_P256", "ECCPUBLICBLOB", _pub_blob(ECDSA_PUBLIC_P256, public))
    except CryptoError:
        return False
    digest = (c_ubyte * 32).from_buffer_copy(hashlib.sha256(data).digest())
    sig = (c_ubyte * 64).from_buffer_copy(signature)
    return _bc.BCryptVerifySignature(key.h, None, digest, 32, sig, 64, 0) == 0


def fingerprint(public):
    """Short, stable name of a public key (hex of SHA-256, first 16 bytes)."""
    return hashlib.sha256(public).hexdigest()[:32]


# ------------------------------------------------------------------ HKDF (RFC 5869)
def hkdf(secret, salt, info, length=32):
    prk = hmac.new(salt or b"\x00" * 32, secret, hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


# ------------------------------------------------------------------ AES-256-GCM
class _AuthInfo(ctypes.Structure):
    _fields_ = [("cbSize", c_ulong), ("dwInfoVersion", c_ulong),
                ("pbNonce", c_void_p), ("cbNonce", c_ulong),
                ("pbAuthData", c_void_p), ("cbAuthData", c_ulong),
                ("pbTag", c_void_p), ("cbTag", c_ulong),
                ("pbMacContext", c_void_p), ("cbMacContext", c_ulong),
                ("cbAAD", c_ulong), ("cbData", c_ulonglong), ("dwFlags", c_ulong)]


class Aead:
    """AES-256-GCM with one key. seal/open take a 12-byte nonce; the 16-byte tag is appended (like Java's GCM)."""

    def __init__(self, key):
        if len(key) != 32:
            raise CryptoError("AES-256 needs a 32-byte key")
        h = c_void_p()
        k = (c_ubyte * 32).from_buffer_copy(key)
        _ok(_bc.BCryptGenerateSymmetricKey(_provider("AES"), byref(h), None, 0, k, 32, 0), "AES key")
        self._k = _Key(h)

    def _run(self, encrypt, nonce, data, aad, tag):
        if len(nonce) != 12:
            raise CryptoError("nonce must be 12 bytes")
        nb = (c_ubyte * 12).from_buffer_copy(nonce)
        ab = (c_ubyte * max(1, len(aad))).from_buffer_copy(aad or b"\0")
        tb = (c_ubyte * 16).from_buffer_copy(tag)
        info = _AuthInfo(sizeof(_AuthInfo), 1, ctypes.cast(nb, c_void_p), 12,
                         ctypes.cast(ab, c_void_p) if aad else None, len(aad),
                         ctypes.cast(tb, c_void_p), 16, None, 0, 0, 0, 0)
        src = (c_ubyte * max(1, len(data))).from_buffer_copy(data or b"\0")
        dst = (c_ubyte * max(1, len(data)))()
        n = c_ulong()
        fn = _bc.BCryptEncrypt if encrypt else _bc.BCryptDecrypt
        st = fn(self._k.h, src, len(data), byref(info), None, 0, dst, len(data), byref(n), 0)
        if st != 0:
            if not encrypt:
                raise CryptoError("message was changed or has the wrong key")
            _ok(st, "encrypt")
        return bytes(dst[:n.value]), bytes(tb)

    def seal(self, nonce, plaintext, aad=b""):
        ct, tag = self._run(True, nonce, plaintext, aad, b"\0" * 16)
        return ct + tag

    def open(self, nonce, sealed, aad=b""):
        if len(sealed) < 16:
            raise CryptoError("message too short")
        pt, _ = self._run(False, nonce, sealed[:-16], aad, sealed[-16:])
        return pt


# ------------------------------------------------------------------ DPAPI: keep the PC's private key unreadable for other Windows users
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", c_ulong), ("pbData", POINTER(c_ubyte))]


def _dpapi(fn, data):
    buf = (c_ubyte * len(data)).from_buffer_copy(data)
    src, dst = _Blob(len(data), buf), _Blob()
    if not fn(byref(src), None, None, None, None, 0x1, byref(dst)):      # CRYPTPROTECT_UI_FORBIDDEN
        raise CryptoError("DPAPI failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        _kernel32.LocalFree(dst.pbData)


def dpapi_protect(data):
    return _dpapi(_crypt32.CryptProtectData, data)


def dpapi_unprotect(data):
    return _dpapi(_crypt32.CryptUnprotectData, data)
