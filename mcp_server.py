# SlideLink: MCP server: lets Claude list, build, edit and control SlideLink presentations (JSON-RPC over stdio).
"""SlideLink MCP server - lets Claude build and control presentations live.

Claude Code / Claude Desktop start this over stdio. It talks to the running
SlideLink app (server.py) at SLIDELINK_URL (default http://localhost:8765).
Pure Python standard library.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("SLIDELINK_URL", "http://localhost:8765").rstrip("/")

CONTENT_HELP = ("Slide body in mini-markdown, one item per line: '- ' bullet, two spaces + '- ' sub-bullet, "
                "'### ' sub-heading, '> ' quote, **bold**, *italic*, `code`, ![alt](https://url) image, "
                "a line with only '---' starts the next column (two-column layout). Keep it to ~6 bullets.")
LAYOUT = {"type": "string", "enum": ["title", "content", "section", "two-column", "quote", "image", "html"],
          "description": "title = big centered title + subtitle; content = title + bullets; section = full-colour "
                         "divider; two-column = split with '---'; quote = big quote (title = attribution); image; "
                         "html = a designed slide: one <section> on a 1920x1080 canvas in the 'html' field (decks "
                         "imported from claude.ai use this; reuse the deck's CSS classes)."}
HTML = {"type": "string", "description": "For layout 'html': the slide as ONE <section>...</section> on a 1920x1080 "
                                         "canvas (inline styles or the deck's CSS classes). Notes go in 'notes'."}
STATUS = {"type": "string", "enum": ["draft", "in-progress", "review", "done"],
          "description": "Progress status shown on every device."}

TOOLS = [
    {"name": "list_presentations",
     "description": "List every presentation in the user's SlideLink library (id, title, slide count) and which "
                    "one is open. Only the open presentation can be edited; use open_presentation to switch.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "open_presentation",
     "description": "Open a presentation from the library on every device (PC, presenter screen, phones).",
     "inputSchema": {"type": "object", "properties": {"presentation_id": {"type": "string"}},
                     "required": ["presentation_id"]}},
    {"name": "create_presentation",
     "description": "Create a new, empty presentation and open it.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"},
                     "theme": {"type": "string", "enum": ["midnight", "paper", "ocean", "sunset"]}},
                     "required": ["title"]}},
    {"name": "get_presentation",
     "description": "Read the whole live presentation: title, theme, every slide (id, layout, status, title, "
                    "content, notes), which slide is on screen and which devices are connected. Call this first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "add_slide",
     "description": "Add a slide. It appears instantly on the user's PC and phone.",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "content": {"type": "string", "description": CONTENT_HELP},
         "html": HTML, "notes": {"type": "string", "description": "Speaker notes, shown on the phone remote."},
         "layout": LAYOUT, "status": STATUS,
         "position": {"type": "integer", "description": "0-based index to insert at; default = end."}},
         "required": ["title"]}},
    {"name": "update_slide",
     "description": "Change fields of an existing slide (only the fields you pass).",
     "inputSchema": {"type": "object", "properties": {
         "slide_id": {"type": "string"}, "title": {"type": "string"},
         "content": {"type": "string", "description": CONTENT_HELP}, "html": HTML, "notes": {"type": "string"},
         "layout": LAYOUT, "status": STATUS}, "required": ["slide_id"]}},
    {"name": "delete_slide", "description": "Delete a slide.",
     "inputSchema": {"type": "object", "properties": {"slide_id": {"type": "string"}}, "required": ["slide_id"]}},
    {"name": "move_slide", "description": "Move a slide to a new 0-based position.",
     "inputSchema": {"type": "object", "properties": {"slide_id": {"type": "string"},
                                                      "position": {"type": "integer"}},
                     "required": ["slide_id", "position"]}},
    {"name": "set_presentation", "description": "Set the presentation title and/or visual theme.",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "theme": {"type": "string", "enum": ["midnight", "paper", "ocean", "sunset"]}}}},
    {"name": "set_working_status",
     "description": "Show the user what you are doing right now, live on PC and phone (e.g. 'Writing the "
                    "pricing slide'). Pass slide_id to highlight that slide. Call with empty text when finished.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "slide_id": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "control_presentation",
     "description": "Drive the live presentation screen: next / prev / goto (with index, 0-based) / blackout.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["next", "prev", "goto", "blackout"]},
         "index": {"type": "integer"}}, "required": ["action"]}},
]


# Calls the SlideLink server's HTTP API.
def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"SlideLink app is not reachable at {BASE} ({e.reason}). "
                           "Start it with: python server.py")


# Runs one SlideLink operation (as 'claude').
def op(**kw):
    kw["by"] = "claude"
    res = http("POST", "/api/op", kw)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "operation failed"))
    return res.get("result", {})


# Short readable description of a presentation for Claude.
def describe(state):
    d = state["deck"]
    p = state["present"]
    lines = [f"Presentation: {d['title']}  (id: {d.get('id')}, theme: {d['theme']}, {len(d['slides'])} slides)",
             f"On screen: slide {p['index'] + 1}{' [BLACKED OUT]' if p['blackout'] else ''}",
             f"Connected devices: {state.get('devices') or 'none'}", ""]
    for i, s in enumerate(d["slides"]):
        lines.append(f"[{i}] id={s['id']}  layout={s['layout']}  status={s['status']}")
        lines.append(f"    title: {s['title']}")
        if s.get("content"):
            lines.append("    content:\n" + "\n".join("      " + x for x in s["content"].splitlines()))
        if s.get("html"):
            lines.append(f"    html ({len(s['html'])} chars): " + " ".join(s["html"].split())[:600])
        if s.get("notes"):
            lines.append(f"    notes: {s['notes']}")
    return "\n".join(lines)


# Executes one MCP tool call.
def call_tool(name, a):
    if name == "list_presentations":
        lib = http("GET", "/api/library")
        rows = [f"- {d['title']}  (id: {d['id']}, {d['slide_count']} slides)" + ("  <- open" if d["id"] == lib["active"] else "")
                for d in lib["decks"]]
        return "SlideLink library:" + chr(10) + chr(10).join(rows)
    if name == "open_presentation":
        op(op="open_deck", deck_id=a["presentation_id"])
        return "Opened on all devices. Call get_presentation to read it."
    if name == "create_presentation":
        r = op(op="create_deck", title=a["title"], theme=a.get("theme"))
        return f"Created and opened presentation id={r['deck_id']}."
    if name == "get_presentation":
        return describe(http("GET", "/api/state"))
    if name == "add_slide":
        r = op(op="add_slide", **{k: a[k] for k in ("title", "content", "html", "notes", "layout", "status", "position") if k in a})
        return f"Added slide id={r['slide_id']} at index {r['position']}."
    if name == "update_slide":
        op(op="update_slide", id=a["slide_id"], **{k: a[k] for k in ("title", "content", "html", "notes", "layout", "status") if k in a})
        return f"Updated slide {a['slide_id']}."
    if name == "delete_slide":
        op(op="delete_slide", id=a["slide_id"])
        return f"Deleted slide {a['slide_id']}."
    if name == "move_slide":
        r = op(op="move_slide", id=a["slide_id"], to=a["position"])
        return f"Moved slide to index {r['position']}."
    if name == "set_presentation":
        op(op="set_deck", **{k: a[k] for k in ("title", "theme") if k in a})
        return "Presentation updated."
    if name == "set_working_status":
        op(op="claude_status", text=a.get("text", ""), slide_id=a.get("slide_id") or None)
        return "Status shown to the user." if a.get("text") else "Status cleared."
    if name == "control_presentation":
        act = a["action"]
        extra = {"index": a.get("index", 0)} if act == "goto" else {}
        r = op(op=act, **extra)
        return f"Screen now on slide {r['index'] + 1}{' (blacked out)' if r['blackout'] else ''}."
    raise RuntimeError(f"Unknown tool {name}")


# Writes one JSON-RPC message to stdout.
def send(msg):
    sys.stdout.buffer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


# Answers one JSON-RPC request (initialize, tools/list, tools/call).
def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return  # notification
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "slidelink", "version": "1.0.0"},
                  "instructions": "Build the user's presentation live in SlideLink. Call get_presentation first. "
                                  "Use set_working_status before each step so the user can follow along on PC and "
                                  "phone, set slide status (in-progress while writing, review when done), and "
                                  "clear the status at the end."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params", {})
        try:
            text = call_tool(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}]}
        except Exception as e:  # report tool errors to Claude instead of crashing
            result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    else:
        return send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
    send({"jsonrpc": "2.0", "id": mid, "result": result})


# Reads JSON-RPC requests from stdin until it closes.
def main():
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            handle(m)


if __name__ == "__main__":
    main()
