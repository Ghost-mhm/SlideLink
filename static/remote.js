// SlideLink · EN: Phone remote: preview, build dots, notes, timer, slide and presentation lists, PC window button.
// SlideLink · DE: Handy-Fernbedienung: Vorschau, Build-Punkte, Notizen, Timer, Folien- und Präsentationslisten, Knopf für das PC-Fenster.
'use strict';
/* SlideLink phone remote: a remote display + presentation manager for the desktop app.
   Shows the live slide, skips, blacks out, jumps to slides and switches presentations.
   No editing here - that happens on the PC. */

const timer = JSON.parse(localStorage.getItem('sl-timer') || '{"start":0,"acc":0,"run":false}');
// EN: Timer as mm:ss.
// DE: Timer als mm:ss.
function timerText() {
  const ms = timer.acc + (timer.run ? Date.now() - timer.start : 0);
  const t = Math.floor(ms / 1000);
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
}
// EN: Keeps the timer across reloads.
// DE: Behält den Timer über Neuladen hinweg.
function saveTimer() { localStorage.setItem('sl-timer', JSON.stringify(timer)); }
// EN: Starts the timer (on the first Next).
// DE: Startet den Timer (beim ersten Weiter).
function startTimer() { if (!timer.run) { timer.run = true; timer.start = Date.now(); saveTimer(); } }

let sheet = null;            // 'slides' | 'decks' | null
let remoteDecks = null;      // library listing for the presentations sheet
let remoteTab = localStorage.getItem('sl-remote-tab') || 'deck';
let remoteLive = !!localStorage.getItem('sl-remote-live');
let myName = '';                                              // this phone's name (for "I'm presenting")   // preview: still picture (default, battery) or live
let lastPresenting = null;   // 'deck' | 'doc'

// EN: Builds the remote screen and its buttons, swipes and keys.
// DE: Baut die Fernbedienung mit Knöpfen, Wischgesten und Tasten.
function initRemote() {
  document.title = 'SlideLink Remote';
  HS_LOWPOWER = true;                    // small preview: ~12 fps, endless loops paused (battery)
  document.body.classList.add('is-remote');
  $('#app').innerHTML = `
  <div class="rm">
    <header class="rm-top">
      <button class="rm-deck" id="deckBtn" aria-label="Switch presentation">
        <span class="rm-deck-t" id="rTitle">…</span><span class="rm-chev">▾</span>
      </button>
      <button class="rm-timer" id="timer" title="Tap: start / pause · hold: reset">00:00</button>
      <button class="rm-sync" id="syncBtn" aria-label="Resync all screens" title="Resync all screens">⟳</button>
    </header>
    <div class="claude-bar" id="claudeBar"></div>
    <div class="rm-now" id="now">
      <div id="nowSlide"></div>
      <button class="rm-livebtn" id="liveBtn" title="Preview: still picture or live animations (live uses more battery). Tap to switch.">○ Still</button>
      <div class="blk" id="nowBlk">Screen is black</div>
    </div>
    <div class="rm-info">
      <span class="rm-count" id="rCount"></span>
      <span class="rm-steps" id="rSteps"></span>
      <span class="rm-pc" id="rInfo"></span>
    </div>
    <div class="rm-crew" id="crew">
      <span id="crewNow"></span>
      <button id="meBtn">🎤 Me now</button>
    </div>
    <div class="rm-next">
      <div class="rm-nx"><div class="rm-lbl">Next</div><div id="nx"></div></div>
      <div class="rm-notes" id="notes"></div>
    </div>
    <div class="rm-actions">
      <button id="slidesBtn"><i>▦</i><span>Slides</span></button>
      <button id="decksBtn"><i>📚</i><span>Decks</span></button>
      <button id="pcBtn" title="Show the presentation on the PC / close it"><i>🖥</i><span>On PC</span></button>
      <button id="liveBtnSheet" aria-label="Audience, polls, presenters"><i>👥</i><span>Live</span></button>
      <button id="devBtn" aria-label="Linked devices"><i>🔗</i><span>Devices</span></button>
    </div>
    <div class="rm-pad">
      <button id="prevBtn" aria-label="Back">◀</button>
      <button id="blkBtn" class="rm-blk"><span>⬛</span><small>Blackout</small></button>
      <button id="nextBtn" class="next">Next ▶</button>
    </div>
    <div class="rm-sheet" id="sheet" aria-hidden="true">
      <div class="rm-sheet-in">
        <div class="rm-sheet-head"><b id="sheetTitle"></b><button id="sheetClose" aria-label="Close">✕</button></div>
        <div class="rm-sheet-body" id="sheetBody"></div>
      </div>
    </div>
  </div>`;

  const tryWake = () => { if (navigator.wakeLock) navigator.wakeLock.request('screen').catch(() => {}); };
  $('#nextBtn').onclick = () => { startTimer(); tryWake(); go('next'); };
  $('#prevBtn').onclick = () => { tryWake(); go('prev'); };
  $('#blkBtn').onclick = () => go('blackout');
  $('#slidesBtn').onclick = () => openSheet('slides');
  $('#decksBtn').onclick = $('#deckBtn').onclick = () => openSheet('decks');
  $('#devBtn').onclick = openDevices;
  $('#pcBtn').onclick = showOnPC;
  $('#liveBtnSheet').onclick = () => openSheet('live');
  if (MODE === 'group') {                     // co-presenter: control only (no decks, devices or PC windows)
    ['decksBtn', 'devBtn', 'pcBtn'].forEach(id => { $('#' + id).hidden = true; });
    $('.rm-actions').classList.add('grp');
    $('#deckBtn').onclick = null; $('#deckBtn').classList.add('static');
  }
  $('#meBtn').onclick = () => api({ op: 'speaker_me' });
  fetch('/api/whoami', { headers: authHeaders() }).then(r => r.json()).then(j => { myName = j.device ? j.device.name : ''; }).catch(() => {});
  $('#liveBtn').onclick = e => {
    e.stopPropagation();
    remoteLive = !remoteLive; localStorage.setItem('sl-remote-live', remoteLive ? '1' : '');
    renderRemote();
  };

  $('#syncBtn').onclick = async () => {
    const b = $('#syncBtn');
    b.classList.add('spin');
    if (navigator.vibrate) navigator.vibrate(25);
    await resync(true);
    setTimeout(() => b.classList.remove('spin'), 600);
    toastOk('All screens resynced');
  };
  $('#sheetClose').onclick = closeSheet;
  $('#sheet').onclick = e => { if (e.target.id === 'sheet') closeSheet(); };
  $('#sheetBody').onclick = onSheetClick;

  // timer: tap = start/pause, hold = reset
  let holdT = null;
  const tm = $('#timer');
  tm.onpointerdown = () => { holdT = setTimeout(() => { holdT = 'reset'; timer.run = false; timer.acc = 0; saveTimer(); tm.textContent = timerText(); if (navigator.vibrate) navigator.vibrate(40); }, 700); };
  tm.onpointerup = () => {
    if (holdT === 'reset') { holdT = null; return; }
    clearTimeout(holdT); holdT = null;
    if (timer.run) { timer.acc += Date.now() - timer.start; timer.run = false; saveTimer(); } else startTimer();
  };

  // swipe on the slide: left = next, right = back
  let x0 = null;
  const now = $('#now');
  now.addEventListener('touchstart', e => { x0 = e.touches[0].clientX; }, { passive: true });
  now.addEventListener('touchend', e => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0; x0 = null;
    if (dx < -50) { startTimer(); go('next'); } else if (dx > 50) go('prev');
  });
  document.addEventListener('keydown', navKeys);   // Bluetooth clickers / keyboards
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tryWake(); });
  setInterval(() => { $('#timer').textContent = timerText(); S && renderClaudeBar(); }, 1000);
}

/* One button, by state: presentation not on the PC → "Show on PC" (borderless fullscreen);
   presentation open on the PC → "Close window on PC" (SlideLink keeps running, show it again any time). */
let pcPending = null;   // {want: true|false, until} while the PC opens/closes the window
// EN: One button by state: show the presentation on the PC, or close its window.
// DE: Ein Knopf je nach Zustand: Präsentation auf dem PC zeigen oder ihr Fenster schließen.
async function showOnPC() {
  const pw = (S && S.pc_windows) || {};
  pcPending = { want: !pw.present, until: Date.now() + 12000 };
  renderPcBtn();
  if (pw.present) {
    const j = await api({ op: 'close_on_pc' });
    if (j.ok) toastOk('Closing the window on the PC…');
  } else {
    const j = await api({ op: 'show_on_pc' });
    if (j.ok) toastOk('Showing the presentation on the PC (fullscreen)…');
  }
}
/* Android app: keep the screen on only while presenting (presentation on the PC or timer running) */
// EN: Android app: screen stays on only while presenting.
// DE: Android-App: Bildschirm bleibt nur beim Präsentieren an.
function presentingChanged() {
  const now = !!((S && S.pc_windows && S.pc_windows.present) || timer.run);
  if (now === lastPresenting) return;
  lastPresenting = now;
  if (window.SlideLinkApp && SlideLinkApp.setPresenting) SlideLinkApp.setPresenting(now);
}
setInterval(() => typeof S !== 'undefined' && S && presentingChanged(), 2000);   // safe even if app.js failed to load
// EN: Text of the PC button (with 'Opening…'/'Closing…' right after a tap).
// DE: Text des PC-Knopfs (direkt nach dem Tippen mit 'Opening…'/'Closing…').
function renderPcBtn() {
  const b = $('#pcBtn');
  if (!b || !S) return;
  const open = !!(S.pc_windows || {}).present;
  if (pcPending && (open === pcPending.want || Date.now() > pcPending.until)) pcPending = null;
  const set = (icon, text, closing) => {
    const html = `<i>${icon}</i><span>${text}</span>`;
    if (b._h !== html) { b.innerHTML = html; b._h = html; }
    b.classList.toggle('closing', closing);
  };
  if (pcPending) return set('⏳', pcPending.want ? 'Opening' : 'Closing', !pcPending.want);
  set(open ? '✕' : '🖥', open ? 'Close' : 'On PC', open);
}
setInterval(() => pcPending && typeof S !== 'undefined' && renderPcBtn(), 1000);
// EN: Short green confirmation message.
// DE: Kurze grüne Bestätigungsmeldung.
function toastOk(msg) {
  const c = $('#conn');
  c.textContent = msg; c.classList.remove('ok'); c.classList.add('good');
  clearTimeout(toastOk.t); toastOk.t = setTimeout(() => { c.classList.add('ok'); c.classList.remove('good'); }, 2500);
}

/* ---------- sheets ---------- */
// EN: Opens the slides or presentations sheet.
// DE: Öffnet die Folien- oder Präsentationsliste.
function openSheet(kind) {
  sheet = kind;
  $('#sheet').classList.add('on');
  $('#sheet').setAttribute('aria-hidden', 'false');
  if (kind === 'decks') loadRemoteDecks();
  renderSheet();
}
// EN: Closes the sheet.
// DE: Schließt die Liste.
function closeSheet() { sheet = null; $('#sheet').classList.remove('on'); $('#sheet').setAttribute('aria-hidden', 'true'); }
// EN: Loads the presentations for the sheet.
// DE: Lädt die Präsentationen für die Liste.
async function loadRemoteDecks() {
  const r = await fetch('/api/library', { headers: authHeaders(), cache: 'no-store' }).catch(() => null);
  if (r && r.ok) { remoteDecks = await r.json(); renderSheet(); }
}
// EN: Taps in the sheet: tab, slide, presentation.
// DE: Tippen in der Liste: Reiter, Folie, Präsentation.
async function onSheetClick(e) {
  const ol = e.target.closest('[data-ownlang]');
  if (ol) { I18N.setOwn(ol.dataset.ownlang, S && S.lang); return renderSheet(); }
  const act = e.target.closest('[data-live]');
  if (act) return liveAction(act.dataset.live, act);
  const tb = e.target.closest('[data-rtab]');
  if (tb) { remoteTab = tb.dataset.rtab; localStorage.setItem('sl-remote-tab', remoteTab); renderSheet(); return; }
  const sl = e.target.closest('[data-goto]');
  if (sl) { go('goto', { index: +sl.dataset.goto }); closeSheet(); return; }
  const dk = e.target.closest('[data-deck]');
  if (dk) {
    if (dk.dataset.deck === S.deck.id) { closeSheet(); return; }
    if (!confirm(`Switch all screens to “${dk.dataset.title}”?`)) return;
    const j = await api({ op: 'open_deck', deck_id: dk.dataset.deck });
    if (j.ok) { closeSheet(); timer.run = false; timer.acc = 0; saveTimer(); }
  }
}
// EN: Draws the slides grid or the presentations list (two tabs, by day).
// DE: Zeichnet das Folienraster oder die Präsentationsliste (zwei Reiter, nach Tagen).
// EN: Buttons in the Live sheet: audience link, polls, who presents.
// DE: Knöpfe in der Live-Liste: Publikums-Link, Umfragen, wer präsentiert.
async function liveAction(kind, el) {
  if (kind === 'qr') return api({ op: 'join_qr', on: !(S.audience && S.audience.qr) });
  if (kind === 'copy') { navigator.clipboard.writeText(audienceUrl()).then(() => toastOk('Audience link copied')); return; }
  if (kind === 'newlink') { if (confirm('New audience link? Everyone watching with the old link is disconnected.')) api({ op: 'audience_new' }); return; }
  if (kind === 'start') {
    const q = $('#pollQ').value.trim(), opts = [...document.querySelectorAll('.poll-opt')].map(i => i.value.trim()).filter(Boolean);
    if (!q || opts.length < 2) return alert('Enter a question and at least 2 answers.');
    const j = await api({ op: 'poll_start', question: q, options: opts });
    if (j.ok) toastOk('Poll started: the audience can vote now');
    return;
  }
  if (kind === 'show') return api({ op: 'poll_show', on: !(S.poll && S.poll.show) });
  if (kind === 'end') return api({ op: 'poll_end' });
  if (kind === 'clear') return api({ op: 'poll_clear' });
  if (kind === 'speaker') return api({ op: 'speaker_set', name: el.dataset.name || '' });
}

// EN: The Live sheet. The poll form is drawn only once, so typing is never interrupted by live updates.
// DE: Die Live-Liste. Das Umfrageformular wird nur einmal gezeichnet, damit Live-Updates das Tippen nie stören.
function renderLiveSheet() {
  const body = $('#sheetBody');
  if (!body.querySelector('#lvAud')) {
    body.innerHTML = `${MODE === 'group' ? '' : '<div class="lv-sec grp-sec" id="lvGroup"></div>'}<div class="lv-sec" id="lvAud"></div><div class="lv-sec" id="lvPoll"></div><div class="lv-sec" id="lvCrew"></div>`;
    body._h = null;
  }
  if ($('#lvGroup')) setHTML($('#lvGroup'), groupAdminHTML());
  const a = S.audience || {};
  setHTML($('#lvAud'), `<h4>👥 Audience <small class="beta">BETA</small></h4>
    <p class="muted">${a.count || 0} watching. Viewers scan the QR code and follow the slides live, watch only.</p>
    <div class="lv-row"><button data-live="qr" class="${a.qr ? 'on' : ''}">${a.qr ? 'Hide QR on screen' : '📺 Show QR on screen'}</button>
      <button data-live="copy">Copy link</button></div>
    ${MODE === 'group' ? '' : '<button class="link" data-live="newlink">New link</button>'}`);
  const p = S.poll, pollBox = $('#lvPoll');
  if (!p) {
    if (!pollBox.querySelector('#pollQ')) {
      pollBox.innerHTML = `<h4>📊 New poll</h4>
        <input id="pollQ" maxlength="200" placeholder="Question, e.g. Which topic next?">
        ${[1, 2, 3, 4].map(n => `<input class="poll-opt" maxlength="80" placeholder="Answer ${n}${n > 2 ? ' (optional)' : ''}">`).join('')}
        <button class="primary" data-live="start" style="width:100%;margin-top:6px">Start poll</button>`;
      pollBox._h = null;
    }
  } else {
    setHTML(pollBox, `<h4>📊 ${esc(p.question)}</h4>${pollBarsHTML(p)}
      <p class="muted">${p.total} vote${p.total === 1 ? '' : 's'} · ${p.open ? 'open' : 'closed'}</p>
      <div class="lv-row"><button data-live="show" class="${p.show ? 'on' : ''}">${p.show ? 'Hide results on screen' : '📺 Show results on screen'}</button>
        ${p.open ? '<button data-live="end">End voting</button>' : '<button data-live="clear">New poll</button>'}</div>`);
  }
  if (MODE === 'group') {
    let lg = $('#lvLang');
    if (!lg) { lg = document.createElement('div'); lg.className = 'lv-sec'; lg.id = 'lvLang'; $('#sheetBody').appendChild(lg); }
    setHTML(lg, `<h4>🌐 My language</h4><div class="lv-names">${[['', 'Same as the presenter'], ...Object.entries(I18N_LANGS)].map(([k, v]) =>
      `<button data-ownlang="${k}" class="${I18N.own() === k ? 'on' : ''}" ${k ? 'data-noi18n' : ''}>${v}</button>`).join('')}</div>`);
  }
  const names = [...new Set(['PC', ...(S.linked || []).filter(d => d.type !== 'host').map(d => d.name),
    ...((S.group && (S.group.names || (S.group.members || []).map(m => m.name))) || [])])];
  setHTML($('#lvCrew'), `<h4>🎤 Who is presenting? <small class="beta">BETA</small></h4>
    <div class="lv-names">${names.map(n => `<button data-live="speaker" data-name="${esc(n)}" class="${S.speaker && S.speaker.name === n ? 'on' : ''}">${esc(n)}</button>`).join('')}
    <button data-live="speaker" data-name="" class="${S.speaker ? '' : 'on'}">Nobody</button></div>`);
}

function renderSheet() {
  if (!sheet || !S) return;
  if (sheet === 'live') { $('#sheetTitle').textContent = 'Live: audience, polls, presenters'; return renderLiveSheet(); }
  const list = slides(), i = S.present.index;
  if (sheet === 'slides') {
    $('#sheetTitle').textContent = `${S.deck.title} · ${list.length} slides`;
    setHTML($('#sheetBody'), `<div class="rm-grid">${list.map((x, n) => `
      <button class="rm-cell ${n === i ? 'cur' : ''}" data-goto="${n}">
        ${slideHTML(x, S.deck, n)}<span class="rm-cell-n">${n + 1}</span>
      </button>`).join('')}</div>`);
    const cur = $('#sheetBody .rm-cell.cur');
    if (cur && !renderSheet._scrolled) { cur.scrollIntoView({ block: 'center' }); renderSheet._scrolled = true; }
  } else {
    renderSheet._scrolled = false;
    $('#sheetTitle').textContent = remoteTab === 'doc' ? 'Docs' : 'Presentations';
    if (!remoteDecks) { setHTML($('#sheetBody'), '<p class="muted">Loading…</p>'); return; }
    const list = remoteDecks.decks.filter(d => deckKind(d) === remoteTab);
    setHTML($('#sheetBody'), tabsHTML(remoteDecks.decks, remoteTab, 'data-rtab') +
      `<div class="rm-decks">${groupByDay(list).map(g => `<div class="lib-day">${esc(g.label)}</div>` + g.items.map(d => `
      <button class="rm-deckrow ${d.id === S.deck.id ? 'cur' : ''}" data-deck="${esc(d.id)}" data-title="${esc(d.title)}">
        <span class="rm-deckcover">${d.cover ? slideHTML(d.cover, d, 0) : ''}</span>
        <span class="rm-decktext"><b>${esc(d.title)}</b><small>${countLabel(d)}${d.id === S.deck.id ? ' · on screen now' : ''}</small></span>
      </button>`).join('')).join('') || '<p class="muted">Nothing here yet.</p>'}</div>`);
  }
}

/* ---------- live view ---------- */
// EN: Updates the remote: preview (still or live), counters, notes, buttons.
// DE: Aktualisiert die Fernbedienung: Vorschau (Standbild oder live), Zähler, Notizen, Knöpfe.
function renderRemote() {
  const list = slides(), i = S.present.index, s = list[i], nx = list[i + 1];
  const steps = hsSteps(s), step = S.present.step || 0;
  $('#rTitle').textContent = S.deck.title;
  $('#rCount').innerHTML = list.length ? `${i + 1}<small> / ${list.length}</small>` : '0';
  setHTML($('#rSteps'), s && (s.layout === 'page' || s.layout === 'doc')
    ? `<span class="muted" style="font-size:12px">${s.layout === 'doc' ? 'Document · Next scrolls' : 'Interactive page'}</span>`
    : steps && steps <= 24 ? Array.from({ length: steps }, (_, k) => `<i class="${k < step ? 'on' : ''}"></i>`).join('') : '');
  const scr = (S.devices && S.devices.present) || 0;
  // which SlideLink windows are open on the PC itself (checked live by the PC)
  const pw = S.pc_windows || {}, onPc = (pw.present || 0) + (pw.library || 0) + (pw.edit || 0);
  renderPcBtn();
  $('#crewNow').innerHTML = S.speaker ? `🎤 <b>${esc(S.speaker.name)}</b> is presenting` : '<span class="muted">🎤 Nobody selected</span>';
  $('#meBtn').hidden = !!(S.speaker && myName && S.speaker.name === myName);
  $('#crew').hidden = !S.speaker;            // only shown once someone presents (choose yourself under 👥 Live)
  $('#rInfo').innerHTML = pw.present ? '<span class="live">● Presentation on PC</span>' : onPc ? '<span class="live">● SlideLink open on PC</span>' : '<span class="muted">○ No SlideLink window on PC</span>';
  // preview: a still picture of the current slide (no animation running) unless "Live" is on;
  // documents always use their scroll view (just pictures)
  const box = $('#nowSlide'), live = remoteLive || (s && s.layout === 'doc');
  if (box._mode !== (live ? 'live' : 'still')) { box.innerHTML = ''; box._h = null; box._slots = null; box._current = null; box._mode = live ? 'live' : 'still'; }
  if (live) renderSlots(box, i, 0);   // phone: only the current slide runs, at low fps
  else setHTML(box, s ? `<div class="pslot cur">${slideHTML(s, S.deck, i)}</div>` : slideHTML(null, S.deck));
  const lb = $('#liveBtn');
  lb.textContent = remoteLive ? '● Live' : '○ Still';   // shows the current state; tap switches
  lb.classList.toggle('on', remoteLive);
  lb.hidden = !!(s && s.layout === 'doc');
  presentingChanged();
  $('#nowBlk').classList.toggle('on', !!S.present.blackout);
  $('#blkBtn').classList.toggle('on', !!S.present.blackout);
  setHTML($('#nx'), nx ? slideHTML(nx, S.deck, i + 1) : '<div class="rm-end">End of presentation</div>');
  $('#notes').textContent = (s && s.notes) || 'No speaker notes for this slide.';
  $('#prevBtn').disabled = i <= 0 && step <= 0;
  const scrolls = s && (s.layout === 'page' || s.layout === 'doc');
  const more = step < steps && !scrolls;
  $('#nextBtn').disabled = !more && i >= list.length - 1 && !scrolls;
  $('#nextBtn').textContent = more ? 'Reveal ▶' : 'Next ▶';
  renderClaudeBar();
  if (sheet === 'decks' && remoteDecks && remoteDecks.active !== S.deck.id) loadRemoteDecks();
  renderSheet();
}
