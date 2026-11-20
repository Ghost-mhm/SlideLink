// SlideLink · EN: Self-update of the Android app from the PC (app-version.json + SlideLink.apk).
// SlideLink · DE: Selbst-Update der Android-App über den PC (app-version.json + SlideLink.apk).
package app.slidelink.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Updates the app from the PC: SlideLink serves /app-version.json and /SlideLink.apk.
 * check() asks the PC for the newest version; if it is newer, the user is asked, the APK is downloaded
 * from the PC and handed to Android's own installer (Android shows its confirmation).
 */
// EN: Checks the PC for a newer app version and installs it with Android's own installer.
// DE: Prüft den PC auf eine neuere App-Version und installiert sie mit Androids eigenem Installer.
final class Updater {
    private static final String ACTION = "app.slidelink.remote.INSTALL_STATUS";
    private final Activity act;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean busy;
    private long lastAuto;

    Updater(Activity act) {
        this.act = act;
    }

    // EN: versionCode of the installed app.
    // DE: versionCode der installierten App.
    static int installedVersion(Context c) {
        try {
            android.content.pm.PackageInfo p = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? (int) p.getLongVersionCode() : p.versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    // EN: versionName of the installed app (e.g. 1.7).
    // DE: versionName der installierten App (z. B. 1.7).
    static String installedName(Context c) {
        try {
            return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    /** Automatic check (at start / when coming back), at most every 10 minutes. */
    // EN: Automatic check, at most every 10 minutes.
    // DE: Automatische Prüfung, höchstens alle 10 Minuten.
    void autoCheck(String base) {
        if (System.currentTimeMillis() - lastAuto < 10 * 60_000) return;
        lastAuto = System.currentTimeMillis();
        check(base, false);
    }

    /** manual = the user pressed "Check for updates": also says "up to date". */
    // EN: Asks the PC for the newest version and offers the update (manual = also says 'up to date').
    // DE: Fragt den PC nach der neuesten Version und bietet das Update an (manuell = sagt auch 'aktuell').
    void check(final String base, final boolean manual) {
        if (base == null || busy) return;
        busy = true;
        new Thread(() -> {
            JSONObject v = null;
            try {
                v = new JSONObject(new String(get(base + "/app-version.json", 64_000), "UTF-8"));
            } catch (Exception ignored) {
            }
            final JSONObject info = v;
            ui.post(() -> {
                busy = false;
                if (act.isFinishing()) return;
                int have = installedVersion(act);
                if (info == null || info.optInt("versionCode") <= have) {
                    if (manual) toast(info == null ? L.t("Could not check for updates.", "Konnte nicht nach Updates suchen.") :
                            L.t("SlideLink app is up to date (", "SlideLink-App ist aktuell (") + installedName(act) + ").");
                    return;
                }
                String name = info.optString("versionName", "new");
                String notes = info.optString("notes", "");
                new AlertDialog.Builder(act)
                        .setTitle(L.t("Update available: ", "Update verf\u00fcgbar: ") + name)
                        .setMessage(L.t("You have ", "Du hast ") + installedName(act) + "." + (notes.isEmpty() ? "" : "\n\n" + notes)
                                + L.t("\n\nDownload it from your PC and install now?", "\n\nJetzt von deinem PC laden und installieren?"))
                        .setPositiveButton(L.t("Update", "Aktualisieren"), (d, w) -> download(base + info.optString("url", "/SlideLink.apk")))
                        .setNegativeButton(L.t("Later", "Sp\u00e4ter"), null)
                        .show();
            });
        }).start();
    }

    // EN: Downloads the APK from the PC and hands it to Android's PackageInstaller.
    // DE: Lädt die APK vom PC und übergibt sie an Androids PackageInstaller.
    private void download(final String url) {
        if (Build.VERSION.SDK_INT >= 26 && !act.getPackageManager().canRequestPackageInstalls()) {
            // Android asks once: "Allow SlideLink to install apps"
            new AlertDialog.Builder(act)
                    .setTitle(L.t("One-time permission", "Einmalige Erlaubnis"))
                    .setMessage(L.t("Android needs your OK once so SlideLink can install its own updates. ", "Android braucht einmal dein OK, damit SlideLink seine Updates selbst installieren kann. ")
                            + L.t("Switch on \"Allow from this source\", come back, and press Update again.", "Schalte \"Aus dieser Quelle zulassen\" ein, komm zur\u00fcck und tippe erneut auf Aktualisieren."))
                    .setPositiveButton(L.t("Open settings", "Einstellungen \u00f6ffnen"), (d, w) -> act.startActivity(
                            new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                    Uri.parse("package:" + act.getPackageName()))))
                    .setNegativeButton(L.t("Cancel", "Abbrechen"), null)
                    .show();
            return;
        }
        busy = true;
        toast(L.t("Downloading the update from your PC…", "Update wird von deinem PC geladen…"));
        new Thread(() -> {
            try {
                byte[] apk = get(url, 50_000_000);
                PackageInstaller pi = act.getPackageManager().getPackageInstaller();
                PackageInstaller.SessionParams params =
                        new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
                params.setAppPackageName(act.getPackageName());
                int id = pi.createSession(params);
                try (PackageInstaller.Session s = pi.openSession(id)) {
                    try (OutputStream out = s.openWrite("SlideLink.apk", 0, apk.length)) {
                        out.write(apk);
                        s.fsync(out);
                    }
                    listenForStatus();
                    Intent i = new Intent(ACTION).setPackage(act.getPackageName());
                    int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
                    s.commit(PendingIntent.getBroadcast(act, id, i, flags).getIntentSender());
                }
            } catch (Exception e) {
                ui.post(() -> toast(L.t("Update failed: ", "Update fehlgeschlagen: ") + e.getMessage()));
            } finally {
                busy = false;
            }
        }).start();
    }

    // EN: Receives the installer's answer and shows Android's confirmation screen.
    // DE: Empfängt die Antwort des Installers und zeigt Androids Bestätigungsbildschirm.
    private void listenForStatus() {
        BroadcastReceiver r = new BroadcastReceiver() {
            // EN: Installer status: confirm, done or failed.
            // DE: Installer-Status: bestätigen, fertig oder fehlgeschlagen.
            @Override
            public void onReceive(Context c, Intent intent) {
                int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
                if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                    Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);   // Android's "Update this app?" screen
                    if (confirm != null) {
                        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        act.startActivity(confirm);
                    }
                    return;
                }
                try { act.unregisterReceiver(this); } catch (Exception ignored) { }
                if (status != PackageInstaller.STATUS_SUCCESS) {
                    String msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
                    toast(L.t("Update not installed", "Update nicht installiert") + (msg != null ? ": " + msg : "."));
                }
            }
        };
        IntentFilter f = new IntentFilter(ACTION);
        if (Build.VERSION.SDK_INT >= 33) act.registerReceiver(r, f, Context.RECEIVER_NOT_EXPORTED);
        else act.registerReceiver(r, f);
    }

    // EN: Downloads a URL into memory (with a size limit).
    // DE: Lädt eine URL in den Speicher (mit Größenlimit).
    private static byte[] get(String url, int max) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(5000);
        c.setReadTimeout(30000);
        c.setUseCaches(false);
        try {
            if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode());
            try (InputStream in = c.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    if (out.size() > max) throw new Exception("file too large");
                }
                return out.toByteArray();
            }
        } finally {
            c.disconnect();
        }
    }

    // EN: Short message at the bottom of the screen.
    // DE: Kurze Meldung unten am Bildschirm.
    private void toast(String s) {
        ui.post(() -> Toast.makeText(act, s, Toast.LENGTH_LONG).show());
    }
}
