// SlideLink · EN: Languages of the app's own screens: follows the admin's language (sent by the page), else the phone's language.
// SlideLink · DE: Sprachen der app-eigenen Bildschirme: folgt der Admin-Sprache (von der Seite gesendet), sonst der Handy-Sprache.
package app.slidelink.remote;

import java.util.Locale;

final class L {
    static String lang = Locale.getDefault().getLanguage();   // replaced by the admin's language once connected

    private L() { }

    // EN: The text in the current language (English or German).
    // DE: Der Text in der aktuellen Sprache (Englisch oder Deutsch).
    static String t(String en, String de) {
        return "de".equals(lang) ? de : en;
    }
}
