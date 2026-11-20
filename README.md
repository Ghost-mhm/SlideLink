# SlideLink

**Present from your PC, control everything from your phone.** 🇩🇪 [Deutsche Version](README.de.md)

SlideLink is a presentation app for Windows with an Android remote. It shows presentations made with
[Claude](https://claude.ai) exactly as they look there (Claude Slides decks, HTML pages and Claude Docs),
keeps PC and phone in sync live, and lets Claude build presentations directly in the app.

![platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Android-7c83ff) ![license](https://img.shields.io/badge/license-MIT-green)

## Features

**On the PC**
- Library like a game launcher: *Presentations* and *Docs* tabs, newest first, grouped by day
- Presenter with click builds, animations and transitions, exactly like claude.ai
- Borderless fullscreen presentation window (Esc closes it)
- Watches the folder `Claude Presentations`: everything Claude saves there appears automatically
- Upload: PowerPoint (converted by PowerPoint), claude.ai deck `.zip`, HTML, PDF docs, images
- Editor with live sync, plus an MCP server so Claude can build and edit slides while you watch
- Tray app (like Steam): runs in the background, optional start with Windows, runs only once

**On the phone (Android app)**
- Remote with live slide, next slide, speaker notes, timer, blackout, build dots
- Switch slides and presentations, "Show on PC" / "Close window on PC"
- Volume keys: **+** next, **−** back
- Connects by itself: home Wi-Fi when on the same network as the PC, HTTPS everywhere else
- Battery friendly: still preview by default, 12 fps when live, dims while presenting, pauses in the background
- Updates itself from your PC

## New in 1.1.0: audience, live polls, present in group
- **Audience link** (`/live?k=…`): viewers scan a QR code (also shown big on the presentation screen) and follow the current slide live on their phone, in the browser. Watch only: no control, no speaker notes. *New link* invalidates the old one.
- **Live polls:** start a poll with 2-4 answers from the phone remote (👥 Live). The audience votes on the audience page (one vote per phone); results can be shown live as bars on the presentation screen.
- **Present in group:** start a group session on the PC or your admin phone. Co-presenters scan a QR code and join with their name, in the browser (also iPhone) or the Android app. They control the talk only (slides, polls, notes, *I'm presenting*), never edit or switch presentations, and are disconnected when the session ends (*End group session* on PC or admin phone, or automatically after 4 hours). Nothing stays linked.

## Install

### Windows
1. Download **`SlideLink-Setup.exe`** from the [Releases](../../releases).
2. Run it. It installs SlideLink for your user (no admin rights). If Python 3.10+ is missing, the setup offers to install it.
   Windows SmartScreen may warn because the EXE is not signed: *More info → Run anyway*.
3. SlideLink opens. It lives in the tray (bottom right) while running.

Silent install: `SlideLink-Setup.exe /S [/Launch] [/Startup] [/NoDesktop]`.

### Android
1. On the PC, open **⚙ Linked devices**. With no phone connected yet, a **QR code** appears: scan it with the phone camera
   to download the app (or download `SlideLink.apk` from the Releases).
2. Install it (Android asks to allow installs from this source; Play Protect may warn because the app is not from
   the Play Store: *More details → Install anyway*).
3. Open the app, enter the PC address shown in SlideLink, then type the 6-digit link code from **Link a new device**.

### Reaching the PC from anywhere (optional)
At home the phone talks to the PC over Wi-Fi. For access from anywhere, SlideLink uses
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) on the PC (free): install Tailscale on the PC, enable Funnel
in your Tailscale account, then run `tailscale funnel --bg 8765`. The phone does **not** need Tailscale.

## Requirements
- Windows 10/11, Python 3.10+ (standard library only), Google Chrome or Microsoft Edge
- Android 8.0+ for the app
- Optional: PowerPoint (for `.pptx` import), Tailscale (for access from anywhere)

## Documentation
| | English | Deutsch |
|---|---|---|
| Desktop app & server (Python, web UI, API) | [docs/desktop.md](docs/desktop.md) | [docs/desktop.de.md](docs/desktop.de.md) |
| Android app (Java) | [docs/android.md](docs/android.md) | [docs/android.de.md](docs/android.de.md) |

## Project layout
```
server.py            HTTP server: library, live sync (SSE), devices, imports, folder watcher
SlideLink.pyw        Windows desktop app: tray icon, app/presenter windows, single instance
mcp_server.py        MCP server: lets Claude list, build and control presentations
static/              Web UI: library, editor, presenter, phone remote (plain JS, no build step)
android/             Android app (Java, no Gradle) + build script
setup/               Windows installer (C#, compiled with the C# compiler built into Windows)
chrome-extension/    Optional: import decks from claude.ai in Chrome
install.ps1 / uninstall.ps1, pdf_pages.ps1, save_deck.py, save_doc.py, import_claude_deck.py, make_icon.py
```

## Security
Only devices you link with a one-time 6-digit code (valid 10 minutes, destroyed after 8 wrong tries) can see or
control anything. Device tokens are stored only as SHA-256 hashes. Requests through a proxy (e.g. Tailscale Funnel)
are never treated as the PC itself. See [docs/desktop.md](docs/desktop.md#security).

## License
[MIT](LICENSE)
