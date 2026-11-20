// SlideLink · EN: Audience page logic: follows the presentation live (read only) and lets viewers vote in polls.
// SlideLink · DE: Logik der Publikumsseite: folgt der Präsentation live (nur lesen) und lässt Zuschauer abstimmen.
'use strict';
const KEY = new URLSearchParams(location.search).get('k') || '';
const CLIENT = Math.random().toString(36).slice(2, 10);
const VOTER = localStorage.getItem('sl-voter') || (() => {   // one anonymous id per phone: one vote per person
  const v = Math.random().toString(36).slice(2) + Date.now().toString(36);
  localStorage.setItem('sl-voter', v); return v;
})();
HS_LOWPOWER = true;                                          // viewers' phones: animations at ~12 fps (battery)
let S = null, es = null, fails = 0, shownKey = null, frameReady = false;

// EN: Escapes text for HTML (hslide.js needs this helper).
// DE: Maskiert Text für HTML (hslide.js braucht diese Hilfsfunktion).
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
const $ = s => document.querySelector(s);

// EN: Live connection: every message is the reduced public state.
// DE: Live-Verbindung: jede Nachricht ist der reduzierte öffentliche Zustand.
function connect() {
  if (es) es.close();
  es = new EventSource(`/events?role=audience&k=${encodeURIComponent(KEY)}&client=${CLIENT}`);
  es.onmessage = e => { fails = 0; const m = JSON.parse(e.data); if (m.type === 'state') { S = m.state; render(); } };
  es.addEventListener('unlinked', () => { es.close(); message('This audience link was replaced. Ask the presenter for the new one.'); });
  es.onerror = () => {
    fails++;
    if (fails > 4 && !S) { es.close(); message('This audience link is not valid (any more), or the presentation has ended.'); }
  };
}

// EN: Shows a full message instead of the presentation.
// DE: Zeigt eine Meldung statt der Präsentation.
function message(text) { $('#lv').innerHTML = `<div class="lv-msg">${esc(text)}</div>`; shownKey = null; }

// EN: Builds the page once, then only updates what changed.
// DE: Baut die Seite einmal und aktualisiert dann nur, was sich ändert.
function render() {
  if (!$('#stage')) {
    $('#lv').innerHTML = `
      <div class="lv-top"><img src="/icon.svg" alt=""><b id="title"></b><span class="lv-live">● LIVE</span></div>
      <div class="lv-stage"><div id="stage"></div><div class="lv-black" id="black">Screen is black</div></div>
      <div class="lv-meta"><span id="count"></span><span id="speaker" class="lv-speaker"></span></div>
      <div id="poll"></div>`;
    shownKey = null;
  }
  const deck = S.deck, i = S.present.index || 0, s = deck.slides[i], step = S.present.step || 0;
  $('#title').textContent = deck.title;
  $('#count').textContent = deck.slides.length ? `Slide ${i + 1} / ${deck.slides.length}` : '';
  $('#speaker').textContent = S.speaker ? '🎤 ' + S.speaker.name : '';
  $('#black').classList.toggle('on', !!S.present.blackout);
  // slide: rebuilt only when the slide (or its content) changes; builds are sent to the running frame
  const key = deck.id + ':' + i + ':' + (s ? (s.html || s.content || '').length : 0);
  const stage = $('#stage');
  if (key !== shownKey) {
    shownKey = key; frameReady = false;
    stage.innerHTML = !s ? '' : s.layout === 'doc' ? hsDocView(s)
      : (s.layout === 'html' || s.layout === 'page') ? hsFrame(s, deck, i, true)
      : `<div class="slide"><div class="inner"><h1 class="s-title">${esc(s.title)}</h1><div class="s-body">${esc(s.content || '')}</div></div></div>`;
    hsObserve(stage);
  }
  const ds = stage.querySelector('.docscroll');                 // documents: each step scrolls one screen
  if (ds) ds.scrollTo({ top: step * ds.clientHeight * 0.85, behavior: 'smooth' });
  const f = stage.querySelector('iframe');
  if (f && frameReady) f.contentWindow.postMessage({ sl: 'step', step, animate: true }, '*');
  renderPoll();
}

// a slide frame finished loading: show the current build step
window.addEventListener('message', e => {
  const f = document.querySelector('#stage iframe');
  if (!f || e.source !== f.contentWindow || !e.data || e.data.sl !== 'ready') return;
  frameReady = true;
  f.contentWindow.postMessage({ sl: 'step', step: (S && S.present.step) || 0, animate: false }, '*');
});

// EN: The poll card: answer buttons while open; results after voting, when shown or when closed.
// DE: Die Umfragekarte: Antwortknöpfe, solange offen; Ergebnisse nach dem Abstimmen, wenn gezeigt oder wenn beendet.
function renderPoll() {
  const box = $('#poll'), p = S.poll;
  if (!p) { box.innerHTML = ''; return; }
  const mine = localStorage.getItem('sl-vote-' + p.id);
  const results = mine !== null || p.show || !p.open;
  const html = `<div class="lv-poll"><h3>📊 ${esc(p.question)}</h3>${p.options.map((o, n) => {
    const pct = p.total ? Math.round(100 * p.counts[n] / p.total) : 0;
    return `<button class="lv-opt ${String(n) === mine ? 'mine' : ''}" data-opt="${n}" ${p.open ? '' : 'disabled'}>
      ${results ? `<i class="bar" style="width:${pct}%"></i>` : ''}<span>${esc(o)}</span>${results ? `<span class="n">${pct}%</span>` : ''}</button>`;
  }).join('')}<div class="lv-note">${!p.open ? 'Poll closed' : mine !== null ? `Thanks for voting! ${p.total} vote${p.total === 1 ? '' : 's'} · tap to change`
    : 'Tap an answer to vote'}</div></div>`;
  if (box._h !== html) { box.innerHTML = html; box._h = html; }
}

document.addEventListener('click', async e => {
  const b = e.target.closest('[data-opt]');
  if (!b || !S || !S.poll || !S.poll.open) return;
  const poll = S.poll.id, option = +b.dataset.opt;
  localStorage.setItem('sl-vote-' + poll, String(option));
  renderPoll();
  const r = await fetch('/api/vote', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ k: KEY, poll, option, voter: VOTER }) }).then(r => r.json()).catch(() => ({ ok: false }));
  if (!r.ok) { localStorage.removeItem('sl-vote-' + poll); renderPoll(); alert(r.error || 'Could not vote. Try again.'); }
});

if (!KEY) message('This link is incomplete. Scan the QR code shown by the presenter.');
else connect();
