# SlideLink Android app: source code documentation

🇩🇪 [Deutsche Version](android.de.md) · [Desktop & server](desktop.md)

The app is a small native shell (**Java, no AndroidX, no Gradle**) around SlideLink's phone remote, which is the web
page `/remote` served by the PC. The native part adds what a web page cannot do well: finding the PC on any network,
keeping the login, volume keys, screen and battery control, and self-updates.

- Package: `app.slidelink.remote` · minimum Android 8.0 (API 26) · target API 35
- Size: about 40 KB

## Files

| File | Purpose |
|---|---|
| `android/AndroidManifest.xml` | Package, version (`versionCode`/`versionName`), permissions, the single activity |
| `android/src/app/slidelink/remote/MainActivity.java` | The whole app UI and connection logic |
| `android/src/app/slidelink/remote/Updater.java` | Self-update from the PC |
| `android/build.ps1` | Builds and signs `SlideLink.apk` with the Android build tools directly |
| `android/release-notes.txt` | Text shown in the update dialog for the next version |

Permissions: `INTERNET`, `ACCESS_NETWORK_STATE` (to notice network changes) and `REQUEST_INSTALL_PACKAGES` (updates).

## MainActivity

### Screen
A full-screen `FrameLayout` with a **WebView** (the remote) and a native **offline screen** ("Can't reach SlideLink on
your PC", *Try again*, *Change PC address*) that retries every 5 s, so the app is never just black. Insets are applied
so content stays clear of the status and navigation bars (Android 15 draws apps edge to edge). The background is true
black, which switches OLED pixels off.

### Finding the PC
No address is built into the APK; everything is learned at runtime and stored in `SharedPreferences`:

| Key | Content | Source |
|---|---|---|
| `lan` | The PC's home-Wi-Fi addresses (`http://192.168.x.y:8765`, `http://<pc>.local:8765`) | page → `setLanUrls()` after connecting |
| `remote` | HTTPS (Tailscale Funnel) first, then Tailscale addresses | page → `setRemoteUrls()` |
| `base` | The address in use | chosen by `findPc()` |
| `token` | The device login | page → `setToken()` |

`candidates()` orders the addresses: **on Wi-Fi** the home addresses first, then HTTPS, then the rest. `findPc()` probes
`/api/whoami` on each (home addresses with a 1.2 s timeout, which is enough on the same network, others 4 s) and opens
the first one that answers. A `ConnectivityManager.NetworkCallback` re-runs this whenever the network changes, so the
app moves between home Wi-Fi and HTTPS by itself. On the very first start (nothing known yet) it asks for the address.

### Keeping the login
Cookies and `localStorage` belong to one address, so the app keeps the token itself and sets the `sl_token` cookie for
whichever address it opens (`CookieManager.setCookie`, `Secure` for HTTPS). The page syncs the token both ways through
the JavaScript bridge, so moving from Wi-Fi to HTTPS never asks to link again.

### JavaScript bridge (`window.SlideLinkApp`)
| Method | Called by the page when… |
|---|---|
| `version()` | showing the app version / reporting it to the PC (`/events?appv=…`) |
| `checkUpdate()` | *Check for updates* is pressed in ⚙ |
| `getToken()`, `setToken(t)` | starting / after linking or unlinking |
| `setLanUrls(urls)`, `setRemoteUrls(urls)` | connected: the PC's current addresses |
| `setPresenting(on)` | a presentation is shown on the PC or the timer runs |

The page also adds `SlideLinkApp/1.0` to the user agent check to hide the "get the app" link inside the app.

### Keys, screen, battery
- **Volume +** clicks *Next*, **volume −** clicks *Back* in the page (`dispatchKeyEvent`); the volume does not change.
- The screen stays on **only while presenting** (`FLAG_KEEP_SCREEN_ON` via `setPresenting`). While presenting, it dims
  to 4 % brightness after 20 s without touching; any touch or key restores it.
- `onPause` calls `WebView.pauseTimers()`, so nothing runs in the background.
- In the page, the preview is a still picture by default; in *Live* mode animations run at about 12 fps.

## Updater
1. On start/resume (at most every 10 minutes) and on *Check for updates*, it downloads `/app-version.json` from the PC:
   `{"versionCode": 8, "versionName": "1.7", "url": "/SlideLink.apk", "notes": "…"}`.
2. If `versionCode` is higher than the installed one, it asks; on *Update* it downloads the APK from the PC.
3. The first time, Android needs *Allow from this source* (`ACTION_MANAGE_UNKNOWN_APP_SOURCES`).
4. The APK is handed to Android's `PackageInstaller` session; Android shows its own confirmation (`STATUS_PENDING_USER_ACTION`).

An update installs over the old app only if it is signed with the **same key**.

## Building
Requirements: JDK 17+ and the Android SDK command-line tools with `platforms;android-35` and `build-tools;35.0.0`
(path given with `-Sdk`, default `..\android-sdk`).

```powershell
powershell -File android\build.ps1 [-Sdk <path to Android SDK>]
```
Steps: `aapt2 compile/link` (manifest, icon) → `javac --release 11` → `d8` (dex) → add `classes.dex` → `zipalign` →
`apksigner sign` → `apksigner verify`. The script also copies the APK to `static/SlideLink.apk` and writes
`static/app-version.json` from the manifest and `release-notes.txt`, so the PC offers the new version to phones.

**Signing key:** on the first build the script creates `android/slidelink.keystore`. Keep it safe and private (it is
in `.gitignore`): every later version must be signed with it, otherwise phones cannot update. If you fork the project,
you create your own key and your app is a separate app for Android.

**Releasing a new version:** raise `versionCode` and `versionName` in `AndroidManifest.xml`, write
`release-notes.txt`, run `build.ps1`, copy `static/` into the installed SlideLink. Phones get the update on their next start.
