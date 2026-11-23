// SlideLink: Languages of the app's own screens: follows the admin's language (sent by the page), else the phone's language.
package app.slidelink.remote;

import java.util.Locale;

final class L {
    static String lang = Locale.getDefault().getLanguage();   // replaced by the admin's language once connected

    private L() { }

    // The text in the current language (English or German).
    static String t(String en, String de) {
        return "de".equals(lang) ? de : en;
    }
}
