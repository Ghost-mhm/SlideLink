// SlideLink · EN: SlideLink Android app: native shell around the phone remote (finding the PC, login, keys, screen, updates).
// SlideLink · DE: SlideLink-Android-App: native Hülle um die Handy-Fernbedienung (PC finden, Anmeldung, Tasten, Bildschirm, Updates).
package app.slidelink.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
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
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

/**
 * SlideLink remote for Android: the phone remote of the SlideLink desktop app in a full-screen app.
 * Finds the PC by itself (Tailscale name, Tailscale IP, home Wi-Fi), never shows a blank screen
 * (a clear "PC not reachable" screen that keeps retrying), keeps the screen on and maps the volume keys
 * to Next / Back.
 */
// EN: The only screen of the app: WebView with the remote plus a native offline screen.
// DE: Der einzige Bildschirm der App: WebView mit der Fernbedienung plus nativer Offline-Bildschirm.
public class MainActivity extends Activity {
    private static final int BG = Color.BLACK;          // true black: OLED pixels off (battery)
    private static final long DIM_AFTER_MS = 20_000;     // no touch for 20 s while presenting: dim the screen
    private boolean presenting, dimmed;
    private final Runnable dim = () -> setBrightness(0.04f, true);
    private static final int ACCENT = Color.rgb(0x7c, 0x83, 0xff);

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private FrameLayout root;
    private WebView web;
    private LinearLayout offline;
    private TextView offlineText;
    private ProgressBar spinner;
    private String base;            // the PC address in use, e.g. http://my-pc.tailnet.ts.net:8765
    private boolean searching;
    private final Runnable autoRetry = this::findPc;
    private final Runnable recheck = this::findPc;
    private ConnectivityManager.NetworkCallback netCallback;

    /** Network changed (Wi-Fi on/off, other Wi-Fi, mobile data): look again which way to the PC is best. */
    // EN: Looks again for the best way to the PC after a short delay (network changed).
    // DE: Sucht nach kurzer Pause erneut den besten Weg zum PC (Netz hat sich geändert).
    private void scheduleRecheck(long delayMs) {
        ui.removeCallbacks(recheck);
        ui.postDelayed(recheck, delayMs);
    }

    // EN: True if the phone is on Wi-Fi (or Ethernet) right now.
    // DE: True, wenn das Handy gerade im WLAN (oder per Ethernet) ist.
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

    /** Used by the SlideLink page (⚙ panel): app version + "Check for updates". */
    // EN: JavaScript bridge (window.SlideLinkApp): the page and the app exchange version, login, addresses and state.
    // DE: JavaScript-Brücke (window.SlideLinkApp): Seite und App tauschen Version, Anmeldung, Adressen und Zustand aus.
    final class Bridge {
        @JavascriptInterface public String version() { return Updater.installedName(MainActivity.this); }
        @JavascriptInterface public void checkUpdate() { ui.post(() -> updater.check(base, true)); }
        /** The page says whether a presentation is running: only then the screen stays on (and dims when idle). */
        @JavascriptInterface public void setPresenting(boolean on) { ui.post(() -> applyPresenting(on)); }
        /** The PC's current home-Wi-Fi addresses (they can change), used to switch to Wi-Fi automatically. */
        @JavascriptInterface public void setLanUrls(String urls) {
            prefs.edit().putString("lan", urls == null ? "" : urls.trim()).apply();
            ui.post(() -> scheduleRecheck(500));     // maybe we are at home right now
        }
        /** The PC's addresses from outside home (HTTPS first, then Tailscale). Nothing is built into the app. */
        @JavascriptInterface public void setRemoteUrls(String urls) {
            prefs.edit().putString("remote", urls == null ? "" : urls.trim()).apply();
        }
        /** The app keeps the login itself, so it works at every address (HTTPS, Tailscale, Wi-Fi). */
        @JavascriptInterface public String getToken() { return prefs.getString("token", ""); }
        @JavascriptInterface public void setToken(String t) {
            String old = prefs.getString("token", "");
            prefs.edit().putString("token", t == null ? "" : t).apply();
            // first time the login is known on an old http address: move over to HTTPS
            if (old.isEmpty() && t != null && !t.isEmpty() && base != null && !base.startsWith("https"))
                ui.postDelayed(() -> { prefs.edit().remove("base").apply(); findPc(); }, 800);
        }
    }

    // EN: Builds the screen, sets up the WebView and starts looking for the PC; watches network changes.
    // DE: Baut den Bildschirm, richtet die WebView ein und sucht den PC; beobachtet Netzwechsel.
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("slidelink", MODE_PRIVATE);
        updater = new Updater(this);
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
            // EN: The page could not load: show the offline screen.
            // DE: Die Seite konnte nicht laden: Offline-Bildschirm zeigen.
            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) showOffline("Can’t reach SlideLink on your PC.");
            }

            // EN: Keeps navigation inside SlideLink (other links are not opened in the app).
            // DE: Hält die Navigation innerhalb von SlideLink (andere Links öffnen sich nicht in der App).
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return base == null || !req.getUrl().toString().startsWith(base);   // stay inside SlideLink
            }
        });
        web.setVisibility(View.INVISIBLE);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        root.addView(buildOffline(), new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        findPc();
        // switch automatically when the network changes (home Wi-Fi <-> mobile data / other Wi-Fi)
        netCallback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network n) { ui.post(() -> scheduleRecheck(1500)); }
            @Override public void onLost(Network n) { ui.post(() -> scheduleRecheck(1500)); }
            @Override public void onCapabilitiesChanged(Network n, NetworkCapabilities c) { ui.post(() -> scheduleRecheck(2500)); }
        };
        try { getSystemService(ConnectivityManager.class).registerDefaultNetworkCallback(netCallback); } catch (Exception ignored) { }
    }

    // ---------- finding the PC ----------

    // EN: Addresses to try, in order: home Wi-Fi first when on Wi-Fi, then HTTPS, then the rest.
    // DE: Adressen zum Ausprobieren, der Reihe nach: im WLAN zuerst Heim-WLAN, dann HTTPS, dann der Rest.
    private List<String> candidates() {
        List<String> list = new ArrayList<>();
        // same Wi-Fi as the PC? then the direct Wi-Fi way first (fast, works without internet)
        if (onWifi()) {
            for (String a : prefs.getString("lan", "").split(" ")) if (!a.isEmpty() && !list.contains(a)) list.add(a);
        }
        // everywhere else: HTTPS over the internet, then Tailscale (addresses learned from the PC)
        String[] remote = prefs.getString("remote", "").split(" ");
        for (String a : remote) if (a.startsWith("https://") && !list.contains(a)) list.add(a);
        for (String a : remote) if (!a.isEmpty() && !list.contains(a)) list.add(a);
        String saved = prefs.getString("base", null);
        // login known: HTTPS (works without Tailscale) first; otherwise the address that still has the login
        boolean haveToken = !prefs.getString("token", "").isEmpty();
        if (saved != null && !haveToken) list.add(saved);
        if (saved != null && !list.contains(saved)) list.add(saved);
        return list;
    }

    // EN: Tries the addresses one by one and opens the first that answers; asks on the very first start.
    // DE: Probiert die Adressen nacheinander und öffnet die erste, die antwortet; fragt beim allerersten Start.
    private void findPc() {
        if (searching) return;
        searching = true;
        ui.removeCallbacks(autoRetry);
        spinner.setVisibility(View.VISIBLE);
        offlineText.setText("Connecting to SlideLink on your PC…");
        final List<String> list = candidates();
        if (list.isEmpty()) {                    // first start: the app doesn't know any PC yet
            searching = false;
            spinner.setVisibility(View.GONE);
            offlineText.setText("Welcome to SlideLink!\n\nEnter your PC's address. You find it in SlideLink on the PC under ⚙ Linked devices → Link a new device.");
            askAddress();
            return;
        }
        new Thread(() -> {
            String found = null;
            for (String a : list) {
                boolean lan = a.startsWith("http://") && !a.contains(".ts.net") && !a.startsWith("http://100.");
                if (reachable(a, lan ? 1200 : 4000)) { found = a; break; }   // a home address answers fast or not at all
            }
            final String result = found;
            ui.post(() -> {
                searching = false;
                if (result == null) {
                    showOffline("Can’t reach SlideLink on your PC.");
                } else {
                    open(result);
                }
            });
        }).start();
    }

    // EN: True if SlideLink answers at this address within the timeout.
    // DE: True, wenn SlideLink an dieser Adresse innerhalb des Zeitlimits antwortet.
    private static boolean reachable(String address, int timeoutMs) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(address + "/api/whoami").openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            return c.getResponseCode() < 500;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    // EN: Opens the remote at an address (hands over the login cookie first).
    // DE: Öffnet die Fernbedienung an einer Adresse (übergibt vorher das Anmelde-Cookie).
    private void open(String address) {
        if (address.equals(base) && web.getVisibility() == View.VISIBLE) return;   // already on the best way
        base = address;
        prefs.edit().putString("base", address).apply();
        offline.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        String token = prefs.getString("token", "");
        if (!token.isEmpty()) {                 // hand the login to this address before the page loads
            CookieManager cm = CookieManager.getInstance();
            cm.setCookie(address, "sl_token=" + token + "; Path=/; Max-Age=315360000; SameSite=Lax"
                    + (address.startsWith("https") ? "; Secure" : ""));
            cm.flush();
        }
        web.loadUrl(address + "/remote");
        updater.autoCheck(address);          // a newer app version on the PC? ask to update
    }

    // EN: Shows the 'PC not reachable' screen and retries every 5 seconds.
    // DE: Zeigt den Bildschirm 'PC nicht erreichbar' und versucht es alle 5 Sekunden erneut.
    private void showOffline(String why) {
        web.setVisibility(View.INVISIBLE);
        offline.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.GONE);
        offlineText.setText(why + "\n\nIs SlideLink running on the PC, and is Tailscale switched on on this phone?\nTrying again automatically…");
        ui.removeCallbacks(autoRetry);
        ui.postDelayed(autoRetry, 5000);
    }

    // ---------- the "not reachable" screen ----------

    // EN: Creates the offline screen (title, spinner, text, Try again, Change PC address).
    // DE: Erstellt den Offline-Bildschirm (Titel, Ladeanzeige, Text, Erneut versuchen, PC-Adresse ändern).
    private View buildOffline() {
        offline = new LinearLayout(this);
        offline.setOrientation(LinearLayout.VERTICAL);
        offline.setGravity(Gravity.CENTER);
        int pad = dp(32);
        offline.setPadding(pad, pad, pad, pad);
        offline.setBackgroundColor(BG);

        TextView title = new TextView(this);
        title.setText("SlideLink");
        title.setTextColor(Color.WHITE);
        title.setTextSize(28);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        offline.addView(title);

        spinner = new ProgressBar(this);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(40), dp(40));
        sp.topMargin = dp(24);
        offline.addView(spinner, sp);

        offlineText = new TextView(this);
        offlineText.setTextColor(Color.rgb(0xb4, 0xb7, 0xc9));
        offlineText.setTextSize(16);
        offlineText.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(-1, -2);
        tp.topMargin = dp(20);
        offline.addView(offlineText, tp);

        Button retry = button("Try again", true);
        retry.setOnClickListener(v -> findPc());
        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(-1, dp(52));
        rp.topMargin = dp(28);
        offline.addView(retry, rp);

        Button change = button("Change PC address", false);
        change.setOnClickListener(v -> askAddress());
        LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(-1, dp(48));
        cp.topMargin = dp(12);
        offline.addView(change, cp);
        return offline;
    }

    // EN: A rounded button in SlideLink style.
    // DE: Ein abgerundeter Knopf im SlideLink-Stil.
    private Button button(String text, boolean primary) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(16);
        b.setTextColor(primary ? Color.rgb(0x14, 0x15, 0x1c) : Color.WHITE);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(primary ? ACCENT : Color.rgb(0x25, 0x27, 0x33));
        b.setBackground(bg);
        return b;
    }

    // EN: Dialog to enter the PC address by hand.
    // DE: Dialog, um die PC-Adresse von Hand einzugeben.
    private void askAddress() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(prefs.getString("base", "https://"));
        new AlertDialog.Builder(this)
                .setTitle("PC address")
                .setMessage("Shown on the PC in SlideLink → Linked devices.")
                .setView(input)
                .setPositiveButton("Connect", (d, w) -> {
                    String a = input.getText().toString().trim().replaceAll("/+$", "");
                    if (!a.startsWith("http")) a = "http://" + a;
                    if (!a.matches(".*:\\d+$")) a = a + ":8765";
                    prefs.edit().putString("base", a).apply();
                    findPc();
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    // ---------- keys, lifecycle ----------

    // EN: Screen stays on only while presenting.
    // DE: Bildschirm bleibt nur beim Präsentieren an.
    private void applyPresenting(boolean on) {
        presenting = on;
        if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        wake();
    }

    /** Any touch or key: full brightness again, and dim again after 20 s without touching (only while presenting). */
    // EN: Any touch or key: full brightness; dim again after 20 s without touching while presenting.
    // DE: Jede Berührung oder Taste: volle Helligkeit; beim Präsentieren nach 20 s ohne Berührung wieder dimmen.
    private void wake() {
        ui.removeCallbacks(dim);
        if (dimmed) setBrightness(WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE, false);
        if (presenting) ui.postDelayed(dim, DIM_AFTER_MS);
    }

    // EN: Sets this window's screen brightness (or back to the system value).
    // DE: Setzt die Bildschirmhelligkeit dieses Fensters (oder zurück auf den Systemwert).
    private void setBrightness(float value, boolean isDim) {
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = value;
        getWindow().setAttributes(lp);
        dimmed = isDim;
    }

    // EN: Every touch wakes the screen.
    // DE: Jede Berührung weckt den Bildschirm.
    @Override
    public boolean dispatchTouchEvent(android.view.MotionEvent ev) {
        if (ev.getAction() == android.view.MotionEvent.ACTION_DOWN) wake();
        return super.dispatchTouchEvent(ev);
    }

    // EN: Volume + = Next, volume - = Back (the volume does not change).
    // DE: Lauter = Weiter, Leiser = Zurück (die Lautstärke ändert sich nicht).
    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        if (e.getAction() == KeyEvent.ACTION_DOWN) wake();
        int k = e.getKeyCode();
        if ((k == KeyEvent.KEYCODE_VOLUME_DOWN || k == KeyEvent.KEYCODE_VOLUME_UP) && web.getVisibility() == View.VISIBLE) {
            if (e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0) {
                String id = k == KeyEvent.KEYCODE_VOLUME_UP ? "nextBtn" : "prevBtn";   // + = forward, - = back
                web.evaluateJavascript("(function(){var b=document.getElementById('" + id + "');if(b&&!b.disabled)b.click();})()", null);
            }
            return true;   // don't change the volume while presenting
        }
        return super.dispatchKeyEvent(e);
    }

    // EN: Back key: back in the page if possible, else leave the app.
    // DE: Zurück-Taste: wenn möglich zurück in der Seite, sonst App verlassen.
    @Override
    public void onBackPressed() {
        if (web.getVisibility() == View.VISIBLE && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    // EN: App visible again: resume the page and check for updates.
    // DE: App wieder sichtbar: Seite fortsetzen und nach Updates sehen.
    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        web.resumeTimers();
        if (base != null) updater.autoCheck(base);
    }

    // EN: App in the background: pause everything (battery).
    // DE: App im Hintergrund: alles pausieren (Akku).
    @Override
    protected void onPause() {
        web.onPause();
        web.pauseTimers();                   // nothing runs while the app is in the background (battery)
        super.onPause();
    }

    // EN: Cleans up the WebView and the network watcher.
    // DE: Räumt WebView und Netzwerk-Beobachter auf.
    @Override
    protected void onDestroy() {
        ui.removeCallbacksAndMessages(null);
        try { getSystemService(ConnectivityManager.class).unregisterNetworkCallback(netCallback); } catch (Exception ignored) { }
        ((ViewGroup) web.getParent()).removeView(web);
        web.destroy();
        super.onDestroy();
    }

    // EN: Converts dp to pixels.
    // DE: Rechnet dp in Pixel um.
    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
