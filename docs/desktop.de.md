# SlideLink Desktop: Dokumentation des Quellcodes

🇬🇧 [English version](desktop.md) · [Android-App](android.de.md)

Die Desktop-Seite besteht aus reinem **Python 3.10+ (nur Standardbibliothek)** und einer **Web-Oberfläche in reinem
JavaScript** (kein Build-Schritt). Ein einziger Prozess bedient alles: Bibliothek, Editor, Präsentation und
Handy-Fernbedienung sprechen mit demselben HTTP-Server auf Port **8765**.

```
 ┌──────────── PC ─────────────────────────────────────────┐        ┌──── Handy ────┐
 │ SlideLink.pyw (Tray-App)                                │        │ Android-App   │
 │   ├─ server.py  ThreadingHTTPServer :8765               │◄──────►│ (WebView mit  │
 │   │    REST /api/*  ·  Live-Zustand per SSE /events     │ WLAN   │  /remote)     │
 │   │    Library (decks/*.json) · Registry (Geräte)       │ oder   └───────────────┘
 │   │    Watcher (~/Claude Presentations)                 │ HTTPS
 │   ├─ Chrome/Edge-App-Fenster → /  (Bibliothek, Editor)  │ (Funnel)
 │   └─ Chrome-Kiosk-Fenster    → /present (randlos)       │
 │ mcp_server.py ── HTTP ──► server.py   (Claude baut Folien)
 └─────────────────────────────────────────────────────────┘
```

## Dateien

| Datei | Zweck |
|---|---|
| `server.py` | HTTP-Server, Datenmodell, Live-Sync, Importe, Geräte, Ordnerüberwachung |
| `SlideLink.pyw` | Windows-Desktop-App: Tray-Symbol, Fenster, nur eine Instanz, Autostart |
| `mcp_server.py` | MCP-Server (stdio JSON-RPC), über den Claude `server.py` steuert |
| `static/` | Web-Oberfläche (siehe unten) |
| `install.ps1` / `uninstall.ps1` | Installation pro Benutzer nach `%LOCALAPPDATA%\Programs\SlideLink`, Verknüpfungen, Eintrag unter *Einstellungen → Apps* |
| `pdf_pages.ps1` | Rendert PDF-Seiten als PNG mit der in Windows eingebauten PDF-Engine (`Windows.Data.Pdf`) |
| `save_deck.py` / `save_doc.py` | Schreiben ein von claude.ai geholtes Deck / Doc in den überwachten Ordner (für eine geplante Claude-Aufgabe) |
| `import_claude_deck.py` | Import eines claude.ai-Deck-Exports über die Kommandozeile |
| `make_icon.py` | Erzeugt die App-Symbole |
| `setup/Setup.cs`, `setup/build_setup.ps1` | Der Windows-Installer (`SlideLink-Setup.exe`) |
| `chrome-extension/` | Optionale Chrome-Erweiterung, die ein claude.ai-Deck an `/api/import` schickt |

## server.py

### Daten
Alles liegt in `%LOCALAPPDATA%\SlideLink\data` (änderbar mit `SLIDELINK_DATA`):

| Pfad | Inhalt |
|---|---|
| `decks/<id>.json` | Eine Präsentation: `title`, `theme`, `css`, `faces` (Google Fonts), `source`, `kind` (`deck`/`doc`), `date`, `slides[]` |
| `library.json` | Aktives Deck, Signaturen der vom Watcher synchronisierten Dateien |
| `devices.json` | Verknüpfte Geräte: ID, Name, Typ, **SHA-256-Hash** des Tokens, zuletzt gesehen, App-Version |
| `media/<24-hex>/` | Bilder/Seiten importierter Decks; der zufällige Ordnername macht die URLs unerratbar |

Eine Folie hat `id`, `title`, `layout`, `html` oder `content`, `notes`, `status` (`draft`/`in-progress`/`review`/`done`).
Layouts: `title`, `content`, `section`, `two-column`, `quote`, `image` (Markdown-ähnlich), `html` (eine gestaltete
1920×1080-`<section>` aus claude.ai), `page` (ein vollständiges HTML-Dokument: App, Quiz, Scroll-Seite) und `doc`
(Seiten eines PDFs).

### Hauptbestandteile
- **`Library`**: lädt/speichert Decks und hält zwischengespeicherte Zusammenfassungen (`listing()` nach Datum sortiert) für die Bibliotheksansichten.
- **`Store`**: der Live-Zustand (offenes Deck, `present` = Index/Schritt/Blackout, Aktivitäten, Claude-Status, verbundene
  Clients). `apply(op)` führt eine Operation unter einer Sperre aus; `publish()` schickt jedem SSE-Client einen neuen Stand.
- **`Registry`**: Verknüpfung. `new_code()` erzeugt einen einmaligen 6-stelligen Code (10 Min., max. 8 Fehlversuche),
  `pair()` macht daraus ein Gerät mit zufälligem Token, `by_token()` prüft ein Token über seinen Hash.
- **`Watcher`** (Thread): durchsucht alle 3 s `~/Claude Presentations` (Deck-Ordner mit `deck.json`, `.html`,
  PowerPoint, `.zip`) und den Unterordner `Docs/` (`.pdf`, `.html`). Eine Datei wird importiert, sobald sie sich nicht
  mehr ändert; ein Deck mit demselben claude.ai-Slug wird aktualisiert statt verdoppelt.
- **Importer**: `claude_to_import` / `folder_deck_raw` (claude.ai-Slides-Format), `html_to_import` (Ein-Datei-Decks
  werden Folien, alles andere eine `page`), `zip_to_import`, `json_to_import`, `powerpoint_to_import` (exportiert jede
  Folie als PNG über die PowerPoint-COM-Schnittstelle), `pdf_to_doc` (Seiten über `pdf_pages.ps1`), `html_to_doc`.
- **`build_steps(slide)`**: zählt die Klick-Builds einer gestalteten Folie genau wie claude.ai
  (`data-build-in`/`data-build-out` mit derselben Nummer = ein Klick, `auto` = kein Klick, jedes `.fragment` = ein Klick).
  Der Browser (`hsSteps` in `hslide.js`) nutzt dieselben Regeln, damit „Weiter“ überall dasselbe bedeutet.
- **`Handler`**: die HTTP-API (unten), statische Dateien mit `Cache-Control: no-cache` und `app_build()`, eine Version der
  Web-Dateien: Ändert sie sich, lädt sich jedes offene Fenster selbst neu.

### HTTP-API
Alle `/api/*`-Aufrufe brauchen ein verknüpftes Gerät (Cookie `sl_token` oder Header `X-SlideLink-Token`), außer wo
angegeben. Der PC selbst (`127.0.0.1` ohne Proxy-Header) ist immer vertrauenswürdig.

| Methode & Pfad | Zweck |
|---|---|
| `GET /`, `/library`, `/edit`, `/present`, `/remote` | Die Web-Oberfläche (eine `index.html`, der Modus kommt aus dem Pfad) |
| `GET /events?role=…&client=…[&appv=…]` | **Server-Sent Events**: kompletter Zustand bei jeder Änderung, Ping alle 10 s |
| `GET /api/state` | Aktueller Zustand |
| `GET /api/library`, `/api/library/<id>` | Liste der Decks / ein vollständiges Deck |
| `GET /api/info` | Port, Adressen für Handys (WLAN, Tailscale), Tailscale-Status |
| `GET /api/whoami` | Ist dieses Gerät verknüpft? (ohne Anmeldung) |
| `GET /api/watch` | Status der Ordnerüberwachung |
| `POST /api/op` | Eine Operation ausführen (Tabelle unten) |
| `POST /api/pair` | Gerät mit dem 6-stelligen Code verknüpfen (ohne Anmeldung) |
| `POST /api/devices/code`, `/rename`, `/unlink` | Link-Code erzeugen, Gerät umbenennen oder entfernen |
| `POST /api/upload?name=…` | Datei hochladen (PowerPoint, zip, html, json, Bilder) |
| `POST /api/import` | Einweg-Import aus claude.ai (Chrome-Erweiterung; nur PC, CORS für claude.ai) |
| `POST /api/watch/open` | Überwachten Ordner im Explorer öffnen (nur PC) |
| `POST /api/bye` | Ein Fenster schließt sich: seine Live-Verbindung sofort entfernen |
| `POST /api/clientlog` | Skriptfehler von Handys landen im PC-Log |
| `GET /SlideLink.apk`, `/app-version.json` | Die Android-App und ihre Version, für Download und Updates in der App |

**Operationen** (`POST /api/op` mit `{"op": …}`):
`next`, `prev`, `goto`, `blackout`, `resync`, `open_deck`, `show_on_pc`, `open_on_pc`, `close_on_pc` (für Handys
erlaubt) sowie für PC/Editor: `add_slide`, `update_slide`, `delete_slide`, `duplicate_slide`, `move_slide`, `set_deck`,
`create_deck`, `import_deck`, `duplicate_deck`, `delete_deck`, `claude_status`.
`next`/`prev` senden `from_index`/`from_step` mit; ist die Präsentation inzwischen weiter, antwortet der Server *stale*,
damit schnelles Tippen auf mehreren Geräten nie versehentlich Folien überspringt.

### Beta-Endpunkte
| | |
|---|---|
| `GET /live?k=…` | Publikumsseite (kein Gerät nötig; der Schlüssel ist die Eintrittskarte) |
| `GET /events?role=audience&k=…` | Live-Stream fürs Publikum: reduzierter Zustand ohne Notizen, Geräte oder Aktivitäten |
| `POST /api/vote` | `{k, poll, option, voter}`: eine Stimme pro Zuschauer, änderbar solange offen |

| `GET /group?k=…` | Beitrittsseite der Gruppen-Sitzung (Name, dann Browser oder App) |
| `POST /api/group/join` | `{k, name}`: tritt der Gruppe bei, liefert ein Mitglieds-Token (Cookie `sl_group`), gültig bis zum Ende der Sitzung |

Gruppenmitglieder sind keine verknüpften Geräte: Sie dürfen nur `next`, `prev`, `goto`, `blackout`, `resync`, Umfrage-Operationen, `speaker_me`, `speaker_set`, `join_qr`. Admins (PC, verknüpfte Handys) haben zusätzlich `group_start`, `group_end`, `group_kick` `{member}`, `group_qr` `{on}`.

Operationen: `poll_start` `{question, options}`, `poll_show` `{on}`, `poll_end`, `poll_clear`, `speaker_me`, `speaker_set` `{name}`, `audience_new`, `join_qr` `{on}` (alle für Handys erlaubt).

### Sicherheit
- Verknüpfung: einmaliger 6-stelliger Code, 10 Minuten gültig, nach 8 Fehlversuchen ungültig; Tokens sind 256-Bit-
  Zufallswerte und werden nur als SHA-256-Hash gespeichert; beim Entfernen wird die Live-Verbindung des Geräts sofort getrennt.
- Handys und Tablets sind **nur Fernbedienung**: Sie dürfen die oben genannten Fernbedienungs-Operationen ausführen, nie bearbeiten.
- Anfragen von `127.0.0.1` mit Proxy-Headern (`X-Forwarded-For`, `Tailscale-*`, …) gelten als entfernte Geräte. Wird der
  Port über Tailscale Funnel oder einen anderen Proxy veröffentlicht, bekommt dadurch niemand PC-Rechte.
- POST-Anfragen müssen JSON vom selben Ursprung sein (CSRF-Schutz); `/api/import` akzeptiert nur claude.ai.
- Gestaltete Folien laufen in abgeschotteten iframes (`allow-scripts allow-forms`, kein Same-Origin-Zugriff).

## SlideLink.pyw (Windows-App)
- **Tray-Symbol**, geschrieben mit `ctypes` (keine Zusatzpakete): Öffnen, Präsentieren, Verknüpfte Geräte, Mit Windows starten, Ordner, Beenden.
- **Nur eine Instanz**: ein benannter Mutex `Local\SlideLink.Desktop`. Ein zweiter Start bittet die laufende Instanz
  (über `open_on_pc`), ihr Fenster nach vorne zu holen, und beendet sich.
- **Fenster**: Das App-Fenster ist Chrome/Edge im `--app`-Modus mit eigenem Profil, maximiert gestartet. „Show on PC“
  öffnet die Präsentation im **Kiosk-Modus** (randloses Vollbild) mit eigenem Profil; ist sie schon offen, wird sie nach
  vorne geholt. Geschlossen wird sie mit Esc (`close_on_pc`) oder vom Handy. Fenster werden über `EnumWindows` gefunden.
- `--background` startet nur das Tray-Symbol (für *Mit Windows starten*). Logs: `%LOCALAPPDATA%\SlideLink\slidelink.log`.

## Web-Oberfläche (`static/`)
| Datei | Inhalt |
|---|---|
| `index.html` | Eine Seite für alle Modi; früher Fehlermelder (zeigt Skriptfehler statt schwarzem Bild) |
| `app.js` | Kern: Live-Verbindung (SSE mit Wächter und Neuverbindung), Verknüpfungsbildschirm, Geräte-Panel, QR-Popup, Popup für Handy-Updates, Editor, Präsentation, Navigations-Warteschlange |
| `library.js` | Bibliothek: Reiter *Präsentationen*/*Docs*, Gruppierung nach Tagen, Hochladen, Ordnerstatus |
| `remote.js` | Handy-Fernbedienung: Live-/Standbild-Vorschau, Build-Punkte, Notizen, Timer, Listen für Folien und Präsentationen, Knopf für das PC-Fenster |
| `hslide.js` | Rendert gestaltete claude.ai-Folien: abgeschottetes iframe pro Folie, Skalierung, `x-icon`/`x-shape`/`x-connector`/`x-embed`, der Klick-Build-Player, die Brücke für interaktive Seiten, die Doc-Ansicht, Stromsparmodus für Handys |
| `qr.js` | Eigenständiger QR-Code-Generator (Byte-Modus, Stufe M, Versionen 1–10) |
| `style.css` | Alle Styles |

Die Präsentation hält vorherige, aktuelle und nächste Folie geladen und blendet zwischen ihnen über, damit beim Wechseln
nichts aufblitzt. Das Handy zeigt nur die aktuelle Folie, als Standbild, außer *Live* ist eingeschaltet.

## mcp_server.py
Ein [Model-Context-Protocol](https://modelcontextprotocol.io)-Server über stdio. Werkzeuge: `list_presentations`,
`open_presentation`, `create_presentation`, `get_presentation`, `add_slide`, `update_slide`, `delete_slide`,
`move_slide`, `set_presentation`, `set_working_status` (live auf PC und Handy sichtbar), `control_presentation`.
Der Installer legt eine `.mcp.json` neben die App, damit Claude Code ihn nutzen kann.

## Aus dem Quellcode bauen & starten
```powershell
python server.py                 # nur der Server, http://localhost:8765
pythonw SlideLink.pyw            # Desktop-App mit Tray-Symbol
powershell -File install.ps1     # für den aktuellen Benutzer installieren
powershell -File setup\build_setup.ps1   # dist\SlideLink-Setup.exe bauen
```
`build_setup.ps1` packt die App-Dateien in die EXE und kompiliert `setup\Setup.cs` mit dem C#-Compiler, der bei
Windows dabei ist (.NET Framework 4.x). Das Setup findet Python (Registry, übliche Ordner, `py`, `python`), bietet die
Installation über `winget` an (oder öffnet python.org, wenn winget fehlt), führt `install.ps1` aus und registriert die
Deinstallation. `SlideLink-Setup.exe /S` installiert still (Log in `%TEMP%\SlideLink-setup.log`).
