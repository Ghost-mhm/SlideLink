const DEFAULTS = { enabled: true, server: 'http://localhost:8765', lastSync: null };
const $ = id => document.getElementById(id);

chrome.storage.local.get(DEFAULTS, cfg => {
  $('on').checked = cfg.enabled;
  if (cfg.lastSync) {
    const r = cfg.lastSync.result || {};
    const what = r.created ? 'added' : r.updated ? 'updated' : 'in sync';
    $('last').textContent = `Last: “${cfg.lastSync.title}” ${what}, ${new Date(cfg.lastSync.at).toLocaleString()}`;
  }
  fetch(cfg.server + '/api/whoami', { cache: 'no-store' })
    .then(r => r.json())
    .then(() => { $('app').innerHTML = '<span class="ok">● SlideLink is running</span>'; })
    .catch(() => { $('app').innerHTML = '<span class="bad">● SlideLink is not running</span> – start it from the desktop icon.'; });
});
$('on').onchange = e => chrome.storage.local.set({ enabled: e.target.checked });
