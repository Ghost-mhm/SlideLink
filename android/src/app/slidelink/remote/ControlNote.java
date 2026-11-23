package app.slidelink.remote;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.drawable.Icon;

/**
 * While a presentation runs: a notification with Back / Black / Next, also on the lock screen, so the phone can
 * stay locked in the pocket. The buttons send a broadcast that MainActivity turns into a tap on the remote.
 */
final class ControlNote {
    static final String NEXT = "app.slidelink.remote.NEXT", PREV = "app.slidelink.remote.PREV", BLACK = "app.slidelink.remote.BLACK";
    private static final String CHANNEL = "present";
    private static final int ID = 7;

    private ControlNote() { }

    // Shows (or updates) the notification; text = e.g. "3 / 12 · Title".
    static void show(Context c, String text) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return;
        if (nm.getNotificationChannel(CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, L.t("Presentation controls", "Präsentations-Steuerung"),
                    NotificationManager.IMPORTANCE_LOW);           // no sound, no pop-up: just there while presenting
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        Intent open = c.getPackageManager().getLaunchIntentForPackage(c.getPackageName());
        Notification n = new Notification.Builder(c, CHANNEL)
                .setSmallIcon(c.getApplicationInfo().icon)
                .setContentTitle("SlideLink")
                .setContentText(text == null || text.isEmpty() ? L.t("Presentation running", "Präsentation läuft") : text)
                .setVisibility(Notification.VISIBILITY_PUBLIC)       // buttons usable without unlocking
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(open == null ? null : PendingIntent.getActivity(c, 0, open, PendingIntent.FLAG_IMMUTABLE))
                .addAction(action(c, PREV, android.R.drawable.ic_media_previous, L.t("Back", "Zurück"), 1))
                .addAction(action(c, BLACK, android.R.drawable.ic_media_pause, L.t("Black", "Schwarz"), 2))
                .addAction(action(c, NEXT, android.R.drawable.ic_media_next, L.t("Next", "Weiter"), 3))
                .setStyle(new Notification.MediaStyle().setShowActionsInCompactView(0, 1, 2))
                .build();
        try { nm.notify(ID, n); } catch (SecurityException ignored) { }   // notifications not allowed: nothing to show
    }

    // Removes the notification (presentation over, app closed).
    static void hide(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(ID);
    }

    private static Notification.Action action(Context c, String what, int icon, String label, int req) {
        Intent i = new Intent(what).setPackage(c.getPackageName());
        PendingIntent pi = PendingIntent.getBroadcast(c, req, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Action.Builder(Icon.createWithResource(c, icon), label, pi).build();
    }
}
