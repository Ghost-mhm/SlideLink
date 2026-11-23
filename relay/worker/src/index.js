// SlideLink Relay as a Cloudflare Worker: the meeting point on the internet for every SlideLink PC.
//
// The PC keeps ONE permanent WebSocket to the relay (outgoing, so routers, carrier NAT and school or hotel
// networks don't matter). Phones open  <relay>/p/<pc-id>  once; a cookie then routes all their requests to that PC.
// Requests go to the PC over the WebSocket, answers come back as a stream (SSE works).
//
//   relay -> PC   {"t":"req","rid":…,"method":…,"path":…,"headers":{…},"client":…,"body":<base64>,"pieces":bool}
//                 binary: 16 bytes rid + data                  pieces of a big upload, sent before its "req"
//                 {"t":"cancel","rid":…}                       the phone left
//   PC -> relay   {"t":"head","rid":…,"status":200,"headers":[[k,v],…]}
//                 binary: 16 bytes rid + data                  any number of pieces
//                 {"t":"end","rid":…}
//                 {"t":"ping"}  ->  {"t":"pong"}
//
// Each PC gets its own Durable Object, found by its id:
// the PC keeps one WebSocket to it, phones send normal HTTPS requests, the object passes them on.
// WebSocket messages don't count as Worker requests, and pings are answered without waking the object.

const HOP = new Set(['connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade', 'proxy-connection', 'host', 'content-length']);
const NULL_BODY = new Set([101, 204, 205, 304]);
const ID = '[a-zA-Z0-9]{6,40}';
const HEAD_WAIT = 30000;
const MAX_BODY = 20 * 1024 * 1024;
const MAX_OPEN = 64;
const MAX_PER_MINUTE = 3000;   // all phones of one PC
const MAX_PER_IP = 300;        // one address, per PC and minute

const text = (status, msg, extra = {}) =>
  new Response(msg, { status, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', ...extra } });

const esc = s => String(s).replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);

// A link to ANOTHER SlideLink PC than the one this browser uses: ask first, so nobody can quietly switch
// a phone to their own PC with a link.
function switchPage(id, path) {
  const go = `/p/${id}${path === '/' ? '' : path}${path.includes('?') ? '&' : '?'}sl_switch=1`;
  return new Response(`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>SlideLink</title>
<body style="font-family:system-ui;background:#111;color:#eee;display:grid;place-items:center;min-height:90vh;margin:0">
<div style="max-width:420px;padding:24px;text-align:center"><h2>Open another SlideLink PC?</h2>
<p>This link leads to a different SlideLink PC than the one you use on this device. Only continue if you trust whoever sent it.</p>
<p><a href="${esc(go)}" style="display:inline-block;padding:12px 20px;border-radius:10px;background:#7c7cff;color:#fff;text-decoration:none">Continue</a></p></div>`,
    { status: 200, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'x-frame-options': 'DENY' } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/_relay/health') return text(200, 'ok');
    const ip = request.headers.get('cf-connecting-ip') || 'unknown';

    let m = url.pathname.match(new RegExp(`^/_pc/(${ID})/ws$`));
    if (m) {                                                                // the PC's permanent line
      if (env.CLAIM_LIMIT && !(await env.CLAIM_LIMIT.limit({ key: ip })).success) return text(429, 'Too many connections from this address.');
      return env.PCS.get(env.PCS.idFromName(m[1])).fetch(request);
    }
    if (env.IP_LIMIT && !(await env.IP_LIMIT.limit({ key: ip })).success) return text(429, 'Too many requests from this address - try again in a minute.');

    // Own domain: every PC has its own address  <pc-id>.<domain>  - its own web origin, so the pages and
    // logins of different PCs are completely separated by the browser itself.
    const dom = (env.RELAY_DOMAIN || '').toLowerCase();
    if (dom && url.hostname.endsWith('.' + dom)) {
      const sub = url.hostname.slice(0, -dom.length - 1);
      if (!new RegExp(`^${ID}$`).test(sub)) return text(404, 'SlideLink Relay – unknown address.');
      const fwd = new Request('https://pc.internal' + url.pathname + url.search, request);
      fwd.headers.set('x-relay-client', ip);
      fwd.headers.set('x-relay-host', url.host);
      fwd.headers.set('x-relay-own-origin', '1');
      return env.PCS.get(env.PCS.idFromName(sub)).fetch(fwd);
    }
    // with an own domain, old shared links move to the PC's own address
    if (dom) {
      const mm = url.pathname.match(new RegExp(`^/p/(${ID})(/.*)?$`));
      if (mm && request.method === 'GET') return Response.redirect(`https://${mm[1]}.${dom}${mm[2] || '/'}${url.search}`, 302);
    }

    let id, path = url.pathname + url.search, cookie = null;
    const current = ((request.headers.get('cookie') || '').match(new RegExp(`(?:^|;\\s*)sl_pc=(${ID})`)) || [])[1];
    m = url.pathname.match(new RegExp(`^/p/(${ID})(/.*)?$`));
    if (m) {
      id = m[1];
      const q = new URLSearchParams(url.search);
      const confirmed = q.get('sl_switch') === '1';
      q.delete('sl_switch');
      const qs = q.toString();
      path = (m[2] || '/') + (qs ? '?' + qs : '');
      if (current && current !== id && !confirmed && request.method === 'GET') return switchPage(id, path);
      cookie = `sl_pc=${id}; Path=/; Max-Age=31536000; HttpOnly; Secure; SameSite=Lax`;
    } else {
      if (!current) return text(404, 'SlideLink Relay – open the link from your SlideLink PC (…/p/<id>).');
      id = current;
    }
    const fwd = new Request('https://pc.internal' + path, request);
    fwd.headers.set('x-relay-client', request.headers.get('cf-connecting-ip') || '');
    fwd.headers.set('x-relay-host', url.host);
    fwd.headers.delete('x-relay-own-origin');            // only the own-domain path may say so
    const res = await env.PCS.get(env.PCS.idFromName(id)).fetch(fwd);
    if (!cookie) return res;
    const out = new Response(res.body, res);
    out.headers.append('set-cookie', cookie);
    return out;
  },
};

async function sha256(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}

function b64(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

const rid16 = () => [...crypto.getRandomValues(new Uint8Array(8))].map(b => b.toString(16).padStart(2, '0')).join('');

export class PcRelay {
  constructor(state, env) {
    this.state = state;
    this.pending = new Map();        // rid -> { resolveHead, writer }
    this.minute = 0;
    this.count = 0;
    this.ips = new Map();            // ip -> requests this minute (exact, per PC)
    state.setWebSocketAutoResponse(new WebSocketRequestResponsePair('{"t":"ping"}', '{"t":"pong"}'));
  }

  pc() {                             // the newest line that still works (closed ones from earlier connections may still be listed)
    const all = this.state.getWebSockets('pc');
    for (let i = all.length - 1; i >= 0; i--) {
      try { all[i].send('{"t":"probe"}'); return all[i]; } catch { try { all[i].close(1011, 'dead'); } catch {} }
    }
    console.log('no open PC line', all.length);
    return null;
  }

  async fetch(request) {
    // the PC connects (or reconnects)
    if ((request.headers.get('upgrade') || '').toLowerCase() === 'websocket') {
      const key = request.headers.get('x-relay-key') || '';
      if (key.length < 16) return text(403, 'no');
      const h = await sha256(key);
      const stored = await this.state.storage.get('key');
      if (stored && stored !== h) return text(403, 'wrong key');
      if (!stored) await this.state.storage.put('key', h);        // the first connection claims this id
      this.pcId = new URL(request.url).pathname.split('/')[2];
      await this.state.storage.put('id', this.pcId);
      for (const old of this.state.getWebSockets('pc')) { try { old.close(1000, 'replaced'); } catch {} }
      const [client, server] = Object.values(new WebSocketPair());
      this.state.acceptWebSocket(server, ['pc']);
      return new Response(null, { status: 101, webSocket: client });
    }

    // a phone request
    const ws = this.pc();
    if (!ws) return text(503, 'SlideLink: the PC is offline. Start SlideLink on the PC and try again.');
    const now = Math.floor(Date.now() / 60000);
    if (now !== this.minute) { this.ips.clear(); this.minute = now; this.count = 0; }
    const ip = request.headers.get('x-relay-client') || '';
    const n = (this.ips.get(ip) || 0) + 1;
    this.ips.set(ip, n);
    if (n > MAX_PER_IP) return text(429, 'Too many requests from this address - try again in a minute.');
    if (++this.count > MAX_PER_MINUTE || this.pending.size >= MAX_OPEN) return text(429, 'Too many requests - try again in a moment.');
    let body = new Uint8Array(0);
    if (!['GET', 'HEAD'].includes(request.method)) {
      body = new Uint8Array(await request.arrayBuffer());
      if (body.length > MAX_BODY) return text(413, 'too large');
    }
    const url = new URL(request.url);
    const pcId = this.pcId || (this.pcId = await this.state.storage.get('id'));
    const ownOrigin = request.headers.get('x-relay-own-origin') === '1';   // <pc-id>.<domain>: cookies stay as they are
    const headers = {};
    for (const [k, v] of request.headers) if (!HOP.has(k) && k !== 'cookie' && !k.startsWith('cf-') && !k.startsWith('x-relay-')) headers[k] = v;
    headers.host = request.headers.get('x-relay-host') || '';
    // only this PC's own cookies reach it (stored as name__<pc-id>), never those of other PCs on the relay
    const own = [];
    for (const part of (request.headers.get('cookie') || '').split(';')) {
      const [k, ...rest] = part.trim().split('=');
      const suffix = `__${pcId}`;
      if (pcId && k && k.endsWith(suffix)) own.push(`${k.slice(0, -suffix.length)}=${rest.join('=')}`);
    }
    if (ownOrigin) { if (request.headers.get('cookie')) headers.cookie = request.headers.get('cookie'); }
    else if (own.length) headers.cookie = own.join('; ');

    const rid = rid16();
    // big uploads go in pieces before the request (one WebSocket message may be at most 1 MiB)
    const PIECE = 512 * 1024;
    const prefix = new TextEncoder().encode(rid);
    const inline = body.length > PIECE ? new Uint8Array(0) : body;
    const { readable, writable } = new TransformStream();
    const writer = writable.getWriter();
    let resolveHead;
    const head = new Promise(r => { resolveHead = r; });
    this.pending.set(rid, { resolveHead, writer });
    try {
      for (let i = 0; i < body.length && body.length > PIECE; i += PIECE) {
        const piece = body.subarray(i, i + PIECE);
        const msg = new Uint8Array(16 + piece.length);
        msg.set(prefix); msg.set(piece, 16);
        ws.send(msg);
      }
      ws.send(JSON.stringify({ t: 'req', rid, method: request.method, path: url.pathname + url.search, headers,
                             client: request.headers.get('x-relay-client') || '', body: b64(inline), pieces: body.length > PIECE }));
    } catch {                          // the line closed just now: answer instead of crashing
      this.pending.delete(rid);
      writer.close().catch(() => {});
      return text(503, 'SlideLink: the PC is reconnecting - try again in a moment.');
    }
    const h = await Promise.race([head, new Promise(r => setTimeout(() => r(null), HEAD_WAIT))]);
    if (!h) {
      this.pending.delete(rid);
      try { ws.send(JSON.stringify({ t: 'cancel', rid })); } catch {}
      return text(504, 'SlideLink: the PC did not answer in time.');
    }
    const rh = new Headers();
    for (const [k, v] of h.headers) {
      const lk = k.toLowerCase();
      if (HOP.has(lk) && lk !== 'content-length') continue;
      if (lk === 'set-cookie' && !ownOrigin) {                       // the PC's cookies get its id, so other PCs never see them
        const eq = v.indexOf('=');
        rh.append(k, eq > 0 ? `${v.slice(0, eq).trim()}__${pcId}${v.slice(eq)}` : v);
        continue;
      }
      rh.append(k, v);
    }
    rh.set('x-frame-options', 'SAMEORIGIN');
    rh.set('referrer-policy', 'no-referrer');
    if (NULL_BODY.has(h.status) || request.method === 'HEAD') {
      writer.close().catch(() => {});
      return new Response(null, { status: h.status, headers: rh });
    }
    return new Response(readable, { status: h.status, headers: rh });
  }

  async webSocketMessage(ws, msg) {
    if (typeof msg === 'string') {
      const m = JSON.parse(msg);
      if (m.t === 'ping') return ws.send('{"t":"pong"}');
      const p = this.pending.get(m.rid);
      if (!p) return;
      if (m.t === 'head') p.resolveHead({ status: m.status, headers: m.headers });
      else if (m.t === 'end') { this.pending.delete(m.rid); p.writer.close().catch(() => {}); }
      return;
    }
    const bytes = new Uint8Array(msg);
    const rid = new TextDecoder().decode(bytes.subarray(0, 16));
    const p = this.pending.get(rid);
    if (!p) return;
    p.writer.write(bytes.slice(16)).catch(() => {           // the phone left: tell the PC to stop
      this.pending.delete(rid);
      try { ws.send(JSON.stringify({ t: 'cancel', rid })); } catch {}
    });
  }

  async webSocketClose(ws) {
    for (const p of this.pending.values()) {
      p.resolveHead({ status: 503, headers: [['content-type', 'text/plain']] });
      p.writer.close().catch(() => {});
    }
    this.pending.clear();
  }

  async webSocketError(ws) {
    return this.webSocketClose(ws);
  }
}
