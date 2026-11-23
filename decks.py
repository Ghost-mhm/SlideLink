"""The presentation library, the live deck state that every screen shows, and end-to-end pairing."""
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid

from core import DATA_DIR, DECKS_DIR, DECK_ID, GROUP_HOURS, KICK, LANGS, LAYOUTS, LIBRARY_FILE, PC_ROLES, PORT, ROOT, SLIDE_FIELDS, STATUSES, THEMES, app_build, build_steps, make_slide, new_id, sample_deck, write_json
from devices import app_latest, registry
from network import audience_base

# ---------------------------------------------------------------- library
# All presentations on disk (decks/*.json) with cached summaries for the library views.
class Library:
    """Every presentation is one JSON file in DECKS_DIR."""

    # Loads library.json and creates the welcome deck if the library is empty.
    def __init__(self):
        self.lock = threading.RLock()
        self.version = 0
        self._summaries = {}  # id -> (mtime, summary)
        try:
            with open(LIBRARY_FILE, encoding="utf-8") as f:
                self.meta = json.load(f)
        except (FileNotFoundError, ValueError):
            self.meta = {}
        self._migrate_legacy()
        if not self.ids():
            self.create(**sample_deck())

    # Moves a deck from the very first SlideLink version into the library.
    def _migrate_legacy(self):
        """Bring over deck.json from the first version (stored next to server.py)."""
        old_deck = os.path.join(ROOT, "deck.json")
        if os.path.exists(old_deck) and not self.ids():
            try:
                with open(old_deck, encoding="utf-8") as f:
                    d = json.load(f)
                self.create(title=d.get("title", "My first deck"), theme=d.get("theme", "midnight"),
                            slides=d.get("slides", []))
                os.replace(old_deck, old_deck + ".migrated")
            except (OSError, ValueError):
                pass

    # File path of a deck.
    def path(self, did):
        if not isinstance(did, str) or not DECK_ID.match(did):
            raise ValueError("Unknown presentation")
        return os.path.join(DECKS_DIR, did + ".json")

    # Ids of all decks.
    def ids(self):
        return [f[:-5] for f in os.listdir(DECKS_DIR) if f.endswith(".json") and DECK_ID.match(f[:-5])]

    # Loads one complete deck.
    def load(self, did):
        try:
            with open(self.path(did), encoding="utf-8") as f:
                deck = json.load(f)
        except FileNotFoundError:
            raise ValueError("Unknown presentation")
        deck.setdefault("id", did)
        deck.setdefault("title", "Untitled")
        deck.setdefault("theme", "midnight")
        deck.setdefault("slides", [])
        return deck

    # Saves a deck and bumps the library version (so all screens refresh).
    def save(self, deck):
        deck["updated_at"] = time.time()
        with self.lock:
            write_json(self.path(deck["id"]), deck)
            self.version += 1

    # Creates a new deck (optionally with slides, CSS, fonts, source, kind and date).
    def create(self, title="Untitled presentation", theme="midnight", slides=None, **extra):
        now = time.time()
        deck = {"id": uuid.uuid4().hex[:12], "title": str(title or "Untitled presentation")[:200],
                "theme": theme if theme in THEMES else "midnight", "created_at": now, "updated_at": now,
                "slides": []}
        for k in ("css", "faces", "source", "kind", "date"):
            if extra.get(k) is not None:
                deck[k] = extra[k]
        for s in slides or []:
            fields = {k: s.get(k) for k in SLIDE_FIELDS if isinstance(s.get(k), str)}
            if fields.get("layout") not in LAYOUTS:
                fields.pop("layout", None)
            if fields.get("status") not in STATUSES:
                fields.pop("status", None)
            sl = make_slide(**fields)
            sl["updated_by"] = s.get("updated_by") or "import"
            if isinstance(s.get("id"), str) and re.match(r"^[A-Za-z0-9_-]{1,64}$", s["id"]):
                sl["id"] = s["id"]
            deck["slides"].append(sl)
        self.save(deck)
        return deck

    # Deletes a deck.
    def delete(self, did):
        with self.lock:
            os.remove(self.path(did))
            self._summaries.pop(did, None)
            self.version += 1

    # Remembers which deck is open (and saves the watcher list).
    def set_active(self, did):
        self.meta["active"] = did
        write_json(LIBRARY_FILE, self.meta)

    # Short summary of a deck for lists (title, cover, counts, kind, date), cached by file time.
    def summary(self, did):
        p = self.path(did)
        mtime = os.path.getmtime(p)
        cached = self._summaries.get(did)
        if cached and cached[0] == mtime:
            return cached[1]
        d = self.load(did)
        counts = {}
        for s in d["slides"]:
            counts[s.get("status", "draft")] = counts.get(s.get("status", "draft"), 0) + 1
        summ = {"id": did, "title": d["title"], "theme": d["theme"], "slide_count": len(d["slides"]),
                "created_at": d.get("created_at"), "updated_at": d.get("updated_at"),
                "source": d.get("source"), "status_counts": counts,
                "kind": d.get("kind") or "deck", "date": d.get("date") or d.get("created_at"),
                "cover": d["slides"][0] if d["slides"] else None,
                "css": d.get("css", ""), "faces": d.get("faces", [])}
        self._summaries[did] = (mtime, summ)
        return summ

    # Summaries of all decks, newest first.
    def listing(self):
        out = []
        for did in self.ids():
            try:
                out.append(self.summary(did))
            except (OSError, ValueError):
                pass
        out.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
        return out


library = Library()


# ---------------------------------------------------------------- deck state
# The live state shared by all screens: open deck, presentation position, activity, Claude status, connected clients.
class Store:
    # Opens the last active deck.
    def __init__(self):
        self.lock = threading.RLock()
        self.clients = {}   # client id -> {"role", "device", "q"}
        self.version = 0
        self.present = {"index": 0, "step": 0, "blackout": False, "changed_at": time.time()}
        self.activity = []
        self.claude = {"text": "", "slide_id": None, "at": 0}
        self.poll = None        # live poll: {id, question, options, votes {voter: option}, open, show}
        self.speaker = None     # who presents right now (several presenters): {"name", "at"}
        self.join_qr = False    # show the big audience QR code on the presentation screen
        self.group = None       # group session: {key, expires, members {id: {name, token_hash, joined}}}
        self.group_qr = False   # show the big group QR code on the presentation screen
        try:
            self.deck = library.load(library.meta.get("active"))
        except ValueError:
            first = (library.listing() or [None])[0]
            self.deck = library.load(first["id"]) if first else library.create()
        library.set_active(self.deck["id"])

    # Saves the open deck.
    def save(self):
        library.save(self.deck)

    # Opens another deck on all screens, starting at slide 1.
    def open_deck(self, did):
        self.deck = library.load(did)
        library.set_active(did)
        self.present.update(index=0, step=0, blackout=False, changed_at=time.time())

    # The complete state as sent to every screen.
    def group_active(self):
        """The running group session (ends by itself after GROUP_HOURS)."""
        if self.group and time.time() > self.group["expires"]:
            self.group = None
            self.group_qr = False
        return self.group

    # Finds the group member that belongs to a token (or None).
    def group_member(self, token):
        g = self.group_active()
        if not g or not token:
            return None
        h = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for mid, m in g["members"].items():
            if secrets.compare_digest(m["token_hash"], h):
                return {"id": "g:" + mid, "name": m["name"], "type": "group"}
        return None

    # Group info for admins: link key, end time, members with online state.
    def group_admin_view(self):
        g = self.group_active()
        if not g:
            return {"active": False, "qr": False}
        online = {c["device"] for c in self.clients.values()}
        return {"active": True, "key": g["key"], "expires": g["expires"], "qr": self.group_qr,
                "members": [{"id": mid, "name": m["name"], "online": ("g:" + mid) in online} for mid, m in g["members"].items()]}

    def audience_key(self):
        """Secret part of the audience link (kept in library.json; a new one makes old links invalid)."""
        key = library.meta.get("audience_key")
        if not key:
            key = secrets.token_urlsafe(9)
            library.meta["audience_key"] = key
            library.set_active(library.meta.get("active"))
        return key

    # Poll for everyone: question, options and vote counts (never who voted what).
    def poll_public(self):
        if not self.poll:
            return None
        counts = [0] * len(self.poll["options"])
        for v in self.poll["votes"].values():
            counts[v] += 1
        return {"id": self.poll["id"], "question": self.poll["question"], "options": self.poll["options"],
                "counts": counts, "total": sum(counts), "open": self.poll["open"], "show": self.poll["show"]}

    # The reduced state for the audience: slides without speaker notes, position, poll, speaker.
    def public_snapshot(self):
        d = self.deck
        slides = [{k: s.get(k) for k in ("id", "title", "layout", "html", "content")} for s in d["slides"]]
        return {"deck": {"id": d["id"], "title": d["title"], "theme": d.get("theme"), "css": d.get("css", ""),
                         "faces": d.get("faces", []), "slides": slides},
                "present": self.present, "poll": self.poll_public(), "speaker": self.speaker,
                "lang": library.meta.get("lang", "en"), "build": app_build(), "server_time": time.time()}

    # The state for group members: like the full state, but without linked devices and group admin data.
    def member_snapshot(self):
        s = self.snapshot()
        g = self.group_active()
        s["linked"] = []
        s["group"] = {"active": bool(g), "member": True,
                      "names": [m["name"] for m in g["members"].values()] if g else []}
        return s

    def snapshot(self):
        roles = {}
        for c in self.clients.values():
            roles[c["role"]] = roles.get(c["role"], 0) + 1
        return {"version": self.version, "deck": self.deck, "present": self.present,
                "activity": self.activity[:30], "claude": self.claude, "devices": roles,
                "linked": registry.public(self.clients), "library_version": library.version,
                "build": app_build(),
                "app_latest": app_latest(),
                "e2e_pair": pairing.view() if pairing else [],
                "poll": self.poll_public(), "speaker": self.speaker, "group": self.group_admin_view(),
                "lang": library.meta.get("lang", "en"),
                "audience": {"key": self.audience_key(), "url": audience_base(), "qr": self.join_qr,
                             "count": sum(1 for c in self.clients.values() if c["role"] == "audience")},
                "pc_windows": {r: sum(1 for c in self.clients.values() if c["device"] == "host" and c["role"] == r)
                               for r in PC_ROLES},
                "server_time": time.time()}

    # Pushes the current state to every connected screen (SSE).
    def publish(self, origin=None):
        with self.lock:
            self.version += 1
            data = json.dumps({"type": "state", "origin": origin, "state": self.snapshot()},
                              ensure_ascii=False)
            public = member = None
            for c in list(self.clients.values()):
                if c["role"] == "audience":           # the audience gets the reduced state only
                    if public is None:
                        public = json.dumps({"type": "state", "state": self.public_snapshot()}, ensure_ascii=False)
                    c["q"].put(public)
                elif str(c["device"]).startswith("g:"):   # group members: no device list, no group admin data
                    if member is None:
                        member = json.dumps({"type": "state", "origin": origin, "state": self.member_snapshot()},
                                            ensure_ascii=False)
                    c["q"].put(member)
                else:
                    c["q"].put(data)

    # Laser pointer from a phone: only the presentation windows get it, nothing is saved (fast, many per second).
    def pointer(self, op):
        try:
            msg = {"type": "pointer", "x": min(1.0, max(0.0, float(op["x"]))), "y": min(1.0, max(0.0, float(op["y"])))}
        except (KeyError, TypeError, ValueError):
            msg = {"type": "pointer", "hide": True}
        data = json.dumps(msg)
        with self.lock:
            for c in self.clients.values():
                if c["role"] == "present":
                    c["q"].put(data)

    # Disconnects all live connections of an unlinked device.
    def kick(self, device_id):
        with self.lock:
            for c in self.clients.values():
                if c["device"] == device_id:
                    c["q"].put(KICK)

    # Adds an entry to the activity feed.
    def log(self, by, text, slide_id=None):
        self.activity.insert(0, {"at": time.time(), "by": by, "text": text, "slide_id": slide_id})
        del self.activity[50:]

    # Position of a slide in the open deck (error if missing).
    def _index(self, slide_id):
        for i, s in enumerate(self.deck["slides"]):
            if s["id"] == slide_id:
                return i
        raise ValueError(f"No slide with id '{slide_id}'")

    # Readable name of a slide for the activity feed.
    def _label(self, i):
        t = self.deck["slides"][i]["title"] or "untitled"
        return f"slide {i + 1} “{t[:40]}”"

    # Validates slide fields (layout, status, lengths).
    @staticmethod
    def _check(fields):
        if "layout" in fields and fields["layout"] not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        for k in ("title", "content", "notes"):
            if k in fields and not isinstance(fields[k], str):
                raise ValueError(f"{k} must be a string")

    # Keeps the presentation position inside the deck after changes.
    def _clamp_present(self):
        n = len(self.deck["slides"])
        self.present["index"] = max(0, min(self.present["index"], max(n - 1, 0)))

    # Runs one operation (navigation, editing, decks, import, Claude status). Returns (result, deck changed).
    def apply(self, op):
        """Apply one operation. Returns (result, deck_changed)."""
        kind = op.get("op")
        by = op.get("by", "you")
        slides = self.deck["slides"]
        with self.lock:
            if kind == "add_slide":
                fields = {k: op.get(k) for k in SLIDE_FIELDS if op.get(k) is not None}
                self._check(fields)
                s = make_slide(**fields)
                s["updated_by"] = by
                pos = op.get("position")
                if op.get("after"):
                    pos = self._index(op["after"]) + 1
                pos = len(slides) if pos is None else max(0, min(int(pos), len(slides)))
                slides.insert(pos, s)
                if pos <= self.present["index"] and len(slides) > 1:
                    self.present["index"] += 1
                self.log(by, f"added {self._label(pos)}", s["id"])
                return {"slide_id": s["id"], "position": pos}, True

            if kind == "update_slide":
                i = self._index(op.get("id"))
                fields = {k: op[k] for k in SLIDE_FIELDS if k in op}
                self._check(fields)
                s = slides[i]
                old_status = s["status"]
                s.update(fields)
                s["updated_at"] = time.time()
                s["updated_by"] = by
                if by == "claude":
                    self.log(by, f"edited {self._label(i)} ({', '.join(fields) or 'nothing'})", s["id"])
                elif "status" in fields and fields["status"] != old_status:
                    self.log(by, f"marked {self._label(i)} as {fields['status']}", s["id"])
                return {"slide_id": s["id"]}, True

            if kind == "delete_slide":
                i = self._index(op.get("id"))
                label = self._label(i)
                slides.pop(i)
                if i < self.present["index"]:
                    self.present["index"] -= 1
                self._clamp_present()
                self.log(by, f"deleted {label}")
                return {"deleted": op.get("id")}, True

            if kind == "duplicate_slide":
                i = self._index(op.get("id"))
                s = dict(slides[i], id=new_id(), updated_at=time.time(), updated_by=by, status="draft")
                slides.insert(i + 1, s)
                self.log(by, f"duplicated {self._label(i)}", s["id"])
                return {"slide_id": s["id"], "position": i + 1}, True

            if kind == "move_slide":
                i = self._index(op.get("id"))
                to = max(0, min(int(op.get("to", i)), len(slides) - 1))
                current_id = slides[self.present["index"]]["id"] if slides else None
                s = slides.pop(i)
                slides.insert(to, s)
                if current_id:
                    self.present["index"] = self._index(current_id)
                self.log(by, f"moved “{s['title'][:40] or 'untitled'}” to position {to + 1}", s["id"])
                return {"slide_id": s["id"], "position": to}, True

            if kind == "set_deck":
                changed = []
                if isinstance(op.get("title"), str):
                    self.deck["title"] = op["title"]
                    changed.append("title")
                if op.get("theme") is not None:
                    if op["theme"] not in THEMES:
                        raise ValueError(f"theme must be one of {THEMES}")
                    self.deck["theme"] = op["theme"]
                    changed.append("theme")
                    self.log(by, f"switched theme to {op['theme']}")
                return {"changed": changed}, True

            if kind in ("next", "prev", "goto", "blackout"):
                n = len(slides)
                i = self.present["index"]
                step = self.present.get("step", 0)
                here = {"index": i, "step": step, "blackout": self.present["blackout"]}
                if kind in ("next", "prev") and "from_index" in op:
                    # the tap says where it started; if that's no longer true (a lost or doubled tap, or another
                    # device moved meanwhile) ignore it and answer with the real position instead
                    same_deck = op.get("from_deck") in (None, self.deck["id"])
                    if not same_deck or op.get("from_index") != i or (op.get("from_step") or 0) != step:
                        return {**here, "stale": True}, False
                if kind == "next":
                    if n and step < build_steps(slides[min(i, n - 1)]):
                        self.present["step"] = step + 1      # reveal the next build on this slide
                    elif i < n - 1:
                        self.present["index"], self.present["step"] = i + 1, 0
                elif kind == "prev":
                    if step > 0:
                        self.present["step"] = step - 1
                    elif i > 0:                               # back to the previous slide, fully built
                        self.present["index"] = i - 1
                        self.present["step"] = build_steps(slides[i - 1])
                elif kind == "goto":
                    self.present["index"] = int(op.get("index", 0))
                    self.present["step"] = 0
                if kind == "blackout":
                    on = op.get("on")
                    self.present["blackout"] = (not self.present["blackout"]) if on is None else bool(on)
                elif n:
                    self.present["blackout"] = False
                self._clamp_present()
                self.present["changed_at"] = time.time()
                return {"index": self.present["index"], "step": self.present.get("step", 0),
                        "blackout": self.present["blackout"]}, False

            # ---- live polls / several presenters / audience link (beta) ----
            if kind == "poll_start":
                question = str(op.get("question") or "").strip()[:200]
                options = [str(o).strip()[:80] for o in (op.get("options") or []) if str(o).strip()][:4]
                if not question or len(options) < 2:
                    raise ValueError("A poll needs a question and 2 to 4 answers")
                self.poll = {"id": new_id(), "question": question, "options": options, "votes": {},
                             "open": True, "show": False}
                self.log(by, f"started a poll: \u201c{question[:60]}\u201d")
                return {"poll": self.poll["id"]}, False
            if kind in ("poll_show", "poll_end", "poll_clear"):
                if kind == "poll_clear":
                    self.poll = None
                elif self.poll:
                    if kind == "poll_show":
                        self.poll["show"] = bool(op.get("on", True))
                    else:
                        self.poll["open"] = False
                return {}, False
            if kind == "speaker_set":
                name = str(op.get("name") or "").strip()[:40]
                self.speaker = {"name": name, "at": time.time()} if name else None
                if name:
                    self.log(by, f"{name} is presenting now")
                return {}, False
            if kind == "audience_new":
                library.meta["audience_key"] = secrets.token_urlsafe(9)
                library.set_active(library.meta.get("active"))
                for c in self.clients.values():       # old links stop working at once
                    if c["role"] == "audience":
                        c["q"].put(KICK)
                return {}, False
            if kind == "join_qr":
                self.join_qr = bool(op.get("on", True))
                return {}, False
            if kind == "group_start":
                if not self.group_active():
                    self.group = {"key": secrets.token_urlsafe(9), "expires": time.time() + GROUP_HOURS * 3600, "members": {}}
                    self.log(by, "started a group session")
                return {"key": self.group["key"]}, False
            if kind == "group_end":
                names = {m["name"] for m in self.group["members"].values()} if self.group else set()
                if self.group:
                    self.log(by, "ended the group session")
                self.group, self.group_qr = None, False
                if self.speaker and self.speaker.get("name") in names:   # a group member was presenting
                    self.speaker = None
                for c in self.clients.values():       # everyone in the group is out at once
                    if str(c["device"]).startswith("g:"):
                        c["q"].put(KICK)
                return {}, False
            if kind == "group_kick":
                g = self.group_active()
                mid = str(op.get("member") or "")
                if g and mid in g["members"]:
                    name = g["members"].pop(mid)["name"]
                    if self.speaker and self.speaker.get("name") == name:   # a removed person no longer presents
                        self.speaker = None
                    for c in self.clients.values():
                        if c["device"] == "g:" + mid:
                            c["q"].put(KICK)
                    self.log(by, f"removed {name} from the group")
                return {}, False
            if kind == "set_lang":
                lang = str(op.get("lang") or "")
                if lang not in LANGS:
                    raise ValueError("Unknown language")
                library.meta["lang"] = lang
                library.set_active(library.meta.get("active"))     # saves library.json
                return {"lang": lang}, False
            if kind == "group_qr":
                self.group_qr = bool(op.get("on", True)) and bool(self.group_active())
                return {}, False

            if kind == "resync":   # every screen rebuilds its slide views from the current position
                self.present["resync"] = time.time()
                self.log(by, "resynced all screens")
                return {"index": self.present["index"], "step": self.present.get("step", 0),
                        "blackout": self.present["blackout"]}, False

            if kind == "open_deck":
                self.open_deck(op.get("deck_id"))
                self.log(by, f"opened \u201c{self.deck['title'][:60]}\u201d")
                return {"deck_id": self.deck["id"]}, False

            if kind == "create_deck":
                d = library.create(title=op.get("title") or "Untitled presentation", theme=op.get("theme") or "midnight")
                self.open_deck(d["id"])
                self.log(by, f"created presentation \u201c{d['title'][:60]}\u201d")
                return {"deck_id": d["id"]}, False

            if kind == "import_deck":
                slides_in = op.get("slides")
                if not isinstance(slides_in, list) or not slides_in:
                    raise ValueError("import needs a non-empty slides list")
                faces = op.get("faces") if isinstance(op.get("faces"), list) else []
                faces = [{"family": str(f.get("family", ""))[:80], "href": f["href"]} for f in faces
                         if isinstance(f, dict) and str(f.get("href", "")).startswith("https://fonts.googleapis.com/")][:8]
                src = op.get("source") if isinstance(op.get("source"), dict) else None
                if src:
                    src = {"kind": str(src.get("kind", ""))[:40], "url": str(src.get("url") or "")[:300],
                           "slug": str(src.get("slug") or "")[:80], "imported_at": time.time()}
                css = str(op.get("css") or "")[:400_000]
                digest = hashlib.sha256(json.dumps([op.get("title"), css, faces, slides_in], sort_keys=True,
                                                   ensure_ascii=False).encode("utf-8")).hexdigest()
                replace = None
                if src and (src["slug"] or src["url"]):  # the same claude.ai deck again: update it, don't duplicate
                    for summ in library.listing():
                        old = summ.get("source") or {}
                        if (src["slug"] and old.get("slug") == src["slug"]) or (src["url"] and old.get("url") == src["url"]):
                            replace = summ["id"]
                            break
                if replace:
                    old = library.load(replace)
                    osrc = old.get("source") or {}
                    if osrc.get("hash") == digest:
                        if any(op.get(k) and old.get(k) != op[k] for k in ("kind", "date")):
                            for k in ("kind", "date"):
                                if op.get(k):
                                    old[k] = op[k]
                            library.save(old)
                        return {"deck_id": replace, "slides": len(old["slides"]), "unchanged": True}, False
                    fresh = library.create(title=op.get("title"), slides=slides_in, css=css, faces=faces)
                    library.delete(fresh["id"])
                    old.update(title=fresh["title"], slides=fresh["slides"], css=css, faces=faces,
                               source={**osrc, **{k: v for k, v in src.items() if v}, "hash": digest})
                    for k in ("kind", "date"):
                        if op.get(k):
                            old[k] = op[k]
                    library.save(old)
                    if self.deck["id"] == replace:
                        self.deck = old
                        self._clamp_present()
                        self.present["step"] = 0
                    self.log(by, f"updated \u201c{old['title'][:60]}\u201d from claude.ai ({len(old['slides'])} slides)")
                    return {"deck_id": replace, "slides": len(old["slides"]), "updated": True}, False
                if src:
                    src["hash"] = digest
                d = library.create(title=op.get("title"), theme=op.get("theme") or "midnight", slides=slides_in,
                                   css=css, faces=faces, source=src,
                                   kind="doc" if op.get("kind") == "doc" else None,
                                   date=float(op["date"]) if isinstance(op.get("date"), (int, float)) else None)
                self.log(by, f"imported \u201c{d['title'][:60]}\u201d ({len(d['slides'])} slides)")
                return {"deck_id": d["id"], "slides": len(d["slides"]), "created": True}, False

            if kind == "duplicate_deck":
                src = library.load(op.get("deck_id"))
                d = library.create(title=src["title"] + " (copy)", theme=src["theme"], slides=src["slides"],
                                   css=src.get("css"), faces=src.get("faces"), kind=src.get("kind"))
                self.log(by, f"duplicated \u201c{src['title'][:60]}\u201d")
                return {"deck_id": d["id"]}, False

            if kind == "edit_deck":
                # title and speaker notes of any deck, without opening it on all screens (library "Edit")
                did = op.get("deck_id")
                d = self.deck if self.deck["id"] == did else library.load(did)
                title = op.get("title")
                if isinstance(title, str) and title.strip():
                    d["title"] = title.strip()[:200]
                notes = op.get("notes") if isinstance(op.get("notes"), dict) else {}
                for s in d["slides"]:
                    if s["id"] in notes and isinstance(notes[s["id"]], str):
                        s["notes"] = notes[s["id"]][:20000]
                        s["updated_at"] = time.time()
                library.save(d)
                self.log(by, f"edited \u201c{d['title'][:60]}\u201d (title and notes)")
                return {"deck_id": did}, d is self.deck

            if kind == "delete_deck":
                did = op.get("deck_id")
                title = library.load(did)["title"]
                library.delete(did)
                if self.deck["id"] == did:
                    rest = library.listing()
                    self.open_deck(rest[0]["id"] if rest else library.create()["id"])
                self.log(by, f"deleted presentation \u201c{title[:60]}\u201d")
                return {"deleted": did}, False

            if kind == "claude_status":
                text = str(op.get("text") or "")[:200]
                sid = op.get("slide_id")
                if sid:
                    self._index(sid)
                self.claude = {"text": text, "slide_id": sid, "at": time.time()}
                if text:
                    self.log("claude", text, sid)
                return {"ok": True}, False

            raise ValueError(f"Unknown op '{kind}'")


store = Store()

# End-to-end pairing (phone app <-> PC through the relay). If it can't start, everything else keeps working.
try:
    import e2e_server
    pairing = e2e_server.Pairing(os.path.join(DATA_DIR, "e2e-key.bin"), registry, on_change=lambda: store.publish())
    import e2e_session
    sessions = e2e_session.Sessions(pairing.key, registry, PORT)
except Exception as e:          # e.g. not Windows
    print(f"  end-to-end pairing unavailable: {e}")
    pairing = sessions = None


# SlideLink Relay client: the PC asks the relay for requests from phones (always outgoing, no open ports),
#     answers them through this server and streams the answer back. Ends when SlideLink ends.
