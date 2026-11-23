// SlideLink · EN: Cross-check of e2e.py against Java's crypto (the same APIs Android uses). Driven by tests/test_e2e_interop.py.
//   stdin:  lines "key value" (hex) from Python;  stdout: lines "key value" (hex) for Python.
// SlideLink · DE: Gegenprüfung von e2e.py mit Javas Krypto (dieselben Schnittstellen wie Android).
import java.math.BigInteger;
import java.security.*;
import java.security.interfaces.*;
import java.security.spec.*;
import java.util.*;
import javax.crypto.*;
import javax.crypto.spec.*;

public class E2eInterop {
    static byte[] hex(String s) {
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return b;
    }
    static String hex(byte[] b) {
        StringBuilder sb = new StringBuilder();
        for (byte x : b) sb.append(String.format("%02x", x));
        return sb.toString();
    }
    static ECParameterSpec params() throws Exception {
        KeyPairGenerator g = KeyPairGenerator.getInstance("EC");
        g.initialize(new ECGenParameterSpec("secp256r1"));
        return ((ECPublicKey) g.generateKeyPair().getPublic()).getParams();
    }
    static PublicKey pub(byte[] point) throws Exception {           // 04||X||Y -> key
        ECPoint p = new ECPoint(new BigInteger(1, Arrays.copyOfRange(point, 1, 33)), new BigInteger(1, Arrays.copyOfRange(point, 33, 65)));
        return KeyFactory.getInstance("EC").generatePublic(new ECPublicKeySpec(p, params()));
    }
    static byte[] point(PublicKey k) {                                 // key -> 04||X||Y
        ECPoint w = ((ECPublicKey) k).getW();
        byte[] out = new byte[65];
        out[0] = 4;
        byte[] x = w.getAffineX().toByteArray(), y = w.getAffineY().toByteArray();
        System.arraycopy(x, Math.max(0, x.length - 32), out, 1 + Math.max(0, 32 - x.length), Math.min(32, x.length));
        System.arraycopy(y, Math.max(0, y.length - 32), out, 33 + Math.max(0, 32 - y.length), Math.min(32, y.length));
        return out;
    }
    static byte[] rawToDer(byte[] rs) throws Exception {               // r||s -> DER (what Java's ECDSA expects)
        byte[] r = new BigInteger(1, Arrays.copyOfRange(rs, 0, 32)).toByteArray();
        byte[] s = new BigInteger(1, Arrays.copyOfRange(rs, 32, 64)).toByteArray();
        byte[] out = new byte[6 + r.length + s.length];
        out[0] = 0x30; out[1] = (byte) (4 + r.length + s.length);
        out[2] = 0x02; out[3] = (byte) r.length; System.arraycopy(r, 0, out, 4, r.length);
        out[4 + r.length] = 0x02; out[5 + r.length] = (byte) s.length; System.arraycopy(s, 0, out, 6 + r.length, s.length);
        return out;
    }
    static byte[] derToRaw(byte[] der) {                               // DER -> r||s
        int i = 3, rl = der[i++] & 0xff;
        byte[] r = Arrays.copyOfRange(der, i, i + rl); i += rl + 1;
        int sl = der[i++] & 0xff;
        byte[] s = Arrays.copyOfRange(der, i, i + sl);
        byte[] out = new byte[64];
        System.arraycopy(r, Math.max(0, r.length - 32), out, 32 - Math.min(32, r.length), Math.min(32, r.length));
        System.arraycopy(s, Math.max(0, s.length - 32), out, 64 - Math.min(32, s.length), Math.min(32, s.length));
        return out;
    }

    public static void main(String[] a) throws Exception {
        Map<String, byte[]> in = new HashMap<>();
        Scanner sc = new Scanner(System.in);
        while (sc.hasNextLine()) { String[] p = sc.nextLine().trim().split(" "); if (p.length == 2) in.put(p[0], hex(p[1])); }

        // ECDH: Java key + Python's public key
        KeyPairGenerator g = KeyPairGenerator.getInstance("EC");
        g.initialize(new ECGenParameterSpec("secp256r1"));
        KeyPair kp = g.generateKeyPair();
        KeyAgreement ka = KeyAgreement.getInstance("ECDH");
        ka.init(kp.getPrivate());
        ka.doPhase(pub(in.get("py_ecdh")), true);
        System.out.println("java_ecdh " + hex(point(kp.getPublic())));
        System.out.println("java_shared " + hex(ka.generateSecret()));

        // AES-GCM: open Python's box, seal one for Python
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(in.get("gcm_key"), "AES"), new GCMParameterSpec(128, in.get("gcm_nonce")));
        c.updateAAD(in.get("gcm_aad"));
        System.out.println("java_opened " + hex(c.doFinal(in.get("gcm_box"))));
        c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(in.get("gcm_key"), "AES"), new GCMParameterSpec(128, in.get("gcm_nonce2")));
        c.updateAAD(in.get("gcm_aad"));
        System.out.println("java_box " + hex(c.doFinal("sealed by java".getBytes("UTF-8"))));

        // ECDSA: check Python's signature, sign for Python
        Signature v = Signature.getInstance("SHA256withECDSA");
        v.initVerify(pub(in.get("py_sign_pub")));
        v.update(in.get("msg"));
        System.out.println("java_verified " + (v.verify(rawToDer(in.get("py_sig"))) ? "01" : "00"));
        KeyPair sk = g.generateKeyPair();
        Signature s = Signature.getInstance("SHA256withECDSA");
        s.initSign(sk.getPrivate());
        s.update(in.get("msg"));
        System.out.println("java_sign_pub " + hex(point(sk.getPublic())));
        System.out.println("java_sig " + hex(derToRaw(s.sign())));
    }
}
