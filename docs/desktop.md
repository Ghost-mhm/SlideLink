# SlideLink desktop: source code documentation

🇩🇪 [Deutsche Version](desktop.de.md) · [Android app](android.md)

The desktop side is plain **Python 3.10+ (standard library only)** plus a **web UI in plain JavaScript** (no build
step). One process serves everything: the library, the editor, the presenter and the phone remote all talk to the
same HTTP server on port **8765**.

```
 ┌──────────── PC ─────────────────────────────────────────┐        ┌──── Phone ────┐
 │ SlideLink.pyw (tray app)                                │        │ Android app   │
 │   ├─ server.py  ThreadingHTTPServer :8765               │◄──────►│ (WebView of   │
 │   │    REST /api/*  ·  live state via SSE /events       │ Wi-Fi  │  /remote)     │
 │   │    Library (decks/*.json) · Registry (devices)      │ or     └───────────────┘
 │   │    Watcher (~/Claude Presentations)                 │ HTTPS
 │   ├─ Chrome/Edge app window  → /  (library, editor)     │ (Funnel)
 │   └─ Chrome kiosk window     → /present (borderless)    │
 │ mcp_server.py ── HTTP ──► server.py   (Claude builds slides)
 └─────────────────────────────────────────────────────────┘
```

## Files

| File | Purpose |
|---|---|
| `server.py` | HTTP server, data model, live sync, imports, devices, folder watcher |
| `SlideLink.pyw` | Windows desktop app: tray icon, windows, single instance, autostart |
| `mcp_server.py` | MCP server (stdio JSON-RPC) that drives `server.py` for Claude |
| `static/` | Web UI (see below) |
| `install.ps1` / `uninstall.ps1` | Per-user install to `%LOCALAPPDATA%\Programs\SlideLink`, shortcuts, *Settings → Apps* entry |
| `pdf_pages.ps1` | Renders PDF pages to PNG with Windows' built-in PDF engine (`Windows.Data.Pdf`) |
| `save_deck.py` / `save_doc.py` | Write a pulled claude.ai deck / doc into the watched folder (used by a scheduled Claude task) |
| `import_claude_deck.py` | Command-line import of a claude.ai deck export |
| `make_icon.py` | Generates the app icons |
| `setup/Setup.cs`, `setup/build_setup.ps1` | The Windows installer (`SlideLink-Setup.exe`) |
| `chrome-extension/` | Optional Chrome extension that sends a claude.ai deck to `/api/import` |

## server.py

### Data
Everything lives in `%LOCALAPPDATA%\SlideLink\data` (override with `SLIDELINK_DATA`):

| Path | Content |
|---|---|
| `decks/<id>.json` | One presentation: `title`, `theme`, `css`, `faces` (Google Fonts), `source`, `kind` (`deck`/`doc`), `date`, `slides[]` |
| `library.json` | Active deck, synced-file signatures of the watcher |
| `devices.json` | Linked devices: id, name, type, **SHA-256 hash** of the token, last seen, app version |
| `media/<24-hex>/` | Pictures/pages of imported decks; the random folder name makes URLs unguessable |

A slide has `id`, `title`, `layout`, `html` or `content`, `notes`, `status` (`draft`/`in-progress`/`review`/`done`).
Layouts: `title`, `content`, `section`, `two-column`, `quote`, `image` (Markdown-like), `html` (a designed 1920×1080
`<section>` from claude.ai), `page` (a complete HTML document: app, quiz, scrolling page) and `doc` (pages of a PDF).

### Main parts
- **`Library`**: loads/saves decks, keeps cached summaries (`listing()` sorted by date) for the library views.
- **`Store`**: the live state (open deck, `present` = index/step/blackout, activity feed, Claude status, connected
  clients). `apply(op)` executes one operation under a lock; `publish()` pushes a new snapshot to every SSE client.
- **`Registry`**: linking. `new_code()` creates a 6-digit one-time code (10 min, max 8 wrong tries), `pair()` turns it
  into a device + random token, `by_token()` checks a token by its hash.
- **`Watcher`** (thread): every 3 s scans `~/Claude Presentations` (deck folders with `deck.json`, `.html`, PowerPoint,
  `.zip`) and its `Docs/` subfolder (`.pdf`, `.html`). A file is imported once it stopped changing; a deck with the same
  claude.ai slug is updated in place instead of duplicated.
- **Importers**: `claude_to_import` / `folder_deck_raw` (claude.ai Slides format), `html_to_import` (single-file decks
  become slides, anything else one `page`), `zip_to_import`, `json_to_import`, `powerpoint_to_import` (exports every
  slide as PNG through the PowerPoint COM API), `pdf_to_doc` (pages via `pdf_pages.ps1`), `html_to_doc`.
- **`build_steps(slide)`**: counts the click builds of a designed slide exactly like claude.ai
  (`data-build-in`/`data-build-out` with the same number = one click, `auto` = no click, each `.fragment` = one click).
  The browser (`hsSteps` in `hslide.js`) uses the same rules, so Next means the same everywhere.
- **`Handler`**: the HTTP API (below), static files with `Cache-Control: no-cache`, and `app_build()`, a version of the
  web files: when it changes, every open window reloads itself.

### HTTP API
All `/api/*` calls need a linked device (cookie `sl_token` or header `X-SlideLink-Token`) except where noted.
The PC itself (`127.0.0.1` without proxy headers) is always trusted.

| Method & path | Purpose |
|---|---|
| `GET /`, `/library`, `/edit`, `/present`, `/remote` | The web UI (one `index.html`, the mode comes from the path) |
| `GET /events?role=…&client=…[&appv=…]` | **Server-Sent Events**: full state on every change, ping every 10 s |
| `GET /api/state` | Current state snapshot |
| `GET /api/library`, `/api/library/<id>` | Deck list / one full deck |
| `GET /api/info` | Ports, addresses for phones (Wi-Fi, Tailscale), Tailscale status |
| `GET /api/whoami` | Is this device linked? (no auth) |
| `GET /api/watch` | Folder watcher status |
| `POST /api/op` | Run an operation (table below) |
| `POST /api/pair` | Link a device with the 6-digit code (no auth) |
| `POST /api/devices/code`, `/rename`, `/unlink` | Create a link code, rename or unlink a device |
| `POST /api/upload?name=…` | Upload a file (PowerPoint, zip, html, json, pictures) |
| `POST /api/import` | One-way import from claude.ai (Chrome extension; PC only, CORS for claude.ai) |
| `POST /api/watch/open` | Open the watched folder in Explorer (PC only) |
| `POST /api/bye` | A window is closing: drop its live connection immediately |
| `POST /api/clientlog` | Script errors from phones end up in the PC log |
| `GET /SlideLink.apk`, `/app-version.json` | The Android app and its version, for download and in-app updates |

**Operations** (`POST /api/op` with `{"op": …}`):
`next`, `prev`, `goto`, `blackout`, `resync`, `open_deck`, `show_on_pc`, `open_on_pc`, `close_on_pc` (allowed for
phones), and for the PC/editor: `add_slide`, `update_slide`, `delete_slide`, `duplicate_slide`, `move_slide`,
`set_deck`, `create_deck`, `import_deck`, `duplicate_deck`, `delete_deck`, `claude_status`.
`next`/`prev` carry `from_index`/`from_step`; if the presentation moved on meanwhile the server answers *stale*, so
fast taps from several devices never skip slides by accident.

### Audience, polls and group endpoints (1.1.0)
| | |
|---|---|
| `GET /live?k=…` | Audience page (no device needed; the key is the ticket) |
| `GET /events?role=audience&k=…` | Audience live stream: reduced state without notes, devices or activity |
| `POST /api/vote` | `{k, poll, option, voter}`: one vote per viewer, changeable while open |

| `GET /group?k=…` | Group session join page (name, then browser or app) |
| `POST /api/group/join` | `{k, name}`: joins the group, returns a member token (cookie `sl_group`) valid until the session ends |

Group members are not linked devices: they may only run `next`, `prev`, `goto`, `blackout`, `resync`, poll ops, `speaker_me`, `speaker_set`, `join_qr`. Admins (PC, linked phones) also have `group_start`, `group_end`, `group_kick` `{member}`, `group_qr` `{on}`.

Operations: `poll_start` `{question, options}`, `poll_show` `{on}`, `poll_end`, `poll_clear`, `speaker_me`, `speaker_set` `{name}`, `audience_new`, `join_qr` `{on}` (all allowed for phones).

### Security
- Linking: one-time 6-digit code, valid 10 minutes, invalid after 8 wrong tries; tokens are 256-bit random values,
  stored only as SHA-256 hashes; unlinking kicks the device's live connection at once.
- Phones and tablets are **remote only**: they may run the remote operations listed above, never edit.
- Requests from `127.0.0.1` that carry proxy headers (`X-Forwarded-For`, `Tailscale-*`, …) are treated as remote
  devices, so publishing the port through Tailscale Funnel or another proxy never grants PC rights.
- POST requests must be JSON from the same origin (CSRF protection); `/api/import` only accepts claude.ai origins.
- Designed slides run in sandboxed iframes (`allow-scripts allow-forms`, no same-origin access).

## SlideLink.pyw (Windows app)
- **Tray icon** written with `ctypes` (no extra packages): open, present, linked devices, start with Windows, folder, quit.
- **Single instance**: a named mutex `Local\SlideLink.Desktop`. A second start asks the running instance (via
  `open_on_pc`) to bring its window forward and quits.
- **Windows**: the app window is Chrome/Edge in `--app` mode with its own profile, started maximized. "Show on PC" opens
  the presenter in **kiosk mode** (borderless fullscreen) with a separate profile; it is brought to the front if it is
  already open, and closed by Esc (`close_on_pc`) or from the phone. Windows are found and focused through `EnumWindows`.
- `--background` starts only the tray (used for *Start with Windows*). Logs go to `%LOCALAPPDATA%\SlideLink\slidelink.log`.

## Web UI (`static/`)
| File | Content |
|---|---|
| `index.html` | One page for all modes; early error reporter (shows script errors instead of a black screen) |
| `app.js` | Core: live connection (SSE with watchdog and reconnect), linking screen, devices panel, QR popup, phone-update popup, editor, presenter, navigation queue |
| `library.js` | Library: tabs *Presentations*/*Docs*, grouping by day, upload, folder status |
| `remote.js` | Phone remote: live/still preview, build dots, notes, timer, sheets for slides and presentations, PC window button |
| `hslide.js` | Renders claude.ai designed slides: sandboxed iframe per slide, scaling, `x-icon`/`x-shape`/`x-connector`/`x-embed`, the click-build player, the bridge for interactive pages, the doc view, low-power mode for phones |
| `qr.js` | Self-contained QR code generator (byte mode, level M, versions 1–10) |
| `style.css` | All styles |

The presenter keeps the previous, current and next slide loaded and crossfades between them, so skipping does not
flash. The phone shows only the current slide, as a still picture unless *Live* is switched on.

## mcp_server.py
A [Model Context Protocol](https://modelcontextprotocol.io) server over stdio. Tools: `list_presentations`,
`open_presentation`, `create_presentation`, `get_presentation`, `add_slide`, `update_slide`, `delete_slide`,
`move_slide`, `set_presentation`, `set_working_status` (shown live on PC and phone), `control_presentation`.
The installer writes a `.mcp.json` next to the app so Claude Code can use it.

## Build & run from source
```powershell
python server.py                 # server only, http://localhost:8765
pythonw SlideLink.pyw            # desktop app with tray icon
powershell -File install.ps1     # install for the current user
powershell -File setup\build_setup.ps1   # build dist\SlideLink-Setup.exe
```
`build_setup.ps1` zips the app files into the EXE and compiles `setup\Setup.cs` with the C# compiler that ships with
Windows (.NET Framework 4.x). The setup finds Python (registry, usual folders, `py`, `python`), offers to install it
with `winget` (or opens python.org if winget is missing), runs `install.ps1` and registers the uninstaller.
`SlideLink-Setup.exe /S` installs silently (log in `%TEMP%\SlideLink-setup.log`).
