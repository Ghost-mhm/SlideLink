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
final class Updater {
    private static final String ACTION = "app.slidelink.remote.INSTALL_STATUS";
    private final Activity act;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean busy;
    private long lastAuto;

    Updater(Activity act) {
        this.act = act;
    }

    static int installedVersion(Context c) {
        try {
            android.content.pm.PackageInfo p = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? (int) p.getLongVersionCode() : p.versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    static String installedName(Context c) {
        try {
            return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    /** Automatic check (at start / when coming back), at most every 10 minutes. */
    void autoCheck(String base) {
        if (System.currentTimeMillis() - lastAuto < 10 * 60_000) return;
        lastAuto = System.currentTimeMillis();
        check(base, false);
    }

    /** manual = the user pressed "Check for updates": also says "up to date". */
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
                    if (manual) toast(info == null ? "Could not check for updates." :
                            "SlideLink app is up to date (" + installedName(act) + ").");
                    return;
                }
                String name = info.optString("versionName", "new");
                String notes = info.optString("notes", "");
                new AlertDialog.Builder(act)
                        .setTitle("Update available: " + name)
                        .setMessage("You have " + installedName(act) + "." + (notes.isEmpty() ? "" : "\n\n" + notes)
                                + "\n\nDownload it from your PC and install now?")
                        .setPositiveButton("Update", (d, w) -> download(base + info.optString("url", "/SlideLink.apk")))
                        .setNegativeButton("Later", null)
                        .show();
            });
        }).start();
    }

    private void download(final String url) {
        if (Build.VERSION.SDK_INT >= 26 && !act.getPackageManager().canRequestPackageInstalls()) {
            // Android asks once: "Allow SlideLink to install apps"
            new AlertDialog.Builder(act)
                    .setTitle("One-time permission")
                    .setMessage("Android needs your OK once so SlideLink can install its own updates. "
                            + "Switch on \"Allow from this source\", come back, and press Update again.")
                    .setPositiveButton("Open settings", (d, w) -> act.startActivity(
                            new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                    Uri.parse("package:" + act.getPackageName()))))
                    .setNegativeButton("Cancel", null)
                    .show();
            return;
        }
        busy = true;
        toast("Downloading the update from your PC…");
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
                ui.post(() -> toast("Update failed: " + e.getMessage()));
            } finally {
                busy = false;
            }
        }).start();
    }

    private void listenForStatus() {
        BroadcastReceiver r = new BroadcastReceiver() {
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
                    toast("Update not installed" + (msg != null ? ": " + msg : "."));
                }
            }
        };
        IntentFilter f = new IntentFilter(ACTION);
        if (Build.VERSION.SDK_INT >= 33) act.registerReceiver(r, f, Context.RECEIVER_NOT_EXPORTED);
        else act.registerReceiver(r, f);
    }

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

    private void toast(String s) {
        ui.post(() -> Toast.makeText(act, s, Toast.LENGTH_LONG).show());
    }
}
