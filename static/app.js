// SlideLink: Core of the web UI: live connection, linking, devices, popups, editor, presenter and navigation.
'use strict';
/* SlideLink client: library (/), editor (/edit), presenter (/present) and phone remote (/remote). */

const MODE = location.pathname.startsWith('/present') ? 'present'
  : location.pathname.startsWith('/remote') ? 'remote'
  : location.pathname.startsWith('/group') ? 'group'
  : location.pathname.startsWith('/edit') ? 'editor' : 'library';
const GROUP_KEY = 'slidelink-group';                 // {k, token, name} of a joined group session
const groupLogin = () => { try { return JSON.parse(localStorage.getItem(GROUP_KEY) || 'null'); } catch (_) { return null; } };
const CLIENT = Math.random().toString(36).slice(2, 10);
// tell the PC at once when this window closes, so phones see it immediately
addEventListener('pagehide', () => {
  try { navigator.sendBeacon('/api/bye', new Blob([JSON.stringify({ client: CLIENT })], { type: 'application/json' })); } catch (_) {}
});
const KIND = /iPad|Tablet/i.test(navigator.userAgent) || (/Android/i.test(navigator.userAgent) && !/Mobile/i.test(navigator.userAgent))
  ? 'tablet' : /Android|iPhone|Mobile/i.test(navigator.userAgent) ? 'phone' : 'pc';
let DEVICE = 'PC';     // how this device is named in "last edited by"; set from the server after linking
let ME = null;         // {id, name, type} of this device
const TOKEN_KEY = 'slidelink-token';
// Android app: the app keeps the login for every address (relay, Wi-Fi)
if (MODE !== 'group' && window.SlideLinkApp && SlideLinkApp.getToken) {
  try {
    const mine = localStorage.getItem('slidelink-token'), kept = SlideLinkApp.getToken();
    if (mine && mine !== kept) SlideLinkApp.setToken(mine);
    else if (!mine && kept) localStorage.setItem('slidelink-token', kept);
  } catch (_) {}
}
// Android app: tell it the PC's current home-Wi-Fi addresses, so it can switch to Wi-Fi automatically at home
// Android app only: tells it the PC's current addresses (home Wi-Fi, relay).
function sendLanToApp() {
  if (!(window.SlideLinkApp && SlideLinkApp.setLanUrls)) return;
  fetch('/api/info', { headers: authHeaders() }).then(r => r.ok ? r.json() : null).then(j => {
    if (!j || !j.urls) return;
    SlideLinkApp.setLanUrls(j.urls.filter(u => u.kind === 'lan').map(u => u.url).join(' '));
    if (SlideLinkApp.setCertPin && j.tls && j.tls.sha256) SlideLinkApp.setCertPin(j.tls.sha256);   // the PC's own HTTPS certificate
    if (SlideLinkApp.setRemoteUrls) {
      SlideLinkApp.setRemoteUrls(j.urls.filter(u => u.kind === 'relay').map(u => u.url).join(' '));
    }
  }).catch(() => {});
}
// Header with the device token for API calls.
const authHeaders = () => MODE === 'group'
  ? (groupLogin() ? { 'X-SlideLink-Group': groupLogin().token } : {}) : (localStorage.getItem(TOKEN_KEY) ? { 'X-SlideLink-Token': localStorage.getItem(TOKEN_KEY) } : {});
const $ = (s, r = document) => r.querySelector(s);

const STATUS = { 'draft': 'Draft', 'in-progress': 'In progress', 'review': 'Review', 'done': 'Done' };
const LAYOUTS = { 'title': 'Title slide', 'content': 'Title + content', 'section': 'Section header',
  'two-column': 'Two columns', 'quote': 'Quote', 'image': 'Image', 'html': 'Designed slide (HTML)',
  'page': 'Interactive page (HTML app)', 'doc': 'Document (pages)' };
// Which field holds a slide's body: 'html' for designed slides, pages and docs, else 'content'.
const bodyField = s => (s && (s.layout === 'html' || s.layout === 'page' || s.layout === 'doc') ? 'html' : 'content');
const THEMES = { midnight: 'Midnight', paper: 'Paper', ocean: 'Ocean', sunset: 'Sunset' };

let S = null;          // latest server state
let skew = 0;          // server clock - local clock (seconds)
let selectedId = sessionStorage.getItem('sl-sel');

/* ---------------- helpers ---------------- */
// Escapes text for safe use in HTML.
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
// Inline Markdown (bold, italic, code) to HTML.
function inline(s) {
  return esc(s)
    .replace(/!\[([^\]]*)\]\(((?:https?:\/\/|\/)[^\s)]+)\)/g, '<img alt="$1" src="$2">')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}
// Simple Markdown (lists, paragraphs) to HTML for basic slides.
function md(text) {
  let html = '', depth = 0;
  const close = to => { while (depth > to) { html += '</ul>'; depth--; } };
  for (const raw of String(text || '').split(/\r?\n/)) {
    const m = raw.match(/^(\s*)[-*•]\s+(.*)$/);
    if (m) {
      const lvl = Math.min(3, Math.floor(m[1].replace(/\t/g, '  ').length / 2) + 1);
      if (lvl > depth) while (depth < lvl) { html += '<ul>'; depth++; } else close(lvl);
      html += `<li>${inline(m[2])}</li>`;
      continue;
    }
    close(0);
    const line = raw.trim();
    if (!line) continue;
    if (line === '---' || line === '|||') html += '</div><div class="col">';
    else if (/^#{1,6}\s/.test(line)) html += `<h3>${inline(line.replace(/^#+\s/, ''))}</h3>`;
    else if (line.startsWith('> ')) html += `<blockquote>${inline(line.slice(2))}</blockquote>`;
    else html += `<p>${inline(line)}</p>`;
  }
  close(0);
  return `<div class="col">${html}</div>`;
}
// HTML for one slide: designed slide or page in a frame, document view, or a basic layout.
function slideHTML(s, deck, index, live) {
  if (typeof deck === 'string' || !deck) deck = { theme: deck || 'midnight' };
  if (s && s.layout === 'doc') return hsDocView(s);
  if (s && (s.layout === 'html' || s.layout === 'page')) return hsFrame(s, deck, index || 0, !!live);
  const t = esc(deck.theme || 'midnight');
  if (!s) return `<div class="slide theme-${t}"><div class="inner"><p class="s-empty">No slides yet</p></div></div>`;
  return `<div class="slide theme-${t} layout-${esc(s.layout)}"><div class="inner">` +
    (s.title ? `<h1 class="s-title">${inline(s.title)}</h1>` : '') +
    `<div class="s-body">${md(s.content)}</div></div></div>`;
}
// Sets innerHTML only if it changed (avoids flicker and reloading frames).
function setHTML(el, html) { if (el && el._h !== html) { el.innerHTML = html; el._h = html; hsObserve(el); } }
// Updates an input field only while the user is not typing in it.
function setIfIdle(el, v) { if (el && document.activeElement !== el && el.value !== (v ?? '')) el.value = v ?? ''; }
// Current time in seconds, corrected to the server clock.
const nowSec = () => Date.now() / 1000 + skew;
// Readable 'x minutes ago' text.
function ago(t) {
  const s = Math.max(0, Math.round(nowSec() - t));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
// Shows a short message in the status pill.
function toast(msg) {
  const c = $('#conn');
  c.textContent = msg; c.classList.remove('ok');
  clearTimeout(toast.t); toast.t = setTimeout(() => S && c.classList.add('ok'), 3000);
}
// POST JSON to the server; shows errors and the link screen if the device was unlinked.
function post(path, body) {
  return fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body),
  }).then(async r => {
    const j = await r.json().catch(() => ({ ok: false, error: 'Bad response' }));
    if (r.status === 401 && path !== '/api/pair') { MODE === 'group' ? showGroupEnded() : showLinkScreen(); return j; }
    if (!j.ok) toast(j.error);
    return j;
  }).catch(e => { toast('Not connected: ' + e.message); return { ok: false }; });
}
// Runs a SlideLink operation on the server.
const api = op => post('/api/op', { by: DEVICE, ...op, origin: CLIENT });
// Slides of the open deck.
const slides = () => (S ? S.deck.slides : []);
// True while Claude is working on the presentation.
const claudeActive = () => S && S.claude.text && nowSec() - S.claude.at < 900;
// True if Claude is working on this slide right now.
function claudeOn(s) {
  return (claudeActive() && S.claude.slide_id === s.id) || (s.updated_by === 'claude' && nowSec() - s.updated_at < 8);
}
// Shows Claude's live status bar.
function renderClaudeBar() {
  const bar = $('#claudeBar');
  if (!bar) return;
  const on = claudeActive();
  bar.classList.toggle('on', !!on);
  if (!on) return;
  const i = slides().findIndex(s => s.id === S.claude.slide_id);
  setHTML(bar, `<span class="dot"></span><b>Claude</b><span>${esc(S.claude.text)}</span>` +
    (i >= 0 ? `<span class="muted">· slide ${i + 1}</span>` : '') +
    `<span class="muted" style="margin-left:auto">${ago(S.claude.at)}</span>`);
}

/* ---------------- live connection (never gives up) ---------------- */
let es = null, lastBeat = 0, retryT = null, retryDelay = 1000, stopped = false;
// Opens the live connection (SSE); every message is the full state. Reconnects by itself.
function connect() {
  if (stopped) return;
  clearTimeout(retryT);
  if (es) es.close();
  const appv = window.SlideLinkApp && SlideLinkApp.version ? '&appv=' + encodeURIComponent(SlideLinkApp.version()) : '';
  es = new EventSource(`/events?role=${MODE === 'group' ? 'remote' : MODE}&client=${CLIENT}${appv}`);
  lastBeat = Date.now();
  es.onopen = () => { retryDelay = 1000; sendLanToApp(); };
  es.onmessage = e => {
    lastBeat = Date.now();
    const m = JSON.parse(e.data);
    if (m.type !== 'state') return;
    const prev = S;
    S = m.state;
    I18N.fromServer(S.lang);            // the admin's language (or this guest's own choice)
    // SlideLink was updated on the PC: reload this window (it comes back at the same slide)
    if (window.__build && S.build && S.build !== window.__build && !document.querySelector('.modal-back')) {
      return location.reload();
    }
    window.__build = window.__build || S.build;
    if (prev && navPending > 0 && prev.deck.id === S.deck.id) {
      // our own taps are still on their way: keep showing where we are going, not where the server was
      S.present = { ...S.present, index: prev.present.index, step: prev.present.step, blackout: prev.present.blackout };
    }
    if (prev && S.present.resync && S.present.resync !== prev.present.resync) {
      S.present = m.state.present;  // a Resync was pressed somewhere: rebuild this screen too
      skew = S.server_time - Date.now() / 1000;
      $('#conn').classList.add('ok');
      return rebuildViews();
    }
    skew = S.server_time - Date.now() / 1000;
    checkPhoneUpdate();
    overlayPending();
    $('#conn').classList.add('ok');
    render(prev);
  };
  es.addEventListener('ping', () => { lastBeat = Date.now(); });
  es.addEventListener('unlinked', () => {
    if (MODE === 'group') return showGroupEnded();
    localStorage.removeItem(TOKEN_KEY);
    if (window.SlideLinkApp && SlideLinkApp.setToken) SlideLinkApp.setToken('');
    showLinkScreen('This device was unlinked from the PC.');
  });
  es.onerror = () => {
    const c = $('#conn'); c.classList.remove('ok');
    c.textContent = retryDelay > 2000 ? 'Can’t reach SlideLink on your PC – is it running? Retrying…' : 'Reconnecting…';
    if (es.readyState === EventSource.CLOSED) scheduleReconnect(); // browser gave up (e.g. server down / 401)
  };
}
// Tries to reconnect after a growing pause (max 8 s).
async function scheduleReconnect() {
  clearTimeout(retryT);
  retryT = setTimeout(async () => {
    const who = await whoami();
    if (who && !who.linked) return MODE === 'group' ? showGroupEnded() : showLinkScreen();
    connect();
  }, retryDelay);
  retryDelay = Math.min(retryDelay * 1.6, 8000);
}
// Watchdog: phones silently drop connections when asleep / switching Wi-Fi. Pings come every 10 s.
setInterval(() => { if (es && !stopped && Date.now() - lastBeat > 25000) connect(); }, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && es && Date.now() - lastBeat > 12000) connect(); });
window.addEventListener('online', () => es && connect());

// Asks the server whether this device is linked.
async function whoami() {
  try {
    const r = await fetch('/api/whoami', { headers: authHeaders(), cache: 'no-store' });
    return await r.json();
  } catch { return null; }
}

/* ---------------- one-time linking ---------------- */
// Shows the screen to link this device with the 6-digit code.
function showLinkScreen(message) {
  stopped = true;
  if (es) es.close();
  document.querySelectorAll('.modal-back').forEach(m => m.remove());
  $('#conn').classList.add('ok');
  const defaultName = { phone: 'My phone', tablet: 'My tablet', pc: 'My laptop' }[KIND];
  $('#app').innerHTML = `
    <div class="link-screen">
      <img src="/icon.svg" alt="" width="72" height="72">
      <h2>Link this ${KIND} to SlideLink</h2>
      ${message ? `<p class="link-msg">${esc(message)}</p>` : ''}
      <p class="muted">You only do this once. On your PC open SlideLink → <b>🔗 Linked devices</b> → <b>Link a new device</b>, then type the code shown there.</p>
      <form id="linkForm">
        <label for="code">Link code</label>
        <input id="code" class="code-input" inputmode="numeric" autocomplete="one-time-code" maxlength="7" placeholder="000000" required>
        <label for="devName">Name this device</label>
        <input id="devName" maxlength="40" value="${esc(defaultName)}">
        <button class="primary" style="width:100%;margin-top:16px;padding:14px">Link device</button>
        <p class="link-err" id="linkErr"></p>
      </form>
      ${/Android/.test(navigator.userAgent) && !/SlideLinkApp/.test(navigator.userAgent)
        ? '<a class="get-app" href="/SlideLink.apk" download>⬇ Get the SlideLink Android app</a>' : ''}
    </div>`;
  $('#code').focus();
  $('#code').oninput = e => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6);
    if (e.target.value.length === 6) $('#devName').focus();
  };
  $('#linkForm').onsubmit = async e => {
    e.preventDefault();
    $('#linkErr').textContent = '';
    if (window.SlideLinkApp && SlideLinkApp.pairE2E && KIND !== 'pc') {
      // app 1.16+: the app links end-to-end encrypted (it shows a number to compare with the PC)
      window.__slPairErr = msg => { $('#linkErr').textContent = msg; };
      SlideLinkApp.pairE2E($('#code').value, $('#devName').value);
      return;
    }
    const j = await post('/api/pair', { code: $('#code').value, name: $('#devName').value, type: KIND });
    if (j.ok) {
      localStorage.setItem(TOKEN_KEY, j.token);
      if (window.SlideLinkApp && SlideLinkApp.setToken) SlideLinkApp.setToken(j.token);
      location.href = KIND === 'pc' ? '/' : '/remote';  // library on PC, remote on phones
    } else {
      $('#linkErr').textContent = j.error || 'Could not link. Is the PC on the same Wi-Fi?';
    }
  };
}

/* ---------------- Linked devices panel ---------------- */
let devModal = null, linkCode = null, linkedNotice = null, tsInfo = null;
const VIA_LABEL = { e2e: '🔒 end-to-end via Relay', relay: '🌍 via SlideLink Relay', wifi: '📶 via Wi-Fi', internet: '🔒 via internet (HTTPS)' };
const TYPE_ICON = { host: '🖥', pc: '💻', phone: '📱', tablet: '📟' };
const ROLE_LABEL = { editor: 'editing', present: 'presenting', remote: 'remote control' };

/* PC: a small popup when a linked phone has an older SlideLink app than the one ready on this PC.
   Not in the presenter (never on stage). "Later" hides it until the next new version. */
// Version text like '1.7' as a list of numbers.
const verNum = v => String(v || '').split('.').map(n => parseInt(n, 10) || 0);
// True if version a is older than version b.
const verOlder = (a, b) => { const x = verNum(a), y = verNum(b); for (let i = 0; i < Math.max(x.length, y.length); i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0); } return false; };
// PC: popup when a linked phone has an older app than the newest one ready.
function checkPhoneUpdate() {
  if (KIND !== 'pc' || MODE === 'present' || !S || !S.app_latest || document.getElementById('phoneUpd')) return;
  const latest = S.app_latest.versionName;
  if (localStorage.getItem('sl-upd-dismissed') === latest) return;
  const behind = (S.linked || []).filter(d => d.type !== 'host' && d.app_version && verOlder(d.app_version, latest));
  if (!behind.length) return;
  const el = document.createElement('div');
  el.id = 'phoneUpd'; el.className = 'upd-pop';
  el.innerHTML = `<div class="upd-ic">📱</div><div class="upd-t"><b>Update for your phone</b>
      <span>SlideLink ${esc(latest)} is ready${behind.length === 1 ? ` (${esc(behind[0].name)} has ${esc(behind[0].app_version)})` : ''}.
      Open SlideLink on the phone and tap <b>Update</b>.</span>
      ${S.app_latest.notes ? `<small>${esc(S.app_latest.notes)}</small>` : ''}</div>
    <button class="upd-x" title="Later">Later</button>`;
  el.querySelector('.upd-x').onclick = () => { localStorage.setItem('sl-upd-dismissed', latest); el.remove(); };
  document.body.appendChild(el);
}

// The group link for co-presenters (HTTPS if available).
function groupUrl() {
  const g = S && S.group;
  return g && g.key ? ((S.audience && S.audience.url) || location.origin) + '/group?k=' + encodeURIComponent(g.key) : '';
}

// Join screen of a group session: name, then continue in the browser or open the SlideLink app.
function showGroupJoin(message) {
  stopped = true;
  if (es) es.close();
  const k = new URLSearchParams(location.search).get('k') || '';
  const inApp = !!window.SlideLinkApp;
  // iPhone/iPad: no app (Android only). Any other phone or tablet gets the app button, even if its browser hides 'Android'
  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isPhone = !isIOS && (/Android|Mobile|Linux/.test(navigator.userAgent) || navigator.maxTouchPoints > 1 || matchMedia('(pointer: coarse)').matches);
  const appLink = `intent://group?u=${encodeURIComponent(location.origin)}&k=${encodeURIComponent(k)}` +
    `#Intent;scheme=slidelink;package=app.slidelink.remote;S.browser_fallback_url=${encodeURIComponent(location.origin + '/SlideLink.apk')};end`;
  $('#conn').classList.add('ok');
  $('#app').innerHTML = `
    <div class="link-screen">
      <img src="/icon.svg" alt="" width="72" height="72">
      <h2>Present together</h2>
      ${message ? `<p class="link-msg">${esc(message)}</p>` : ''}
      <p class="muted">You were invited to co-present. You can move through the slides, start polls and see the notes, but not change the presentation.</p>
      <form id="groupForm">
        <label for="gName">Your name</label>
        <input id="gName" maxlength="30" autocomplete="name" placeholder="e.g. Anna" value="${esc((groupLogin() || {}).name || '')}" required>
        <button class="primary" style="width:100%;margin-top:16px;padding:14px">${inApp ? 'Join' : 'Continue in the browser'}</button>
        <p class="link-err" id="gErr"></p>
      </form>
      ${inApp ? '' : isIOS ? `<p class="muted" style="font-size:12px;margin-top:14px">The SlideLink app is only available for Android. On iPhone everything works right here in the browser.</p>`
        : isPhone ? `<a class="get-app" href="${appLink}">📱 Open in the SlideLink app</a>
        <p class="muted" style="font-size:12px;margin-top:6px">Android: opens the app. Not installed? The download starts instead.</p>` : ''}
    </div>`;
  $('#groupForm').onsubmit = async e => {
    e.preventDefault();
    const r = await fetch('/api/group/join', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ k, name: $('#gName').value }) }).then(r => r.json()).catch(() => ({ ok: false, error: 'Not connected' }));
    if (!r.ok) { $('#gErr').textContent = r.error || 'Could not join.'; return; }
    localStorage.setItem(GROUP_KEY, JSON.stringify({ k, token: r.token, name: r.name }));
    location.reload();
  };
}

// The group session ended (or this person was removed): nothing stays linked.
function showGroupEnded() {
  stopped = true;
  if (es) es.close();
  localStorage.removeItem(GROUP_KEY);
  document.querySelectorAll('.modal-back').forEach(m => m.remove());
  $('#conn').classList.add('ok');
  const leave = window.SlideLinkApp && SlideLinkApp.leaveGroup;
  $('#app').innerHTML = `<div class="link-screen"><img src="/icon.svg" alt="" width="72" height="72">
    <h2>The group session has ended</h2><p class="muted">Thanks for presenting! You are no longer connected to this presentation.</p>
    ${leave ? '<button class="primary" id="leaveBtn" style="width:100%;padding:14px">Back to my SlideLink</button>' : ''}</div>`;
  if (leave) $('#leaveBtn').onclick = () => SlideLinkApp.leaveGroup();
}

// Admin controls for the group session (PC popup and admin phone): start, QR, members, end.
function groupAdminHTML() {
  const g = (S && S.group) || {};
  if (!g.active) return `<h4>🎤 Present in group</h4>
    <p class="muted">Co-presenters join with a QR code, in the browser or the app. They control the talk only and are disconnected when the session ends.</p>
    <button class="primary" data-grp="start" style="width:100%">Start group session</button>`;
  const left = Math.max(0, Math.round((g.expires - nowSec()) / 60));
  return `<h4>🎤 Group session</h4>
    <p class="muted">${g.members.length} joined · ends by itself in ${Math.floor(left / 60)}h ${left % 60}min</p>
    <div class="grp-members">${g.members.map(m => `<div class="grp-m"><span class="${m.online ? 'on-dot' : 'off-dot'}"></span>${esc(m.name)}
      <button class="link danger" data-grp="kick" data-id="${esc(m.id)}">remove</button></div>`).join('') || '<p class="muted">Nobody yet.</p>'}</div>
    <div class="lv-row"><button data-grp="qr" class="${g.qr ? 'on' : ''}">${g.qr ? 'Hide QR on screen' : '📺 Show QR on screen'}</button>
      <button data-grp="copy">Copy link</button></div>
    <button class="danger-btn" data-grp="end" style="width:100%">End group session</button>`;
}
async function groupAction(kind, el) {
  if (kind === 'start') return api({ op: 'group_start' });
  if (kind === 'qr') return api({ op: 'group_qr', on: !(S.group && S.group.qr) });
  if (kind === 'copy') return navigator.clipboard.writeText(groupUrl()).then(() => toast('Group link copied'));
  if (kind === 'kick') return api({ op: 'group_kick', member: el.dataset.id });
  if (kind === 'end' && confirm('End the group session? Everyone in the group is disconnected.')) return api({ op: 'group_end' });
}
document.addEventListener('click', e => { const b = e.target.closest('[data-grp]'); if (b) { e.stopPropagation(); groupAction(b.dataset.grp, b); } }, true);

// The audience link (HTTPS if available) for viewers' phones.
function audienceUrl() {
  const a = S && S.audience;
  return a ? (a.url || location.origin) + '/live?k=' + encodeURIComponent(a.key) : '';
}

// Poll results as HTML bars (used on the presentation screen and the phone).
function pollBarsHTML(p) {
  return p.options.map((o, n) => {
    const pct = p.total ? Math.round(100 * p.counts[n] / p.total) : 0;
    return `<div class="pbar"><div class="pbar-t"><span>${esc(o)}</span><b>${pct}% · ${p.counts[n]}</b></div>
      <div class="pbar-track"><i style="width:${pct}%"></i></div></div>`;
  }).join('');
}

// Presentation screen: big audience QR, live poll results and the current speaker on top of the slide.
function renderLiveOverlay() {
  let ov = document.getElementById('liveOv');
  if (!ov) { ov = document.createElement('div'); ov.id = 'liveOv'; document.body.appendChild(ov); }
  const a = S.audience || {}, p = S.poll;
  let html = '';
  if (a.qr && a.key) html += `<div class="lo-card lo-qr"><div class="lo-qrbox">${QR.svg(audienceUrl(), 360)}</div>
      <div><h2>Follow live on your phone</h2><p>Scan the code to see the slides and vote in polls.</p>
      <p class="lo-small">${a.count || 0} watching</p></div></div>`;
  if (S.group && S.group.qr && S.group.key) html += `<div class="lo-card lo-qr lo-grp"><div class="lo-qrbox">${QR.svg(groupUrl(), 360)}</div>
      <div><h2>Co-presenters: join here</h2><p>Scan to control the slides together (browser or app).</p>
      <p class="lo-small">${S.group.members.length} joined</p></div></div>`;
  if (p && p.show) html += `<div class="lo-card lo-poll"><h2>📊 ${esc(p.question)}</h2>${pollBarsHTML(p)}
      <p class="lo-small">${p.total} vote${p.total === 1 ? '' : 's'}${p.open ? ' · voting is open' : ' · closed'}</p></div>`;
  if (S.speaker) html += `<div class="lo-speaker">🎤 ${esc(S.speaker.name)}</div>`;
  if (ov._h !== html) { ov.innerHTML = html; ov._h = html; }
}

// PC: the audience link as a popup with QR code, copy, "show on screen" and a new link.
function openAudience() {
  if (!S || !S.audience) return;
  const back = document.createElement('div');
  back.className = 'modal-back';
  const draw = () => `<div class="modal qr-modal">
    <div class="row" style="align-items:center;flex:none"><h3 style="margin:0">👥 Group & audience</h3>
      <button class="close" data-x style="flex:none" aria-label="Close">✕</button></div>
    <div class="lv-sec grp-sec" id="pcGroup">${groupAdminHTML()}</div>
    <h4 style="margin:6px 0 0;text-align:left">👥 Audience link</h4>
    <p class="muted" style="margin:8px 0 14px">Viewers scan this and see the current slide live on their phone and can vote in polls.
      Watch only: no control, no notes. ${S.audience.count || 0} watching now.</p>
    <div class="qr-box">${QR.svg(audienceUrl(), 240)}</div>
    <div class="row" style="gap:10px;margin-top:16px">
      <button class="primary" data-show style="flex:1">${S.audience.qr ? 'Hide QR on screen' : '📺 Show QR on screen'}</button>
      <button data-copy style="flex:1">Copy link</button>
    </div>
    <button class="link" data-new style="margin-top:10px">New link (the old one stops working)</button>
  </div>`;
  back.innerHTML = draw();
  document.body.appendChild(back);
  const live = setInterval(() => { if (!back.isConnected) return clearInterval(live); setHTML(back.querySelector('#pcGroup'), groupAdminHTML()); }, 1000);
  back.onclick = async e => {
    if (e.target === back || e.target.closest('[data-x]')) return back.remove();
    if (e.target.closest('[data-copy]')) { navigator.clipboard.writeText(audienceUrl()); e.target.textContent = 'Copied ✓'; return; }
    if (e.target.closest('[data-show]')) { await api({ op: 'join_qr', on: !S.audience.qr }); back.remove(); return; }
    if (e.target.closest('[data-new]') && confirm('Make a new audience link? Everyone watching with the old link is disconnected.')) {
      await api({ op: 'audience_new' }); setTimeout(() => { back.innerHTML = draw(); }, 400);
    }
  };
}

// General settings (gear at the top of the library). New settings are added here as further sections.
function openSettings() {
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal settings-modal">
    <div class="row" style="align-items:center;flex:none"><h3 style="margin:0">⚙ Settings</h3>
      <button class="close" data-x style="flex:none" aria-label="Close">✕</button></div>
    <section class="set-sec">
      <h4>Appearance</h4>
      <div class="set-row">
        <div><b>🌓 Brightness</b><p class="muted">For this device only. Slides and the presentation screen keep their own colors.</p></div>
        <div class="seg" id="setTheme">${[['light', '☀️ Light'], ['dark', '🌙 Dark'], ['system', '💻 System']].map(([k, v]) =>
          `<button data-theme="${k}" class="${THEME.choice() === k ? 'on' : ''}">${v}</button>`).join('')}</div>
      </div>
    </section>
    ${MODE === 'group' ? '' : `<section class="set-sec">
      <h4>General</h4>
      <div class="set-row">
        <div><b>🌐 Language</b><p class="muted">SlideLink on all screens: PC, phones, co-presenters and audience. Guests can still choose their own.</p></div>
        <select id="setLang" data-noi18n>${Object.entries(I18N_LANGS).map(([k, v]) => `<option value="${k}" ${S && S.lang === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
      </div>
    </section>`}
  </div>`;
  document.body.appendChild(back);
  const sl = back.querySelector('#setLang');
  if (sl) sl.onchange = e => api({ op: 'set_lang', lang: e.target.value });
  back.querySelector('#setTheme').onclick = e => {
    const b = e.target.closest('[data-theme]');
    if (!b) return;
    THEME.set(b.dataset.theme);
    back.querySelectorAll('#setTheme button').forEach(x => x.classList.toggle('on', x === b));
  };
  back.onclick = e => { if (e.target === back || e.target.closest('[data-x]')) back.remove(); };
}

/* "Linked devices" on the PC: with no phone connected, first offer the app download as a QR code. */
// PC 'Linked devices' button: with no phone online, first a QR code to download the app.
async function openConnect() {
  const online = (S && S.linked || []).filter(d => d.type !== 'host' && d.online);
  if (online.length) return openDevices();
  const info = await fetch('/api/info', { headers: authHeaders() }).then(r => r.json()).catch(() => null);
  const base = info && info.relay && info.relay.on ? info.relay.url : location.origin;   // the relay works on any network
  const url = base + '/SlideLink.apk';
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal qr-modal">
    <div class="row" style="align-items:center;flex:none"><h3 style="margin:0">📱 No phone connected</h3>
      <button class="close" data-x style="flex:none" aria-label="Close">✕</button></div>
    <p class="muted" style="margin:8px 0 14px">Scan this with your phone's camera to download the <b>SlideLink app</b>.
      Install it, open it, then link it with a code from here.</p>
    <div class="qr-box">${QR.svg(url, 240)}</div>
    <div class="qr-url"><button class="link" data-copy>Copy download link</button></div>
    <div class="row" style="gap:10px;margin-top:16px">
      <button class="primary" data-link style="flex:1">+ Link a new device</button>
      <button data-devs style="flex:1">🔗 Linked devices</button>
    </div>
  </div>`;
  document.body.appendChild(back);
  back.onclick = e => {
    if (e.target === back || e.target.closest('[data-x]')) return back.remove();
    if (e.target.closest('[data-copy]')) { navigator.clipboard.writeText(url).then(() => { e.target.textContent = 'Link copied ✓'; }); return; }
    if (e.target.closest('[data-devs]')) { back.remove(); return openDevices(); }
    if (e.target.closest('[data-link]')) {
      back.remove(); openDevices();
      setTimeout(() => { const b = devModal && [...devModal.querySelectorAll('button')].find(x => /Link a new device/.test(x.textContent)); if (b) b.click(); }, 300);
    }
  };
}
// Opens the linked devices panel.
function openDevices() {
  devModal = document.createElement('div');
  devModal.className = 'modal-back';
  devModal.innerHTML = `<div class="modal devices-modal">
    <div class="row" style="align-items:center;flex:none"><h3 style="margin:0">🔗 Linked devices</h3>
      <button class="close" id="devClose" style="flex:none" aria-label="Close">✕</button></div>
    <p class="muted" style="margin:6px 0 12px">Linked devices stay connected permanently and reconnect on their own.
      Only linked devices can see or control your presentations.</p>
    ${MODE === 'library' ? '' : '<button class="link set-link" id="devSettings">⚙ Settings (language, brightness)</button>'}
    <div class="ts-box" id="tsBox"></div>
    <ul class="dev-list" id="devList"></ul>
    <div class="link-box" id="linkBox"></div>
    ${window.SlideLinkApp
      ? `<button class="get-app" id="appUpdate">📲 SlideLink app ${esc(SlideLinkApp.version())} · Check for updates</button>`
      : /Android/.test(navigator.userAgent)
        ? '<a class="get-app" href="/SlideLink.apk" download>⬇ Get the SlideLink Android app</a>' : ''}
  </div>`;
  document.body.appendChild(devModal);
  devModal.addEventListener('click', onDevClick);
  const ds = $('#devSettings', devModal);
  if (ds) ds.onclick = e => { e.stopPropagation(); openSettings(); };
  const up = $('#appUpdate', devModal);
  if (up) up.onclick = () => SlideLinkApp.checkUpdate();
  loadRelay();
  renderDevices();
}
// Loads the relay status for the devices panel.
async function loadRelay() {
  const r = await fetch('/api/info', { headers: authHeaders() }).then(r => r.json()).catch(() => null);
  if (r && r.relay) { tsInfo = r.relay; renderDevices(); }
}
setInterval(() => devModal && loadRelay(), 15000);
// Info box about reaching the PC from anywhere.
function tsBoxHTML() {
  if (!tsInfo) return '';
  if (tsInfo.on && tsInfo.online) {
    return `<div class="ts on"><b>🌍 Anywhere access is on</b>
      <span>Linked phones reach SlideLink through the SlideLink Relay on mobile data or any Wi-Fi – no extra app needed.</span></div>`;
  }
  if (tsInfo.on) {
    return `<div class="ts off"><b>🌍 Anywhere access: relay not reachable</b>
      <span>SlideLink can't reach the relay right now (no internet?). Phones on the same Wi-Fi still work. Trying again automatically.</span></div>`;
  }
  return `<div class="ts off"><b>🌍 Anywhere access is off (same Wi-Fi only)</b>
    <span>No relay is set up. Phones on the same Wi-Fi work as usual.</span></div>`;
}
// Closes the devices panel.
function closeDevices() { if (devModal) devModal.remove(); devModal = null; }

// Clicks in the devices panel: link code, rename, unlink.
async function onDevClick(e) {
  if (e.target === devModal || e.target.id === 'devClose') return closeDevices();
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  const id = b.dataset.id, dev = S.linked.find(d => d.id === id);
  if (b.dataset.act === 'e2eyes' || b.dataset.act === 'e2eno') {
    const yes = b.dataset.act === 'e2eyes';
    if (yes || confirm('The numbers are different? Then someone may be in between. The pairing is cancelled and the code stops working.')) {
      const j = await post('/api/e2e/pair/confirm', { id: b.dataset.pid, yes });
      if (j.ok) linkCode = yes ? linkCode : null;
      renderDevices();
    }
  } else if (b.dataset.act === 'newcode') {
    const j = await post('/api/devices/code', {});
    if (j.ok) { linkCode = { ...j, known: new Set(S.linked.map(d => d.id)) }; renderDevices(); }
  } else if (b.dataset.act === 'rename' && dev) {
    const name = prompt('Device name', dev.name);
    if (name && name.trim()) post('/api/devices/rename', { id, name });
  } else if (b.dataset.act === 'unlink' && dev) {
    const self = ME && ME.id === id;
    if (confirm(self ? 'Unlink THIS device? You will need a new code from the PC to link it again.'
      : `Unlink "${dev.name}"? It loses access immediately and needs a new code to link again.`)) {
      const j = await post('/api/devices/unlink', { id });
      if (j.ok && self) { localStorage.removeItem(TOKEN_KEY); showLinkScreen('This device was unlinked.'); }
    }
  }
}
// Draws the device list and the link code card.
function renderDevices() {
  if (!devModal || !S) return;
  const list = S.linked || [];
  setHTML($('#tsBox', devModal), tsBoxHTML());
  setHTML($('#devList', devModal), list.map(d => {
    const self = ME && ME.id === d.id;
    const status = d.online
      ? `<span class="on-dot"></span>Online${d.roles.length ? ' · ' + [...new Set(d.roles)].map(r => ROLE_LABEL[r] || r).join(', ') : ''}${(d.via || []).map(v => ' · ' + (VIA_LABEL[v] || v)).join('')}`
      : `<span class="off-dot"></span>Offline · last seen ${ago(d.last_seen)}`;
    return `<li class="dev">
      <span class="dev-icon">${TYPE_ICON[d.type] || '📱'}</span>
      <div class="dev-main">
        <div class="dev-name">${esc(d.name)}${d.e2e ? ' <span class="tag tag-e2e" title="End-to-end encrypted">🔒</span>' : ''}${self ? ' <span class="tag">this device</span>' : ''}${d.type === 'host' ? ' <span class="tag">host · always linked</span>' : ''}</div>
        <div class="dev-status">${status}</div>
        ${d.created_at ? `<div class="dev-status">Linked ${new Date(d.created_at * 1000).toLocaleDateString()}</div>` : ''}
      </div>
      ${d.type === 'host' ? '' : `<div class="dev-actions">
        <button data-act="rename" data-id="${esc(d.id)}">Rename</button>
        <button class="danger" data-act="unlink" data-id="${esc(d.id)}">Unlink</button></div>`}
    </li>`;
  }).join(''));

  let box = '<button class="primary" data-act="newcode" style="width:100%;padding:12px">＋ Link a new device</button>';
  // end-to-end pairing: the user compares the number on both screens (a relay in the middle can't make them match)
  const e2e = IS_HOST ? (S.e2e_pair || []) : [];
  if (e2e.length) {
    box = e2e.map(p => `<div class="code-card sas-card">
        <div class="muted"><b data-noi18n>${esc(p.name)}</b>: <span>Link end-to-end encrypted?</span> <span>Does the phone show this number?</span></div>
        <div class="big-code">${p.sas.slice(0, 3)} ${p.sas.slice(3)}</div>
        <div class="sas-btns"><button class="primary" data-act="e2eyes" data-pid="${esc(p.id)}">Yes, same number</button>
          <button class="danger" data-act="e2eno" data-pid="${esc(p.id)}">No, different</button></div>
        <div class="muted" style="font-size:12px;margin-top:8px">Only confirm if the numbers are exactly the same.</div>
      </div>`).join('');
  }
  if (linkCode) {
    const fresh = list.find(d => !linkCode.known.has(d.id));
    if (fresh) { linkedNotice = { name: fresh.name, until: Date.now() + 10000 }; linkCode = null; }
  }
  if (linkedNotice && Date.now() < linkedNotice.until) {
    box = `<div class="linked-ok">✓ <b>${esc(linkedNotice.name)}</b> is now linked, permanently.</div>` + box;
  }
  if (linkCode) {
    const left = Math.round(linkCode.expires_at - nowSec());
    if (left > 0) {
      box = `<div class="code-card">
        <div class="muted">On the phone, open this address in Chrome (once):</div>
        ${linkCode.urls.map((u, i) => `<div class="url-label">${esc(u.label)}</div><div class="url ${i ? 'alt' : ''}">${esc(u.url)}</div>`).join('')}
        <div class="muted">…then enter this code:</div>
        <div class="big-code">${linkCode.code.slice(0, 3)} ${linkCode.code.slice(3)}</div>
        <div class="muted">Valid for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} · works once</div>
        <p class="hint">${linkCode.relay && linkCode.relay.on
          ? 'Use the <b>SlideLink Relay</b> address. Then the phone works from any network.'
          : 'Without a relay, the phone must be on the same Wi-Fi.'}
        <b>Android app:</b> open <b>${esc(linkCode.urls[0].url)}/SlideLink.apk</b> on the phone, install it, then enter this code in the app.</p>
      </div>`;
    } else {
      linkCode = null;
    }
  }
  const lb = $('#linkBox', devModal);
  if (lb._h !== box) { lb.innerHTML = box; lb._h = box; }
}
setInterval(() => {
  if (devModal) renderDevices();
  else if (IS_HOST && S && (S.e2e_pair || []).length && typeof openDevices === 'function') { document.querySelectorAll('.qr-modal').forEach(m => m.closest('.modal-back').remove()); openDevices(); }   // a phone waits for "yes"
}, 1000);

/* ======================================================================
   EDITOR
   ====================================================================== */
const pending = {};
let pendingTimer = null;
var IS_HOST = false;

// Keeps the user's unsaved edits visible while server updates arrive.
function overlayPending() {
  for (const [id, f] of Object.entries(pending)) {
    const s = slides().find(x => x.id === id);
    if (s) Object.assign(s, f);
  }
  if (pendingDeckTitle !== null && S) S.deck.title = pendingDeckTitle;
}
// Collects an edit and saves it shortly after typing stops.
function queueEdit(field, value) {
  const id = selectedId;
  if (!id) return;
  pending[id] = { ...(pending[id] || {}), [field]: value };
  overlayPending();
  render();
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(flushEdits, 220);
}
// Sends all collected edits to the server.
function flushEdits() {
  for (const id of Object.keys(pending)) {
    const f = pending[id];
    delete pending[id];
    api({ op: 'update_slide', id, ...f });
  }
}
let pendingDeckTitle = null, deckTimer = null;

// Builds the editor screen (slide list, preview, fields).
function initEditor() {
  document.title = 'SlideLink – Editor';
  $('#app').innerHTML = `
  <div class="editor">
    <header class="topbar">
      <button id="libBtn" title="All presentations">◀ Library</button>
      <div class="logo">Slide<span>Link</span></div>
      <input class="deck-title" id="deckTitle" placeholder="Presentation title" aria-label="Presentation title">
      <select id="theme" aria-label="Theme">${Object.entries(THEMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
      <button id="addBtn">+ Slide</button>
      <div class="spacer"></div>
      <div class="devices" id="devices"></div>
      <button id="devBtn" title="Linked devices">🔗 Linked devices <span class="count" id="devCount"></span></button>
      <button id="remoteBtn">Remote</button>
      <button class="primary" id="presentBtn">▶ Present</button>
    </header>
    <div class="claude-bar" id="claudeBar"></div>
    <div class="workspace">
      <aside class="thumbs" id="thumbs"></aside>
      <main class="stage">
        <div class="preview-bar"><b id="slidePos"></b><div class="progress-summary" id="summary"></div></div>
        <div class="progress-track" id="track"></div>
        <div class="preview" id="preview"></div>
      </main>
      <section class="panel">
        <h4>Slide</h4>
        <label for="fTitle">Title</label><input id="fTitle">
        <label for="fLayout">Layout</label>
        <select id="fLayout">${Object.entries(LAYOUTS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
        <label>Status</label>
        <div class="status-pick" id="fStatus">${Object.entries(STATUS).map(([k, v]) =>
          `<button data-st="${k}"><span class="sdot st-${k}"></span>${v}</button>`).join('')}</div>
        <label for="fContent" id="fContentLbl">Content</label><textarea id="fContent" rows="10" spellcheck="false"></textarea>
        <div class="hint"><code>- </code>bullet · <code>&nbsp;&nbsp;- </code>sub-bullet · <code>**bold**</code> · <code>*italic*</code> ·
          <code>### </code>heading · <code>&gt; </code>quote · <code>![](https://…)</code> image · <code>---</code> next column</div>
        <label for="fNotes">Speaker notes (shown on the phone remote)</label><textarea id="fNotes" rows="4"></textarea>
        <div class="row" style="margin-top:10px">
          <button id="upBtn" title="Move up">↑</button><button id="downBtn" title="Move down">↓</button>
          <button id="dupBtn">Duplicate</button><button class="danger" id="delBtn">Delete</button>
        </div>
        <button id="showBtn" style="width:100%;margin-top:8px">Show this slide on the screen</button>
        <div class="edited-by" id="editedBy"></div>
        <h4>Activity</h4>
        <ul class="feed" id="feed"></ul>
      </section>
    </div>
  </div>`;

  const sel = () => slides().find(s => s.id === selectedId);
  const idx = () => slides().findIndex(s => s.id === selectedId);
  const select = id => { selectedId = id; sessionStorage.setItem('sl-sel', id || ''); render(); };

  $('#thumbs').addEventListener('click', e => {
    if (e.target.closest('#addThumb')) return addSlide();
    const t = e.target.closest('.thumb');
    if (t) { flushEdits(); select(t.dataset.id); }
  });
  async function addSlide() {
    flushEdits();
    const designed = !!S.deck.css || (slides().find(x => x.id === selectedId) || {}).layout === 'html';
    const j = await api(designed
      ? { op: 'add_slide', title: 'New slide', layout: 'html', after: selectedId || undefined,
          html: '<section>\n<h2>New slide</h2>\n<p>Write your text here.</p>\n</section>' }
      : { op: 'add_slide', title: 'New slide', after: selectedId || undefined });
    if (j.ok) select(j.result.slide_id);
  }
  $('#addBtn').onclick = addSlide;
  $('#fTitle').oninput = e => queueEdit('title', e.target.value);
  $('#fContent').oninput = e => queueEdit(bodyField(sel()), e.target.value);
  $('#libBtn').onclick = () => { flushEdits(); location.href = '/'; };
  $('#fNotes').oninput = e => queueEdit('notes', e.target.value);
  $('#fLayout').onchange = e => { flushEdits(); api({ op: 'update_slide', id: selectedId, layout: e.target.value }); };
  $('#fStatus').onclick = e => {
    const b = e.target.closest('button');
    if (b && selectedId) { flushEdits(); api({ op: 'update_slide', id: selectedId, status: b.dataset.st }); }
  };
  $('#upBtn').onclick = () => { flushEdits(); if (idx() > 0) api({ op: 'move_slide', id: selectedId, to: idx() - 1 }); };
  $('#downBtn').onclick = () => { flushEdits(); api({ op: 'move_slide', id: selectedId, to: idx() + 1 }); };
  $('#dupBtn').onclick = async () => {
    flushEdits();
    const j = await api({ op: 'duplicate_slide', id: selectedId });
    if (j.ok) select(j.result.slide_id);
  };
  $('#delBtn').onclick = () => {
    const s = sel();
    if (s && confirm(`Delete slide ${idx() + 1} "${s.title || 'Untitled'}"?`)) {
      const list = slides(), i = idx();
      const neighbour = list[i + 1] || list[i - 1];
      api({ op: 'delete_slide', id: s.id }).then(() => select(neighbour ? neighbour.id : null));
    }
  };
  $('#showBtn').onclick = () => api({ op: 'goto', index: idx() });
  $('#deckTitle').oninput = e => {
    pendingDeckTitle = e.target.value;
    clearTimeout(deckTimer);
    deckTimer = setTimeout(() => { api({ op: 'set_deck', title: pendingDeckTitle }); pendingDeckTitle = null; }, 300);
  };
  $('#theme').onchange = e => api({ op: 'set_deck', theme: e.target.value });
  $('#presentBtn').onclick = () => window.open('/present', 'slidelink-present');
  $('#remoteBtn').onclick = () => window.open('/remote', 'slidelink-remote', 'width=420,height=860');
  $('#devBtn').onclick = openConnect;
  document.addEventListener('keydown', e => {
    if (e.target.matches('input, textarea, select')) return;
    const i = idx();
    if ((e.key === 'ArrowDown' || e.key === 'ArrowRight') && i < slides().length - 1) select(slides()[i + 1].id);
    if ((e.key === 'ArrowUp' || e.key === 'ArrowLeft') && i > 0) select(slides()[i - 1].id);
  });
  setInterval(() => S && render(), 4000); // refresh "x ago" labels and Claude badges
}

let prevUpdated = {};
// Updates the editor from the current state.
function renderEditor() {
  const d = S.deck, list = d.slides;
  if (!list.find(s => s.id === selectedId)) selectedId = list[0] ? list[0].id : null;
  const s = list.find(x => x.id === selectedId);
  const i = list.indexOf(s);
  document.title = `${d.title} – SlideLink`;

  setIfIdle($('#deckTitle'), d.title);
  setIfIdle($('#theme'), d.theme);
  const dv = S.devices || {};
  setHTML($('#devices'), `<span>💻 <b>${dv.editor || 0}</b> editing</span><span>🖥 <b>${dv.present || 0}</b> presenting</span><span>📱 <b>${dv.remote || 0}</b> remote</span>`);
  const others = (S.linked || []).filter(d => d.type !== 'host');
  setHTML($('#devCount'), others.length ? `${others.filter(d => d.online).length}/${others.length} online` : '');
  renderClaudeBar();

  // thumbnails, flashing the ones that changed remotely
  const flash = new Set();
  for (const x of list) {
    if (prevUpdated[x.id] !== undefined && prevUpdated[x.id] !== x.updated_at && x.updated_by !== DEVICE) flash.add(x.id);
  }
  prevUpdated = Object.fromEntries(list.map(x => [x.id, x.updated_at]));
  const thumbs = $('#thumbs');
  const html = list.map((x, n) => `
    <div class="thumb st-${esc(x.status)} ${x.id === selectedId ? 'sel' : ''}" data-id="${esc(x.id)}">
      ${slideHTML(x, d, n)}
      <div class="badges">${n === S.present.index && (S.devices.present || 0) > 0 ? '<span class="badge badge-live">LIVE</span>' : ''}
        ${claudeOn(x) ? '<span class="badge badge-claude"><span class="dot"></span>Claude</span>' : ''}</div>
      <div class="meta"><span class="n">${n + 1}</span><span class="t">${esc(x.title || 'Untitled')}</span>
        <span class="sdot" title="${STATUS[x.status]}"></span></div>
    </div>`).join('') + '<button class="add-thumb" id="addThumb">+ Add slide</button>';
  setHTML(thumbs, html);
  flash.forEach(id => { const el = thumbs.querySelector(`[data-id="${id}"]`); if (el) el.classList.add('flash'); });

  // preview + form
  setHTML($('#preview'), slideHTML(s, d, i, true));
  $('#slidePos').textContent = s ? `Slide ${i + 1} of ${list.length}` : 'No slides';
  for (const el of document.querySelectorAll('.panel input, .panel textarea, .panel select, .panel button')) el.disabled = !s;
  if (s) {
    setIfIdle($('#fTitle'), s.title);
    setIfIdle($('#fContent'), s[bodyField(s)]);
    $('#fContentLbl').textContent = s.layout === 'html' ? 'Slide HTML (designed slide, 1920×1080)' : 'Content';
    document.querySelector('.panel .hint').style.display = s.layout === 'html' ? 'none' : '';
    setIfIdle($('#fNotes'), s.notes);
    setIfIdle($('#fLayout'), s.layout);
    document.querySelectorAll('#fStatus button').forEach(b => b.classList.toggle('on', b.dataset.st === s.status));
    $('#editedBy').textContent = `Last edited by ${s.updated_by === 'claude' ? 'Claude' : s.updated_by} · ${ago(s.updated_at)}`;
  }

  // status progress
  const counts = Object.fromEntries(Object.keys(STATUS).map(k => [k, 0]));
  list.forEach(x => counts[x.status] = (counts[x.status] || 0) + 1);
  const colors = { 'draft': 'var(--draft)', 'in-progress': 'var(--progress)', 'review': 'var(--review)', 'done': 'var(--done)' };
  setHTML($('#summary'), Object.entries(STATUS).map(([k, v]) => `<span><span class="sdot st-${k}"></span>${counts[k]} ${v}</span>`).join(''));
  setHTML($('#track'), list.length ? ['done', 'review', 'in-progress', 'draft'].map(k =>
    `<div style="width:${100 * counts[k] / list.length}%;background:${colors[k]}"></div>`).join('') : '');

  // activity feed
  setHTML($('#feed'), (S.activity || []).slice(0, 25).map(a =>
    `<li><span class="who ${a.by === 'claude' ? 'claude' : ''}">${a.by === 'claude' ? 'Claude' : esc(a.by)}</span>
     <span>${esc(a.text)}</span><span class="when">${ago(a.at)}</span></li>`).join('') ||
    '<li class="muted">Nothing yet. Edits from you, your phone and Claude show up here.</li>');
}

/* ======================================================================
   PRESENTER
   ====================================================================== */
let shownIndex = -1;
/* Navigation stays in sync even when tapping fast:
   - taps are sent one after another (never overtaking each other on Wi-Fi or the relay)
   - each tap says where it started, so the server ignores taps that no longer fit (no double skips)
   - while taps are on their way, late updates from the server can't jump the display back
   - when the last tap is answered, the display settles on the server's real position */
let navChain = Promise.resolve(), navPending = 0;
// Called when all taps were answered: shows the true server position again.
function settle(res) {
  if (!res || !('index' in res)) return;
  S.present.index = res.index;
  S.present.step = res.step || 0;
  S.present.blackout = !!res.blackout;
  render();
}
// Navigation (next, prev, goto, blackout): taps are queued and sent in order, never skipped.
function go(kind, extra = {}) {
  if (!S) return;
  const n = slides().length;
  const P = S.present, here = slides()[P.index];
  P.step = P.step || 0;
  const from = { from_index: P.index, from_step: P.step, from_deck: S.deck.id };
  if (kind === 'next') {
    if (P.step < hsSteps(here)) P.step++;
    else if (P.index < n - 1) { P.index++; P.step = 0; }
  }
  if (kind === 'prev') {
    if (P.step > 0) P.step--;
    else if (P.index > 0) { P.index--; P.step = hsSteps(slides()[P.index]); }
  }
  if (kind === 'goto') { P.index = Math.max(0, Math.min(n - 1, extra.index)); P.step = 0; }
  if (kind === 'blackout') S.present.blackout = !S.present.blackout; else S.present.blackout = false;
  if (navigator.vibrate) navigator.vibrate(15);
  render();
  navPending++;
  navChain = navChain.then(async () => {
    const j = await api({ op: kind, ...extra, ...from });
    navPending--;
    if (!j || !j.ok) { navPending = 0; return resync(false); }
    if (j.result && j.result.stale) { navPending = 0; return settle(j.result); }   // someone else moved: take the truth
    if (navPending === 0) settle(j.result);
  });
}

/* ⟳ Resync: fetch the real state and rebuild every slide view from scratch.
   everywhere=true also tells all other screens (PC presenter, other phones) to do the same. */
// Rebuilds this screen (or all screens) from the server state.
async function resync(everywhere) {
  navPending = 0;
  navChain = Promise.resolve();
  if (everywhere) await api({ op: 'resync' });
  try {
    const r = await fetch('/api/state', { headers: authHeaders(), cache: 'no-store' });
    if (r.ok) S = await r.json();
  } catch (_) { /* reconnect below */ }
  rebuildViews();
  connect();
}
// Throws away and redraws all slide views.
function rebuildViews() {
  for (const el of document.querySelectorAll('#frame, #nowSlide')) {
    el.innerHTML = ''; el._slots = null; el._current = null; el._h = undefined;
  }
  if (S) render();
}
/* Esc in the presenter: leave browser fullscreen if on, otherwise close the presentation window.
   The borderless (kiosk) window ignores most shortcuts, so the PC app closes it for us. */
// Esc in the presenter: leave fullscreen, or close the presentation window.
function closePresenter() {
  if (document.fullscreenElement) return document.exitFullscreen();
  api({ op: 'close_on_pc' });
  setTimeout(() => { try { window.close(); } catch (_) {} }, 400);
}
// keys pressed while the focus is inside a slide frame arrive as messages
window.addEventListener('message', e => { if (e.data && e.data.sl === 'esc' && MODE === 'present') closePresenter(); });
// Keyboard and clicker keys: arrows, space, PageUp/Down, B, F, Esc.
function navKeys(e) {
  if (e.target.matches && e.target.matches('input, textarea, select')) return;
  const k = e.key;
  if (['ArrowRight', 'ArrowDown', 'PageDown', ' ', 'Enter', 'n'].includes(k)) { e.preventDefault(); go('next'); }
  else if (['ArrowLeft', 'ArrowUp', 'PageUp', 'Backspace', 'p'].includes(k)) { e.preventDefault(); go('prev'); }
  else if (k === 'Home') go('goto', { index: 0 });
  else if (k === 'End') go('goto', { index: slides().length - 1 });
  else if (k === 'b' || k === '.') go('blackout');
  else if (k === 'f' && MODE === 'present') toggleFullscreen();
  else if (k === 'Escape' && MODE === 'present') closePresenter();
  else if (k === 'r' || k === 'R') resync(true);
}
// Switches browser fullscreen on or off.
function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => {});
}

// Builds the presenter screen.
function initPresent() {
  document.title = 'SlideLink – Presenting';
  $('#app').innerHTML = `
    <div class="present" id="pres">
      <div class="frame" id="frame"></div>
      <div class="black" id="black"></div>
      <div class="counter" id="pcount"></div>
      <div class="overlay" id="ov">
        <span>← → or clicker · your phone remote · B = black · F = fullscreen</span>
        <button id="syncBtnP" title="Resync all screens (R)">⟳ Resync</button>
        <button id="fsBtn">Fullscreen</button><button id="exitBtn">Editor</button>
      </div>
    </div>`;
  const pres = $('#pres'), ov = $('#ov');
  let hideT;
  const wake = () => {
    ov.classList.remove('hide'); pres.classList.add('cursor');
    clearTimeout(hideT); hideT = setTimeout(() => { ov.classList.add('hide'); pres.classList.remove('cursor'); }, 2500);
  };
  wake();
  document.addEventListener('mousemove', wake);
  document.addEventListener('keydown', navKeys);
  $('#fsBtn').onclick = e => { e.stopPropagation(); toggleFullscreen(); };
  $('#syncBtnP').onclick = e => { e.stopPropagation(); resync(true); };
  $('#exitBtn').onclick = e => { e.stopPropagation(); location.href = '/'; };
  // back on screen (window was hidden, display switched): draw again and fetch the latest state
  const heal = () => {
    if (document.visibilityState !== 'visible' || !S) return;
    if (slides().length && !document.querySelector('#frame .pslot.cur iframe, #frame .pslot.cur .docscroll')) resync(false);
    else render();
  };
  document.addEventListener('visibilitychange', heal);
  window.addEventListener('focus', heal);
  pres.addEventListener('click', e => {
    if (e.target.closest('.overlay')) return;
    go(e.clientX < innerWidth * 0.25 ? 'prev' : 'next');
  });
}
/* Smooth slide switching: the current slide plus its neighbours stay loaded (hidden) and we
   crossfade between them, so changing slides never reloads anything (no white flash, no stutter). */
// Short hash of a text (to notice changed slides).
function strHash(t) { let h = 0; for (let k = 0; k < t.length; k++) h = (h * 31 + t.charCodeAt(k)) | 0; return h; }
// Key of a loaded slide view (changes when the slide changes).
function slotKey(s, n) {
  return [s.id, n, S.deck.theme, strHash((S.deck.css || '') + (s.html || '') + s.title + s.content + s.layout)].join(':');
}
// Keeps the current slide (and neighbours) loaded and crossfades between them; sends build steps.
function renderSlots(container, index, keepAround) {
  const list = slides();
  container._slots = container._slots || new Map();
  const slots = container._slots, want = new Map();
  for (let n = index - keepAround; n <= index + keepAround; n++) {
    if (list[n]) want.set(slotKey(list[n], n), n);
  }
  for (const [key, el] of slots) if (!want.has(key)) { el.remove(); slots.delete(key); }
  let current = null;
  for (const [key, n] of want) {
    let el = slots.get(key);
    if (!el) {
      el = document.createElement('div');
      el.className = 'pslot';
      el.innerHTML = slideHTML(list[n], S.deck, n, true);
      container.appendChild(el);
      hsObserve(el);
      // the frame's 'ready' can get lost (slow start, window in the background): treat it as ready after 2 s
      const fr = el.querySelector('iframe');
      if (fr) setTimeout(() => { if (!fr._ready && fr.isConnected) { fr._ready = true; postFrame(fr, { sl: 'step', step: el._step || 0, animate: false }); el._sent = el._step; } }, 2000);
      slots.set(key, el);
    }
    if (n === index) current = el;
  }
  if (!current && list.length) {   // index past the end (slides deleted meanwhile): show the last slide, never black
    return renderSlots(container, list.length - 1, keepAround);
  }
  if (!list.length) { container.innerHTML = slideHTML(null, S.deck); container._slots = null; return; }
  // every loaded slide gets the build step it should show: current = live step, before = finished, after = start
  const step = S.present.step || 0;
  for (const [key, el] of slots) {
    const n = want.get(key);
    el._step = n === index ? step : n < index ? hsSteps(list[n]) : 0;
  }
  const entering = container._current !== current;
  if (entering) for (const el of slots.values()) el.classList.toggle('cur', el === current);
  for (const el of slots.values()) {
    if (el.querySelector('.docscroll')) { hsDocStep(el); continue; }
    const f = el.querySelector('iframe');
    if (!f || !f._ready) continue;
    if (el === current && entering && container._current) postFrame(f, { sl: 'enter', step: el._step });
    else if (el._sent !== el._step) postFrame(f, { sl: 'step', step: el._step, animate: el === current });
    el._sent = el._step;
  }
  container._current = current;
}
// Sends a message to a slide frame.
function postFrame(f, msg) { try { f.contentWindow.postMessage(msg, '*'); } catch (_) {} }
// a slide iframe finished loading: tell it which build step to show
window.addEventListener('message', e => {
  if (!e.data || e.data.sl !== 'ready') return;
  for (const f of document.querySelectorAll('.pslot iframe')) {
    if (f.contentWindow !== e.source) continue;
    f._ready = true;
    const slot = f.closest('.pslot');
    postFrame(f, { sl: 'step', step: slot._step || 0, animate: slot.classList.contains('cur') });
    slot._sent = slot._step;
  }
});

// Updates the presenter from the current state.
function renderPresent() {
  const list = slides(), i = S.present.index;
  renderSlots($('#frame'), i, 1);
  shownIndex = i;
  $('#black').classList.toggle('on', !!S.present.blackout);
  $('#pcount').textContent = list.length ? `${i + 1} / ${list.length}` : '';
  document.title = `${i + 1}/${list.length} · ${S.deck.title}`;
  renderLiveOverlay();
}

/* PHONE REMOTE: see remote.js */

/* ---------------- boot ---------------- */
// Redraws the screen of the current mode after a state change.
function render() {
  if (!S) return;
  if (MODE === 'editor') renderEditor();
  else if (MODE === 'present') renderPresent();
  else if (MODE === 'library') renderLibrary();
  else renderRemote();
  renderDevices();
}
(async function boot() {
  if (MODE === 'group') {                      // co-presenter: joined with the group link, not linked
    const k = new URLSearchParams(location.search).get('k') || '';
    const g = groupLogin();
    if (!g || g.k !== k) return showGroupJoin();
    const who = await whoami();
    if (!who || !who.linked || !who.device || who.device.type !== 'group') {
      localStorage.removeItem(GROUP_KEY);
      return showGroupJoin(who ? 'This group session has ended or you were removed. Join again if it is still running.' : 'Cannot reach the presentation right now.');
    }
    ME = who.device; DEVICE = ME.name;
    initRemote();
    return connect();
  }
  let who = await whoami();
  while (!who) {                       // PC not reachable yet: keep trying
    $('#conn').textContent = 'Waiting for the SlideLink PC…'; $('#conn').classList.remove('ok');
    await new Promise(r => setTimeout(r, 2000));
    who = await whoami();
  }
  if (!who.linked) return showLinkScreen();
  ME = who.device;
  DEVICE = who.is_host ? 'PC' : ME.name;
  IS_HOST = !!who.is_host;
  // phones and tablets are remote controls for the desktop app: no editor or library there
  if (ME && (ME.type === 'phone' || ME.type === 'tablet') && MODE !== 'remote' && MODE !== 'present') {
    return location.replace('/remote');
  }
  ({ editor: initEditor, present: initPresent, remote: initRemote, library: initLibrary })[MODE]();
  connect();
})();
