package app.slidelink.remote;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * EN: Runs the app's own E2e.java against a running SlideLink: pairs through the given address (the relay), confirms on the
 *     PC through localhost, opens a session, does requests and a live stream, then unlinks. Exit code 0 = all fine.
 *     java app.slidelink.remote.E2eLiveTest <base-url> <local-pc-url>
 * DE: Führt das E2e.java der App gegen einen laufenden SlideLink aus (Koppeln, Sitzung, Anfragen, Live-Stream, Entfernen).
 */
public class E2eLiveTest {
    static int fails = 0;

    static void check(String what, boolean ok, String detail) {
        System.out.println((ok ? "PASS  " : "FAIL  ") + what + (detail.isEmpty() ? "" : "  " + detail));
        if (!ok) fails++;
    }

    static Map<String, Object> local(String pc, String path, String body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(pc + path).openConnection();
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        try (OutputStream o = c.getOutputStream()) { o.write(body.getBytes(StandardCharsets.UTF_8)); }
        return Json.obj(new String(E2e.readAll(c.getInputStream()), StandardCharsets.UTF_8));
    }

    @SuppressWarnings("unchecked")
    static List<Object> pcPending(String pc) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(pc + "/events?role=library&client=javatest").openConnection();
        BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8));
        for (String line; (line = r.readLine()) != null; ) {
            if (line.startsWith("data:")) {
                c.disconnect();
                Map<String, Object> st = (Map<String, Object>) Json.obj(line.substring(5)).get("state");
                return (List<Object>) st.get("e2e_pair");
            }
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] a) throws Exception {
        String base = a[0], pc = a[1];
        E2e.SoftSigner me = new E2e.SoftSigner();

        // ---- pairing
        String code = Json.str(local(pc, "/api/devices/code", "{}"), "code");
        E2e.Pairing p = new E2e.Pairing(base, E2e.PLAIN, me);
        long t = System.currentTimeMillis();
        String sas = p.start(code, "Java test phone");
        List<Object> pend = pcPending(pc);
        Map<String, Object> last = (Map<String, Object>) pend.get(pend.size() - 1);
        check("pairing: app and PC show the same number", sas.equals(Json.str(last, "sas")), "app " + sas + " / PC " + Json.str(last, "sas") + ", " + (System.currentTimeMillis() - t) + " ms");
        check("pairing: waiting for yes", "confirm".equals(p.poll().get("state")), "");
        local(pc, "/api/e2e/pair/confirm", "{\"id\":\"" + Json.str(last, "id") + "\",\"yes\":true}");
        Map<String, Object> done = p.poll();
        check("pairing: token opened from the sealed box", "done".equals(done.get("state")) && done.get("token") != null, String.valueOf(done.get("name")));
        String device = (String) done.get("device");
        byte[] pcPub = E2e.unb64((String) done.get("pc_pub"));

        // ---- session + requests
        E2e.Client cl = new E2e.Client(base, device, pcPub, me, E2e.PLAIN);
        t = System.currentTimeMillis();
        cl.hello();
        check("session opened, PC proved its key", true, (System.currentTimeMillis() - t) + " ms");
        E2e.Response r = cl.request("GET", "/api/whoami", null, null);
        String who = new String(E2e.readAll(r.body), StandardCharsets.UTF_8);
        check("GET /api/whoami encrypted", r.status == 200 && who.contains("\"is_host\": false") && who.contains(device), who);
        Map<String, String> h = new LinkedHashMap<>();
        h.put("Content-Type", "application/json");
        r = cl.request("POST", "/api/op", h, "{\"op\":\"noop_test\"}".getBytes(StandardCharsets.UTF_8));
        String opBody = new String(E2e.readAll(r.body), StandardCharsets.UTF_8);
        check("POST /api/op encrypted: runs with phone rights (not blocked as cross-site)", r.status == 403 && opBody.contains("remote control"), r.status + " " + opBody);
        r = cl.request("GET", "/remote", null, null);
        String html = new String(E2e.readAll(r.body), StandardCharsets.UTF_8);
        check("page /remote", r.status == 200 && html.contains("<html"), r.headers.getOrDefault("Content-Type", ""));
        t = System.currentTimeMillis();
        r = cl.request("GET", "/SlideLink.apk", null, null);
        byte[] apk = E2e.readAll(r.body);
        check("APK encrypted", r.status == 200 && apk.length > 100000 && apk[0] == 'P' && apk[1] == 'K', apk.length / 1024 + " KB in " + (System.currentTimeMillis() - t) + " ms");
        long[] times = new long[10];
        for (int i = 0; i < times.length; i++) {
            t = System.currentTimeMillis();
            E2e.readAll(cl.request("GET", "/api/whoami", null, null).body);
            times[i] = System.currentTimeMillis() - t;
        }
        java.util.Arrays.sort(times);
        check("speed", true, "median " + times[5] + " ms, max " + times[9] + " ms per encrypted request");

        // ---- live stream
        r = cl.request("GET", "/events?role=remote&client=javae2e", null, null);
        BufferedReader ev = new BufferedReader(new InputStreamReader(r.body, StandardCharsets.UTF_8));
        int events = 0;
        t = System.currentTimeMillis();
        for (String line; events < 2 && (line = ev.readLine()) != null; ) if (line.startsWith("data:")) events++;
        r.body.close();
        check("live stream: updates arrive decrypted", events >= 2, events + " updates in " + (System.currentTimeMillis() - t) / 1000.0 + " s");

        // ---- a wrong PC key must be refused (someone pretending to be the PC)
        E2e.Client wrong = new E2e.Client(base, device, new E2e.SoftSigner().publicPoint(), me, E2e.PLAIN);
        boolean refused = false;
        try { wrong.hello(); } catch (SecurityException e) { refused = true; }
        check("a different PC key is refused", refused, "");

        // ---- PC restarts its sessions: the client re-opens by itself
        local(pc, "/api/devices/unlink", "{\"id\":\"" + device + "\"}");
        boolean out = false;
        try { E2e.readAll(cl.request("GET", "/api/whoami", null, null).body); } catch (Exception e) { out = true; }
        check("unlinked: the phone is out", out, "");
        System.out.println(fails == 0 ? "ALL PASSED" : fails + " FAILED");
        System.exit(fails == 0 ? 0 : 1);
    }
}
