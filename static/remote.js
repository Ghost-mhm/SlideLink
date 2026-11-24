// SlideLink: Phone remote: preview, build dots, notes, timer, slide and presentation lists, PC window button.
'use strict';
/* SlideLink phone remote: a remote display + presentation manager for the desktop app.
   Shows the live slide, skips, blacks out, jumps to slides and switches presentations.
   No editing here - that happens on the PC. */

const timer = JSON.parse(localStorage.getItem('sl-timer') || '{"start":0,"acc":0,"run":false}');
// Timer as mm:ss.
function timerText() {
  const ms = timer.acc + (timer.run ? Date.now() - timer.start : 0);
  const t = Math.floor(ms / 1000);
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
}
// Keeps the timer across reloads.
function saveTimer() { localStorage.setItem('sl-timer', JSON.stringify(timer)); }
// Starts the timer (on the first Next).
function startTimer() { if (!timer.run) { timer.run = true; timer.start = Date.now(); saveTimer(); } }

let sheet = null;            // 'slides' | 'decks' | null
let remoteDecks = null;      // library listing for the presentations sheet
let remoteTab = localStorage.getItem('sl-remote-tab') || 'deck';
let remoteLive = !!localStorage.getItem('sl-remote-live');
let myName = '';                                              // this phone's name (for "I'm presenting")   // preview: still picture (default, battery) or live
let lastPresenting = null;   // 'deck' | 'doc'

// Builds the remote screen and its buttons, swipes and keys.
function initRemote() {
  document.title = 'SlideLink Remote';
  HS_LOWPOWER = true;                    // small preview: ~12 fps, endless loops paused (battery)
  document.body.classList.add('is-remote');
  $('#app').innerHTML = `
  <div class="rm">
    <header class="rm-top">
      <button class="rm-deck" id="deckBtn" aria-label="Switch presentation">
        <span class="rm-deck-t" id="rTitle">…</span><span class="rm-chev">${IC('chev')}</span>
      </button>
      <button class="rm-net" id="netDot" aria-label="Connection"><i></i><span id="netMs">…</span></button>
      <button class="rm-timer" id="timer" title="Tap: start / pause · hold: reset">00:00</button>
      <button class="rm-sync" id="syncBtn" aria-label="Resync all screens" title="Resync all screens">${IC('sync')}</button>
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
      <button id="meBtn">${IC('mic')}<span>Me now</span></button>
    </div>
    <div class="rm-next">
      <div class="rm-nx"><div class="rm-lbl">Next</div><div id="nx"></div></div>
      <div class="rm-notes-wrap"><div class="rm-notes" id="notes"></div>
        <button class="rm-notes-edit" id="notesEdit" aria-label="Edit notes">${IC('edit')}</button></div>
    </div>
    <div class="rm-actions">
      <button id="slidesBtn"><i>${IC('slides')}</i><span>Slides</span></button>
      <button id="decksBtn"><i>${IC('decks')}</i><span>Decks</span></button>
      <button id="pcBtn" title="Show the presentation on the PC / close it"><i>${IC('pc')}</i><span>On PC</span></button>
      <button id="laserBtn" title="Big slide: point at it with your finger, a red dot shows on the PC"><i>${IC('pointer')}</i><span>Point</span></button>
      <button id="liveBtnSheet" aria-label="Audience, polls, presenters"><i>${IC('live')}</i><span>Live</span></button>
      <button id="devBtn" aria-label="Linked devices"><i>${IC('devices')}</i><span>Devices</span></button>
    </div>
    <button class="rm-go" id="goBtn" hidden><span class="rm-go-ic">${IC('play')}</span>
      <span class="rm-go-t"><b>Start</b><small id="goDeck" data-noi18n></small></span></button>
    <div class="rm-pad">
      <button id="prevBtn" aria-label="Back">${IC('prev')}</button>
      <button id="blkBtn" class="rm-blk">${IC('blackout')}<small>Blackout</small></button>
      <button id="nextBtn" class="next"><span>Next</span>${IC('next')}</button>
    </div>
    <div class="rm-laser" id="laserView" hidden>
      <div class="rm-laser-stage" id="laserStage"><div class="rm-laser-box" id="laserBox"></div><div class="rm-laser-dot" id="laserDot"></div></div>
      <div class="rm-laser-bar">
        <button id="lzPrev" aria-label="Back">${IC('prev')}</button>
        <button id="lzClose" class="lz-done">${IC('close')}<span>Done</span></button>
        <button id="lzNext" class="next" aria-label="Next">${IC('next')}</button>
      </div>
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
    buzz(25);
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
  tm.onpointerdown = () => { holdT = setTimeout(() => { holdT = 'reset'; timer.run = false; timer.acc = 0; saveTimer(); tm.textContent = timerText(); buzz(40); }, 700); };
  tm.onpointerup = () => {
    if (holdT === 'reset') { holdT = null; return; }
    clearTimeout(holdT); holdT = null;
    if (timer.run) { timer.acc += Date.now() - timer.start; timer.run = false; saveTimer(); } else startTimer();
  };

  // one hand: swipe left/right on the slide or the notes = next/back, double tap on the slide = black screen
  for (const area of [$('#now'), $('.rm-next')]) {
    let t0 = null;
    area.addEventListener('touchstart', e => {
      t0 = e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
    }, { passive: true });
    area.addEventListener('touchend', e => {
      if (!t0) return;
      const dx = e.changedTouches[0].clientX - t0.x, dy = e.changedTouches[0].clientY - t0.y;
      t0 = null;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;   // a scroll, not a swipe
      if (dx < 0) { startTimer(); go('next'); } else go('prev');
    });
  }
  let lastTap = 0;
  $('#now').addEventListener('click', e => {
    if (e.target.closest('button')) return;
    const t = Date.now();
    if (t - lastTap < 350) { lastTap = 0; go('blackout'); } else lastTap = t;
  });
  initLaser();
  initNet();
  $('#notesEdit').onclick = editNotes;
  $('#goBtn').onclick = startTalk;
  if (MODE === 'group') $('#notesEdit').hidden = true;   // co-presenters only read the notes
  document.addEventListener('keydown', navKeys);   // Bluetooth clickers / keyboards
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tryWake(); });
  setInterval(() => { $('#timer').textContent = timerText(); S && renderClaudeBar(); }, 1000);
}

/* One button, by state: presentation not on the PC → "Show on PC" (borderless fullscreen);
   presentation open on the PC → "Close window on PC" (SlideLink keeps running, show it again any time). */
let pcPending = null;   // {want: true|false, until} while the PC opens/closes the window
// One button by state: show the presentation on the PC, or close its window.
/* "Start": one tap in the room and the talk runs - back to slide 1, fullscreen on the projector, timer from 0:00.
   Shown only while the presentation is not on the PC yet (pick another one under "Decks" first if needed). */
async function startTalk() {
  if (!S) return;
  buzz(30);
  if (slides().length && (S.present.index || S.present.step)) go('goto', { index: 0 });
  timer.acc = 0; timer.run = true; timer.start = Date.now(); saveTimer();
  $('#timer').textContent = timerText();
  pcPending = { want: true, until: Date.now() + 12000 };
  renderPcBtn();
  const j = await api({ op: 'show_on_pc' });
  if (j.ok) toastOk('Starting the presentation on the PC…');
  else { pcPending = null; renderPcBtn(); }
}
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
// Android app: screen stays on only while presenting.
function presentingChanged() {
  if (window.SlideLinkApp && SlideLinkApp.setPcName && S && S.pc_name) SlideLinkApp.setPcName(S.pc_name);   // list of PCs
  if (window.SlideLinkApp && SlideLinkApp.setSlideInfo && S) {        // lock-screen controls: where we are
    const n = slides().length;
    SlideLinkApp.setSlideInfo(n ? `${S.present.index + 1} / ${n} · ${S.deck.title}${S.present.blackout ? ' · ⬛' : ''}` : S.deck.title);
  }
  const now = !!((S && S.pc_windows && S.pc_windows.present) || timer.run);
  if (now === lastPresenting) return;
  lastPresenting = now;
  if (window.SlideLinkApp && SlideLinkApp.setPresenting) SlideLinkApp.setPresenting(now);
}
setInterval(() => typeof S !== 'undefined' && S && presentingChanged(), 2000);   // safe even if app.js failed to load
// Text of the PC button (with 'Opening…'/'Closing…' right after a tap).
function renderPcBtn() {
  const b = $('#pcBtn');
  if (!b || !S) return;
  const open = !!(S.pc_windows || {}).present;
  if (pcPending && (open === pcPending.want || Date.now() > pcPending.until)) pcPending = null;
  const g = $('#goBtn');                                  // "Start" only while nothing runs on the projector yet
  if (g) {
    g.hidden = open || !!pcPending || MODE === 'group' || !slides().length;
    $('#goDeck').textContent = S.deck.title;
  }
  const set = (icon, text, closing) => {
    const html = `<i>${icon}</i><span>${text}</span>`;
    if (b._h !== html) { b.innerHTML = html; b._h = html; }
    b.classList.toggle('closing', closing);
  };
  if (pcPending) return set(IC('wait'), pcPending.want ? 'Opening' : 'Closing', !pcPending.want);
  set(open ? IC('close') : IC('pc'), open ? 'Close' : 'On PC', open);
}
setInterval(() => pcPending && typeof S !== 'undefined' && renderPcBtn(), 1000);
// Short green confirmation message.
function toastOk(msg) {
  const c = $('#conn');
  c.textContent = msg; c.classList.remove('ok'); c.classList.add('good');
  clearTimeout(toastOk.t); toastOk.t = setTimeout(() => { c.classList.add('ok'); c.classList.remove('good'); }, 2500);
}

/* ---------- sheets ---------- */
// Opens the slides or presentations sheet.
function openSheet(kind) {
  sheet = kind;
  $('#sheet').classList.add('on');
  $('#sheet').setAttribute('aria-hidden', 'false');
  if (kind === 'decks') loadRemoteDecks();
  renderSheet();
}
// Closes the sheet.
function closeSheet() { sheet = null; $('#sheet').classList.remove('on'); $('#sheet').setAttribute('aria-hidden', 'true'); }
// Loads the presentations for the sheet.
async function loadRemoteDecks() {
  const r = await fetch('/api/library', { headers: authHeaders(), cache: 'no-store' }).catch(() => null);
  if (r && r.ok) { remoteDecks = await r.json(); renderSheet(); }
}
// Taps in the sheet: tab, slide, presentation.
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
// Draws the slides grid or the presentations list (two tabs, by day).
// Buttons in the Live sheet: audience link, polls, who presents.
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
  if (kind === 'qshow') return api({ op: 'question_show', id: el.dataset.id, on: !el.classList.contains('on') });
  if (kind === 'qdel') return api({ op: 'question_dismiss', id: el.dataset.id });
  if (kind === 'qclear') { if (confirm('Remove all questions?')) api({ op: 'questions_clear' }); return; }
  if (kind === 'qopen') return api({ op: 'questions_open', on: !S.questions_open });
}

// The Live sheet. The poll form is drawn only once, so typing is never interrupted by live updates.
function renderLiveSheet() {
  const body = $('#sheetBody');
  if (!body.querySelector('#lvAud')) {
    body.innerHTML = `${MODE === 'group' ? '' : '<div class="lv-sec grp-sec" id="lvGroup"></div>'}<div class="lv-sec" id="lvAud"></div><div class="lv-sec" id="lvAsk"></div><div class="lv-sec" id="lvPoll"></div><div class="lv-sec" id="lvCrew"></div>`;
    body._h = null;
  }
  // audience questions: only the presenter decides which one goes on the screen
  const qs = S.questions || [];
  setHTML($('#lvAsk'), `<h4>💬 Questions${qs.length ? ` <span data-noi18n>(${qs.length})</span>` : ''}</h4>
    ${qs.length ? qs.slice().reverse().map(q => `<div class="lv-q${q.show ? ' on' : ''}"><p>${esc(q.text)}</p>
      <div class="lv-q-row"><small>${q.name ? esc(q.name) + ' · ' : ''}${ago(q.at)}</small>
        <button data-live="qshow" data-id="${q.id}" class="${q.show ? 'on' : ''}">${q.show ? 'Hide from screen' : '📺 Show on screen'}</button>
        <button data-live="qdel" data-id="${q.id}" aria-label="Remove">✕</button></div></div>`).join('')
      : `<p class="muted">Viewers can send questions from the audience page. They show up here first; nothing appears on the screen until you tap “Show on screen”.</p>`}
    <div class="lv-row"><button data-live="qopen" class="${S.questions_open ? 'on' : ''}">${S.questions_open ? 'Questions are open' : 'Questions are closed'}</button>
      ${qs.length ? '<button data-live="qclear">Remove all</button>' : ''}</div>`);
  if ($('#lvGroup')) setHTML($('#lvGroup'), groupAdminHTML());
  const a = S.audience || {};
  setHTML($('#lvAud'), `<h4>👥 Audience</h4>
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
  setHTML($('#lvCrew'), `<h4>🎤 Who is presenting?</h4>
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
// Updates the remote: preview (still or live), counters, notes, buttons.
let seenQuestions = null;    // ids of questions already noticed (a new one: buzz + dot on "Live")
function renderRemote() {
  renderLaser();
  const qids = (S.questions || []).map(q => q.id);
  if (seenQuestions && qids.some(id => !seenQuestions.has(id)) && sheet !== 'live') { buzz([30, 80, 30]); $('#liveBtnSheet').classList.add('dot'); }
  if (sheet === 'live') $('#liveBtnSheet').classList.remove('dot');
  seenQuestions = new Set(qids);
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
  lb.textContent = remoteLive ? 'Live' : 'Still';   // shows the current state; tap switches
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
  const nl = $('#nextBtn span'); if (nl) nl.textContent = more ? 'Reveal' : 'Next';
  renderClaudeBar();
  if (sheet === 'decks' && remoteDecks && remoteDecks.active !== S.deck.id) loadRemoteDecks();
  renderSheet();
}

/* Pointing: the slide big on the phone (turned sideways when the phone is upright); where the finger is,
   a red dot shows on the PC. Only the latest position is sent, one request at a time (no lag builds up). */
let laserOn = false, lzWant, lzBusy = false, lzRot = false;
// Sets up the pointing view and its buttons.
function initLaser() {
  $('#laserBtn').onclick = () => { laserOn = true; $('#laserView').hidden = false; renderLaser(); buzz(15); };
  $('#lzClose').onclick = () => { laserOn = false; $('#laserView').hidden = true; lzSend(null); };
  $('#lzPrev').onclick = () => go('prev');
  $('#lzNext').onclick = () => { startTimer(); go('next'); };
  const st = $('#laserStage');
  const at = e => {
    const r = $('#laserBox').getBoundingClientRect();
    let x, y;
    if (lzRot) { x = (e.clientY - r.top) / r.height; y = (r.right - e.clientX) / r.width; }
    else { x = (e.clientX - r.left) / r.width; y = (e.clientY - r.top) / r.height; }
    if (x < 0 || x > 1 || y < 0 || y > 1) return lzSend(null);
    const d = $('#laserDot');
    d.style.left = e.clientX + 'px'; d.style.top = e.clientY + 'px'; d.classList.add('on');
    lzSend({ x: +x.toFixed(4), y: +y.toFixed(4) });
  };
  st.addEventListener('pointerdown', e => { st.setPointerCapture(e.pointerId); at(e); });
  st.addEventListener('pointermove', e => { if (e.buttons || e.pointerType === 'touch') at(e); });
  const up = () => { $('#laserDot').classList.remove('on'); lzSend(null); };
  st.addEventListener('pointerup', up);
  st.addEventListener('pointercancel', up);
  addEventListener('resize', () => laserOn && renderLaser());
}
// Draws the current slide as big as the screen allows.
function renderLaser() {
  if (!laserOn || !S) return;
  const list = slides(), i = S.present.index, box = $('#laserBox'), st = $('#laserStage');
  const key = list[i] ? list[i].id + ':' + i + ':' + (list[i].html || list[i].content || '').length : '';
  if (box._key !== key) { box._key = key; box.innerHTML = list[i] ? slideHTML(list[i], S.deck, i, false) : ''; }
  const W = st.clientWidth, H = st.clientHeight;
  lzRot = H > W;                                     // upright phone: turn the slide so it fills the screen
  const long = lzRot ? H : W, short = lzRot ? W : H;
  const w = Math.min(long, short * 16 / 9), h = w * 9 / 16;
  box.style.width = w + 'px'; box.style.height = h + 'px';
  box.style.transform = `translate(-50%, -50%)${lzRot ? ' rotate(90deg)' : ''}`;
  hsObserve(box);
}
// Sends the finger position (or null = hide), dropping positions that are already out of date.
function lzSend(p) {
  lzWant = p;
  if (lzBusy) return;
  lzBusy = true;
  (async () => {
    while (lzWant !== undefined) {
      const q = lzWant; lzWant = undefined;
      try {
        await fetch('/api/op', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ op: 'pointer', ...(q || { hide: true }), origin: CLIENT }) });
      } catch (_) {}
      await new Promise(r => setTimeout(r, 30));
    }
    lzBusy = false;
  })();
}

/* Connection dot: which way the phone talks to the PC and how fast. Green = home Wi-Fi, blue = relay,
   yellow = slow (over 400 ms), red = no connection. Tap it for the details. */
let netMs = null, netFail = 0;
// The way this page reaches the PC, from its own address.
function netRoute() {
  const h = location.hostname;
  if (h.endsWith('.slidelink.invalid')) return { kind: 'relay', text: 'Over the internet (SlideLink Relay), end-to-end encrypted' };
  if (/workers\.dev$|\/p\//.test(h + location.pathname)) return { kind: 'relay', text: 'Over the internet (SlideLink Relay)' };
  if (h === 'localhost' || h === '127.0.0.1') return { kind: 'wifi', text: 'On this PC' };
  return { kind: 'wifi', text: 'Same Wi-Fi as the PC, encrypted' };
}
// Measures the time for one small request every 5 s (only while the page is visible).
async function netPing() {
  if (document.hidden) return;
  const t0 = performance.now();
  try {
    const r = await fetch('/api/whoami', { headers: authHeaders(), cache: 'no-store' });
    if (!r.ok && r.status >= 500) throw new Error();
    netMs = Math.round(performance.now() - t0); netFail = 0;
  } catch (_) { netFail++; }
  renderNet();
}
// Colours the dot.
function renderNet() {
  const d = $('#netDot');
  if (!d) return;
  const live = typeof lastBeat === 'number' && Date.now() - lastBeat < 15000;
  const r = netRoute();
  const state = netFail >= 2 || !live ? 'off' : netMs > 400 ? 'slow' : r.kind;
  d.className = 'rm-net ' + state;
  $('#netMs').textContent = state === 'off' ? '–' : netMs == null ? '…' : netMs + ' ms';
}
// Sets up the dot: first measurement now, then every 5 s; tap = details.
function initNet() {
  $('#netDot').onclick = () => {
    const r = netRoute(), live = Date.now() - lastBeat < 15000;
    if (!live || netFail >= 2) return toastOk('No connection to the PC right now. Trying again…');
    toastOk('');                                     // same toast, but in parts so each part gets translated
    $('#conn').innerHTML = `<span>${esc(r.text)}</span> · <span data-noi18n>${netMs} ms</span>${netMs > 400 ? ' · <span>slow</span>' : ''}`;
  };
  netPing();
  setInterval(netPing, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) netPing(); });
}

/* Editing the speaker notes on the phone (e.g. a last-minute fix before the talk). The editor keeps the slide it was
   opened for, so moving on in the presentation meanwhile never saves the text to the wrong slide. */
function editNotes() {
  const list = slides(), i = S.present.index, s = list[i];
  if (!s) return;
  const back = document.createElement('div');
  back.className = 'rm-notes-editor';
  back.innerHTML = `<div class="rm-ne-in">
    <div class="rm-ne-head"><b>Speaker notes</b><small data-noi18n>${esc(`${i + 1} / ${list.length} · ${s.title || ''}`)}</small></div>
    <textarea id="neText" maxlength="5000" placeholder="What you want to say on this slide"></textarea>
    <div class="rm-ne-row"><button id="neCancel">Cancel</button><button id="neSave" class="primary">Save</button></div>
  </div>`;
  document.body.appendChild(back);
  const ta = back.querySelector('#neText');
  ta.value = s.notes || '';
  ta.focus();
  const close = () => back.remove();
  back.querySelector('#neCancel').onclick = close;
  back.querySelector('#neSave').onclick = async () => {
    const b = back.querySelector('#neSave');
    b.disabled = true;
    const j = await api({ op: 'set_notes', id: s.id, notes: ta.value });
    b.disabled = false;
    if (j && j.ok) { close(); buzz(25); toastOk('Notes saved'); }
  };
}
