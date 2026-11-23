// SlideLink: Lists interface texts in static/*.js / *.html that have no German translation in static/i18n.js.
//   node setup/check_i18n.js            report (exit 0)
//   node setup/check_i18n.js --strict   exit 1 if anything is missing (for release checks)
'use strict';
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname, '..', 'static');

global.localStorage = { getItem: () => null, setItem() {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'de' }, configurable: true });
global.window = { alert() {}, confirm() {}, prompt() {} };
global.document = { documentElement: { dataset: {} }, body: {}, querySelector: () => null, addEventListener() {} };
global.MutationObserver = class { observe() {} };
const I = new Function(fs.readFileSync(path.join(dir, 'i18n.js'), 'utf8') + ';I18N.apply("de");return I18N;')();

// texts that are the same in German or are not words for people (brand, units, code)
const SAME = new Set(['SlideLink', 'OK', 'PDF', 'HTML', 'QR', 'Claude', 'claude.ai', 'Docs', 'Live', 'Relay', 'Wi-Fi', 'PowerPoint', 'Chrome', 'Edge', 'Claude Presentations', 'Decks', 'SlideLink Live', 'Link', 'Layout', 'Status', 'LIVE', 'Editor']);

const found = new Map();   // text -> first place it was seen
function add(text, where) {
  const t = text.replace(/\s+/g, ' ').trim();
  if (t.length < 2 || !/[A-Za-z]{2}/.test(t) || /\$\{|[{};=]|^\w+\(|^[a-z-]+$|^'|'$| \+ |\?\s*'|&&|\*\*|^\*|^!\[|\)\s*while\b/.test(t) || SAME.has(t)) return;
  if (!found.has(t)) found.set(t, where);
}
for (const f of fs.readdirSync(dir).filter(f => /\.(js|html)$/.test(f) && !['i18n.js', 'qr.js', 'icons.js', 'hslide.js'].includes(f))) {
  const src = fs.readFileSync(path.join(dir, f), 'utf8');
  const line = i => `${f}:${src.slice(0, i).split('\n').length}`;
  let m;
  const text = />([^<>`]{2,160})</g;                                            // text between tags in markup
  while ((m = text.exec(src))) add(m[1], line(m.index));
  const attr = /\b(?:title|placeholder|aria-label)="([^"${}]{2,160})"/g;        // tooltips, placeholders
  while ((m = attr.exec(src))) add(m[1], line(m.index));
  const call = /\b(?:toast|alert|confirm|prompt|flash)\(\s*'([^'\\]{2,200})'/g;   // messages
  while ((m = call.exec(src))) add(m[1], line(m.index));
}

const missing = [...found].filter(([t]) => I.t(t) === t && !SAME.has(t.replace(/^[^A-Za-z]+/, '')));
for (const [t, where] of missing) console.log(`${where.padEnd(18)} ${t}`);
console.log(`\n${found.size} texts checked, ${missing.length} without a German translation`);
process.exit(process.argv.includes('--strict') && missing.length ? 1 : 0);
