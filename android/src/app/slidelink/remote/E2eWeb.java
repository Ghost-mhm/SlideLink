package app.slidelink.remote;

import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Runs the remote page end-to-end encrypted: the WebView shows a virtual address (https://<pc-id>.slidelink.invalid)
 *     that never touches the network. Every request of the page is answered here through E2e.Client; requests with a body
 *     (commands) come over a small fetch() replacement injected into each page, because Android doesn't hand request bodies
 *     to shouldInterceptRequest.
 */
final class E2eWeb {
    final E2e.Client client;
    final String origin;          // https://<pc-id>.slidelink.invalid
    private final String host;

    /** Called when the relay answers with something that isn't from the paired PC (never fall back to unencrypted). */
    interface Alarm { void notThePc(); }
    private final Alarm alarm;

    E2eWeb(E2e.Client client, String pcId, Alarm alarm) {
        this.client = client;
        this.host = pcId.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9-]", "") + ".slidelink.invalid";
        this.origin = "https://" + host;
        this.alarm = alarm;
    }

    boolean mine(String url) {
        return url != null && (url.equals(origin) || url.startsWith(origin + "/"));
    }

    // fetch()/sendBeacon replacement: sends POSTs to the app (window.SlideLinkApp.e2eFetch), which encrypts them
    static final String SHIM = "<script>(function(){if(!window.SlideLinkApp||!SlideLinkApp.e2eFetch)return;"
            + "var of=window.fetch.bind(window),n=0,P={};"
            + "window.__e2eDone=function(id,st,h,b){var p=P[id];delete P[id];if(!p)return;"
            + "if(st<0){p.j(new TypeError(b||'Network error'));return;}"
            + "var s=atob(b),u=new Uint8Array(s.length);for(var i=0;i<s.length;i++)u[i]=s.charCodeAt(i);"
            + "p.r(new Response((st==204||st==304)?null:u,{status:st,headers:JSON.parse(h)}));};"
            + "function bytes(b){if(b==null)return Promise.resolve(null);if(typeof b==='string')return Promise.resolve(new TextEncoder().encode(b));"
            + "if(b instanceof Blob)return b.arrayBuffer().then(function(a){return new Uint8Array(a);});"
            + "if(b instanceof ArrayBuffer)return Promise.resolve(new Uint8Array(b));if(ArrayBuffer.isView(b))return Promise.resolve(new Uint8Array(b.buffer,b.byteOffset,b.byteLength));"
            + "if(b instanceof FormData||b instanceof URLSearchParams)return new Response(b).arrayBuffer().then(function(a){return new Uint8Array(a);});"
            + "return Promise.resolve(new TextEncoder().encode(String(b)));}"
            + "window.fetch=function(input,init){init=init||{};var m=(init.method||(input&&input.method)||'GET').toUpperCase();"
            + "var url=new URL(typeof input==='string'?input:(input.url||String(input)),location.href);"
            + "if(m==='GET'||m==='HEAD'||url.origin!==location.origin)return of(input,init);"
            + "return bytes(init.body).then(function(a){var s='';if(a)for(var i=0;i<a.length;i+=8192)s+=String.fromCharCode.apply(null,a.subarray(i,i+8192));"
            + "var h={};new Headers(init.headers||{}).forEach(function(v,k){h[k]=v;});"
            + "return new Promise(function(r,j){var id=++n;P[id]={r:r,j:j};SlideLinkApp.e2eFetch(id,m,url.pathname+url.search,JSON.stringify(h),btoa(s));});});};"
            + "navigator.sendBeacon=function(u,d){window.fetch(u,{method:'POST',body:d,headers:{'Content-Type':'application/json'}}).catch(function(){});return true;};"
            // live stream (EventSource) straight from the app: WebView would buffer an intercepted stream for seconds
            + "var S={};window.__e2eSse=function(id,t,d){var s=S[id];if(!s||s.readyState===2)return;"
            + "if(t==='open'){s.readyState=1;s._f('open',new Event('open'));return;}"
            + "if(t==='error'){s.readyState=2;delete S[id];s._f('error',new Event('error'));return;}"
            + "s._f(t,new MessageEvent(t,{data:d}));};"
            + "function ES(url){var self=this,L={},id=++n;self.url=String(url);self.readyState=0;self.withCredentials=false;"
            + "self.onopen=self.onmessage=self.onerror=null;"
            + "self.addEventListener=function(t,f){(L[t]=L[t]||[]).push(f);};"
            + "self.removeEventListener=function(t,f){L[t]=(L[t]||[]).filter(function(x){return x!==f;});};"
            + "self._f=function(t,ev){try{if(typeof self['on'+t]==='function')self['on'+t](ev);}catch(e){setTimeout(function(){throw e;});}"
            + "(L[t]||[]).slice().forEach(function(f){try{f(ev);}catch(e){setTimeout(function(){throw e;});}});};"
            + "self.close=function(){if(self.readyState===2)return;self.readyState=2;delete S[id];SlideLinkApp.e2eStreamClose(id);};"
            + "S[id]=self;var u=new URL(self.url,location.href);SlideLinkApp.e2eStream(id,u.pathname+u.search);}"
            + "ES.CONNECTING=0;ES.OPEN=1;ES.CLOSED=2;window.EventSource=ES;"
            + "})();</script>";

    private final Map<Integer, E2e.Response> streams = new java.util.concurrent.ConcurrentHashMap<>();

    /** Opens a live stream for the page and hands every event over as it arrives. */
    void stream(WebView view, int id, String path) {
        new Thread(() -> {
            E2e.Response r = null;
            try {
                if (!path.startsWith("/") || path.startsWith("//")) throw new IllegalArgumentException("bad path");
                Map<String, String> h = new LinkedHashMap<>();
                h.put("Accept", "text/event-stream");
                r = client.request("GET", path, h, null);
                if (r.status != 200) throw new java.io.IOException("stream refused " + r.status);
                streams.put(id, r);
                js(view, "window.__e2eSse(" + id + ",'open')");
                java.io.BufferedReader in = new java.io.BufferedReader(new java.io.InputStreamReader(r.body, StandardCharsets.UTF_8));
                String type = "message";
                StringBuilder data = new StringBuilder();
                for (String line; (line = in.readLine()) != null; ) {
                    if (line.isEmpty()) {                     // end of one event: hand it to the page now
                        if (data.length() > 0) {
                            String d = data.charAt(data.length() - 1) == '\n' ? data.substring(0, data.length() - 1) : data.toString();
                            js(view, "window.__e2eSse(" + id + "," + jsString(type) + "," + jsString(d) + ")");
                        }
                        type = "message";
                        data.setLength(0);
                    } else if (line.startsWith("event:")) {
                        type = line.substring(6).trim();
                    } else if (line.startsWith("data:")) {
                        data.append(line.substring(line.startsWith("data: ") ? 6 : 5)).append('\n');
                    }
                }
            } catch (SecurityException e) {
                alarm.notThePc();
            } catch (Exception ignored) {
                // ended or cut: the page reconnects by itself
            } finally {
                streams.remove(id);
                if (r != null) try { r.body.close(); } catch (Exception ignored) { }
                js(view, "window.__e2eSse(" + id + ",'error')");
            }
        }).start();
    }

    void closeStream(int id) {
        E2e.Response r = streams.remove(id);
        if (r != null) new Thread(() -> { try { r.body.close(); } catch (Exception ignored) { } }).start();
    }

    private static void js(WebView view, String code) {
        view.post(() -> view.evaluateJavascript(code, null));
    }

    /** Answers one request of the page (GET/HEAD); returns null for other addresses. */
    WebResourceResponse intercept(WebView view, WebResourceRequest req) {
        String url = req.getUrl().toString();
        if (!mine(url)) return null;
        String path = url.substring(origin.length());
        if (path.isEmpty()) path = "/";
        String method = req.getMethod() == null ? "GET" : req.getMethod().toUpperCase(Locale.ROOT);
        if (!method.equals("GET") && !method.equals("HEAD")) {
            return text(204, "No Content", "");      // bodies only come through the fetch() replacement
        }
        Map<String, String> h = new LinkedHashMap<>();
        for (Map.Entry<String, String> e : req.getRequestHeaders().entrySet()) {
            String k = e.getKey().toLowerCase(Locale.ROOT);
            if (k.equals("accept") || k.equals("accept-language") || k.equals("last-event-id") || k.equals("range")) h.put(e.getKey(), e.getValue());
        }
        try {
            E2e.Response r = client.request(method, path, h, null);
            String ctype = header(r.headers, "Content-Type", "application/octet-stream");
            String mime = ctype.split(";")[0].trim(), charset = null;
            for (String part : ctype.split(";")) if (part.trim().toLowerCase(Locale.ROOT).startsWith("charset=")) charset = part.trim().substring(8);
            Map<String, String> out = new LinkedHashMap<>();
            for (Map.Entry<String, String> e : r.headers.entrySet()) {
                String k = e.getKey().toLowerCase(Locale.ROOT);
                if (!k.equals("content-type") && !k.equals("set-cookie") && !k.equals("content-length") && !k.equals("location")) out.put(e.getKey(), e.getValue());
            }
            int st = r.status;
            if (st >= 300 && st < 400) st = 200;      // WebView can't take redirects here (SlideLink doesn't send any to phones)
            if (mime.equals("text/html")) {          // inject the fetch() replacement before the page's own scripts
                String html = new String(E2e.readAll(r.body), charset == null ? "UTF-8" : charset);
                r.body.close();
                int at = html.indexOf("<head>");
                html = at >= 0 ? html.substring(0, at + 6) + SHIM + html.substring(at + 6) : SHIM + html;
                return new WebResourceResponse(mime, "UTF-8", st, reason(st), out, new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8)));
            }
            return new WebResourceResponse(mime, charset, st, reason(st), out, r.body);
        } catch (SecurityException e) {
            alarm.notThePc();
            return text(502, "Bad Gateway", "Not your PC");
        } catch (Exception e) {
            return text(502, "Bad Gateway", "SlideLink on your PC can't be reached right now.");
        }
    }

    /** A command from the page (fetch() with a body): encrypt, send, give the answer back to the page. */
    void fetch(WebView view, int id, String method, String path, String headersJson, String bodyB64) {
        new Thread(() -> {
            String js;
            try {
                Map<String, String> h = new LinkedHashMap<>();
                for (Map.Entry<String, Object> e : Json.obj(headersJson == null ? "{}" : headersJson).entrySet())
                    h.put(e.getKey(), String.valueOf(e.getValue()));
                byte[] body = bodyB64 == null || bodyB64.isEmpty() ? new byte[0] : E2e.unb64(bodyB64);
                if (!path.startsWith("/") || path.startsWith("//")) throw new IllegalArgumentException("bad path");
                E2e.Response r = client.request(method.toUpperCase(Locale.ROOT), path, h, body);
                byte[] out = E2e.readAll(r.body);
                r.body.close();
                Map<String, Object> hdrs = new LinkedHashMap<>();
                for (Map.Entry<String, String> e : r.headers.entrySet()) {
                    String k = e.getKey().toLowerCase(Locale.ROOT);
                    if (!k.equals("set-cookie") && !k.equals("content-length")) hdrs.put(e.getKey(), e.getValue());
                }
                js = "window.__e2eDone(" + id + "," + r.status + "," + jsString(Json.write(hdrs)) + "," + jsString(E2e.b64(out)) + ")";
            } catch (SecurityException e) {
                alarm.notThePc();
                js = "window.__e2eDone(" + id + ",-1,'{}','Not your PC')";
            } catch (Exception e) {
                js = "window.__e2eDone(" + id + ",-1,'{}'," + jsString("Not connected") + ")";
            }
            final String call = js;
            view.post(() -> view.evaluateJavascript(call, null));
        }).start();
    }

    static String jsString(String s) {
        StringBuilder b = new StringBuilder();
        Json.quote(b, s);
        return b.toString();
    }

    private static String header(Map<String, String> h, String name, String fallback) {
        for (Map.Entry<String, String> e : h.entrySet()) if (e.getKey().equalsIgnoreCase(name)) return e.getValue();
        return fallback;
    }

    private static String reason(int st) {
        switch (st) {
            case 200: return "OK";
            case 204: return "No Content";
            case 400: return "Bad Request";
            case 401: return "Unauthorized";
            case 403: return "Forbidden";
            case 404: return "Not Found";
            default: return st < 400 ? "OK" : "Error";
        }
    }

    private static WebResourceResponse text(int st, String reason, String body) {
        return new WebResourceResponse("text/plain", "UTF-8", st, reason, new LinkedHashMap<>(),
                new ByteArrayInputStream(body.getBytes(StandardCharsets.UTF_8)));
    }
}
