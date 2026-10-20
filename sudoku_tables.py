"""
Public Sudoku tables for the open landing page (/sudoku).

Separate from the party room: no host password or room code. Anyone picks a
name, then plays solo or opens a table others can join or spectate. Each
table runs its own sudoku.py engine; whoever opened it (or the next person
left, if they go) controls mode, difficulty and rounds.

Thread-safe: every public method takes the module lock.
"""

import secrets
import threading
import time

import sudoku

TABLE_ONLY = {"sdSet", "sdStart", "sdEnd", "sdLobby", "sdPause"}   # table owner only
MAX_TABLES = 40
MAX_MEMBERS = 30
MAX_VISITORS = 500
MEMBER_GRACE = 60          # seconds without a poll before leaving a table
VISITOR_GRACE = 6 * 3600   # seconds before a forgotten name/token expires
HELLO_WINDOW, HELLO_MAX = 60, 10
CREATE_WINDOW, CREATE_MAX = 60, 8


class Tables:
    def __init__(self, colors):
        self.colors = colors
        self.lock = threading.Lock()
        self.visitors = {}       # mid -> {name, token, color, last}
        self.tables = {}         # tid -> {id, name, owner, players, seen, g, listed, created}
        self.where = {}          # mid -> tid
        self._next = 1
        self._hits = {}          # (kind, key) -> [times]

    # ---------------------------------------------------------- helpers ---
    def _rate(self, kind, key, window, limit):
        now = time.time()
        hits = [t for t in self._hits.get((kind, key), []) if now - t < window]
        ok = len(hits) < limit
        if ok:
            hits.append(now)
        self._hits[(kind, key)] = hits
        return ok

    def _auth(self, mid, token):
        try:
            v = self.visitors.get(int(mid))
        except (TypeError, ValueError):
            return None
        if v and token and secrets.compare_digest(str(token), v["token"]):
            v["last"] = time.time()
            return v
        return None

    def _reap(self):
        now = time.time()
        for t in list(self.tables.values()):
            for mid, seen in list(t["seen"].items()):
                if now - seen > MEMBER_GRACE:
                    self._leave(mid)
        for mid, v in list(self.visitors.items()):
            if now - v["last"] > VISITOR_GRACE and mid not in self.where:
                del self.visitors[mid]

    def _leave(self, mid):
        tid = self.where.pop(mid, None)
        t = self.tables.get(tid)
        if not t:
            return
        t["players"].pop(mid, None)
        t["seen"].pop(mid, None)
        t["g"]["watchers"].pop(mid, None)
        if not t["players"]:
            sudoku.act(t["g"], t["players"], None, "sdLobby", {})    # logs open boards
            del self.tables[tid]
            return
        if t["owner"] == mid:
            t["owner"] = next(iter(t["players"]))
        sudoku.tick(t["g"], t["players"])

    def _join(self, mid, t):
        if self.where.get(mid) == t["id"]:
            return
        self._leave(mid)
        v = self.visitors[mid]
        t["players"][mid] = {"name": v["name"], "color": v["color"], "connected": True, "score": 0}
        t["seen"][mid] = time.time()
        self.where[mid] = t["id"]

    def _summary(self, t):
        g = t["g"]
        return {"id": t["id"], "name": t["name"], "owner": t["players"][t["owner"]]["name"],
                "mode": g["mode"], "difficulty": g["difficulty"], "phase": g["phase"],
                "people": len(t["players"]),
                "playing": sum(1 for p in g["players"] if p in t["players"])}

    # ------------------------------------------------------------- api ---
    def hello(self, name, ip, mid=None, token=None):
        name = " ".join(str(name or "").split())[:20]
        if not name:
            return {"ok": False, "error": "Pick a name first."}
        with self.lock:
            v = self._auth(mid, token)
            if v:
                v["name"] = name
                tid = self.where.get(int(mid))
                if tid in self.tables:
                    self.tables[tid]["players"][int(mid)]["name"] = name
                return {"ok": True, "mid": int(mid), "token": v["token"], "name": name}
            if not self._rate("hello", ip, HELLO_WINDOW, HELLO_MAX):
                return {"ok": False, "error": "Too many tries — wait a minute."}
            self._reap()
            if len(self.visitors) >= MAX_VISITORS:
                return {"ok": False, "error": "The server is full right now."}
            new = self._next
            self._next += 1
            self.visitors[new] = {"name": name, "token": secrets.token_urlsafe(16),
                                  "color": self.colors[(new - 1) % len(self.colors)],
                                  "last": time.time()}
            return {"ok": True, "mid": new, "token": self.visitors[new]["token"], "name": name}

    def listing(self, mid, token):
        with self.lock:
            if not self._auth(mid, token):
                return {"ok": False, "error": "who"}
            self._reap()
            rows = [self._summary(t) for t in self.tables.values() if t["listed"]]
            rows.sort(key=lambda r: (r["phase"] != "lobby", -r["people"]))
            return {"ok": True, "tables": rows, "current": self.where.get(int(mid))}

    def create(self, mid, token, mode, difficulty, name="", listed=True, start=False,
               boards="same", lives=0):
        with self.lock:
            v = self._auth(mid, token)
            if not v:
                return {"ok": False, "error": "who"}
            mid = int(mid)
            if mode not in sudoku.MODES or difficulty not in sudoku.sp.LEVELS:
                return {"ok": False, "error": "Pick a mode and difficulty."}
            boards = boards if boards in sudoku.BOARDS else "same"
            lives = int(lives) if str(lives) in {str(n) for n in sudoku.LIVES} else 0
            if not self._rate("create", mid, CREATE_WINDOW, CREATE_MAX):
                return {"ok": False, "error": "Too many new tables — wait a minute."}
            self._reap()
            if len(self.tables) >= MAX_TABLES:
                return {"ok": False, "error": "All tables are busy right now. Join one instead."}
            tid = secrets.token_hex(3)
            while tid in self.tables:
                tid = secrets.token_hex(3)
            name = " ".join(str(name or "").split())[:32] or f"{v['name']}'s table"
            t = {"id": tid, "name": name, "owner": mid, "players": {}, "seen": {},
                 "g": sudoku.fresh(mode, difficulty, boards, lives), "listed": bool(listed), "created": time.time()}
            self.tables[tid] = t
            self._join(mid, t)
            if start:
                err, _ = sudoku.act(t["g"], t["players"], mid, "sdStart", {})
                if err:
                    return {"ok": False, "error": err}
            return {"ok": True, "tid": tid}

    def enter(self, mid, token, tid):
        with self.lock:
            if not self._auth(mid, token):
                return {"ok": False, "error": "who"}
            t = self.tables.get(tid)
            if not t:
                return {"ok": False, "error": "That table has closed."}
            if int(mid) not in t["players"] and len(t["players"]) >= MAX_MEMBERS:
                return {"ok": False, "error": "That table is full."}
            self._join(int(mid), t)
            return {"ok": True, "tid": tid}

    def leave(self, mid, token):
        with self.lock:
            if self._auth(mid, token):
                self._leave(int(mid))
            return {"ok": True}

    def act(self, mid, token, tid, data):
        with self.lock:
            if not self._auth(mid, token):
                return {"ok": False, "error": "who"}
            mid = int(mid)
            t = self.tables.get(tid)
            if not t or mid not in t["players"]:
                return {"ok": False, "error": "You're not at that table."}
            t["seen"][mid] = time.time()
            action = data.get("action")
            if not isinstance(action, str) or not action.startswith("sd"):
                return {"ok": False, "error": "Unknown action."}
            if action in TABLE_ONLY and t["owner"] != mid:
                return {"ok": False, "error": "Only the table owner can do that."}
            if action == "sdListed" and t["owner"] == mid:
                t["listed"] = bool(data.get("value"))
                return {"ok": True}
            err, extra = sudoku.act(t["g"], t["players"], mid, action, data)
            return {"ok": not err, **extra, **({"error": err} if err else {})}

    def state(self, mid, token, tid):
        with self.lock:
            if not self._auth(mid, token):
                return {"ok": False, "error": "who"}
            mid = int(mid)
            t = self.tables.get(tid)
            if not t or mid not in t["players"]:
                return {"ok": False, "error": "gone"}
            t["seen"][mid] = time.time()
            self._reap()
            if tid not in self.tables:
                return {"ok": False, "error": "gone"}
            sudoku.tick(t["g"], t["players"])
            return {"ok": True,
                    "table": {"id": tid, "name": t["name"], "owner": t["owner"], "listed": t["listed"],
                              "you": mid, "isOwner": t["owner"] == mid,
                              "people": [{"mid": m, "name": p["name"], "color": p["color"],
                                          "owner": m == t["owner"],
                                          "seated": m in t["g"]["players"]}
                                         for m, p in t["players"].items()]},
                    "sudoku": sudoku.public(t["g"], t["players"], mid)}
