// SlideLink Sync - background: forwards decks to the SlideLink app on this PC.
const DEFAULTS = { enabled: true, server: 'http://localhost:8765', lastSync: null };

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || msg.type !== 'slidelink-sync') return false;
  chrome.storage.local.get(DEFAULTS, async cfg => {
    if (!cfg.enabled) return reply({ ok: false, off: true });
    try {
      const r = await fetch(cfg.server + '/api/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(msg.deck),
      });
      const j = await r.json();
      if (j.ok) {
        chrome.storage.local.set({ lastSync: { title: (msg.deck.index || {}).title || 'Deck', at: Date.now(), result: j.result } });
      }
      reply(j);
    } catch (e) {
      reply({ ok: false, error: 'SlideLink is not running' });
    }
  });
  return true; // answer asynchronously
});
