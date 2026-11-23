package app.slidelink.remote;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.interfaces.ECPublicKey;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.ECParameterSpec;
import java.security.spec.ECPoint;
import java.security.spec.ECPublicKeySpec;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import javax.crypto.Cipher;
import javax.crypto.KeyAgreement;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * End-to-end encryption between this app and the PC through the (untrusted) relay. Same protocol as the PC's
 *     e2e_server.py (pairing with a 6-digit number check) and e2e_session.py (encrypted requests and streams).
 *     Plain Java (no Android classes), so it is tested against the real PC on a computer as well.
 */
final class E2e {
    /** The phone's long-lived identity key (Android: in the Keystore, never leaves it). */
    interface Signer {
        byte[] publicPoint();                  // 04||X||Y
        byte[] signRaw(byte[] data) throws Exception;   // r||s over SHA-256(data)
    }

    /** Opens a connection to an address (lets the app pin the PC's certificate on the home Wi-Fi). */
    interface Opener {
        HttpURLConnection open(String url) throws Exception;
    }

    static final Opener PLAIN = u -> (HttpURLConnection) new URL(u).openConnection();
    private static final SecureRandom RNG = new SecureRandom();

    // ------------------------------------------------------------------ building blocks
    static byte[] sha256(byte[]... parts) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        for (byte[] p : parts) md.update(p);
        return md.digest();
    }

    static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : parts) o.write(p, 0, p.length);
        return o.toByteArray();
    }

    static byte[] random(int n) {
        byte[] b = new byte[n];
        RNG.nextBytes(b);
        return b;
    }

    static String b64(byte[] b) { return Base64.getEncoder().encodeToString(b); }
    static byte[] unb64(String s) { return Base64.getDecoder().decode(s); }

    static byte[] hkdf(byte[] secret, byte[] salt, byte[] info, int length) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(salt == null || salt.length == 0 ? new byte[32] : salt, "HmacSHA256"));
        byte[] prk = mac.doFinal(secret);
        mac.init(new SecretKeySpec(prk, "HmacSHA256"));
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] block = new byte[0];
        for (int i = 1; out.size() < length; i++) {
            mac.update(block);
            mac.update(info);
            mac.update((byte) i);
            block = mac.doFinal();
            out.write(block, 0, block.length);
        }
        return Arrays.copyOf(out.toByteArray(), length);
    }

    static byte[] gcm(boolean seal, byte[] key, byte[] nonce, byte[] data, byte[] aad) throws Exception {
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(seal ? Cipher.ENCRYPT_MODE : Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
        c.updateAAD(aad);
        return c.doFinal(data);
    }

    private static ECParameterSpec P256;

    static synchronized ECParameterSpec p256() throws Exception {
        if (P256 == null) P256 = ((ECPublicKey) ephemeral().getPublic()).getParams();
        return P256;
    }

    static KeyPair ephemeral() throws Exception {
        KeyPairGenerator g = KeyPairGenerator.getInstance("EC");
        g.initialize(new ECGenParameterSpec("secp256r1"));
        return g.generateKeyPair();
    }

    static byte[] point(PublicKey k) {
        ECPoint w = ((ECPublicKey) k).getW();
        byte[] out = new byte[65];
        out[0] = 4;
        put32(out, 1, w.getAffineX());
        put32(out, 33, w.getAffineY());
        return out;
    }

    private static void put32(byte[] out, int at, BigInteger v) {
        byte[] b = v.toByteArray();
        int n = Math.min(32, b.length);
        System.arraycopy(b, b.length - n, out, at + 32 - n, n);
    }

    static PublicKey publicKey(byte[] point) throws Exception {
        if (point.length != 65 || point[0] != 4) throw new IllegalArgumentException("bad public key");
        ECPoint p = new ECPoint(new BigInteger(1, Arrays.copyOfRange(point, 1, 33)), new BigInteger(1, Arrays.copyOfRange(point, 33, 65)));
        return KeyFactory.getInstance("EC").generatePublic(new ECPublicKeySpec(p, p256()));
    }

    static byte[] ecdh(KeyPair mine, byte[] theirPoint) throws Exception {
        KeyAgreement ka = KeyAgreement.getInstance("ECDH");
        ka.init(mine.getPrivate());
        ka.doPhase(publicKey(theirPoint), true);
        return ka.generateSecret();
    }

    /** r||s -> DER, as Java's ECDSA wants it. */
    static byte[] rawToDer(byte[] rs) {
        byte[] r = new BigInteger(1, Arrays.copyOfRange(rs, 0, 32)).toByteArray();
        byte[] s = new BigInteger(1, Arrays.copyOfRange(rs, 32, 64)).toByteArray();
        byte[] out = new byte[6 + r.length + s.length];
        out[0] = 0x30; out[1] = (byte) (4 + r.length + s.length);
        out[2] = 0x02; out[3] = (byte) r.length; System.arraycopy(r, 0, out, 4, r.length);
        out[4 + r.length] = 0x02; out[5 + r.length] = (byte) s.length; System.arraycopy(s, 0, out, 6 + r.length, s.length);
        return out;
    }

    /** DER -> r||s. */
    static byte[] derToRaw(byte[] der) {
        int i = 2;
        if ((der[1] & 0x80) != 0) i += der[1] & 0x7f;
        int rl = der[i + 1] & 0xff;
        byte[] r = Arrays.copyOfRange(der, i + 2, i + 2 + rl);
        i += 2 + rl;
        int sl = der[i + 1] & 0xff;
        byte[] s = Arrays.copyOfRange(der, i + 2, i + 2 + sl);
        byte[] out = new byte[64];
        put32(out, 0, new BigInteger(1, r));
        put32(out, 32, new BigInteger(1, s));
        return out;
    }

    static boolean verify(byte[] point, byte[] data, byte[] rawSig) {
        try {
            Signature v = Signature.getInstance("SHA256withECDSA");
            v.initVerify(publicKey(point));
            v.update(data);
            return rawSig.length == 64 && v.verify(rawToDer(rawSig));
        } catch (Exception e) {
            return false;
        }
    }

    // ------------------------------------------------------------------ HTTP
    static Map<String, Object> postJson(Opener opener, String url, Map<String, Object> body) throws Exception {
        HttpURLConnection c = opener.open(url);
        try {
            c.setConnectTimeout(10000);
            c.setReadTimeout(20000);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            try (OutputStream o = c.getOutputStream()) { o.write(Json.write(body).getBytes(StandardCharsets.UTF_8)); }
            int st = c.getResponseCode();
            InputStream in = st < 400 ? c.getInputStream() : c.getErrorStream();
            Map<String, Object> m = Json.obj(new String(readAll(in), StandardCharsets.UTF_8));
            m.put("_status", (long) st);
            return m;
        } finally {
            c.disconnect();
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        if (in == null) return new byte[0];
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] b = new byte[16384];
        for (int n; (n = in.read(b)) > 0; ) o.write(b, 0, n);
        return o.toByteArray();
    }

    // ------------------------------------------------------------------ pairing (6-digit number check)
    /** One pairing attempt: start() -> show sas() -> wait for the PC's "yes" with finish(). */
    static final class Pairing {
        final String base;
        final Opener opener;
        final Signer me;
        final KeyPair eph;
        final byte[] nonce = random(32);
        String id, sas;
        byte[] pcPub, pcEph, transcript;

        Pairing(String base, Opener opener, Signer me) throws Exception {
            this.base = base; this.opener = opener; this.me = me; this.eph = ephemeral();
        }

        /** Sends the code and the commitment, reveals, and returns the number to compare with the PC. */
        String start(String code, String name) throws Exception {
            byte[] myPub = me.publicPoint(), myEph = point(eph.getPublic());
            Map<String, Object> b = new LinkedHashMap<>();
            b.put("code", code); b.put("name", name); b.put("type", "phone");
            b.put("phone_pub", b64(myPub)); b.put("phone_eph", b64(myEph)); b.put("commit", b64(sha256(myPub, myEph, nonce)));
            Map<String, Object> r = postJson(opener, base + "/api/e2e/pair/start", b);
            if (!Boolean.TRUE.equals(r.get("ok"))) throw new IllegalStateException(Json.str(r, "error"));
            id = Json.str(r, "id");
            pcPub = unb64(Json.str(r, "pc_pub"));
            pcEph = unb64(Json.str(r, "pc_eph"));
            transcript = cat("SL-PAIR-1".getBytes(StandardCharsets.US_ASCII), pcPub, pcEph, unb64(Json.str(r, "pc_nonce")), myPub, myEph, nonce);
            Map<String, Object> rv = new LinkedHashMap<>();
            rv.put("id", id); rv.put("phone_nonce", b64(nonce));
            Map<String, Object> r2 = postJson(opener, base + "/api/e2e/pair/reveal", rv);
            if (!Boolean.TRUE.equals(r2.get("ok"))) throw new IllegalStateException(Json.str(r2, "error"));
            byte[] h = sha256("SL-SAS-1".getBytes(StandardCharsets.US_ASCII), transcript);
            long v = ((h[0] & 0xffL) << 24 | (h[1] & 0xffL) << 16 | (h[2] & 0xffL) << 8 | (h[3] & 0xffL)) % 1_000_000L;
            sas = String.format("%06d", v);
            return sas;
        }

        /** Asks once: "confirm" (waiting), "done" -> {token, device, name}, "rejected"/"failed"/"gone". */
        Map<String, Object> poll() throws Exception {
            Map<String, Object> b = new LinkedHashMap<>();
            b.put("id", id);
            Map<String, Object> r = postJson(opener, base + "/api/e2e/pair/status", b);
            String state = Json.str(r, "state");
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("state", state);
            if ("done".equals(state)) {
                byte[] k = hkdf(ecdh(eph, pcEph), sha256(transcript), "slidelink pair v1".getBytes(StandardCharsets.US_ASCII), 32);
                Map<String, Object> got = Json.obj(new String(gcm(false, k, new byte[12], unb64(Json.str(r, "box")),
                        id.getBytes(StandardCharsets.UTF_8)), StandardCharsets.UTF_8));
                @SuppressWarnings("unchecked") Map<String, Object> dev = (Map<String, Object>) got.get("device");
                out.put("token", Json.str(got, "token"));
                out.put("device", Json.str(dev, "id"));
                out.put("name", Json.str(dev, "name"));
                out.put("pc_pub", b64(pcPub));
            }
            return out;
        }
    }

    // ------------------------------------------------------------------ encrypted sessions
    /** The answer to one encrypted request: status, headers and the body as a stream (decrypted frame by frame). */
    static final class Response {
        int status;
        final Map<String, String> headers = new LinkedHashMap<>();
        InputStream body;
    }

    /** A session with the paired PC through one address (the relay). Thread-safe; re-opens itself when needed. */
    static final class Client {
        final String base, device;
        final byte[] pcPub;
        final Signer me;
        final Opener opener;
        private byte[] sid, kUp, kDown;
        private final AtomicLong seq = new AtomicLong();

        Client(String base, String device, byte[] pcPub, Signer me, Opener opener) {
            this.base = base; this.device = device; this.pcPub = pcPub; this.me = me; this.opener = opener;
        }

        synchronized void hello() throws Exception {
            KeyPair eph = ephemeral();
            byte[] ephPub = point(eph.getPublic());
            long ts = System.currentTimeMillis() / 1000;
            byte[] tr = cat("SL-HELLO-1".getBytes(StandardCharsets.US_ASCII), pcPub, device.getBytes(StandardCharsets.UTF_8), ephPub,
                    ByteBuffer.allocate(8).putLong(ts).array());
            Map<String, Object> b = new LinkedHashMap<>();
            b.put("device", device); b.put("phone_eph", b64(ephPub)); b.put("ts", ts); b.put("sig", b64(me.signRaw(tr)));
            Map<String, Object> r = postJson(opener, base + "/api/e2e/hello", b);
            if (!Boolean.TRUE.equals(r.get("ok"))) throw new SecurityException(Json.str(r, "error") == null ? "hello refused" : Json.str(r, "error"));
            byte[] s = unb64(Json.str(r, "sid")), pcEph = unb64(Json.str(r, "pc_eph"));
            tr = cat(tr, pcEph, s);
            if (!verify(pcPub, cat("SL-HELLO-PC-1".getBytes(StandardCharsets.US_ASCII), tr), unb64(Json.str(r, "sig"))))
                throw new SecurityException("not the paired PC");          // someone in between: never talk to it
            byte[] keys = hkdf(ecdh(eph, pcEph), sha256(tr), "slidelink session v1".getBytes(StandardCharsets.US_ASCII), 64);
            sid = s;
            kUp = Arrays.copyOf(keys, 32);
            kDown = Arrays.copyOfRange(keys, 32, 64);
            seq.set(0);
        }

        /** One request through the relay, end-to-end encrypted. headers: name -> value. */
        Response request(String method, String path, Map<String, String> headers, byte[] body) throws Exception {
            for (int attempt = 0; ; attempt++) {
                byte[] s, up, down;
                synchronized (this) {
                    if (sid == null) hello();
                    s = sid; up = kUp; down = kDown;
                }
                long n = seq.incrementAndGet();
                byte[] seqb = ByteBuffer.allocate(8).putLong(n).array();
                Map<String, Object> meta = new LinkedHashMap<>();
                meta.put("m", method); meta.put("p", path);
                meta.put("h", headers == null ? new LinkedHashMap<String, Object>() : new LinkedHashMap<String, Object>(headers));
                byte[] mj = Json.write(meta).getBytes(StandardCharsets.UTF_8);
                byte[] plain = cat(ByteBuffer.allocate(4).putInt(mj.length).array(), mj, body == null ? new byte[0] : body);
                byte[] env = cat(s, seqb, gcm(true, up, cat(new byte[4], seqb), plain, cat("SL-REQ".getBytes(StandardCharsets.US_ASCII), s, seqb)));

                HttpURLConnection c = opener.open(base + "/api/e2e/req");
                c.setConnectTimeout(10000);
                c.setReadTimeout(0);                      // live streams stay open
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(env.length);
                c.setRequestProperty("Content-Type", "application/octet-stream");
                try (OutputStream o = c.getOutputStream()) { o.write(env); }
                int st = c.getResponseCode();
                if (st == 401 && attempt == 0) {          // session unknown (PC restarted): open a new one, once
                    c.disconnect();
                    synchronized (this) { if (sid == s) sid = null; }
                    continue;
                }
                if (st != 200) {
                    String err = new String(readAll(c.getErrorStream()), StandardCharsets.UTF_8);
                    c.disconnect();
                    throw new IOException("encrypted request refused (" + st + "): " + err);
                }
                Frames f = new Frames(new DataInputStream(c.getInputStream()), down, s, seqb, c);
                byte[] head = f.next('H');
                Map<String, Object> h = Json.obj(new String(head, StandardCharsets.UTF_8));
                Response r = new Response();
                r.status = ((Number) h.get("status")).intValue();
                @SuppressWarnings("unchecked") List<Object> hl = (List<Object>) h.get("headers");
                for (Object o : hl) {
                    @SuppressWarnings("unchecked") List<Object> kv = (List<Object>) o;
                    r.headers.put(String.valueOf(kv.get(0)), String.valueOf(kv.get(1)));
                }
                r.body = f;
                return r;
            }
        }
    }

    /** Reads and checks the encrypted answer frames; as an InputStream it gives the decrypted body. */
    static final class Frames extends InputStream {
        private final DataInputStream in;
        private final byte[] key, sid, seqb;
        private final HttpURLConnection conn;
        private int index;
        private byte[] buf = new byte[0];
        private int pos;
        private boolean ended;

        Frames(DataInputStream in, byte[] key, byte[] sid, byte[] seqb, HttpURLConnection conn) {
            this.in = in; this.key = key; this.sid = sid; this.seqb = seqb; this.conn = conn;
        }

        /** The next frame; its type must be 'expect' (or any of H/D/E if 0). */
        byte[] next(char expect) throws IOException {
            int len;
            long ctr;
            try {
                len = in.readInt();
                ctr = in.readLong();
            } catch (EOFException e) {
                throw new IOException("answer cut off (no end frame)");
            }
            if (len < 17 || len > 32 * 1024 * 1024) throw new IOException("bad frame");
            byte[] box = new byte[len];
            in.readFully(box);
            byte[] plain;
            try {
                byte[] aad = cat("SL-RES".getBytes(StandardCharsets.US_ASCII), sid, seqb, ByteBuffer.allocate(4).putInt(index).array());
                plain = gcm(false, key, cat(new byte[]{0, 0, 0, 1}, ByteBuffer.allocate(8).putLong(ctr).array()), box, aad);
            } catch (Exception e) {
                throw new IOException("answer was changed on the way");      // not from the paired PC
            }
            index++;
            char type = (char) plain[0];
            if (expect != 0 && type != expect) throw new IOException("unexpected frame " + type);
            if (type == 'E') ended = true;
            return Arrays.copyOfRange(plain, 1, plain.length);
        }

        @Override public int read() throws IOException {
            byte[] one = new byte[1];
            int n = read(one, 0, 1);
            return n <= 0 ? -1 : one[0] & 0xff;
        }

        @Override public int read(byte[] b, int off, int len) throws IOException {
            while (pos >= buf.length) {
                if (ended) return -1;
                byte[] f = next((char) 0);
                if (ended) return -1;
                buf = f;
                pos = 0;
            }
            int n = Math.min(len, buf.length - pos);
            System.arraycopy(buf, pos, b, off, n);
            pos += n;
            return n;
        }

        @Override public void close() throws IOException {
            // fully read: keep the connection for the next request (no new TLS handshake); cut short: drop it
            if (ended) in.close(); else conn.disconnect();
        }
    }

    /** A software identity key (tests on a computer; the app uses the Android Keystore instead). */
    static final class SoftSigner implements Signer {
        private final KeyPair kp;
        SoftSigner() throws Exception { kp = ephemeral(); }
        public byte[] publicPoint() { return point(kp.getPublic()); }
        public byte[] signRaw(byte[] data) throws Exception {
            Signature s = Signature.getInstance("SHA256withECDSA");
            s.initSign(kp.getPrivate());
            s.update(data);
            return derToRaw(s.sign());
        }
    }

    static List<String> list(String... a) { return new ArrayList<>(Arrays.asList(a)); }
}
