// SlideLink · EN: Appearance per device: light, dark or like the system. Loaded first, so the page never flashes in the wrong colors.
// SlideLink · DE: Anzeige pro Gerät: hell, dunkel oder wie das System. Wird zuerst geladen, damit die Seite nie in falschen Farben aufblitzt.
'use strict';
const THEME = (() => {
  const media = window.matchMedia ? matchMedia('(prefers-color-scheme: light)') : null;
  const choice = () => localStorage.getItem('sl-theme') || 'system';          // 'light' | 'dark' | 'system'
  function apply() {
    const c = choice();
    const light = c === 'light' || (c === 'system' && media && media.matches);
    document.documentElement.dataset.theme = light ? 'light' : 'dark';
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = light ? '#f4f5f9' : (document.body && document.body.classList.contains('is-remote') ? '#000000' : '#14151c');
  }
  if (media && media.addEventListener) media.addEventListener('change', apply);   // "System" follows changes live
  apply();
  document.addEventListener('DOMContentLoaded', apply);
  return { choice, set(c) { localStorage.setItem('sl-theme', c); apply(); }, apply };
})();
