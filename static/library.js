// SlideLink · EN: Library (home screen on the PC): Presentations and Docs tabs, newest first, upload, folder status.
// SlideLink · DE: Bibliothek (Startbildschirm am PC): Reiter Präsentationen und Docs, neueste zuerst, Upload, Ordnerstatus.
'use strict';
/* SlideLink library: every presentation in one place (home screen of the desktop app). */

let LIB = null;                 // {active, version, decks:[summary]}
let libSel = sessionStorage.getItem('sl-lib-sel') || null;
let libQuery = '';
const deckCache = {};           // id -> full deck (keyed with updated_at)
let libLoading = null;
let libTab = localStorage.getItem('sl-lib-tab') || 'deck';   // 'deck' = presentations (slides/html), 'doc' = docs

/* ---- shared by the library and the phone: two explorers, newest first, grouped by day ---- */
// Docs: Claude Docs and everything that has only one slide (single pages, HTML apps, pictures)
// EN: Which tab an item belongs to: 'doc' for Claude Docs and anything with one slide, else 'deck'.
// DE: Zu welchem Reiter ein Eintrag gehört: 'doc' für Claude Docs und alles mit einer Folie, sonst 'deck'.
const deckKind = d => (d.kind === 'doc' || d.slide_count === 1 ? 'doc' : 'deck');
// EN: Date used for sorting (claude.ai date, else created/updated).
// DE: Datum für die Sortierung (claude.ai-Datum, sonst erstellt/geändert).
const deckDate = d => d.date || d.created_at || d.updated_at || 0;
// EN: Day heading: Today, Yesterday or the full date.
// DE: Tagesüberschrift: Heute, Gestern oder das volle Datum.
function dayLabel(t) {
  const d = new Date(t * 1000), today = new Date();   // dates in the interface language
  const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return d.toLocaleDateString(I18N.locale(), { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}
// EN: Sorts newest first and groups by day.
// DE: Sortiert neueste zuerst und gruppiert nach Tagen.
function groupByDay(list) {
  const out = [];
  [...list].sort((a, b) => deckDate(b) - deckDate(a)).forEach(d => {
    const label = dayLabel(deckDate(d));
    if (!out.length || out[out.length - 1].label !== label) out.push({ label, items: [] });
    out[out.length - 1].items.push(d);
  });
  return out;
}
// EN: The two tabs with their counts.
// DE: Die beiden Reiter mit ihren Anzahlen.
function tabsHTML(decks, current, attr) {
  const n = k => decks.filter(d => deckKind(d) === k).length;
  return `<div class="lib-tabs" role="tablist">
    <button role="tab" ${attr}="deck" class="${current === 'deck' ? 'on' : ''}">${IC('deck')}<span>Presentations</span> <small>${n('deck')}</small></button>
    <button role="tab" ${attr}="doc" class="${current === 'doc' ? 'on' : ''}">${IC('doc')}<span>Docs</span> <small>${n('doc')}</small></button>
  </div>`;
}
// EN: 'Document' or 'n slides'.
// DE: 'Document' oder 'n slides'.
const countLabel = d => deckKind(d) === 'doc' ? 'Document' : plural(d.slide_count);

// EN: Loads the list of all presentations.
// DE: Lädt die Liste aller Präsentationen.
async function loadLibrary() {
  if (libLoading) return libLoading;
  libLoading = fetch('/api/library', { headers: authHeaders(), cache: 'no-store' })
    .then(r => r.status === 401 ? (showLinkScreen(), null) : r.json())
    .then(j => { if (j) { LIB = j; renderLibrary(); } })
    .catch(() => {})
    .finally(() => { libLoading = null; });
  return libLoading;
}
// EN: Loads one complete deck (cached).
// DE: Lädt ein vollständiges Deck (zwischengespeichert).
async function loadDeck(id) {
  const summ = LIB && LIB.decks.find(d => d.id === id);
  const c = deckCache[id];
  if (c && summ && c.updated_at === summ.updated_at) return c;
  const d = await fetch('/api/library/' + id, { headers: authHeaders(), cache: 'no-store' }).then(r => r.json());
  deckCache[id] = d;
  return d;
}

// EN: Builds the library screen.
// DE: Baut den Bibliotheksbildschirm.
function initLibrary() {
  document.title = 'SlideLink';
  $('#app').innerHTML = `
  <div class="lib">
    <aside class="lib-side">
      <div class="lib-brand"><img src="/icon.svg" alt="" width="30" height="30"><div class="logo">Slide<span>Link</span></div>
        <button class="settings-btn" id="setBtn" title="Settings" aria-label="Settings">${IC('settings')}</button></div>
      <button class="lib-home-btn" id="homeBtn">${IC('home')}<span>Library home</span></button>
      <div id="libTabs"></div>
      <input id="libSearch" placeholder="Search" aria-label="Search">
      <div class="lib-list" id="libList"></div>
      <div class="lib-side-foot">
        <div class="up-status" id="upStatus"></div>
        <button class="primary" id="uploadBtn" style="width:100%" title="PowerPoint, claude.ai deck (.zip), HTML, PDF doc, images – or drag files here">${IC('plus')}<span>Add presentation</span></button>
        <button id="watchBtn" class="watch-btn" style="width:100%" title="Everything Claude saves in this folder appears here automatically">
          <span class="wb-l">${IC('folder')}<span>Claude Presentations</span></span><span class="sync-badge" id="syncBadge" hidden></span></button>
        <input type="file" id="uploadInput" multiple hidden
          accept=".pptx,.ppt,.odp,.ppsx,.pps,.potx,.zip,.json,.html,.htm,.pdf,.png,.jpg,.jpeg,.webp,.gif">
        <button id="audBtn" style="width:100%">${IC('group')}<span>Group & audience</span></button>
        <button id="devBtn" style="width:100%">${IC('devices')}<span>Linked devices</span> <span class="count" id="devCount"></span></button>
      </div>
    </aside>
    <main class="lib-main" id="libMain"><div class="muted" style="padding:40px">Loading your presentations…</div></main>
  </div>
  <div class="claude-bar lib-claude" id="claudeBar"></div>`;

  $('#libSearch').oninput = e => { libQuery = e.target.value.toLowerCase(); renderLibrary(); };
  $('#homeBtn').onclick = () => selectDeck(null);
  $('#devBtn').onclick = openConnect;
  $('#audBtn').onclick = openAudience;
  $('#setBtn').onclick = openSettings;
  $('#uploadBtn').onclick = () => $('#uploadInput').click();
  $('#watchBtn').onclick = () => post('/api/watch/open', {});
  const showWatch = async () => {
    const w = await fetch('/api/watch', { headers: authHeaders(), cache: 'no-store' }).then(r => r.json()).catch(() => null);
    // small badge on the folder button: green = auto-sync running (number of items), red = problem; details on hover
    const badge = $('#syncBadge'), btn = $('#watchBtn');
    if (!w || !badge) return;
    badge.hidden = false;
    badge.className = 'sync-badge ' + (w.error ? 'err' : 'ok');
    badge.textContent = w.error ? '!' : String(w.count);
    const tip = w.error ? I18N.t('Sync problem') + ': ' + w.error
      : I18N.t('Auto-sync on') + ' · ' + I18N.t(`${w.count} items in the folder`) +
        (w.last ? ' · ' + I18N.t('last') + ': ' + w.last.name + ' (' + I18N.t(w.last.what) + ')' : '');
    btn.title = I18N.t('Everything Claude saves in this folder appears here automatically') + '\n' + tip;
    btn.__i18n_title = { src: btn.title, out: btn.title };    // already translated: the language layer leaves it alone
  };
  showWatch(); setInterval(showWatch, 5000);
  $('#uploadInput').onchange = e => { uploadFiles(e.target.files); e.target.value = ''; };
  const lib = $('.lib');
  lib.addEventListener('dragover', e => { e.preventDefault(); lib.classList.add('dropping'); });
  lib.addEventListener('dragleave', e => { if (!lib.contains(e.relatedTarget)) lib.classList.remove('dropping'); });
  lib.addEventListener('drop', e => { e.preventDefault(); lib.classList.remove('dropping'); uploadFiles(e.dataTransfer.files); });
  $('#libList').onclick = e => { const it = e.target.closest('[data-id]'); if (it) selectDeck(it.dataset.id); };
  $('#libTabs').onclick = e => {
    const t = e.target.closest('[data-tab]');
    if (!t) return;
    libTab = t.dataset.tab; localStorage.setItem('sl-lib-tab', libTab);
    selectDeck(null);
  };
  $('#libMain').onclick = onLibMainClick;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && libSel) selectDeck(null); });
  loadLibrary();
  if (new URLSearchParams(location.search).has('devices')) {
    const t = setInterval(() => { if (S) { clearInterval(t); openDevices(); history.replaceState(null, '', '/'); } }, 200);
  }
}

/* Upload: PowerPoint (converted by PowerPoint on the PC), claude.ai deck .zip, HTML deck, SlideLink .json,
   images (several images become one presentation). Also works by dragging files onto the window. */
// EN: Uploads files (PowerPoint, zip, html, json, pictures), also by drag and drop.
// DE: Lädt Dateien hoch (PowerPoint, zip, html, json, Bilder), auch per Drag-and-drop.
async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const status = $('#upStatus');
  let imageDeck = null, last = null, ok = 0;
  for (const [n, f] of files.entries()) {
    const isImg = /\.(png|jpe?g|webp|gif)$/i.test(f.name);
    const isPpt = /\.(pptx?|odp|ppsx?|potx)$/i.test(f.name);
    status.className = 'up-status on';
    status.textContent = `${files.length > 1 ? `(${n + 1}/${files.length}) ` : ''}Importing ${f.name}` +
      (isPpt ? ' – PowerPoint is converting the slides…' : '…');
    const url = '/api/upload?name=' + encodeURIComponent(f.name) + (isImg && imageDeck ? '&deck=' + imageDeck : '');
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream', ...authHeaders() }, body: f });
      const j = await r.json().catch(() => ({ ok: false, error: 'unexpected answer from SlideLink' }));
      if (!j.ok) { alert(`${f.name}: ${j.error}`); continue; }
      if (isImg && !imageDeck) imageDeck = j.result.deck_id;
      last = j.result.deck_id; ok++;
    } catch (e) { alert(`${f.name}: ${e.message}`); }
  }
  status.className = 'up-status on done';
  status.textContent = ok ? `✓ ${ok} file${ok > 1 ? 's' : ''} imported` : 'Nothing imported';
  setTimeout(() => { status.className = 'up-status'; }, 4000);
  await loadLibrary();
  if (last) selectDeck(last);
}

// EN: Shows one presentation (or the overview with null).
// DE: Zeigt eine Präsentation (oder mit null die Übersicht).
function selectDeck(id) {
  libSel = id;
  sessionStorage.setItem('sl-lib-sel', id || '');
  $('#libMain').scrollTop = 0;
  renderLibrary();
}

// EN: Buttons on a presentation: present, edit, remote, duplicate, delete.
// DE: Knöpfe einer Präsentation: präsentieren, bearbeiten, Fernbedienung, duplizieren, löschen.
// EN: The ⋯ menu of a library tile: edit (title, speaker notes), duplicate, delete.
// DE: Das ⋯-Menü einer Bibliothekskachel: bearbeiten (Titel, Sprechernotizen), duplizieren, löschen.
function openCardMenu(btn) {
  closeCardMenu();
  const id = btn.dataset.more, deck = LIB.decks.find(d => d.id === id);
  if (!deck) return;
  const m = document.createElement('div');
  m.className = 'card-menu'; m.id = 'cardMenu';
  m.innerHTML = `<button data-cm="edit">${IC('edit')}<span>Edit</span></button><button data-cm="duplicate">${IC('copy')}<span>Duplicate</span></button>
    <button data-cm="delete" class="danger">${IC('trash')}<span>Delete</span></button>`;
  document.body.appendChild(m);
  const r = btn.getBoundingClientRect();
  m.style.top = Math.min(innerHeight - m.offsetHeight - 8, r.bottom + 6) + 'px';
  m.style.left = Math.max(8, Math.min(innerWidth - m.offsetWidth - 8, r.right - m.offsetWidth)) + 'px';
  btn.closest('.lib-card').classList.add('menu-open');
  m.onclick = async e => {
    const b = e.target.closest('[data-cm]');
    if (!b) return;
    closeCardMenu();
    if (b.dataset.cm === 'edit') return openEditDeck(id);
    if (b.dataset.cm === 'duplicate') {
      const j = await api({ op: 'duplicate_deck', deck_id: id });
      if (j.ok) { toast('Duplicated'); loadLibrary(); }
    }
    if (b.dataset.cm === 'delete' && confirm(`Delete "${deck.title}" with all ${deck.slide_count} slides? This cannot be undone.`)) {
      const j = await api({ op: 'delete_deck', deck_id: id });
      if (j.ok) { if (libSel === id) selectDeck(null); loadLibrary(); }
    }
  };
}
function closeCardMenu() {
  document.getElementById('cardMenu')?.remove();
  document.querySelectorAll('.lib-card.menu-open').forEach(c => c.classList.remove('menu-open'));
}
document.addEventListener('click', e => { if (!e.target.closest('#cardMenu, [data-more]')) closeCardMenu(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCardMenu(); });

// EN: Edit window: the title and the speaker notes of every slide (the slides themselves stay as they are).
// DE: Bearbeiten-Fenster: Titel und Sprechernotizen jeder Folie (die Folien selbst bleiben, wie sie sind).
async function openEditDeck(id) {
  const d = await loadDeck(id);
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal edit-modal">
    <div class="row" style="align-items:center;flex:none"><h3 style="margin:0">✎ Edit presentation</h3>
      <button class="close" data-x style="flex:none" aria-label="Close">✕</button></div>
    <label for="edTitle">Title</label>
    <input id="edTitle" maxlength="200" value="${esc(d.title)}">
    <p class="muted" style="margin:10px 0 6px">Speaker notes are shown on the phone remote while you present.</p>
    <div class="ed-slides">${d.slides.map((s, i) => `
      <div class="ed-slide">
        <div class="ed-cover">${slideHTML(s, d, i)}<span class="ed-n">${i + 1}</span></div>
        <div class="ed-fields"><b data-noi18n>${esc(s.title && !/^s?\d+$/i.test(s.title) ? s.title : I18N.t('Slide') + ' ' + (i + 1))}</b>
          <textarea data-sid="${esc(s.id)}" rows="3" placeholder="Speaker notes for this slide">${esc(s.notes || '')}</textarea></div>
      </div>`).join('')}</div>
    <div class="row" style="gap:10px;margin-top:14px;flex:none">
      <button data-x style="flex:1">Cancel</button><button class="primary" data-save style="flex:1">Save</button></div>
  </div>`;
  document.body.appendChild(back);
  hsObserve(back);
  back.onclick = async e => {
    if (e.target === back || e.target.closest('[data-x]')) return back.remove();
    if (!e.target.closest('[data-save]')) return;
    const notes = {};
    back.querySelectorAll('textarea[data-sid]').forEach(t => {
      const s = d.slides.find(x => x.id === t.dataset.sid);
      if (s && (s.notes || '') !== t.value) notes[t.dataset.sid] = t.value;
    });
    const j = await api({ op: 'edit_deck', deck_id: id, title: back.querySelector('#edTitle').value, notes });
    if (j.ok) { delete deckCache[id]; back.remove(); toast('Saved'); loadLibrary(); }
  };
}

async function onLibMainClick(e) {
  const more = e.target.closest('[data-more]');
  if (more) { e.stopPropagation(); return openCardMenu(more); }
  const card = e.target.closest('.lib-card');
  if (card) return selectDeck(card.dataset.id);
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  const id = b.dataset.id, act = b.dataset.act;
  const deck = LIB.decks.find(d => d.id === id);
  if (act === 'present') {
    await api({ op: 'open_deck', deck_id: id });
    if (KIND === 'pc') window.open('/present', 'slidelink-present'); else location.href = '/remote';
  } else if (act === 'edit') {
    await api({ op: 'open_deck', deck_id: id }); location.href = '/edit';
  } else if (act === 'remote') {
    await api({ op: 'open_deck', deck_id: id }); location.href = '/remote';
  } else if (act === 'duplicate') {
    const j = await api({ op: 'duplicate_deck', deck_id: id });
    if (j.ok) { await loadLibrary(); selectDeck(j.result.deck_id); }
  } else if (act === 'delete' && deck) {
    if (confirm(`Delete "${deck.title}" with all ${deck.slide_count} slides? This cannot be undone.`)) {
      const j = await api({ op: 'delete_deck', deck_id: id });
      if (j.ok) { selectDeck(null); loadLibrary(); }
    }
  } else if (act === 'goto') {
    await api({ op: 'open_deck', deck_id: id });
    await api({ op: 'goto', index: +b.dataset.i });
    if (KIND === 'pc') window.open('/present', 'slidelink-present'); else location.href = '/remote';
  }
}

// EN: Cover picture of a presentation (its first slide).
// DE: Titelbild einer Präsentation (ihre erste Folie).
function coverHTML(d) {
  return d.cover ? slideHTML(d.cover, d, 0) : `<div class="slide theme-${esc(d.theme)}"><div class="inner"><p class="s-empty">Empty</p></div></div>`;
}
// EN: Progress bar of the slide statuses.
// DE: Fortschrittsbalken der Folienstatus.
function statusBar(counts, total) {
  if (!total) return '';
  const col = { 'done': 'var(--done)', 'review': 'var(--review)', 'in-progress': 'var(--progress)', 'draft': 'var(--draft)' };
  return `<div class="progress-track">${['done', 'review', 'in-progress', 'draft'].map(k =>
    `<div style="width:${100 * (counts[k] || 0) / total}%;background:${col[k]}"></div>`).join('')}</div>`;
}
// EN: '1 slide' / 'n slides'.
// DE: '1 slide' / 'n slides'.
const plural = n => `${n} slide${n === 1 ? '' : 's'}`;
// EN: 'From claude.ai' label.
// DE: Kennzeichnung 'From claude.ai'.
const sourceLabel = d => d.source && (d.source.kind === 'claude-slides' || d.source.kind === 'doc') ? 'From claude.ai' : '';

// EN: Draws the list, the overview grid or the detail view.
// DE: Zeichnet die Liste, das Übersichtsraster oder die Detailansicht.
function renderLibrary() {
  if (MODE !== 'library' || !$('#libList')) return;
  renderClaudeBar();
  if (S) {
    const others = (S.linked || []).filter(d => d.type !== 'host');
    setHTML($('#devCount'), others.length ? `${others.filter(d => d.online).length}/${others.length} online` : '');
  }
  if (!LIB) return;
  if (S && S.library_version !== LIB.version) loadLibrary();
  setHTML($('#libTabs'), tabsHTML(LIB.decks, libTab, 'data-tab'));
  const inTab = LIB.decks.filter(d => deckKind(d) === libTab);
  const decks = inTab.filter(d => !libQuery || d.title.toLowerCase().includes(libQuery));
  const days = groupByDay(decks);
  if (libSel && !LIB.decks.find(d => d.id === libSel)) libSel = null;

  setHTML($('#libList'), days.map(g => `<div class="lib-day">${esc(g.label)}</div>` + g.items.map(d => `
    <div class="lib-item ${d.id === libSel ? 'sel' : ''}" data-id="${esc(d.id)}">
      <div class="lib-item-cover">${coverHTML(d)}</div>
      <div class="lib-item-t"><span>${esc(d.title)}</span>
        <small>${countLabel(d)}${d.id === LIB.active ? ' · <b class="live">● open</b>' : ''}</small></div>
    </div>`).join('')).join('') || `<div class="muted" style="padding:12px">${libQuery ? 'No matches' : libTab === 'doc'
      ? 'No docs yet. Docs saved in “Claude Presentations\\Docs” appear here.' : 'No presentations yet'}</div>`);

  const main = $('#libMain');
  if (!libSel) {
    setHTML(main, `
      <div class="lib-home">
        <div class="lib-home-head"><h1>${libTab === 'doc' ? 'Docs' : 'Presentations'}</h1>
          <span class="muted">${inTab.length} ${libTab === 'doc' ? 'docs' : 'presentations'} · newest first</span></div>
        ${days.map(g => `<h3 class="lib-dayhead">${esc(g.label)}</h3>
        <div class="lib-grid">${g.items.map(d => `
          <div class="lib-card ${deckKind(d)}" data-id="${esc(d.id)}">
            <button class="card-more" data-more="${esc(d.id)}" title="More" aria-label="More">${IC('more')}</button>
            <div class="lib-card-cover">${coverHTML(d)}${d.id === LIB.active ? '<span class="badge badge-live lib-badge">OPEN</span>' : ''}</div>
            <div class="lib-card-t">${esc(d.title)}</div>
            <div class="lib-card-m"><span>${countLabel(d)}</span><span class="src">${sourceLabel(d)}</span></div>
          </div>`).join('')}
        </div>`).join('') || '<p class="muted">Nothing here yet.</p>'}
      </div>`);
    hsObserve(main);
    return;
  }
  const d = LIB.decks.find(x => x.id === libSel);
  const cached = deckCache[d.id] && deckCache[d.id].updated_at === d.updated_at ? deckCache[d.id] : null;
  const slidesGrid = cached ? cached.slides.map((s, i) => `
      <div class="lib-slide"><div class="lib-slide-cover">${slideHTML(s, cached, i)}</div>
        <div class="lib-slide-m"><span class="n">${i + 1}</span><span class="t">${esc(s.title || 'Untitled')}</span>
        <button class="mini" data-act="goto" data-id="${esc(d.id)}" data-i="${i}" title="Present from this slide">${IC('play')}</button></div></div>`).join('')
    : '<div class="muted">Loading slides…</div>';
  setHTML(main, `
    <div class="lib-detail">
      <div class="hero">
        <div class="hero-cover">${coverHTML(d)}</div>
        <div class="hero-info">
          <div class="hero-src">${sourceLabel(d)}</div>
          <h1>${esc(d.title)}</h1>
          <div class="hero-meta">${countLabel(d)} · ${esc(dayLabel(deckDate(d)))}${d.id === LIB.active ? ' · <b class="live">● open on all devices</b>' : ''}</div>
          ${statusBar(d.status_counts || {}, d.slide_count)}
          <div class="hero-actions">
            <button class="play" data-act="present" data-id="${esc(d.id)}">${IC('play')}<span>Present</span></button>
            <button data-act="edit" data-id="${esc(d.id)}">${IC('edit')}<span>Edit</span></button>
            <button data-act="remote" data-id="${esc(d.id)}">${IC('devices')}<span>Remote</span></button>
          </div>
          <div class="hero-more">
            <button class="link" data-act="duplicate" data-id="${esc(d.id)}">Duplicate</button>
            <button class="link danger" data-act="delete" data-id="${esc(d.id)}">Delete</button>
          </div>
        </div>
      </div>
      <h3 class="lib-h3">Slides</h3>
      <div class="lib-slides">${slidesGrid}</div>
    </div>`);
  hsObserve(main);
  if (!cached) loadDeck(d.id).then(() => renderLibrary());
}
