# SlideLink Android-App: Dokumentation des Quellcodes

🇬🇧 [English version](android.md) · [Desktop & Server](desktop.de.md)

Die App ist eine kleine native Hülle (**Java, ohne AndroidX, ohne Gradle**) um die Handy-Fernbedienung von SlideLink,
also die Webseite `/remote`, die der PC ausliefert. Der native Teil ergänzt, was eine Webseite nicht gut kann: den PC
in jedem Netz finden, die Anmeldung behalten, Lautstärketasten, Bildschirm- und Akkusteuerung sowie Selbst-Updates.

- Paket: `app.slidelink.remote` · mindestens Android 8.0 (API 26) · Ziel-API 35
- Größe: etwa 40 KB

## Dateien

| Datei | Zweck |
|---|---|
| `android/AndroidManifest.xml` | Paket, Version (`versionCode`/`versionName`), Berechtigungen, die einzige Activity |
| `android/src/app/slidelink/remote/MainActivity.java` | Die gesamte Oberfläche und Verbindungslogik |
| `android/src/app/slidelink/remote/Updater.java` | Selbst-Update über den PC |
| `android/build.ps1` | Baut und signiert `SlideLink.apk` direkt mit den Android-Build-Tools |
| `android/release-notes.txt` | Text im Update-Dialog für die nächste Version |

Berechtigungen: `INTERNET`, `ACCESS_NETWORK_STATE` (um Netzwechsel zu bemerken) und `REQUEST_INSTALL_PACKAGES` (Updates).

## MainActivity

### Bildschirm
Ein Vollbild-`FrameLayout` mit einer **WebView** (die Fernbedienung) und einem nativen **Offline-Bildschirm** („Can't
reach SlideLink on your PC“, *Try again*, *Change PC address*), der es alle 5 s erneut versucht, damit die App nie nur
schwarz ist. Insets werden angewendet, damit der Inhalt nicht unter Status- und Navigationsleiste liegt (Android 15
zeichnet Apps randlos). Der Hintergrund ist echtes Schwarz, das schaltet OLED-Pixel ab.

### Den PC finden
In der APK ist keine Adresse eingebaut; alles wird zur Laufzeit gelernt und in `SharedPreferences` gespeichert:

| Schlüssel | Inhalt | Quelle |
|---|---|---|
| `lan` | Die Heim-WLAN-Adressen des PCs (`http://192.168.x.y:8765`, `http://<pc>.local:8765`) | Seite → `setLanUrls()` nach dem Verbinden |
| `remote` | Die Adresse im SlideLink-Relay (`…/p/<pc-id>`) | Seite → `setRemoteUrls()` |
| `base` | Die gerade genutzte Adresse | gewählt von `findPc()` |
| `token` | Die Anmeldung des Geräts | Seite → `setToken()` |

`candidates()` ordnet die Adressen: **im WLAN** zuerst die Heim-Adressen, dann HTTPS, dann der Rest. `findPc()` fragt
bei jeder `/api/whoami` ab (Heim-Adressen mit 1,2 s Zeitlimit, das reicht im selben Netz, andere mit 4 s) und öffnet die
erste, die antwortet. Ein `ConnectivityManager.NetworkCallback` wiederholt das bei jedem Netzwechsel, so wechselt die
App selbstständig zwischen Heim-WLAN und HTTPS. Beim allerersten Start (noch nichts bekannt) fragt sie nach der Adresse.

### Die Anmeldung behalten
Cookies und `localStorage` gehören zu einer Adresse. Deshalb speichert die App das Token selbst und setzt das Cookie
`sl_token` für die Adresse, die sie öffnet (`CookieManager.setCookie`, `Secure` bei HTTPS). Die Seite gleicht das Token
über die JavaScript-Brücke in beide Richtungen ab, sodass ein Wechsel von WLAN zu HTTPS nie eine neue Verknüpfung verlangt.

### JavaScript-Brücke (`window.SlideLinkApp`)
| Methode | Wird von der Seite aufgerufen, wenn… |
|---|---|
| `version()` | die App-Version angezeigt / an den PC gemeldet wird (`/events?appv=…`) |
| `checkUpdate()` | in ⚙ *Check for updates* gedrückt wird |
| `getToken()`, `setToken(t)` | beim Start / nach dem Verknüpfen oder Entfernen |
| `setLanUrls(urls)`, `setRemoteUrls(urls)` | verbunden: die aktuellen Adressen des PCs |
| `setPresenting(on)` | eine Präsentation auf dem PC gezeigt wird oder der Timer läuft |

### Tasten, Bildschirm, Akku
- **Lauter** klickt *Weiter*, **Leiser** klickt *Zurück* in der Seite (`dispatchKeyEvent`); die Lautstärke ändert sich nicht.
- Der Bildschirm bleibt **nur beim Präsentieren** an (`FLAG_KEEP_SCREEN_ON` über `setPresenting`). Beim Präsentieren
  dimmt er nach 20 s ohne Berührung auf 4 % Helligkeit; jede Berührung oder Taste stellt sie wieder her.
- `onPause` ruft `WebView.pauseTimers()` auf, im Hintergrund läuft also nichts.
- In der Seite ist die Vorschau standardmäßig ein Standbild; im *Live*-Modus laufen Animationen mit etwa 12 fps.

## Updater
1. Beim Start/Fortsetzen (höchstens alle 10 Minuten) und bei *Check for updates* lädt er `/app-version.json` vom PC:
   `{"versionCode": 8, "versionName": "1.7", "url": "/SlideLink.apk", "notes": "…"}`.
2. Ist `versionCode` höher als die installierte Version, fragt er nach; bei *Update* lädt er die APK vom PC.
3. Beim ersten Mal braucht Android *Aus dieser Quelle zulassen* (`ACTION_MANAGE_UNKNOWN_APP_SOURCES`).
4. Die APK geht an eine `PackageInstaller`-Sitzung von Android; Android zeigt seine eigene Bestätigung (`STATUS_PENDING_USER_ACTION`).

Ein Update lässt sich nur über die alte App installieren, wenn es mit **demselben Schlüssel** signiert ist.

## Bauen
Voraussetzungen: JDK 17+ und die Android-SDK-Kommandozeilentools mit `platforms;android-35` und `build-tools;35.0.0`
(Pfad über `-Sdk`, Standard `..\android-sdk`).

```powershell
powershell -File android\build.ps1 [-Sdk <Pfad zum Android SDK>]
```
Schritte: `aapt2 compile/link` (Manifest, Symbol) → `javac --release 11` → `d8` (Dex) → `classes.dex` hinzufügen →
`zipalign` → `apksigner sign` → `apksigner verify`. Das Skript kopiert die APK außerdem nach `static/SlideLink.apk`
und schreibt `static/app-version.json` aus dem Manifest und `release-notes.txt`, damit der PC Handys die neue Version anbietet.

**Signaturschlüssel:** Beim ersten Bauen erzeugt das Skript `android/slidelink.keystore`. Gut und privat aufbewahren
(steht in `.gitignore`): Jede spätere Version muss damit signiert sein, sonst können Handys nicht aktualisieren. Wer das
Projekt forkt, erzeugt einen eigenen Schlüssel; seine App ist für Android dann eine eigene App.

**Neue Version veröffentlichen:** `versionCode` und `versionName` in `AndroidManifest.xml` erhöhen, `release-notes.txt`
schreiben, `build.ps1` ausführen, `static/` in das installierte SlideLink kopieren. Handys bekommen das Update beim nächsten Start.
