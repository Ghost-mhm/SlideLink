# SlideLink

**Am PC präsentieren, alles vom Handy aus steuern.** 🇬🇧 [English version](README.md)

SlideLink ist eine Präsentations-App für Windows mit einer Android-Fernbedienung. Sie zeigt Präsentationen aus
[Claude](https://claude.ai) genau so, wie sie dort aussehen (Claude-Slides-Decks, HTML-Seiten und Claude Docs),
hält PC und Handy live synchron und lässt Claude Präsentationen direkt in der App erstellen.

![Plattformen](https://img.shields.io/badge/platform-Windows%20%7C%20Android-7c83ff) ![Lizenz](https://img.shields.io/badge/license-MIT-green)

## Funktionen

**Am PC**
- Bibliothek wie ein Spiele-Launcher: Reiter *Präsentationen* und *Docs*, neueste zuerst, nach Tagen gruppiert
- Präsentationsmodus mit Klick-Animationen (Builds), Animationen und Übergängen, genau wie auf claude.ai
- Randloses Vollbild-Präsentationsfenster (Esc schließt es)
- Überwacht den Ordner `Claude Presentations`: Alles, was Claude dort speichert, erscheint automatisch
- Hochladen: PowerPoint (wird von PowerPoint umgewandelt), claude.ai-Deck als `.zip`, HTML, PDF-Docs, Bilder
- Editor mit Live-Sync sowie ein MCP-Server, damit Claude Folien erstellen und bearbeiten kann, während du zusiehst
- Tray-App (wie Steam): läuft im Hintergrund, optional Start mit Windows, läuft nur einmal

**Auf dem Handy (Android-App)**
- Fernbedienung mit aktueller Folie, nächster Folie, Sprechernotizen, Timer, Blackout, Build-Punkten
- Folien und Präsentationen wechseln, „Show on PC“ / „Close window on PC“
- Lautstärketasten: **+** weiter, **−** zurück
- Verbindet sich selbst: im Heim-WLAN direkt, wenn Handy und PC im selben Netz sind, sonst über HTTPS
- Akkuschonend: standardmäßig Standbild-Vorschau, 12 fps im Live-Modus, dimmt beim Präsentieren, pausiert im Hintergrund
- Aktualisiert sich selbst über deinen PC

## Neu in 1.1.0: Publikum, Live-Umfragen, Präsentieren in der Gruppe
- **Publikums-Link** (`/live?k=…`): Zuschauer scannen einen QR-Code (auch groß auf der Leinwand) und sehen die aktuelle Folie live im Browser ihres Handys. Nur ansehen: keine Steuerung, keine Sprechernotizen. *New link* macht den alten Link ungültig.
- **Live-Umfragen:** Am Handy (👥 Live) eine Umfrage mit 2-4 Antworten starten. Das Publikum stimmt auf der Publikumsseite ab (eine Stimme pro Handy); das Ergebnis lässt sich live als Balken auf der Leinwand zeigen.
- **Present in group:** Gruppen-Sitzung am PC oder Admin-Handy starten. Mitvortragende scannen einen QR-Code und treten mit ihrem Namen bei, im Browser (auch iPhone) oder in der Android-App. Sie steuern nur den Vortrag (Folien, Umfragen, Notizen, *I'm presenting*), bearbeiten oder wechseln nie Präsentationen und fliegen raus, wenn die Sitzung endet (*End group session* am PC oder Admin-Handy, oder automatisch nach 4 Stunden). Nichts bleibt verknüpft.

## Installation

### Windows
1. **`SlideLink-Setup.exe`** aus den [Releases](../../releases) herunterladen.
2. Starten. SlideLink wird für deinen Benutzer installiert (keine Admin-Rechte nötig). Fehlt Python 3.10+, bietet das
   Setup an, es zu installieren. Windows SmartScreen kann warnen, weil die EXE nicht signiert ist: *Weitere Informationen → Trotzdem ausführen*.
3. SlideLink öffnet sich und läuft im Infobereich (unten rechts) weiter.

Stille Installation: `SlideLink-Setup.exe /S [/Launch] [/Startup] [/NoDesktop]`.

### Android
1. Am PC **⚙ Linked devices** öffnen. Ist noch kein Handy verbunden, erscheint ein **QR-Code**: mit der Handy-Kamera
   scannen und die App herunterladen (oder `SlideLink.apk` aus den Releases laden).
2. Installieren (Android fragt, ob Installationen aus dieser Quelle erlaubt sind; Play Protect kann warnen, weil die
   App nicht aus dem Play Store kommt: *Weitere Details → Trotzdem installieren*).
3. App öffnen, die in SlideLink angezeigte PC-Adresse eingeben und dann den 6-stelligen Code aus **Link a new device** eintippen.

### Den PC von überall erreichen (optional)
Zu Hause spricht das Handy über WLAN mit dem PC. Für den Zugriff von überall nutzt SlideLink
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) auf dem PC (kostenlos): Tailscale auf dem PC installieren,
Funnel im Tailscale-Konto freischalten und `tailscale funnel --bg 8765` ausführen. Das Handy braucht **kein** Tailscale.

## Voraussetzungen
- Windows 10/11, Python 3.10+ (nur Standardbibliothek), Google Chrome oder Microsoft Edge
- Android 8.0+ für die App
- Optional: PowerPoint (für `.pptx`-Import), Tailscale (für Zugriff von überall)

## Dokumentation
| | Deutsch | English |
|---|---|---|
| Desktop-App & Server (Python, Web-Oberfläche, API) | [docs/desktop.de.md](docs/desktop.de.md) | [docs/desktop.md](docs/desktop.md) |
| Android-App (Java) | [docs/android.de.md](docs/android.de.md) | [docs/android.md](docs/android.md) |

## Projektstruktur
```
server.py            HTTP-Server: Bibliothek, Live-Sync (SSE), Geräte, Importe, Ordnerüberwachung
SlideLink.pyw        Windows-Desktop-App: Tray-Symbol, App-/Präsentationsfenster, nur eine Instanz
mcp_server.py        MCP-Server: Claude kann Präsentationen auflisten, erstellen und steuern
static/              Web-Oberfläche: Bibliothek, Editor, Präsentation, Handy-Fernbedienung (reines JS, kein Build)
android/             Android-App (Java, ohne Gradle) + Build-Skript
setup/               Windows-Installer (C#, kompiliert mit dem in Windows enthaltenen C#-Compiler)
chrome-extension/    Optional: Decks aus claude.ai in Chrome importieren
install.ps1 / uninstall.ps1, pdf_pages.ps1, save_deck.py, save_doc.py, import_claude_deck.py, make_icon.py
```

## Sicherheit
Nur Geräte, die du mit einem einmaligen 6-stelligen Code verknüpfst (10 Minuten gültig, nach 8 Fehlversuchen
ungültig), können etwas sehen oder steuern. Geräte-Tokens werden nur als SHA-256-Hash gespeichert. Anfragen über
einen Proxy (z. B. Tailscale Funnel) gelten nie als der PC selbst. Siehe [docs/desktop.de.md](docs/desktop.de.md#sicherheit).

## Lizenz
[MIT](LICENSE)
