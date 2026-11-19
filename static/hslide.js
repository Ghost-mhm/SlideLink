'use strict';
/* Renders Claude "Slides" HTML (one <section> on a 1920x1080 canvas) inside a sandboxed,
   scaled iframe, so imported claude.ai decks look exactly as designed. */

const HS_BASE = `
html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:transparent}
*{box-sizing:border-box;margin:0}
body{font-family:Inter,'Segoe UI',system-ui,sans-serif;color:#111;font-size:32px;line-height:1.4}
section{width:1920px;height:1080px;position:relative;overflow:hidden;background:#fff}
div{min-width:0}
h1{font-size:96px;font-weight:600;line-height:1.1}
h2{font-size:64px;font-weight:600;line-height:1.15}
h3{font-size:44px;font-weight:600;line-height:1.2}
p,ul,ol,li{font-size:inherit}
ul,ol{padding-left:1.25em}
aside{display:none!important}
img{display:block;max-width:none}
svg{flex:none;overflow:visible}
table{border-collapse:collapse}
th,td{padding:.35em .6em;border-bottom:1px solid rgba(127,127,127,.35);text-align:left}
x-icon,x-shape,x-embed{display:block;flex:none}
x-connector{display:block}
`;

/* Runs inside the slide iframe: draws <x-icon>, <x-shape>, <x-connector>, <x-embed>. */
const HS_POLY = `(() => {
const P = {
 Activity:'M3 12h4l3 8 4-16 3 8h4', Book:'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM6.5 17A2.5 2.5 0 0 0 4 19.5 2.5 2.5 0 0 0 6.5 22H20v-5',
 Chart:'M3 3v18h18M7 15v3M12 10v8M17 6v12', Chat:'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
 Check:'M20 6 9 17l-5-5', CheckCircle:'M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14l-3-3',
 Clock:'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 6v6l4 2', Cloud:'M17.5 19H9a7 7 0 1 1 6.7-9h1.8a4.5 4.5 0 1 1 0 9z',
 Code:'m16 18 6-6-6-6M8 6l-6 6 6 6', Database:'M12 2C7 2 3 3.3 3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5c0-1.7-4-3-9-3zM3 5c0 1.7 4 3 9 3s9-1.3 9-3M3 12c0 1.7 4 3 9 3s9-1.3 9-3',
 Globe:'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10 15 15 0 0 1-4-10 15 15 0 0 1 4-10z',
 GraduationCap:'M22 10 12 5 2 10l10 5 10-5zM6 12v5c3 3 9 3 12 0v-5', Home:'m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 22V12h6v10',
 Key:'M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4',
 Lightbulb:'M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z',
 Lightning:'M13 2 3 14h9l-1 8 10-12h-9z', Link:'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
 Lock:'M5 11h14v11H5zM7 11V7a5 5 0 0 1 10 0v4', PaperPlane:'m22 2-7 20-4-9-9-4zM22 2 11 13',
 Play:'m6 3 14 9-14 9z', Search:'M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM21 21l-4.3-4.3',
 Settings:'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
 Star:'m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z', ThumbsUp:'M7 10v12M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.4 22H7V10l4-8a3.1 3.1 0 0 1 4 3.9zM2 10h5v12H2z',
 Tool:'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z',
 Trust:'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z', Users:'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
 Verified:'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4', Warning:'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
 Wrench:'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z'
};
const NS = 'http://www.w3.org/2000/svg';
document.querySelectorAll('section, div').forEach(el => {
  const cs = getComputedStyle(el);
  if (cs.display === 'block') { el.style.display = 'flex'; if (cs.flexDirection === 'row') el.style.flexDirection = 'column'; }
});
document.querySelectorAll('x-icon').forEach(el => {
  const d = P[el.getAttribute('name')] || 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z';
  el.innerHTML = '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="' + d + '"/></svg>';
});
const CLIP = { diamond: 'polygon(50% 0,100% 50%,50% 100%,0 50%)',
  'arrow-right': 'polygon(0 30%,60% 30%,60% 0,100% 50%,60% 100%,60% 70%,0 70%)',
  'arrow-left': 'polygon(100% 30%,40% 30%,40% 0,0 50%,40% 100%,40% 70%,100% 70%)',
  'arrow-up': 'polygon(30% 100%,30% 40%,0 40%,50% 0,100% 40%,70% 40%,70% 100%)',
  'arrow-down': 'polygon(30% 0,30% 60%,0 60%,50% 100%,100% 60%,70% 60%,70% 0)' };
document.querySelectorAll('x-shape').forEach(el => {
  const k = el.getAttribute('kind');
  if (k === 'ellipse') el.style.borderRadius = '50%';
  else if (CLIP[k]) el.style.clipPath = CLIP[k];
  else if (k === 'rounded') el.style.borderRadius = '14%';
  else if (k === 'line') { el.style.height = el.style.height || '0'; }
});
addEventListener('keydown', function (e) { if (e.key === 'Escape') parent.postMessage({ sl: 'esc' }, '*'); }, true);
document.querySelectorAll('x-embed').forEach(el => {
  const f = document.createElement('iframe');
  f.setAttribute('sandbox', 'allow-scripts');
  const lp = document.getElementById('hs-low');   // low-power phone preview: embedded mini-pages too
  f.srcdoc = (lp ? '<script>' + lp.textContent + '</scr' + 'ipt>' : '') + (el.getAttribute('data-doc') || '');
  f.style.cssText = 'border:0;width:100%;height:100%;background:transparent';
  el.innerHTML = ''; el.appendChild(f);
});
const num = (v, total) => { v = String(v || '0').trim(); return v.endsWith('%') ? parseFloat(v) / 100 * total : parseFloat(v) || 0; };
document.querySelectorAll('x-connector').forEach(el => {
  const host = el.parentElement, W = host.clientWidth || 1920, H = host.clientHeight || 1080;
  const cs = getComputedStyle(el);
  const color = el.style.color || cs.color || '#333';
  const w = parseFloat(el.style.borderWidth) || 2;
  const dash = /dash/.test(el.style.borderStyle) ? (w * 3) + ' ' + (w * 2) : /dot/.test(el.style.borderStyle) ? w + ' ' + w * 1.5 : '';
  let pts;
  if (el.hasAttribute('x1')) {
    const x1 = num(el.getAttribute('x1'), W), y1 = num(el.getAttribute('y1'), H), x2 = num(el.getAttribute('x2'), W), y2 = num(el.getAttribute('y2'), H);
    const r = el.getAttribute('route') || 'straight';
    pts = r === 'hv' ? [[x1, y1], [x2, y1], [x2, y2]] : r === 'vh' ? [[x1, y1], [x1, y2], [x2, y2]]
      : r === 'elbow' ? [[x1, y1], [(x1 + x2) / 2, y1], [(x1 + x2) / 2, y2], [x2, y2]] : [[x1, y1], [x2, y2]];
    el.style.cssText += ';position:absolute;left:0;top:0;width:' + W + 'px;height:' + H + 'px;pointer-events:none';
  } else {
    const bw = el.offsetWidth || 96, bh = el.offsetHeight || 2, from = el.getAttribute('from') || 'tl';
    const c = { tl: [0, 0], tr: [bw, 0], bl: [0, bh], br: [bw, bh] }[from] || [0, 0];
    pts = bh < 4 ? [[0, bh / 2], [bw, bh / 2]] : [c, [bw - c[0], bh - c[1]]];
  }
  const head = el.getAttribute('head') || 'end';
  const hs = el.getAttribute('head-start') || (head === 'both' ? 'filled' : 'none');
  const he = el.getAttribute('head-end') || (head === 'none' ? 'none' : 'filled');
  const id = 'm' + Math.random().toString(36).slice(2);
  const mk = (n, kind, rev) => kind === 'none' ? '' : '<marker id="' + id + n + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="' + Math.max(4, 12 / w * 2) + '" markerHeight="' + Math.max(4, 12 / w * 2) + '" orient="' + (rev ? 'auto-start-reverse' : 'auto') + '"><path d="M0 0L10 5L0 10' + (kind === 'open' ? '' : 'z') + '" fill="' + (kind === 'open' ? 'none' : color) + '" stroke="' + color + '" stroke-width="1.5"/></marker>';
  el.innerHTML = '<svg xmlns="' + NS + '" width="100%" height="100%" style="overflow:visible;position:absolute;left:0;top:0"><defs>' + mk('s', hs, true) + mk('e', he, false) + '</defs><polyline points="' + pts.map(p => p.join(',')).join(' ') + '" fill="none" stroke="' + color + '" stroke-width="' + w + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + (hs !== 'none' ? ' marker-start="url(#' + id + 's)"' : '') + (he !== 'none' ? ' marker-end="url(#' + id + 'e)"' : '') + '/></svg>';
});
// ---- click builds, played like claude.ai: autos run on their own, every other item waits for a click
const LIVE = document.documentElement.hasAttribute('data-live');
const items = [...document.querySelectorAll('[data-build-in],[data-build-out]')].map((el, dom) => {
  const kind = el.hasAttribute('data-build-in') ? 'in' : 'out';
  const parts = (el.getAttribute('data-build-' + kind) || '').trim().split(/\\s+/);
  const o = parts.find(p => /^\\d+$/.test(p));
  return { el: el, kind: kind, fx: parts[0] || 'fade', order: o ? +o : 0, auto: parts.indexOf('auto') >= 0, dom: dom };
}).sort((a, b) => a.order - b.order || a.dom - b.dom);
const groups = [[]];
let lastOrder = null;
items.forEach(it => {
  if (!it.auto && it.order !== lastOrder) { groups.push([]); lastOrder = it.order; }   // same number = same click
  groups[groups.length - 1].push(it);
});
document.querySelectorAll('.fragment').forEach(f => groups.push([{ el: f, kind: 'frag' }]));
const FX = { fade: [0, 0, 1], rise: [0, 48, 1], drop: [0, -48, 1], left: [-80, 0, 1], right: [80, 0, 1], scale: [0, 0, 0.6], pop: [0, 0, 0.3] };
function look(it, on, delay, animate) {
  const s = it.el.style;
  if (it.kind === 'frag') { it.el.classList.toggle('visible', on); return; }
  const show = it.kind === 'in' ? on : !on;
  const f = FX[it.fx] || FX.fade;
  s.transition = animate ? 'opacity .5s ease ' + delay + 'ms, translate .55s cubic-bezier(.22,1,.36,1) ' + delay + 'ms, scale .5s cubic-bezier(.34,1.56,.64,1) ' + delay + 'ms' : 'none';
  s.opacity = show ? '' : '0';
  s.translate = show ? '' : f[0] + 'px ' + f[1] + 'px';
  s.scale = show ? '' : String(f[2]);
}
let cur = -1;
function setStep(step, animate) {
  step = Math.max(0, Math.min(step, groups.length - 1));
  groups.forEach((g, gi) => {
    const on = gi <= step, fresh = animate && gi > cur && on;
    g.forEach((it, k) => look(it, on, fresh ? (gi === 0 ? 300 : 0) + k * 380 : 0, fresh));
  });
  cur = step;
}
function enter(step) {   // slide comes on screen: replay its entrance animations from the start
  document.getAnimations().forEach(a => { try { a.currentTime = 0; a.play(); } catch (_) {} });
  document.querySelectorAll('x-embed iframe').forEach(f => { const d = f.srcdoc; f.srcdoc = ''; f.srcdoc = d; });
  cur = -1; setStep(0, false); requestAnimationFrame(() => { cur = -1; setStep(step, true); });
}
if (LIVE) {
  setStep(0, false); cur = -1;
  window.addEventListener('message', e => {
    const d = e.data || {};
    if (d.sl === 'step') setStep(d.step, d.animate !== false);
    if (d.sl === 'enter') enter(d.step || 0);
  });
  parent.postMessage({ sl: 'ready' }, '*');
} else {
  setStep(groups.length - 1, false);   // still picture: the finished slide
}
})();`;

const HS_FREEZE = '*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;' +
  'animation-iteration-count:1!important;transition:none!important}';

/* live = runs animations and embedded mini-pages (main view, presenter, phone).
   Not live = a still picture of the finished slide (thumbnails): fast and calm. */
/* Interactive pages keep their own document; this bridge turns Next/Back into arrow keys for them. */
const PAGE_BRIDGE = `(() => {
  let last = null;
  // Next/Back: first offered to the page as arrow keys (apps with their own navigation). If the page
  // doesn't react (nothing changes), it is a scrolling page: scroll one screen instead.
  function key(k) {
    const code = k === 'ArrowRight' ? 39 : 37;
    let changed = false;
    const mo = new MutationObserver(() => { changed = true; });
    mo.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
    const y0 = scrollY;
    let handled = false;
    ['keydown', 'keyup'].forEach(type => {
      const e = new KeyboardEvent(type, { key: k, code: k, bubbles: true, cancelable: true });
      Object.defineProperty(e, 'keyCode', { get: () => code });
      Object.defineProperty(e, 'which', { get: () => code });
      (document.activeElement || document.body || document).dispatchEvent(e);
      if (e.defaultPrevented) handled = true;
    });
    setTimeout(() => {
      mo.disconnect();
      if (!handled && !changed && scrollY === y0) {
        scrollBy({ top: (k === 'ArrowRight' ? 1 : -1) * innerHeight * 0.85, behavior: 'smooth' });
      }
    }, 150);
  }
  addEventListener('message', ev => {
    const d = ev.data || {};
    if (d.sl !== 'step' && d.sl !== 'enter') return;
    const s = d.step || 0;
    if (last === null || d.sl === 'enter') { last = s; return; }
    while (last < s) { key('ArrowRight'); last++; }
    while (last > s) { key('ArrowLeft'); last--; }
  });
  addEventListener('keydown', function (e) { if (e.key === 'Escape') parent.postMessage({ sl: 'esc' }, '*'); }, true);
  parent.postMessage({ sl: 'ready' }, '*');
})();`;

/* Phone preview, low power: animation frames at ~12 fps instead of 60, endless decorative CSS loops paused
   (click builds still play). Only for the small preview on the phone, never for the PC presenter. */
let HS_LOWPOWER = false;
const HS_LOW_SHIM = `(function(){
  var FPS = 12, next = 1, q = {}, timer = null, last = 0;
  function flush(){ timer = null; last = performance.now(); var run = q; q = {};
    Object.keys(run).forEach(function(id){ try { run[id](last); } catch (e) {} }); }
  window.requestAnimationFrame = function(cb){ var id = next++; q[id] = cb;
    if (!timer) timer = setTimeout(flush, Math.max(0, 1000 / FPS - (performance.now() - last))); return id; };
  window.cancelAnimationFrame = function(id){ delete q[id]; };
  function pauseLoops(){ var all = document.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) { var cs = getComputedStyle(all[i]);
      if (cs.animationName !== 'none' && cs.animationIterationCount.indexOf('infinite') >= 0) all[i].style.animationPlayState = 'paused'; } }
  addEventListener('load', function(){ setTimeout(pauseLoops, 1200); setInterval(pauseLoops, 4000); });
})();`;
const hsLowTag = () => (HS_LOWPOWER ? '<script id="hs-low">' + HS_LOW_SHIM + '<\/script>' : '');

function hsDoc(s, deck, index, live) {
  if (s.layout === 'page') {
    const doc = String(s.html || '');
    const bridge = '<script>' + PAGE_BRIDGE + '<\/script>';
    const at = doc.toLowerCase().lastIndexOf('</body>');
    const withBridge = at >= 0 ? doc.slice(0, at) + bridge + doc.slice(at) : doc + bridge;
    if (!HS_LOWPOWER) return withBridge;
    const h = withBridge.search(/<head[^>]*>/i);
    return h >= 0 ? withBridge.replace(/<head[^>]*>/i, m => m + hsLowTag()) : hsLowTag() + withBridge;
  }
  deck = deck || {};
  const faces = (deck.faces || []).filter(f => /^https:\/\/fonts\.googleapis\.com\//.test(f.href || ''))
    .map(f => `<link rel="stylesheet" href="${esc(f.href)}">`).join('');
  const css = String(deck.css || '').replace(/<\/style/gi, '<\\/style');
  let html = String(s.html || '').replace(/<aside[\s\S]*?<\/aside>/gi, '')
    .replace(/(<section\b[^>]*?)\shidden(?=[\s>=])(="[^"]*")?/i, '$1');
  // keep each embedded mini-page intact (its own <html>/<script>) by moving it into an attribute
  html = html.replace(/<x-embed\b([^>]*)>([\s\S]*?)<\/x-embed>/gi,
    (m, attrs, body) => (live ? `<x-embed${attrs} data-doc="${esc(body)}"></x-embed>` : ''));
  if (!/<section\b/i.test(html)) html = `<section>${html}</section>`;
  const extra = live ? '' : '.fragment{opacity:1!important;transform:none!important}' + HS_FREEZE;
  return `<!doctype html><html${live ? ' data-live' : ''}><head><meta charset="utf-8">${live ? hsLowTag() : ''}${faces}<style>${HS_BASE}</style><style>${css}</style><style>${extra}</style></head>` +
    `<body style="counter-reset:page ${index | 0}">${html}<script>${HS_POLY}<\/script></body></html>`;
}

function hsFrame(s, deck, index, live) {
  return `<div class="slide hslide${s.layout === 'page' ? ' page' : ''}"><iframe sandbox="allow-scripts allow-forms" tabindex="-1"${live ? '' : ' loading="lazy"'} title="${esc(s.title || 'Slide')}" srcdoc="${esc(hsDoc(s, deck, index, live))}"></iframe></div>`;
}

/* A document (claude.ai Doc as page pictures): shown directly, not in a sandboxed frame (Chrome blocks such
   frames from loading anything from this PC). Only the page pictures are taken over, nothing else. */
function hsDocView(s) {
  const pages = [...String(s.html || '').matchAll(/src="(\/media\/[0-9a-f]{24}\/page-\d{3}\.png)"/g)].map(m => m[1]);
  return `<div class="slide docview"><div class="docscroll">${pages.map((p, i) =>
    `<img src="${p}" alt="Page ${i + 1}" loading="${i < 2 ? 'eager' : 'lazy'}" draggable="false">`).join('')}</div></div>`;
}
/* Next/Back on a document: scroll one screen per step */
function hsDocStep(el) {
  const ds = el.querySelector('.docscroll');
  if (!ds || el._sent === el._step) return;
  if (el._sent == null || !el._step) ds.scrollTo({ top: 0 });
  else ds.scrollBy({ top: (el._step - el._sent) * ds.clientHeight * 0.85, behavior: 'smooth' });
  el._sent = el._step;
}

/* Same click counting as the server and the iframe player. */
function hsSteps(s) {
  if (s && (s.layout === 'page' || s.layout === 'doc')) return 10000;
  if (!s || s.layout !== 'html' || !s.html) return 0;
  let n = 0;
  const items = [];
  s.html.replace(/data-build-(?:in|out)="([^"]*)"/gi, (m, v) => {
    const parts = v.trim().split(/\s+/), o = parts.find(p => /^\d+$/.test(p));
    items.push({ order: o ? +o : 0, auto: parts.indexOf('auto') >= 0 });
    return m;
  });
  items.sort((a, b) => a.order - b.order);
  let last = null;
  for (const it of items) if (!it.auto && it.order !== last) { n++; last = it.order; }
  return n + (s.html.match(/class="[^"]*\bfragment\b/gi) || []).length;
}

/* Keep every designed slide scaled to its box. */
const hsObserver = new ResizeObserver(entries => {
  for (const e of entries) e.target.style.setProperty('--k', (e.contentRect.width / 1920).toFixed(5));
});
function hsObserve(root) {
  (root || document).querySelectorAll('.hslide:not([data-ro])').forEach(el => { el.dataset.ro = '1'; hsObserver.observe(el); });
}

/* Pull speaker notes and a title out of a claude.ai slide file. */
function hsParse(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const aside = doc.querySelector('section > aside, aside');
  const notes = aside ? aside.textContent.trim() : '';
  const h = doc.querySelector('h1, h2, h3');
  const title = (h ? h.textContent : (doc.querySelector('p') || {}).textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  return { notes, title, hidden: !!doc.querySelector('section[hidden]') };
}
