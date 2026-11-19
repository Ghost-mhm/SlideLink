// SlideLink Sync - runs inside the frame that shows one of YOUR claude.ai artifacts.
// If that artifact is a slide deck, it reads the deck's own files (deck.json, deck.css, slides/*.html)
// and hands them to the extension, which sends them to SlideLink on this PC. Nothing else is read.
(() => {
  const SLUG = location.hostname.split('.')[0];
  let lastSent = '', base = null, shownOnce = false;

  function findBase() {
    for (const e of performance.getEntriesByType('resource')) {
      const m = e.name.match(/^(.*\/project\/)deck\.json/);
      if (m) return m[1];
    }
    return null;
  }
  async function get(url) {
    const r = await fetch(url, { credentials: 'include', cache: 'no-store' });
    if (!r.ok) throw new Error(r.status + ' ' + url);
    return r.text();
  }
  async function collect() {
    const b = base || findBase() || location.origin + '/project/';
    const index = JSON.parse(await get(b + 'deck.json'));
    if (!Array.isArray(index.order) || !index.order.length) return null;   // not a slide deck
    base = b;
    let css = '';
    const shared = (index.format && Array.isArray(index.format.shared) && index.format.shared.length)
      ? index.format.shared : ['project/deck.css'];
    for (const sp of shared) {
      if (!/^project\/[A-Za-z0-9_./-]+\.css$/.test(sp) || sp.includes('..')) continue;
      try { css += (await get(b + sp.replace(/^project\//, ''))) + '\n'; } catch (_) { /* deck without shared css */ }
    }
    const slides = [];
    for (const id of index.order) {
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) continue;
      try { slides.push({ id, html: await get(b + 'slides/' + id + '.html') }); } catch (_) { /* slide removed meanwhile */ }
    }
    return { format: 'claude-raw', slug: SLUG, index, css, slides };
  }

  function badge(text, ok) {
    const el = document.createElement('div');
    el.textContent = text;
    el.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:2147483647;font:600 13px system-ui,sans-serif;' +
      'padding:8px 14px;border-radius:999px;box-shadow:0 6px 20px rgba(0,0,0,.25);transition:opacity .6s;pointer-events:none;' +
      (ok ? 'background:#7c8cff;color:#0e1020' : 'background:#3a2020;color:#ffb4b4');
    document.documentElement.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; }, 3500);
    setTimeout(() => el.remove(), 4300);
  }

  async function sync() {
    let deck;
    try { deck = await collect(); } catch (_) { return; }
    if (!deck || !deck.slides.length) return;
    const sig = JSON.stringify(deck);
    if (sig === lastSent) return;
    chrome.runtime.sendMessage({ type: 'slidelink-sync', deck }, res => {
      if (chrome.runtime.lastError || !res) return;
      if (res.off) return;
      if (!res.ok) { if (!shownOnce) badge('SlideLink is not running - start it to sync this deck', false); shownOnce = true; return; }
      lastSent = sig;
      const r = res.result || {};
      if (r.created) badge('✓ Added to SlideLink', true);
      else if (r.updated) badge('✓ Updated in SlideLink', true);
      else if (!shownOnce) badge('✓ In sync with SlideLink', true);
      shownOnce = true;
    });
  }

  setTimeout(sync, 2500);          // after the deck has loaded
  setInterval(sync, 15000);        // keep SlideLink up to date while you edit the deck
})();
