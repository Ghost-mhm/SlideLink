// SlideLink Android app: native shell around the phone remote (finding the PC, login, keys, screen, updates).
package app.slidelink.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.net.http.SslCertificate;
import android.net.http.SslError;
import android.webkit.SslErrorHandler;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.net.HttpURLConnection;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * SlideLink remote for Android: the phone remote of the SlideLink desktop app in a full-screen app.
 * Finds the PC by itself (home Wi-Fi, SlideLink Relay), never shows a blank screen
 * (a clear "PC not reachable" screen that keeps retrying), keeps the screen on and maps the volume keys
 * to Next / Back.
 */
// The only screen of the app: WebView with the remote plus a native offline screen.
public class MainActivity extends Activity {
    private static final int BG = Color.rgb(0x08, 0x09, 0x0c);   // near black: OLED pixels almost off (battery)
    private static final int ACCENT = Color.rgb(0x8b, 0x93, 0xff), ACCENT2 = Color.rgb(0x6f, 0x5c, 0xff);

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private FrameLayout root;
    private WebView web;
    private LinearLayout offline;
    private TextView offlineText;
    private ProgressBar spinner;
    static MainActivity current;    // for the updater: downloads from the PC's Wi-Fi HTTPS use the same certificate pin
    private String base;            // the PC address in use, e.g. http://192.168.1.20:8765 or the relay address
    private boolean searching;
    private volatile boolean presenting;      // a presentation runs: screen on, lock-screen controls shown
    private volatile String slideInfo = "";   // "3 / 12 · Title" for the lock-screen controls
    private final Runnable autoRetry = this::findPc;
    private final Runnable recheck = this::findPc;
    private ConnectivityManager.NetworkCallback netCallback;

    /** Network changed (Wi-Fi on/off, other Wi-Fi, mobile data): look again which way to the PC is best. */
    // Looks again for the best way to the PC after a short delay (network changed).
    private void scheduleRecheck(long delayMs) {
        if (groupMode) return;              // in a group session the address is fixed by the link
        ui.removeCallbacks(recheck);
        ui.postDelayed(recheck, delayMs);
    }

    // True if the phone is on Wi-Fi (or Ethernet) right now.
    private boolean onWifi() {
        try {
            ConnectivityManager cm = getSystemService(ConnectivityManager.class);
            NetworkCapabilities nc = cm.getNetworkCapabilities(cm.getActiveNetwork());
            return nc != null && (nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || nc.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET));
        } catch (Exception e) {
            return false;
        }
    }
    private Updater updater;
    private boolean groupMode;
    private volatile E2eWeb e2eWeb;     // end-to-end mode: the page runs on a virtual address, all traffic encrypted
    private E2eKeys keys;
    private AlertDialog pairDialog;

    // This phone's end-to-end key (created on first use, kept in the Android Keystore).
    private synchronized E2eKeys keys() throws Exception {
        if (keys == null) keys = new E2eKeys();
        return keys;
    }

    // Opens connections; on the home Wi-Fi only the paired PC's own certificate is trusted.
    private final E2e.Opener opener = u -> {
        HttpURLConnection c = (HttpURLConnection) new URL(u).openConnection();
        if (c instanceof HttpsURLConnection && isLan(u)) pinned((HttpsURLConnection) c);
        return c;
    };

    // True once this phone is paired end-to-end with its PC.
    private boolean e2ePaired() {
        return !prefs.getString("e2e_dev", "").isEmpty() && !prefs.getString("e2e_pc", "").isEmpty();
    }          // joined someone's group session (co-presenter), not linked to a PC

    /** Used by the SlideLink page (⚙ panel): app version + "Check for updates". */
    // JavaScript bridge (window.SlideLinkApp): the page and the app exchange version, login, addresses and state.
    final class Bridge {
        @JavascriptInterface public String version() { return Updater.installedName(MainActivity.this); }
        @JavascriptInterface public void checkUpdate() { ui.post(() -> updater.check(base, true)); }
        /** Group session over: back to this phone's own SlideLink (or the welcome screen). */
        @JavascriptInterface public void leaveGroup() {
            ui.post(() -> {
                prefs.edit().remove("group_base").remove("group_key").apply();
                groupMode = false;
                base = null;
                findPc();
            });
        }
        /** The page says whether a presentation is running: only then the screen stays on. */
        /** The admin's interface language (sent with every state): the app's own screens follow it. */
        @JavascriptInterface public void setLang(String lang) {
            if (lang == null || lang.equals(L.lang)) return;
            L.lang = lang;
            prefs.edit().putString("lang", lang).apply();
        }
        @JavascriptInterface public void setPresenting(boolean on) { ui.post(() -> applyPresenting(on)); }
        /** The PC's name (for the list of PCs), and the list itself to switch or add one. */
        @JavascriptInterface public void setPcName(String name) {
            if (name == null || name.isEmpty() || groupMode || name.equals(prefs.getString("pc_name", ""))) return;
            prefs.edit().putString("pc_name", name.length() > 40 ? name.substring(0, 40) : name).apply();
        }
        @JavascriptInterface public void choosePc() { ui.post(MainActivity.this::choosePc); }
        /** Where the presentation is ("3 / 12 · Title"): shown in the lock-screen controls. */
        @JavascriptInterface public void setSlideInfo(String text) {
            String t = text == null ? "" : text.length() > 80 ? text.substring(0, 80) : text;
            if (t.equals(slideInfo)) return;
            slideInfo = t;
            if (presenting) ui.post(() -> ControlNote.show(MainActivity.this, slideInfo));
        }
        /** Short vibration: a JSON list of milliseconds (buzz, pause, buzz, …), e.g. [25] or [40,60,40]. */
        @JavascriptInterface public void vibrate(String pattern) {
            try {
                org.json.JSONArray a = new org.json.JSONArray(pattern);
                long[] t = new long[a.length() + 1];             // the pattern starts with a pause: 0
                for (int i = 0; i < a.length(); i++) t[i + 1] = Math.max(0, Math.min(500, a.getLong(i)));
                android.os.Vibrator v = (android.os.Vibrator) getSystemService(VIBRATOR_SERVICE);
                if (v == null || !v.hasVibrator()) return;
                v.vibrate(android.os.VibrationEffect.createWaveform(t, -1));
            } catch (Exception ignored) { }
        }
        /** The PC's current home-Wi-Fi addresses (they can change), used to switch to Wi-Fi automatically. */
        @JavascriptInterface public void setLanUrls(String urls) {
            prefs.edit().putString("lan", urls == null ? "" : urls.trim()).apply();
            ui.post(() -> scheduleRecheck(500));     // maybe we are at home right now
        }
        /** The PC's addresses from outside home (the SlideLink Relay). Nothing is built into the app. */
        /** Fingerprint of the PC's own HTTPS certificate for the home Wi-Fi. Taken over only from a trusted
         *  connection (relay HTTPS or the pinned Wi-Fi address), or once when none is known yet. */
        @JavascriptInterface public void setCertPin(String sha256) {
            if (sha256 == null || !sha256.matches("[0-9a-f]{64}")) return;
            String now = prefs.getString("pin", "");
            boolean trusted = base != null && base.startsWith("https://");
            if (now.isEmpty() || trusted) prefs.edit().putString("pin", sha256).apply();
        }
        @JavascriptInterface public void setRemoteUrls(String urls) {
            prefs.edit().putString("remote", urls == null ? "" : urls.trim()).apply();
        }
        /** The app keeps the login itself, so it works at every address (relay, Wi-Fi). */
        @JavascriptInterface public String getToken() { return e2eWeb != null ? "" : prefs.getString("token", ""); }
        /** The link page hands the code to the app: it pairs end-to-end (6-digit number check with the PC). */
        @JavascriptInterface public void pairE2E(String code, String name) { ui.post(() -> startE2ePair(code, name)); }
        /** fetch() with a body from the end-to-end page: encrypted by the app. */
        @JavascriptInterface public void e2eFetch(int id, String method, String path, String headers, String body) {
            E2eWeb w = e2eWeb;
            if (w != null) w.fetch(web, id, method, path, headers, body);
        }
        @JavascriptInterface public boolean e2eActive() { return e2eWeb != null; }
        /** Live stream (EventSource) of the end-to-end page, delivered event by event. */
        @JavascriptInterface public void e2eStream(int id, String path) {
            E2eWeb w = e2eWeb;
            if (w != null) w.stream(web, id, path);
        }
        @JavascriptInterface public void e2eStreamClose(int id) {
            E2eWeb w = e2eWeb;
            if (w != null) w.closeStream(id);
        }
        @JavascriptInterface public void setToken(String t) {
            String old = prefs.getString("token", "");
            prefs.edit().putString("token", t == null ? "" : t).apply();
            // first time the login is known on an old http address: move over to HTTPS
            if (old.isEmpty() && t != null && !t.isEmpty() && base != null && !base.startsWith("https"))
                ui.postDelayed(() -> { prefs.edit().remove("base").apply(); findPc(); }, 800);
        }
    }

    // Builds the screen, sets up the WebView and starts looking for the PC; watches network changes.
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        current = this;
        prefs = getSharedPreferences("slidelink", MODE_PRIVATE);
        updater = new Updater(this);
        L.lang = prefs.getString("lang", L.lang);       // last language of the admin, else the phone's
        // the screen stays on only while presenting (see applyPresenting); otherwise it times out normally
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);

        root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        // Android 15 draws apps edge to edge: keep the remote clear of the status and navigation bars
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets b = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                v.setPadding(b.left, b.top, b.right, b.bottom);
                return WindowInsets.CONSUMED;
            }
            v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });

        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setUserAgentString(s.getUserAgentString() + " SlideLinkApp/1.0");
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebChromeClient(new WebChromeClient());
        web.addJavascriptInterface(new Bridge(), "SlideLinkApp");
        web.setWebViewClient(new WebViewClient() {
            // The page could not load: show the offline screen.
            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) showOffline(L.t("Can’t reach SlideLink on your PC.", "SlideLink auf deinem PC ist nicht erreichbar."));
            }

            // Keeps navigation inside SlideLink (other links are not opened in the app).
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                E2eWeb w = e2eWeb;
                if (w != null) return !w.mine(req.getUrl().toString());                     // stay inside the encrypted page
                return base == null || !req.getUrl().toString().startsWith(origin(base));   // stay inside SlideLink
            }

            // End-to-end mode: every request of the page is answered encrypted by the app (nothing goes out in the clear).
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                E2eWeb w = e2eWeb;
                return w == null ? null : w.intercept(view, req);
            }

            // The PC's own certificate on the home Wi-Fi: accepted only if it is exactly the paired PC's.
            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                byte[] der = SslCertificate.saveState(error.getCertificate()).getByteArray("x509-certificate");
                if (der != null && pinMatches(der)) handler.proceed(); else handler.cancel();
            }
        });
        web.setVisibility(View.INVISIBLE);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        root.addView(buildOffline(), new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        // opened from a group link? (or still in a group session from before) else: find this phone's PC
        if (!handleGroupIntent(getIntent())) {
            if (prefs.getString("group_base", null) != null) enterGroup(); else findPc();
        }
        // switch automatically when the network changes (home Wi-Fi <-> mobile data / other Wi-Fi)
        netCallback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network n) { ui.post(() -> scheduleRecheck(1500)); }
            @Override public void onLost(Network n) { ui.post(() -> scheduleRecheck(1500)); }
            @Override public void onCapabilitiesChanged(Network n, NetworkCapabilities c) { ui.post(() -> scheduleRecheck(2500)); }
        };
        try { getSystemService(ConnectivityManager.class).registerDefaultNetworkCallback(netCallback); } catch (Exception ignored) { }
        // the Back / Black / Next buttons of the lock-screen controls (only this app can send them)
        android.content.IntentFilter f = new android.content.IntentFilter();
        f.addAction(ControlNote.NEXT); f.addAction(ControlNote.PREV); f.addAction(ControlNote.BLACK);
        if (android.os.Build.VERSION.SDK_INT >= 33) registerReceiver(noteButtons, f, RECEIVER_NOT_EXPORTED);
        else registerReceiver(noteButtons, f);
    }

    // ---------- finding the PC ----------

    // Addresses to try, in order: home Wi-Fi first when on Wi-Fi, then the relay, then the rest.
    private List<String> candidates() {
        List<String> list = new ArrayList<>();
        // same Wi-Fi as the PC? then the direct Wi-Fi way first (fast, works without internet)
        if (onWifi()) {
            for (String a : prefs.getString("lan", "").split(" ")) if (!a.isEmpty() && !list.contains(a)) list.add(a);
        }
        // everywhere else: the SlideLink Relay over the internet (address learned from the PC)
        String[] remote = prefs.getString("remote", "").split(" ");
        for (String a : remote) if (a.startsWith("https://") && !list.contains(a)) list.add(a);
        for (String a : remote) if (!a.isEmpty() && !list.contains(a)) list.add(a);
        String saved = prefs.getString("base", null);
        // login known: the relay first; otherwise the address that still has the login
        boolean haveToken = !prefs.getString("token", "").isEmpty();
        if (saved != null && !haveToken) list.add(saved);
        if (saved != null && !list.contains(saved)) list.add(saved);
        return list;
    }

    // Tries the addresses one by one and opens the first that answers; asks on the very first start.
    // Opens a group link (slidelink://group?u=…&k=…) from the join page: co-presenter mode, no linking.
    private boolean handleGroupIntent(Intent intent) {
        Uri d = intent == null ? null : intent.getData();
        if (d == null || !"slidelink".equals(d.getScheme()) || !"group".equals(d.getHost())) return false;
        String u = d.getQueryParameter("u"), k = d.getQueryParameter("k");
        if (u == null || k == null || k.isEmpty() || !(u.startsWith("https://") || u.startsWith("http://"))) return false;
        prefs.edit().putString("group_base", u.replaceAll("/+$", "")).putString("group_key", k).apply();
        enterGroup();
        return true;
    }

    // Shows the group page of the session (name, then the co-presenter remote).
    private void enterGroup() {
        String gb = prefs.getString("group_base", null), gk = prefs.getString("group_key", null);
        if (gb == null || gk == null) { groupMode = false; findPc(); return; }
        groupMode = true;
        ui.removeCallbacks(recheck);
        ui.removeCallbacks(autoRetry);
        base = gb;
        offline.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(gb + "/group?k=" + Uri.encode(gk));
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleGroupIntent(intent);
    }

    private void findPc() {
        if (groupMode) { enterGroup(); return; }   // a group session keeps its own address
        if (searching) return;
        searching = true;
        ui.removeCallbacks(autoRetry);
        spinner.setVisibility(View.VISIBLE);
        offlineText.setText(L.t("Connecting to SlideLink on your PC…", "Verbinde mit SlideLink auf deinem PC…"));
        final List<String> list = candidates();
        if (list.isEmpty()) {                    // first start: the app doesn't know any PC yet
            searching = false;
            spinner.setVisibility(View.GONE);
            offlineText.setText(L.t("Welcome to SlideLink!\n\nEnter your PC's address. You find it in SlideLink on the PC under ⚙ Linked devices → Link a new device.",
                    "Willkommen bei SlideLink!\n\nGib die Adresse deines PCs ein. Du findest sie in SlideLink am PC unter ⚙ Verknüpfte Geräte → Neues Gerät verknüpfen."));
            askAddress();
            return;
        }
        new Thread(() -> {
            String found = null;
            for (String a : list) {
                boolean lan = isLan(a);   // relay addresses end in /p/<pc-id>
                if (reachable(a, lan ? 1200 : 4000)) { found = a; break; }   // a home address answers fast or not at all
            }
            final String result = found;
            ui.post(() -> {
                searching = false;
                if (result == null) {
                    showOffline(L.t("Can’t reach SlideLink on your PC.", "SlideLink auf deinem PC ist nicht erreichbar."));
                } else {
                    open(result);
                }
            });
        }).start();
    }

    // True if SlideLink answers at this address within the timeout.
    private boolean reachable(String address, int timeoutMs) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(address + "/api/whoami").openConnection();
            if (c instanceof HttpsURLConnection && isLan(address)) pinned((HttpsURLConnection) c);
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            return c.getResponseCode() < 500;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    // scheme://host:port of an address (relay addresses end in /p/<pc-id>, but the pages live at the root).
    private static String origin(String address) {
        int i = address.indexOf('/', address.indexOf("://") + 3);
        return i < 0 ? address : address.substring(0, i);
    }

    // True for the PC's home Wi-Fi addresses (from the PC's own address list), not the relay.
    boolean isLan(String address) {
        for (String e : prefs.getString("lan", "").split(" ")) if (!e.isEmpty() && address.startsWith(e)) return true;
        return false;
    }

    // True if a certificate is exactly the paired PC's own one (SHA-256 of its bytes).
    private boolean pinMatches(byte[] der) {
        String pin = prefs.getString("pin", "");
        if (pin.isEmpty()) return false;
        try {
            StringBuilder sb = new StringBuilder();
            for (byte b : MessageDigest.getInstance("SHA-256").digest(der)) sb.append(String.format("%02x", b));
            return MessageDigest.isEqual(sb.toString().getBytes(), pin.getBytes());
        } catch (Exception e) {
            return false;
        }
    }

    // Lets a connection trust only the paired PC's certificate (any name: the PC is identified by the certificate).
    void pinned(HttpsURLConnection c) throws Exception {
        TrustManager tm = new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] chain, String type) { }
            public void checkServerTrusted(X509Certificate[] chain, String type) throws java.security.cert.CertificateException {
                try {
                    if (chain == null || chain.length == 0 || !pinMatches(chain[0].getEncoded())) throw new java.security.cert.CertificateException("not the paired PC");
                } catch (java.security.cert.CertificateEncodingException e) {
                    throw new java.security.cert.CertificateException(e);
                }
            }
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        };
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[]{tm}, null);
        c.setSSLSocketFactory(ctx.getSocketFactory());
        c.setHostnameVerifier((host, session) -> true);
    }

    // Opens the remote at an address (hands over the login cookie first).
    private void open(String address) {
        if (address.equals(base) && web.getVisibility() == View.VISIBLE) return;   // already on the best way
        base = address;
        prefs.edit().putString("base", address).apply();
        offline.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        int rp = address.indexOf("/p/");
        if (rp >= 0 && !isLan(address) && e2ePaired()) {
            // through the relay and paired end-to-end: never send the login to the relay, encrypt everything
            try {
                String pcId = address.substring(rp + 3).split("[/?]")[0];
                E2e.Client cl = new E2e.Client(address, prefs.getString("e2e_dev", ""), E2e.unb64(prefs.getString("e2e_pc", "")), keys(), opener);
                CookieManager.getInstance().removeAllCookies(null);
                e2eWeb = new E2eWeb(cl, pcId, () -> ui.post(this::notThePc));
                web.loadUrl(e2eWeb.origin + "/remote");
                updater.autoCheck(address);
                return;
            } catch (Exception e) {
                showOffline(L.t("End-to-end encryption could not start on this phone.", "Die Ende-zu-Ende-Verschlüsselung konnte auf diesem Handy nicht starten."));
                return;
            }
        }
        e2eWeb = null;
        String token = prefs.getString("token", "");
        if (!token.isEmpty()) {                 // hand the login to this address before the page loads
            CookieManager cm = CookieManager.getInstance();
            // on the relay each PC's cookies carry its id (sl_token__<pc-id>), so other PCs there never get them
            int pi = address.indexOf("/p/");
            String name = pi >= 0 ? "sl_token__" + address.substring(pi + 3).split("[/?]")[0] : "sl_token";
            cm.setCookie(address, name + "=" + token + "; Path=/; Max-Age=315360000; SameSite=Lax"
                    + (address.startsWith("https") ? "; Secure" : ""));
            cm.flush();
        }
        web.loadUrl(address + "/remote");
        updater.autoCheck(address);          // a newer app version on the PC? ask to update
        ui.postDelayed(this::offerE2eUpgrade, 4000);
    }

    // The relay answered with something that is not from the paired PC: stop and warn (never fall back to unencrypted).
    private void notThePc() {
        e2eWeb = null;
        web.stopLoading();
        web.loadUrl("about:blank");
        showOffline(L.t("Warning: the connection did not come from your PC. SlideLink stopped it to protect you.",
                "Achtung: Die Verbindung kam nicht von deinem PC. SlideLink hat sie zu deinem Schutz gestoppt."));
    }

    // ---------- end-to-end pairing ----------

    // Pairs end-to-end with the code from the PC: shows the 6-digit number to compare and waits for "Yes" on the PC.
    private void startE2ePair(String code, String name) {
        final String at = base;
        if (at == null) return;
        final AlertDialog wait = new AlertDialog.Builder(this)
                .setTitle(L.t("Linking…", "Verknüpfe…"))
                .setMessage(L.t("Talking to your PC…", "Verbinde mit deinem PC…"))
                .setCancelable(false).create();
        wait.show();
        pairDialog = wait;
        new Thread(() -> {
            try {
                E2e.Pairing p = new E2e.Pairing(at, opener, keys());
                String sas = p.start(code, name == null || name.trim().isEmpty() ? android.os.Build.MODEL : name.trim());
                ui.post(() -> {
                    wait.setTitle(L.t("Compare this number", "Vergleiche diese Zahl"));
                    wait.setMessage(sas.substring(0, 3) + " " + sas.substring(3) + "\n\n"
                            + L.t("Your PC shows a number now. If it is the same, click „Yes“ on the PC.",
                                  "Dein PC zeigt jetzt eine Zahl. Ist sie gleich, klicke am PC auf „Ja“."));
                });
                long until = System.currentTimeMillis() + 180_000;
                while (System.currentTimeMillis() < until && pairDialog == wait) {
                    Thread.sleep(1200);
                    Map<String, Object> r = p.poll();
                    String st = String.valueOf(r.get("state"));
                    if (st.equals("confirm")) continue;
                    if (st.equals("done")) {
                        prefs.edit().putString("token", String.valueOf(r.get("token")))
                                .putString("e2e_dev", String.valueOf(r.get("device")))
                                .putString("e2e_pc", String.valueOf(r.get("pc_pub"))).apply();
                        ui.post(() -> {
                            wait.dismiss();
                            pairDialog = null;
                            base = null;
                            findPc();          // opens the remote, now end-to-end encrypted
                        });
                        return;
                    }
                    final String msg = st.equals("rejected")
                            ? L.t("The PC said the numbers are different. Nothing was linked. Create a new code on the PC.",
                                  "Am PC wurde „Nein“ gewählt. Es wurde nichts verknüpft. Erzeuge am PC einen neuen Code.")
                            : L.t("The link did not finish. Create a new code on the PC and try again.",
                                  "Das Verknüpfen wurde nicht beendet. Erzeuge am PC einen neuen Code und versuche es erneut.");
                    ui.post(() -> { wait.dismiss(); pairDialog = null; pairError(msg); });
                    return;
                }
                ui.post(() -> { wait.dismiss(); pairDialog = null; pairError(L.t("Timed out. Try again with a new code.", "Zeit abgelaufen. Versuche es mit einem neuen Code erneut.")); });
            } catch (Exception e) {
                String m = e.getMessage() == null ? L.t("Could not link.", "Verknüpfen fehlgeschlagen.") : e.getMessage();
                ui.post(() -> { wait.dismiss(); pairDialog = null; pairError(m); });
            }
        }).start();
    }

    // Shows a pairing error on the link page (and as a dialog if the page can't show it).
    private void pairError(String msg) {
        web.evaluateJavascript("window.__slPairErr&&window.__slPairErr(" + E2eWeb.jsString(msg) + ")", null);
        new AlertDialog.Builder(this).setMessage(msg).setPositiveButton("OK", null).show();
    }

    // Linked the old way (before end-to-end)? Offer once to link again end-to-end encrypted.
    private void offerE2eUpgrade() {
        if (e2ePaired() || prefs.getString("token", "").isEmpty() || prefs.getBoolean("e2e_offered", false) || base == null || isLan(base)) return;
        prefs.edit().putBoolean("e2e_offered", true).apply();
        final EditText code = new EditText(this);
        code.setInputType(InputType.TYPE_CLASS_NUMBER);
        code.setHint("123456");
        new AlertDialog.Builder(this)
                .setTitle(L.t("Encrypt end-to-end?", "Ende-zu-Ende verschlüsseln?"))
                .setMessage(L.t("SlideLink can now encrypt everything between this phone and your PC, so even the relay can't read it. "
                                + "On the PC open Linked devices → Link a new device and enter the code here.",
                        "SlideLink kann jetzt alles zwischen diesem Handy und deinem PC verschlüsseln, sodass selbst das Relay nichts lesen kann. "
                                + "Öffne am PC Verknüpfte Geräte → Neues Gerät verknüpfen und gib den Code hier ein."))
                .setView(code)
                .setPositiveButton(L.t("Link", "Verknüpfen"), (d, w) -> startE2ePair(code.getText().toString().trim(), android.os.Build.MODEL))
                .setNegativeButton(L.t("Later", "Später"), null)
                .show();
    }

    // Shows the 'PC not reachable' screen and retries every 5 seconds.
    private void showOffline(String why) {
        web.setVisibility(View.INVISIBLE);
        offline.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.GONE);
        offlineText.setText(why + "\n\nIs SlideLink running on the PC, and does this phone have internet?\nTrying again automatically…");
        ui.removeCallbacks(autoRetry);
        ui.postDelayed(autoRetry, 5000);
    }

    // ---------- the "not reachable" screen ----------

    // Creates the offline screen (title, spinner, text, Try again, Change PC address).
    private View buildOffline() {
        offline = new LinearLayout(this);
        offline.setOrientation(LinearLayout.VERTICAL);
        offline.setGravity(Gravity.CENTER);
        int pad = dp(32);
        offline.setPadding(pad, pad, pad, pad);
        offline.setBackgroundColor(BG);

        android.widget.ImageView logo = new android.widget.ImageView(this);
        logo.setImageResource(getApplicationInfo().icon);
        offline.addView(logo, new LinearLayout.LayoutParams(dp(88), dp(88)));

        TextView title = new TextView(this);
        title.setText("SlideLink");
        title.setTextColor(Color.rgb(0xee, 0xf0, 0xf6));
        title.setTextSize(26);
        title.setLetterSpacing(-0.01f);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        title.setPadding(0, dp(14), 0, 0);
        title.setGravity(Gravity.CENTER);
        offline.addView(title);

        spinner = new ProgressBar(this);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(40), dp(40));
        sp.topMargin = dp(28);
        offline.addView(spinner, sp);

        offlineText = new TextView(this);
        offlineText.setTextColor(Color.rgb(0x8a, 0x90, 0xa3));
        offlineText.setTextSize(15);
        offlineText.setLineSpacing(0, 1.25f);
        offlineText.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(-1, -2);
        tp.topMargin = dp(20);
        offline.addView(offlineText, tp);

        Button retry = button(L.t("Try again", "Erneut versuchen"), true);
        retry.setOnClickListener(v -> findPc());
        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(-1, dp(58));
        rp.topMargin = dp(36);
        offline.addView(retry, rp);

        Button change = button(L.t("Change PC address", "PC-Adresse \u00e4ndern"), false);
        change.setOnClickListener(v -> askAddress());
        LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(-1, dp(54));
        cp.topMargin = dp(10);
        offline.addView(change, cp);

        Button other = button(L.t("Other PC", "Anderer PC"), false);   // e.g. the school PC is off: switch to the one at home
        other.setOnClickListener(v -> choosePc());
        LinearLayout.LayoutParams op = new LinearLayout.LayoutParams(-1, dp(54));
        op.topMargin = dp(10);
        offline.addView(other, op);
        return offline;
    }

    // A rounded button in SlideLink style.
    private Button button(String text, boolean primary) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(16);
        b.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        b.setTextColor(primary ? Color.WHITE : Color.rgb(0xee, 0xf0, 0xf6));
        b.setStateListAnimator(null);
        GradientDrawable bg = primary
            ? new GradientDrawable(GradientDrawable.Orientation.TL_BR, new int[]{ACCENT, ACCENT2})
            : new GradientDrawable();
        bg.setCornerRadius(dp(20));
        if (!primary) { bg.setColor(Color.rgb(0x14, 0x16, 0x1c)); bg.setStroke(dp(1), Color.argb(16, 255, 255, 255)); }
        b.setBackground(bg);
        return b;
    }

    // Dialog to enter the PC address by hand.
    private void askAddress() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(prefs.getString("base", "https://"));
        new AlertDialog.Builder(this)
                .setTitle(L.t("PC address", "PC-Adresse"))
                .setMessage(L.t("Shown on the PC in SlideLink → Linked devices.", "Steht am PC in SlideLink → Verknüpfte Geräte."))
                .setView(input)
                .setPositiveButton(L.t("Connect", "Verbinden"), (d, w) -> {
                    String a = input.getText().toString().trim().replaceAll("/+$", "");
                    if (!a.startsWith("http")) a = "https://" + a;
                    if (a.startsWith("http://")) a = "https://" + a.substring(7);
                    if (!a.contains("/p/") && !a.matches(".*:\\d+$")) a = a + ":8766";
                    prefs.edit().putString("base", a).apply();
                    findPc();
                })
                .setNegativeButton(L.t("Cancel", "Abbrechen"), null)
                .show();
    }

    // ---------- several PCs (e.g. school and home) ----------
    // Everything that belongs to one PC. The active PC's values live in prefs as before; the others are kept in "pcs".
    private static final String[] PC_KEYS = {"base", "token", "lan", "pin", "remote", "e2e_dev", "e2e_pc", "e2e_offered", "pc_name"};

    // The saved PCs: [{id, name, data{…}}], with the active one brought up to date first.
    private org.json.JSONArray savedPcs() {
        org.json.JSONArray list;
        try { list = new org.json.JSONArray(prefs.getString("pcs", "[]")); } catch (Exception e) { list = new org.json.JSONArray(); }
        String cur = prefs.getString("pc_cur", "");
        if (cur.isEmpty()) { cur = "pc" + System.currentTimeMillis(); prefs.edit().putString("pc_cur", cur).apply(); }
        try {
            org.json.JSONObject data = new org.json.JSONObject();
            for (String k : PC_KEYS) {
                if (!prefs.contains(k)) continue;
                data.put(k, k.equals("e2e_offered") ? prefs.getBoolean(k, false) : prefs.getString(k, ""));
            }
            boolean known = data.has("base") || data.has("token") || data.has("remote");
            org.json.JSONArray out = new org.json.JSONArray();
            boolean found = false;
            for (int i = 0; i < list.length(); i++) {
                org.json.JSONObject p = list.getJSONObject(i);
                if (p.optString("id").equals(cur)) {
                    found = true;
                    if (!known) continue;                            // nothing linked yet: not a saved PC
                    p.put("data", data);
                    p.put("name", prefs.getString("pc_name", p.optString("name")));
                }
                out.put(p);
            }
            if (!found && known) out.put(new org.json.JSONObject().put("id", cur).put("name", prefs.getString("pc_name", "PC")).put("data", data));
            prefs.edit().putString("pcs", out.toString()).apply();
            return out;
        } catch (Exception e) {
            return list;
        }
    }

    // Makes another saved PC the active one (or a new, empty one with id = null) and connects to it.
    private void switchPc(String id) {
        savedPcs();                                                  // keep the current PC's state
        SharedPreferences.Editor ed = prefs.edit();
        for (String k : PC_KEYS) ed.remove(k);
        String next = id == null ? "pc" + System.currentTimeMillis() : id;
        try {
            org.json.JSONArray list = new org.json.JSONArray(prefs.getString("pcs", "[]"));
            for (int i = 0; id != null && i < list.length(); i++) {
                org.json.JSONObject p = list.getJSONObject(i);
                if (!p.optString("id").equals(id)) continue;
                org.json.JSONObject d = p.optJSONObject("data");
                for (java.util.Iterator<String> it = d == null ? null : d.keys(); it != null && it.hasNext(); ) {
                    String k = it.next();
                    if (k.equals("e2e_offered")) ed.putBoolean(k, d.optBoolean(k)); else ed.putString(k, d.optString(k));
                }
            }
        } catch (Exception ignored) { }
        ed.putString("pc_cur", next).apply();
        applyPresenting(false);
        e2eWeb = null;
        base = null;
        CookieManager.getInstance().removeAllCookies(null);         // the old PC's login never goes to the new one
        web.stopLoading();
        web.loadUrl("about:blank");
        web.setVisibility(View.INVISIBLE);
        offline.setVisibility(View.VISIBLE);
        findPc();                                                    // new PC: asks for its address
    }

    // The PC list: tap one to switch, or add a new PC. Removing asks first.
    private void choosePc() {
        final org.json.JSONArray list = savedPcs();
        final String cur = prefs.getString("pc_cur", "");
        final List<String> ids = new ArrayList<>(), names = new ArrayList<>();
        for (int i = 0; i < list.length(); i++) {
            org.json.JSONObject p = list.optJSONObject(i);
            if (p == null) continue;
            ids.add(p.optString("id"));
            names.add((p.optString("id").equals(cur) ? "✓  " : "      ") + p.optString("name", "PC"));
        }
        names.add("+  " + L.t("Add another PC", "Weiteren PC hinzufügen"));
        new AlertDialog.Builder(this)
                .setTitle(L.t("Your PCs", "Deine PCs"))
                .setItems(names.toArray(new String[0]), (d, w) -> {
                    if (w == ids.size()) { switchPc(null); return; }
                    String id = ids.get(w), name = names.get(w).trim().replaceFirst("^✓\\s*", "");
                    if (id.equals(cur)) return;
                    new AlertDialog.Builder(this).setTitle(name)
                            .setPositiveButton(L.t("Switch to this PC", "Zu diesem PC wechseln"), (d2, w2) -> switchPc(id))
                            .setNeutralButton(L.t("Remove", "Entfernen"), (d2, w2) -> forgetPc(id, name))
                            .setNegativeButton(L.t("Cancel", "Abbrechen"), null).show();
                })
                .show();
    }

    // Removes a saved PC from this phone (after asking). The PC itself still lists the phone until it is unlinked there.
    private void forgetPc(String id, String name) {
        new AlertDialog.Builder(this)
                .setMessage(L.t("Remove „" + name + "“ from this phone? To use it again you link it once more.",
                        "„" + name + "“ von diesem Handy entfernen? Um ihn wieder zu nutzen, verknüpfst du ihn neu."))
                .setPositiveButton(L.t("Remove", "Entfernen"), (d, w) -> {
                    try {
                        org.json.JSONArray list = new org.json.JSONArray(prefs.getString("pcs", "[]")), out = new org.json.JSONArray();
                        for (int i = 0; i < list.length(); i++) if (!list.getJSONObject(i).optString("id").equals(id)) out.put(list.get(i));
                        prefs.edit().putString("pcs", out.toString()).apply();
                    } catch (Exception ignored) { }
                })
                .setNegativeButton(L.t("Cancel", "Abbrechen"), null).show();
    }

    // ---------- keys, lifecycle ----------

    // Screen stays on only while presenting; the lock-screen controls are there only then too.
    private void applyPresenting(boolean on) {
        presenting = on;
        if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (on) {
            askNotificationsOnce();
            ControlNote.show(this, slideInfo);
        } else {
            ControlNote.hide(this);
        }
    }

    // Android 13+: notifications need the user's OK. Asked once, the first time a presentation runs.
    private void askNotificationsOnce() {
        if (android.os.Build.VERSION.SDK_INT < 33 || prefs.getBoolean("asked_notes", false)) return;
        if (checkSelfPermission("android.permission.POST_NOTIFICATIONS") == android.content.pm.PackageManager.PERMISSION_GRANTED) return;
        prefs.edit().putBoolean("asked_notes", true).apply();
        requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 7);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code == 7 && presenting) ControlNote.show(this, slideInfo);   // allowed just now: show the controls
    }

    // A button of the lock-screen notification: same as tapping the button on the remote.
    private final android.content.BroadcastReceiver noteButtons = new android.content.BroadcastReceiver() {
        @Override public void onReceive(android.content.Context c, Intent i) {
            String a = i.getAction(), id = ControlNote.NEXT.equals(a) ? "nextBtn" : ControlNote.PREV.equals(a) ? "prevBtn"
                    : ControlNote.BLACK.equals(a) ? "blkBtn" : null;
            if (id != null && web.getVisibility() == View.VISIBLE)
                web.evaluateJavascript("(function(){var b=document.getElementById('" + id + "');if(b&&!b.disabled)b.click();})()", null);
        }
    };

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int k = e.getKeyCode();
        String id = null;
        switch (k) {
            // volume keys: + = forward, - = back (and the volume stays as it is)
            case KeyEvent.KEYCODE_VOLUME_UP: id = "nextBtn"; break;
            case KeyEvent.KEYCODE_VOLUME_DOWN: id = "prevBtn"; break;
            // Bluetooth presentation clickers send these (arrow keys, space and B reach the page by themselves)
            case KeyEvent.KEYCODE_PAGE_DOWN: case KeyEvent.KEYCODE_MEDIA_NEXT: case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
                id = "nextBtn"; break;
            case KeyEvent.KEYCODE_PAGE_UP: case KeyEvent.KEYCODE_MEDIA_PREVIOUS: case KeyEvent.KEYCODE_MEDIA_REWIND:
                id = "prevBtn"; break;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE: id = "blkBtn"; break;
            default: break;
        }
        if (id != null && web.getVisibility() == View.VISIBLE) {
            if (e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0) {
                web.evaluateJavascript("(function(){var b=document.getElementById('" + id + "');if(b&&!b.disabled)b.click();})()", null);
            }
            return true;
        }
        // other clicker keys (arrows, space, B) go to the page: make sure it has the keyboard focus
        if (web.getVisibility() == View.VISIBLE && !web.hasFocus()) web.requestFocus();
        return super.dispatchKeyEvent(e);
    }

    // Back key: back in the page if possible, else leave the app.
    @Override
    public void onBackPressed() {
        if (web.getVisibility() == View.VISIBLE && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    // App visible again: resume the page and check for updates.
    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        web.resumeTimers();
        if (base != null) updater.autoCheck(base);
    }

    // App in the background: pause everything (battery) - except while presenting, so the lock-screen buttons work at once.
    @Override
    protected void onPause() {
        if (!presenting) {
            web.onPause();
            web.pauseTimers();
        }
        super.onPause();
    }

    // Cleans up the WebView and the network watcher.
    @Override
    protected void onDestroy() {
        ControlNote.hide(this);
        try { unregisterReceiver(noteButtons); } catch (Exception ignored) { }
        ui.removeCallbacksAndMessages(null);
        try { getSystemService(ConnectivityManager.class).unregisterNetworkCallback(netCallback); } catch (Exception ignored) { }
        ((ViewGroup) web.getParent()).removeView(web);
        web.destroy();
        super.onDestroy();
    }

    // Converts dp to pixels.
    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
