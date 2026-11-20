// SlideLink · EN: Interface languages. The admin picks the language on the PC; every screen follows it (guests may override it for themselves).
// SlideLink · DE: Oberflächensprachen. Der Admin wählt die Sprache am PC; jeder Bildschirm folgt ihr (Gäste können sie für sich ändern).
'use strict';
/* How it works: the interface is written in English. This layer translates every visible text (text nodes, placeholders,
   titles, alerts, confirms) as it appears, so no template needs changing. User content (presentation titles, notes,
   poll questions, names) is skipped, and slides run in their own frames, so they are never touched.
   A new language = one more entry in I18N_DICT (+ I18N_PATTERNS). */

const I18N_LANGS = { en: 'English', de: 'Deutsch' };

const I18N_DICT = { de: {
  // library, editor, presenter
  'Library': 'Bibliothek', '▦ Library home': '▦ Bibliothek', 'Search': 'Suchen', 'Presentations': 'Präsentationen',
  '📽 Presentations': '📽 Präsentationen', '📄 Docs': '📄 Docs', 'Docs': 'Docs', 'Today': 'Heute', 'Yesterday': 'Gestern',
  '＋ New presentation': '＋ Neue Präsentation', '⬆ Upload presentation': '⬆ Präsentation hochladen',
  '📁 Claude Presentations': '📁 Claude Presentations', '⚙ Linked devices': '⚙ Verknüpfte Geräte', '🔗 Linked devices': '🔗 Verknüpfte Geräte',
  '👥 Group & audience': '👥 Gruppe & Publikum', '▶ PRESENT': '▶ PRÄSENTIEREN', '▶ Present': '▶ Präsentieren',
  '✎ Edit': '✎ Bearbeiten', '📱 Remote': '📱 Fernbedienung', 'Duplicate': 'Duplizieren', 'Delete': 'Löschen',
  'Slides': 'Folien', 'Loading slides…': 'Folien werden geladen…', 'Loading your presentations…': 'Deine Präsentationen werden geladen…',
  'No matches': 'Keine Treffer', 'No presentations yet': 'Noch keine Präsentationen', 'Nothing here yet.': 'Hier ist noch nichts.',
  'No docs yet. Docs saved in “Claude Presentations\\Docs” appear here.': 'Noch keine Docs. Docs, die in „Claude Presentations\\Docs“ gespeichert werden, erscheinen hier.',
  'Document': 'Dokument', 'From claude.ai': 'Von claude.ai', '✦ From claude.ai': '✦ Von claude.ai', 'OPEN': 'OFFEN', '● open': '● offen',
  '● open on all devices': '● auf allen Geräten offen', 'Empty': 'Leer', 'Untitled presentation': 'Unbenannte Präsentation',
  'Name of the new presentation': 'Name der neuen Präsentation', 'Nothing imported': 'Nichts importiert',
  'Present this slide': 'Ab dieser Folie präsentieren', 'Present from this slide': 'Ab dieser Folie präsentieren',
  '◀ Library': '◀ Bibliothek', '+ Add slide': '+ Folie hinzufügen', '+ Slide': '+ Folie', 'New slide': 'Neue Folie',
  'Activity': 'Aktivität', 'Title': 'Titel', 'Content': 'Inhalt', 'Layout': 'Layout', 'Status': 'Status',
  'Title slide': 'Titelfolie', 'Title + content': 'Titel + Inhalt', 'Section header': 'Abschnittstitel', 'Two columns': 'Zwei Spalten',
  'Quote': 'Zitat', 'Image': 'Bild', 'Designed slide (HTML)': 'Gestaltete Folie (HTML)', 'Interactive page (HTML app)': 'Interaktive Seite (HTML-App)',
  'Document (pages)': 'Dokument (Seiten)', 'Draft': 'Entwurf', 'In progress': 'In Arbeit', 'Review': 'Prüfen', 'Done': 'Fertig',
  'Write your text here.': 'Schreib hier deinen Text.', 'Speaker notes (shown on the phone remote)': 'Sprechernotizen (auf der Handy-Fernbedienung sichtbar)',
  'Slide HTML (designed slide, 1920×1080)': 'Folien-HTML (gestaltete Folie, 1920×1080)', 'No slides yet': 'Noch keine Folien', 'No slides': 'Keine Folien',
  'Nothing yet. Edits from you, your phone and Claude show up here.': 'Noch nichts. Änderungen von dir, deinem Handy und Claude erscheinen hier.',
  'Show this slide on the screen': 'Diese Folie auf der Leinwand zeigen', 'Editor': 'Editor', 'Fullscreen': 'Vollbild', '⟳ Resync': '⟳ Neu synchronisieren',
  '← → or clicker · your phone remote · B = black · F = fullscreen': '← → oder Presenter · deine Handy-Fernbedienung · B = schwarz · F = Vollbild',
  'Resync all screens (R)': 'Alle Bildschirme neu synchronisieren (R)', 'Screen is black': 'Bildschirm ist schwarz',
  'SlideLink – Presenting': 'SlideLink – Präsentation', 'SlideLink – Editor': 'SlideLink – Editor',
  // devices, linking
  'Linked devices': 'Verknüpfte Geräte', '+ Link a new device': '+ Neues Gerät verknüpfen', '＋ Link a new device': '＋ Neues Gerät verknüpfen',
  'Link a new device': 'Neues Gerät verknüpfen', 'Linked devices stay connected permanently and reconnect on their own. Only linked devices can see or control your presentations.':
    'Verknüpfte Geräte bleiben dauerhaft verbunden und verbinden sich selbst neu. Nur verknüpfte Geräte können deine Präsentationen sehen oder steuern.',
  'Rename': 'Umbenennen', 'Unlink': 'Entfernen', 'this device': 'dieses Gerät', 'host · always linked': 'PC · immer verknüpft', 'Online': 'Online', 'Offline': 'Offline',
  'Unlink THIS device? You will need a new code from the PC to link it again.': 'DIESES Gerät entfernen? Zum erneuten Verknüpfen brauchst du einen neuen Code vom PC.',
  'Device name': 'Gerätename', 'On the phone, open this address in Chrome (once):': 'Öffne auf dem Handy diese Adresse in Chrome (einmalig):',
  '…then enter this code:': '…und gib dann diesen Code ein:', 'Link code': 'Link-Code', 'Name this device': 'Name dieses Geräts', 'Link device': 'Gerät verknüpfen',
  'My phone': 'Mein Handy', 'My tablet': 'Mein Tablet', 'My laptop': 'Mein Laptop', 'This device was unlinked from the PC.': 'Dieses Gerät wurde vom PC entfernt.',
  'This device was unlinked.': 'Dieses Gerät wurde entfernt.', 'Could not link. Is the PC on the same Wi-Fi?': 'Verknüpfen fehlgeschlagen. Ist der PC im selben WLAN?',
  'You only do this once. On your PC open SlideLink →': 'Das machst du nur einmal. Öffne am PC SlideLink →', ', then type the code shown there.': ', und tippe den dort angezeigten Code ein.',
  'is now linked, permanently.': 'ist jetzt dauerhaft verknüpft.', '🌍 Anywhere access is on': '🌍 Zugriff von überall ist an',
  '🌍 Anywhere access is off (same Wi-Fi only)': '🌍 Zugriff von überall ist aus (nur gleiches WLAN)',
  'Without Tailscale, the phone must be on the same Wi-Fi.': 'Ohne Tailscale muss das Handy im selben WLAN sein.',
  'Tailscale is installed but not signed in. Open Tailscale on this PC and log in.': 'Tailscale ist installiert, aber nicht angemeldet. Öffne Tailscale auf diesem PC und melde dich an.',
  '📱 No phone connected': '📱 Kein Handy verbunden', 'Copy download link': 'Download-Link kopieren', 'Link copied ✓': 'Link kopiert ✓',
  'Scan this with your phone\'s camera to download the': 'Scanne das mit der Handy-Kamera, um die', 'SlideLink app': 'SlideLink-App',
  '. Install it, open it, then link it with a code from here.': ' herunterzuladen. Installieren, öffnen und mit einem Code von hier verknüpfen.',
  '⬇ Get the SlideLink Android app': '⬇ SlideLink-App für Android holen', 'Update for your phone': 'Update für dein Handy', 'Later': 'Später',
  // connection
  'Connecting…': 'Verbinde…', 'Reconnecting…': 'Verbinde neu…', 'Waiting for the SlideLink PC…': 'Warte auf den SlideLink-PC…',
  'Can’t reach SlideLink on your PC – is it running? Retrying…': 'SlideLink auf dem PC ist nicht erreichbar – läuft es? Neuer Versuch…',
  'Not connected': 'Nicht verbunden', 'Bad response': 'Ungültige Antwort',
  // remote
  'SlideLink Remote': 'SlideLink Fernbedienung', 'Next': 'Weiter', 'Next ▶': 'Weiter ▶', 'Reveal ▶': 'Zeigen ▶', 'Blackout': 'Schwarz',
  'End of presentation': 'Ende der Präsentation', 'No speaker notes for this slide.': 'Keine Sprechernotizen für diese Folie.',
  'Decks': 'Decks', 'Devices': 'Geräte', 'Live': 'Live', 'On PC': 'Auf PC', 'Close': 'Schließen', 'Opening': 'Öffne', 'Closing': 'Schließe',
  '○ Still': '○ Standbild', '● Live': '● Live', 'Interactive page': 'Interaktive Seite', 'Document · Next scrolls': 'Dokument · Weiter scrollt',
  '● Presentation on PC': '● Präsentation auf PC', '● SlideLink open on PC': '● SlideLink auf PC offen', '○ No SlideLink window on PC': '○ Kein SlideLink-Fenster auf PC',
  'All screens resynced': 'Alle Bildschirme neu synchronisiert', 'Showing the presentation on the PC (fullscreen)…': 'Präsentation wird auf dem PC gezeigt (Vollbild)…',
  'Closing the window on the PC…': 'Fenster auf dem PC wird geschlossen…', 'Opening SlideLink on the PC (fullscreen)…': 'SlideLink wird auf dem PC geöffnet…',
  'Switch all screens to': 'Alle Bildschirme umschalten auf', '🎤 Me now': '🎤 Jetzt ich', '🎤 Nobody selected': '🎤 Niemand ausgewählt',
  // live: audience, polls, group
  'Live: audience, polls, presenters': 'Live: Publikum, Umfragen, Vortragende', '👥 Audience': '👥 Publikum', '👥 Audience link': '👥 Publikums-Link',
  '📺 Show QR on screen': '📺 QR auf Leinwand zeigen', 'Hide QR on screen': 'QR auf Leinwand ausblenden', 'Copy link': 'Link kopieren', 'Copied ✓': 'Kopiert ✓',
  'New link': 'Neuer Link', 'New link (the old one stops working)': 'Neuer Link (der alte funktioniert dann nicht mehr)', 'Audience link copied': 'Publikums-Link kopiert',
  'Make a new audience link? Everyone watching with the old link is disconnected.': 'Neuen Publikums-Link erstellen? Wer mit dem alten Link zusieht, wird getrennt.',
  'New audience link? Everyone watching with the old link is disconnected.': 'Neuer Publikums-Link? Wer mit dem alten Link zusieht, wird getrennt.',
  '📊 New poll': '📊 Neue Umfrage', 'New poll': 'Neue Umfrage', 'Start poll': 'Umfrage starten', 'End voting': 'Abstimmung beenden',
  '📺 Show results on screen': '📺 Ergebnis auf Leinwand zeigen', 'Hide results on screen': 'Ergebnis auf Leinwand ausblenden',
  'Question, e.g. Which topic next?': 'Frage, z. B. Welches Thema als Nächstes?', 'Enter a question and at least 2 answers.': 'Gib eine Frage und mindestens 2 Antworten ein.',
  'Poll started: the audience can vote now': 'Umfrage gestartet: Das Publikum kann jetzt abstimmen', 'Poll closed': 'Umfrage beendet',
  'Tap an answer to vote': 'Tippe auf eine Antwort, um abzustimmen', 'Could not vote. Try again.': 'Abstimmen fehlgeschlagen. Bitte erneut versuchen.',
  '🎤 Who is presenting?': '🎤 Wer präsentiert?', 'Nobody': 'Niemand', '🎤 Present in group': '🎤 In der Gruppe präsentieren', '🎤 Group session': '🎤 Gruppen-Sitzung',
  'Co-presenters join with a QR code, in the browser or the app. They control the talk only and are disconnected when the session ends.':
    'Mitvortragende treten per QR-Code bei, im Browser oder in der App. Sie steuern nur den Vortrag und werden getrennt, wenn die Sitzung endet.',
  'Start group session': 'Gruppen-Sitzung starten', 'End group session': 'Gruppen-Sitzung beenden', 'Nobody yet.': 'Noch niemand.', 'remove': 'entfernen',
  'End the group session? Everyone in the group is disconnected.': 'Gruppen-Sitzung beenden? Alle in der Gruppe werden getrennt.', 'Group link copied': 'Gruppen-Link kopiert',
  'Follow live on your phone': 'Live auf dem Handy mitverfolgen', 'Scan the code to see the slides and vote in polls.': 'Scanne den Code, um die Folien zu sehen und abzustimmen.',
  'Co-presenters: join here': 'Mitvortragende: hier beitreten', 'Scan to control the slides together (browser or app).': 'Scannen, um die Folien gemeinsam zu steuern (Browser oder App).',
  'Present together': 'Gemeinsam präsentieren', 'Your name': 'Dein Name', 'e.g. Anna': 'z. B. Anna', 'Continue in the browser': 'Im Browser weiter', 'Join': 'Beitreten',
  'You were invited to co-present. You can move through the slides, start polls and see the notes, but not change the presentation.':
    'Du wurdest eingeladen mitzupräsentieren. Du kannst durch die Folien gehen, Umfragen starten und die Notizen sehen, aber die Präsentation nicht ändern.',
  '📱 Open in the SlideLink app': '📱 In der SlideLink-App öffnen', 'Android: opens the app. Not installed? The download starts instead.': 'Android: öffnet die App. Nicht installiert? Dann startet der Download.',
  'The SlideLink app is only available for Android. On iPhone everything works right here in the browser.': 'Die SlideLink-App gibt es nur für Android. Auf dem iPhone funktioniert alles direkt hier im Browser.',
  'Could not join.': 'Beitreten fehlgeschlagen.', 'The group session has ended': 'Die Gruppen-Sitzung ist beendet',
  'Thanks for presenting! You are no longer connected to this presentation.': 'Danke fürs Präsentieren! Du bist nicht mehr mit dieser Präsentation verbunden.',
  'Back to my SlideLink': 'Zurück zu meinem SlideLink', 'This group session has ended or you were removed. Join again if it is still running.': 'Diese Gruppen-Sitzung ist beendet oder du wurdest entfernt. Tritt erneut bei, falls sie noch läuft.',
  'Cannot reach the presentation right now.': 'Die Präsentation ist gerade nicht erreichbar.', 'Please enter your name.': 'Bitte gib deinen Namen ein.',
  'This group session has ended or the link is wrong.': 'Diese Gruppen-Sitzung ist beendet oder der Link ist falsch.',
  // audience page
  'SlideLink Live': 'SlideLink Live', 'Connecting to the presentation…': 'Verbinde mit der Präsentation…', '● LIVE': '● LIVE',
  'This audience link was replaced. Ask the presenter for the new one.': 'Dieser Publikums-Link wurde ersetzt. Frag die vortragende Person nach dem neuen.',
  'This audience link is not valid (any more), or the presentation has ended.': 'Dieser Publikums-Link ist (nicht mehr) gültig, oder die Präsentation ist vorbei.',
  'This link is incomplete. Scan the QR code shown by the presenter.': 'Dieser Link ist unvollständig. Scanne den QR-Code der vortragenden Person.',
  'This audience link is no longer valid.': 'Dieser Publikums-Link ist nicht mehr gültig.', 'This poll is closed.': 'Diese Umfrage ist beendet.',
  'The phone is a remote control - edit presentations on the PC.': 'Das Handy ist eine Fernbedienung – Präsentationen am PC bearbeiten.',
  'Group members control the presentation only.': 'Gruppenmitglieder steuern nur die Präsentation.', '🌐 Language': '🌐 Sprache',
  'Same as the presenter': 'Wie die vortragende Person', 'My language': 'Meine Sprache',
  '🌐 My language': '🌐 Meine Sprache', 'Language': 'Sprache', 'for SlideLink on all screens': 'für SlideLink auf allen Bildschirmen',
  'Language for SlideLink on all screens': 'Sprache für SlideLink auf allen Bildschirmen',
  'PowerPoint, claude.ai deck (.zip), HTML deck, images': 'PowerPoint, claude.ai-Deck (.zip), HTML-Deck, Bilder',
  'Everything Claude saves in this folder appears here automatically': 'Alles, was Claude in diesem Ordner speichert, erscheint hier automatisch',
  "Play the slide's animations in the preview (uses more battery)": 'Animationen der Folie in der Vorschau abspielen (braucht mehr Akku)',
  'Preview: still picture or live animations (live uses more battery). Tap to switch.': 'Vorschau: Standbild oder Live-Animationen (live braucht mehr Akku). Tippen zum Wechseln.',
  'Show the presentation on the PC / close it': 'Präsentation auf dem PC zeigen / schließen', 'Resync all screens': 'Alle Bildschirme neu synchronisieren',
  'Tap: start / pause · hold: reset': 'Tippen: Start / Pause · halten: zurücksetzen', 'Switch presentation': 'Präsentation wechseln', 'Back': 'Zurück',
  'Audience, polls, presenters': 'Publikum, Umfragen, Vortragende',
  'Update': 'Aktualisieren', 'Appearance': 'Anzeige', '🌓 Brightness': '🌓 Helligkeit', '☀️ Light': '☀️ Hell', '🌙 Dark': '🌙 Dunkel', '💻 System': '💻 System',
  'For this device only. Slides and the presentation screen keep their own colors.': 'Nur für dieses Gerät. Folien und die Leinwand behalten ihre eigenen Farben.',
  '⚙ Settings (language, brightness)': '⚙ Einstellungen (Sprache, Helligkeit)',
  '＋ Add presentation': '＋ Präsentation hinzufügen',
  'PowerPoint, claude.ai deck (.zip), HTML, PDF doc, images – or drag files here': 'PowerPoint, claude.ai-Deck (.zip), HTML, PDF-Doc, Bilder – oder Dateien hierher ziehen',
  'Slide': 'Folie', 'More': 'Mehr', '✎ Edit': '✎ Bearbeiten', '⧉ Duplicate': '⧉ Duplizieren', '🗑 Delete': '🗑 Löschen', 'Duplicated': 'Dupliziert',
  '✎ Edit presentation': '✎ Präsentation bearbeiten', 'Speaker notes are shown on the phone remote while you present.': 'Sprechernotizen erscheinen beim Präsentieren auf der Handy-Fernbedienung.',
  'Speaker notes for this slide': 'Sprechernotizen für diese Folie', 'Cancel': 'Abbrechen', 'Save': 'Speichern', 'Saved': 'Gespeichert',
  'Settings': 'Einstellungen', '⚙ Settings': '⚙ Einstellungen', 'General': 'Allgemein', '🌐 Language': '🌐 Sprache',
  'SlideLink on all screens: PC, phones, co-presenters and audience. Guests can still choose their own.':
    'SlideLink auf allen Bildschirmen: PC, Handys, Mitvortragende und Publikum. Gäste können trotzdem ihre eigene wählen.',
  'Sync problem': 'Sync-Problem', 'Auto-sync on': 'Auto-Sync an', 'last': 'zuletzt', 'added': 'hinzugefügt', 'updated': 'aktualisiert', 'unchanged': 'unverändert',
} };

// texts with numbers or names: [regex on the English text, German replacement]
const I18N_PATTERNS = { de: [
  [/^Answer (\d)$/, "Antwort $1"], [/^Answer (\d) \(optional\)$/, "Antwort $1 (optional)"],
  [/^1 items in the folder$/, '1 Eintrag im Ordner'], [/^(\d+) items in the folder$/, '$1 Einträge im Ordner'],
  [/^1 slide$/, '1 Folie'], [/^(\d+) slides$/, '$1 Folien'], [/^Slide (\d+) \/ (\d+)$/, 'Folie $1 / $2'],
  [/^(\d+) presentations · newest first$/, '$1 Präsentationen · neueste zuerst'], [/^(\d+) docs · newest first$/, '$1 Docs · neueste zuerst'],
  [/^just now$/, 'gerade eben'], [/^(\d+)s ago$/, 'vor $1 s'], [/^(\d+)m ago$/, 'vor $1 Min.'], [/^(\d+)h ago$/, 'vor $1 Std.'],
  [/^edited (.+)$/, 'bearbeitet $1'], [/^(\d+) watching$/, '$1 schauen zu'], [/^(\d+) joined$/, '$1 beigetreten'],
  [/^(\d+) watching\. Viewers scan the QR code and follow the slides live, watch only\.$/, '$1 schauen zu. Zuschauer scannen den QR-Code und folgen den Folien live, nur ansehen.'],
  [/^Viewers scan this and see the current slide live on their phone and can vote in polls\.\s+Watch only: no control, no notes\. (\d+) watching now\.$/,
    'Zuschauer scannen das, sehen die aktuelle Folie live auf dem Handy und können abstimmen. Nur ansehen: keine Steuerung, keine Notizen. Gerade $1 Zuschauer.'],
  [/^(\d+) votes? · open$/, (m, n) => `${n} Stimme${n === '1' ? '' : 'n'} · offen`], [/^(\d+) votes? · closed$/, (m, n) => `${n} Stimme${n === '1' ? '' : 'n'} · beendet`],
  [/^(\d+) votes? · voting is open$/, (m, n) => `${n} Stimme${n === '1' ? '' : 'n'} · Abstimmung läuft`],
  [/^(\d+) votes? · closed$/, (m, n) => `${n} Stimme${n === '1' ? '' : 'n'} · beendet`],
  [/^Thanks for voting! (\d+) votes? · tap to change$/, (m, n) => `Danke fürs Abstimmen! ${n} Stimme${n === '1' ? '' : 'n'} · tippen zum Ändern`],
  [/^(\d+) joined · ends by itself in (\d+)h (\d+)min$/, '$1 beigetreten · endet von selbst in $2 Std. $3 Min.'],
  [/^is presenting$/, 'präsentiert'], [/^🎤 (.+) is presenting$/, '🎤 $1 präsentiert'],
  [/^SlideLink ([\d.]+) is ready \((.+) has ([\d.]+)\)\. Open SlideLink on the phone and tap$/, 'SlideLink $1 ist bereit ($2 hat $3). Öffne SlideLink auf dem Handy und tippe auf'],
  [/^SlideLink ([\d.]+) is ready\. Open SlideLink on the phone and tap$/, 'SlideLink $1 ist bereit. Öffne SlideLink auf dem Handy und tippe auf'],
  [/^Valid for (\d+:\d+) · works once$/, 'Gültig für $1 · nur einmal verwendbar'],
  [/^Switch all screens to “(.+)”\?$/, 'Alle Bildschirme auf „$1“ umschalten?'],
  [/^Online · (.+)$/, (m, rest) => 'Online · ' + rest.replace(/Remote control/g, 'Fernbedienung').replace(/Library/g, 'Bibliothek').replace(/Presenting/g, 'Präsentation')
      .replace(/Editor/g, 'Editor').replace(/via Wi-Fi/g, 'über WLAN').replace(/via internet \(HTTPS\)/g, 'über Internet (HTTPS)').replace(/via Tailscale/g, 'über Tailscale')],
  [/^Offline · last seen (.+)$/, 'Offline · zuletzt gesehen $1'], [/^Linked (.+)$/, 'Verknüpft $1'],
  [/^Delete "(.+)" with all (\d+) slides\? This cannot be undone\.$/, '„$1“ mit allen $2 Folien löschen? Das lässt sich nicht rückgängig machen.'],
  [/^(\d+) files? imported$/, '$1 Datei(en) importiert'], [/^● Auto-sync on · (\d+) items? in the folder(.*)$/, (m, n, rest) => `● Auto-Sync an · ${n} im Ordner` + rest.replace(' · last: ', ' · zuletzt: ').replace(/ updated$/, ' aktualisiert').replace(/ added$/, ' hinzugefügt').replace(/ unchanged$/, ' unverändert')],
] };

// these hold the user's own content: never translated
const I18N_SKIP = '#notes, .rm-decktext b, .lib-card-t, .lib-item-t > span, .hero-info h1, #rTitle, #title, .rm-deck-t, .lv-poll h3, .lo-poll h2, ' +
  '.pbar-t span, .lv-opt > span:not(.n), .grp-m, .s-title, .s-body, .slide, textarea, input, [data-noi18n], #lvPoll h4, .lo-speaker, #crewNow b, .dev-name';

const I18N = (() => {
  const orig = new WeakMap();                 // node -> {src (English), out (shown)}
  let lang = 'en';
  function pick(serverLang) {                 // own choice > admin's language > browser language
    const own = localStorage.getItem('sl-lang-own');
    if (own && I18N_LANGS[own]) return own;
    if (serverLang && I18N_LANGS[serverLang]) return serverLang;
    const last = localStorage.getItem('sl-lang-last');
    if (last && I18N_LANGS[last]) return last;
    const nav = (navigator.language || 'en').slice(0, 2);
    return I18N_LANGS[nav] ? nav : 'en';
  }
  function t(text) {
    if (lang === 'en' || !text) return text;
    const dict = I18N_DICT[lang] || {}, trimmed = text.trim();
    if (!trimmed) return text;
    const key = trimmed.replace(/\s+/g, ' ');          // texts wrapped over several lines in the code
    let out = dict[key];
    if (out === undefined) {
      for (const [re, rep] of I18N_PATTERNS[lang] || []) {
        if (re.test(key)) { out = key.replace(re, rep); break; }
      }
    }
    if (out === undefined) return text;
    return text.replace(trimmed, out);        // keep the surrounding spaces
  }
  function translateNode(n) {
    if (n.nodeType === 3) {
      const p = n.parentElement;
      if (!p || p.closest(I18N_SKIP) || /^(SCRIPT|STYLE)$/.test(p.tagName)) return;
      const rec = orig.get(n);
      const src = rec && n.nodeValue === rec.out ? rec.src : n.nodeValue;
      const out = t(src);
      if (out !== n.nodeValue) n.nodeValue = out;
      orig.set(n, { src, out });
      return;
    }
    if (n.nodeType !== 1 || /^(SCRIPT|STYLE|IFRAME|SVG|svg)$/.test(n.tagName)) return;
    for (const a of ['placeholder', 'title', 'aria-label']) {
      if (!n.hasAttribute(a) || n.closest('[data-noi18n]')) continue;
      const key = '__i18n_' + a, v = n.getAttribute(a);
      const src = n[key] && v === n[key].out ? n[key].src : v;
      const out = t(src);
      if (out !== v) n.setAttribute(a, out);
      n[key] = { src, out };
    }
    for (const c of n.childNodes) translateNode(c);
  }
  function translateAll() {
    translateNode(document.body);
    const ti = document.querySelector('title');
    if (ti) translateNode(ti.firstChild || ti);
    document.documentElement.lang = lang;
  }
  let busy = false;
  const mo = new MutationObserver(muts => {
    if (busy || lang === 'en' && !document.body.__i18nWasOther) return;
    busy = true;
    for (const m of muts) {
      if (m.type === 'characterData') translateNode(m.target);
      else m.addedNodes.forEach(translateNode);
      if (m.type === 'attributes') translateNode(m.target);
    }
    busy = false;
  });
  function start() {
    mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['placeholder', 'title', 'aria-label'] });
  }
  // alerts and questions in the chosen language too
  const _alert = window.alert.bind(window), _confirm = window.confirm.bind(window), _prompt = window.prompt.bind(window);
  window.alert = m => _alert(t(String(m)));
  window.confirm = m => _confirm(t(String(m)));
  window.prompt = (m, d) => _prompt(t(String(m)), d === undefined ? d : t(String(d)));
  return {
    get lang() { return lang; },
    locale: () => (lang === 'de' ? 'de-DE' : 'en-GB'),
    t,
    /** called with the admin's language from every state update */
    fromServer(serverLang) {
      if (serverLang && I18N_LANGS[serverLang]) localStorage.setItem('sl-lang-last', serverLang);
      this.apply(pick(serverLang));
      if (window.SlideLinkApp && SlideLinkApp.setLang) try { SlideLinkApp.setLang(lang); } catch (_) {}
    },
    apply(l) {
      if (l === lang && document.body.__i18nDone) return;
      if (lang !== 'en') document.body.__i18nWasOther = true;
      lang = l; busy = true; translateAll(); busy = false;
      document.body.__i18nDone = true;
    },
    /** a guest's own choice ('' = follow the presenter) */
    setOwn(l, serverLang) {
      if (l) localStorage.setItem('sl-lang-own', l); else localStorage.removeItem('sl-lang-own');
      this.apply(pick(serverLang));
    },
    own: () => localStorage.getItem('sl-lang-own') || '',
    init() { start(); this.apply(pick(null)); },
  };
})();
document.addEventListener('DOMContentLoaded', () => I18N.init());
