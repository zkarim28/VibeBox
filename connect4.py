"""Connect 4 for VibeBox.

Formats:
  duels  everyone is paired up and plays at the same time (odd one out plays the computer)
  teams  Red vs Yellow; teammates take turns dropping for their team
  bot    every player gets their own game against the computer

Pure engine like bs.py / sudoku.py / minigolf.py: the caller (server.py)
already holds the lock. Boards are lists of columns, bottom-up:
board[c][r] is 0 (empty), 1 (red, moves first) or 2 (yellow).
"""

import random
import threading
import time

FORMATS = ["duels", "teams", "bot"]
SIZES = {"6x5": (6, 5), "7x6": (7, 6), "8x7": (8, 7), "9x7": (9, 7)}
CONNECTS = [4, 5]
BOTS = ["easy", "medium", "hard"]
TIMERS = [0, 15, 30, 60]          # seconds per move; 0 = no clock
BOT_DELAY = 0.5                   # the computer "thinks" at least this long so the drop is visible
SYNC_BOT = False                  # tests: compute bot moves inline instead of on a thread
BOT_BUDGET = 0.3                  # CPU seconds a bot move may take (we're under the lock)
BOT_DEPTH = {"easy": 2, "medium": 3, "hard": 12}
BOT_SLOPPY = {"easy": 0.3, "medium": 0.15, "hard": 0.0}   # chance of a random move (after win/block checks)
WIN_PTS, DRAW_PTS = 100, 40
BOT_WIN_PTS = {"easy": 50, "medium": 100, "hard": 200}
COLORS = ["red", "yellow"]
TEAM_NAMES = {1: "Team Red", 2: "Team Yellow"}

DEFAULTS = {"format": "duels", "size": "7x6", "connect": 4, "bot": "medium", "timer": 0}


def fresh(cfg=None, team_of=None):
    return {
        "phase": "lobby",                 # lobby -> playing -> done
        "cfg": dict(cfg or DEFAULTS),
        "teamOf": dict(team_of or {}),    # pid -> 1 (red) / 2 (yellow), teams format
        "matches": [],
        "round": 0,
        "record": {},                     # pid -> {"w", "d", "l"} across rounds
        "met": {},                        # "a-b" -> times paired, so rematches mix it up
        "revision": 0,
    }


# ---------------------------------------------------------------- the board ---
def _cols_rows(g):
    return SIZES.get(g["cfg"]["size"], (7, 6))


def legal(m):
    return [c for c in range(m["cols"]) if m["board"][c][-1] == 0]


def _line_through(board, c, r, need):
    """Cells of a winning line through (c, r), or [] if none."""
    me = board[c][r]
    cols, rows = len(board), len(board[0])
    for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
        cells = [(c, r)]
        for sgn in (1, -1):
            x, y = c + dc * sgn, r + dr * sgn
            while 0 <= x < cols and 0 <= y < rows and board[x][y] == me:
                cells.append((x, y))
                x, y = x + dc * sgn, y + dr * sgn
        if len(cells) >= need:
            return sorted(cells)
    return []


# ---------------------------------------------------------------- the bot ---
# Bitboards (one bit per cell, columns of rows+1 bits with an empty sentinel
# on top) so a few thousand positions a move stay cheap in pure Python.
class _Timeout(Exception):
    pass


class _Solver:
    def __init__(self, cols, rows, need):
        self.cols, self.rows, self.need, self.h = cols, rows, need, rows + 1
        self.bottom = [1 << (c * self.h) for c in range(cols)]
        self.top = [1 << (c * self.h + rows - 1) for c in range(cols)]
        self.colmask = [((1 << rows) - 1) << (c * self.h) for c in range(cols)]
        mid = (cols - 1) / 2
        self.order = sorted(range(cols), key=lambda c: abs(c - mid))
        self.windows = []
        for c in range(cols):
            for r in range(rows):
                for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
                    ec, er = c + dc * (need - 1), r + dr * (need - 1)
                    if 0 <= ec < cols and 0 <= er < rows:
                        w = 0
                        for i in range(need):
                            w |= 1 << ((c + dc * i) * self.h + r + dr * i)
                        self.windows.append(w)
        self.weight = {1: 1, 2: 5, 3: 25, 4: 125}
        self.center = sum(self.colmask[c] for c in range(cols) if abs(c - mid) < 1)

    def won(self, b):
        for s in (1, self.h, self.h - 1, self.h + 1):
            m = b
            for i in range(1, self.need):
                m &= b >> (i * s)
                if not m:
                    break
            if m:
                return True
        return False

    def evaluate(self, me, opp):
        score = 0
        wt = self.weight
        for w in self.windows:
            a, b = w & me, w & opp
            if a and not b:
                score += wt.get(a.bit_count(), 0)
            elif b and not a:
                score -= wt.get(b.bit_count(), 0)
        return score + 3 * ((me & self.center).bit_count() - (opp & self.center).bit_count())

    def negamax(self, pos, mask, depth, alpha, beta, ply):
        self.nodes += 1
        if self.nodes & 255 == 0 and time.perf_counter() > self.deadline:
            raise _Timeout
        moves = [c for c in self.order if not mask & self.top[c]]
        if not moves:
            return 0
        for c in moves:      # win right now?
            if self.won(pos | ((mask + self.bottom[c]) & self.colmask[c])):
                return 100000 - ply
        if depth == 0:
            return self.evaluate(pos, pos ^ mask)
        key = (pos, mask)
        hit = self.tt.get(key)
        if hit and hit[0] >= depth:
            d, flag, val, bm = hit
            if flag == 0 or (flag > 0 and val >= beta) or (flag < 0 and val <= alpha):
                return val
        if hit:      # try the previously best move first
            moves.remove(hit[3])
            moves.insert(0, hit[3])
        a0 = alpha
        best, best_c = -10 ** 9, moves[0]
        for c in moves:
            nmask = mask | (mask + self.bottom[c])
            v = -self.negamax(pos ^ mask, nmask, depth - 1, -beta, -alpha, ply + 1)
            if v > best:
                best, best_c = v, c
            if v > alpha:
                alpha = v
            if alpha >= beta:
                break
        self.tt[key] = (depth, 1 if best >= beta else (-1 if best <= a0 else 0), best, best_c)
        return best

    def best_move(self, pos, mask, depth, budget, rng, sloppy=0.0):
        self.deadline = time.perf_counter() + budget
        self.nodes = 0
        self.tt = {}
        moves = [c for c in self.order if not mask & self.top[c]]
        for c in moves:      # take a win, then block one
            if self.won(pos | ((mask + self.bottom[c]) & self.colmask[c])):
                return c
        opp = pos ^ mask
        for c in moves:
            if self.won(opp | ((mask + self.bottom[c]) & self.colmask[c])):
                return c
        if rng.random() < sloppy:    # easy/medium: sometimes just play anywhere
            return rng.choice(moves)
        choice = moves[0]
        for d in range(1, depth + 1):          # iterative deepening until the budget runs out
            try:
                scores = {}
                for c in moves:
                    nmask = mask | (mask + self.bottom[c])
                    scores[c] = -self.negamax(pos ^ mask, nmask, d - 1, -10 ** 9, 10 ** 9, 1)
            except _Timeout:
                break
            top = max(scores.values())
            choice = rng.choice([c for c in moves if scores[c] == top])
            if top >= 90000:       # forced win found
                break
        return choice


_solvers = {}


def bot_move(m, level, rng=random):
    moves = legal(m)
    if not moves:
        return None
    if level == "easy" and rng.random() < 0.2:     # easy even misses obvious ones
        return rng.choice(moves)
    key = (m["cols"], m["rows"], m["need"])
    s = _solvers.get(key) or _solvers.setdefault(key, _Solver(*key))
    me_id = m["turn"] + 1
    pos = mask = 0
    for c, col in enumerate(m["board"]):
        for r, v in enumerate(col):
            if v:
                bit = 1 << (c * s.h + r)
                mask |= bit
                if v == me_id:
                    pos |= bit
    return s.best_move(pos, mask, BOT_DEPTH.get(level, 4), BOT_BUDGET, rng, BOT_SLOPPY.get(level, 0))


# ---------------------------------------------------------------- matches ---
def _side(kind, pids, name):
    return {"kind": kind, "pids": list(pids), "name": name, "next": 0}


def _bot_side(level):
    return _side("bot", [], f"🤖 Computer ({level})")


def _new_match(g, mid, sides, now, shuffle=True):
    cols, rows = _cols_rows(g)
    sides = list(sides)
    if shuffle:
        random.shuffle(sides)              # who's red (and goes first) is random
    m = {"id": mid, "cols": cols, "rows": rows, "need": int(g["cfg"]["connect"]),
         "board": [[0] * rows for _ in range(cols)], "sides": sides,
         "turn": 0, "moves": 0, "last": None, "line": [], "winner": None,
         "result": None, "turnAt": now, "endsAt": 0, "botAt": 0, "auto": None}
    _arm(g, m, now)
    return m


def _arm(g, m, now):
    """Set up the clock / bot timer for whoever is to move."""
    side = m["sides"][m["turn"]]
    m["turnAt"] = now
    m["botAt"] = now + BOT_DELAY if side["kind"] == "bot" else 0
    t = int(g["cfg"]["timer"])
    m["endsAt"] = now + t if t and side["kind"] != "bot" else 0


def dropper(side):
    """The pid who drops next for a side (None for the computer)."""
    if side["kind"] == "bot" or not side["pids"]:
        return None
    return side["pids"][side["next"] % len(side["pids"])]


def _pair(g, pids):
    """Pair players, preferring people who've met least. Returns (pairs, leftover)."""
    pool = list(pids)
    random.shuffle(pool)
    met = g["met"]
    pairs = []
    while len(pool) >= 2:
        a = pool.pop(0)
        b = min(pool, key=lambda p: (met.get(_mkey(a, p), 0), random.random()))
        pool.remove(b)
        pairs.append((a, b))
        met[_mkey(a, b)] = met.get(_mkey(a, b), 0) + 1
    return pairs, (pool[0] if pool else None)


def _mkey(a, b):
    return f"{min(a, b)}-{max(a, b)}"


def _balance(g, pids):
    """Make sure everyone has a team, keeping teams even."""
    for p in pids:
        if g["teamOf"].get(p) not in (1, 2):
            n1 = sum(1 for q in pids if g["teamOf"].get(q) == 1)
            n2 = sum(1 for q in pids if g["teamOf"].get(q) == 2)
            g["teamOf"][p] = 1 if n1 <= n2 else 2


def _start(g, players, now):
    pids = sorted(players)
    cfg = g["cfg"]
    names = {p: players[p]["name"] for p in pids}
    matches = []
    if cfg["format"] == "teams":
        _balance(g, pids)
        sides = []
        for t in (1, 2):
            members = [p for p in pids if g["teamOf"].get(p) == t]
            random.shuffle(members)
            sides.append(_side("team", members, TEAM_NAMES[t]) if members else _bot_side(cfg["bot"]))
        # red team is always red here: keep the side order, randomise who starts by colour swap
        matches.append(_new_match(g, 1, sides, now, shuffle=False))
        if random.random() < 0.5:
            m = matches[0]
            m["sides"].reverse()
            _arm(g, m, now)
    elif cfg["format"] == "bot":
        for i, p in enumerate(pids, 1):
            matches.append(_new_match(g, i, [_side("player", [p], names[p]), _bot_side(cfg["bot"])], now))
    else:
        pairs, odd = _pair(g, pids)
        for i, (a, b) in enumerate(pairs, 1):
            matches.append(_new_match(g, i, [_side("player", [a], names[a]),
                                             _side("player", [b], names[b])], now))
        if odd is not None:
            matches.append(_new_match(g, len(matches) + 1,
                                      [_side("player", [odd], names[odd]), _bot_side(cfg["bot"])], now))
    g["matches"] = matches
    g["phase"] = "playing"
    g["round"] += 1
    g["revision"] += 1


def _record(g, pid, k):
    r = g["record"].setdefault(pid, {"w": 0, "d": 0, "l": 0})
    r[k] += 1


def _finish_match(g, players, m, winner):
    """winner: side index, or None for a draw."""
    m["winner"] = winner
    m["result"] = "draw" if winner is None else "win"
    m["endsAt"] = m["botAt"] = 0
    level = g["cfg"]["bot"]
    for i, side in enumerate(m["sides"]):
        if side["kind"] == "bot":
            continue
        other = m["sides"][1 - i]
        if winner is None:
            pts, k = DRAW_PTS, "d"
        elif winner == i:
            pts, k = (BOT_WIN_PTS.get(level, WIN_PTS) if other["kind"] == "bot" else WIN_PTS), "w"
        else:
            pts, k = 0, "l"
        for p in side["pids"]:
            _record(g, p, k)
            if pts and p in players:
                players[p]["score"] += pts
    if all(x["result"] for x in g["matches"]):
        g["phase"] = "done"


def _drop(g, players, m, col, now):
    board = m["board"][col]
    r = board.index(0)
    board[r] = m["turn"] + 1
    m["moves"] += 1
    m["last"] = [col, r]
    side = m["sides"][m["turn"]]
    side["next"] += 1
    line = _line_through(m["board"], col, r, m["need"])
    g["revision"] += 1
    if line:
        m["line"] = [list(x) for x in line]
        _finish_match(g, players, m, m["turn"])
    elif m["moves"] == m["cols"] * m["rows"]:
        _finish_match(g, players, m, None)
    else:
        m["turn"] ^= 1
        _arm(g, m, now)


def _hand_to_bot(g, players):
    """Players who left: the computer takes over their seat. Late joiners join a team."""
    changed = False
    for m in g["matches"]:
        if m["result"]:
            continue
        for side in m["sides"]:
            if side["kind"] == "bot":
                continue
            gone = [p for p in side["pids"] if p not in players]
            if not gone:
                continue
            side["pids"] = [p for p in side["pids"] if p in players]
            if not side["pids"]:
                side["kind"] = "bot"
                side["name"] = f"🤖 Computer (for {side['name']})"
            changed = True
            if m["sides"][m["turn"]] is side:
                _arm(g, m, time.time())
    if g["cfg"]["format"] == "teams" and g["phase"] == "playing" and g["matches"]:
        m = g["matches"][0]
        seated = {p for s in m["sides"] for p in s["pids"]}
        for p in sorted(players):
            if p in seated or m["result"]:
                continue
            humans = [i for i, s in enumerate(m["sides"]) if s["kind"] == "team"]
            if not humans:
                break
            i = min(humans, key=lambda i: len(m["sides"][i]["pids"]))
            m["sides"][i]["pids"].append(p)
            g["teamOf"][p] = 1 if m["sides"][i]["name"] == TEAM_NAMES[1] else 2
            changed = True
    if changed:
        g["revision"] += 1
    return changed


def tick(g, players, now=None):
    """Bots move, clocks run out, departed players get replaced. True if anything changed."""
    if g["phase"] != "playing":
        return False
    now = now or time.time()
    changed = _hand_to_bot(g, players)
    for m in g["matches"]:
        if m["result"]:
            continue
        side = m["sides"][m["turn"]]
        if side["kind"] == "bot" and m["botAt"] and now >= m["botAt"]:
            col = _bot_pick(m, g["cfg"]["bot"])
            if col is not None:
                _drop(g, players, m, col, now)
                m["auto"] = None
                changed = True
        elif m["endsAt"] and now >= m["endsAt"]:
            who = players.get(dropper(side), {}).get("name", side["name"])
            m["auto"] = f"⏰ Time! A disc was dropped for {who}."
            _drop(g, players, m, bot_move(m, "easy"), now)
            changed = True
    return changed


def _bot_pick(m, level):
    """The bot's column once it's ready, else None. The search runs on a
    thread with a snapshot of the board, so the server lock isn't held while
    it thinks; tick() picks the answer up on a later pass."""
    if SYNC_BOT:
        return bot_move(m, level)
    pick = m.get("botPick")
    if pick and pick[0] == m["moves"]:
        m["botPick"] = None
        return pick[1]
    if m.get("botJob") != m["moves"]:
        m["botJob"] = m["moves"]
        snap = {k: m[k] for k in ("cols", "rows", "need", "turn")}
        snap["board"] = [col[:] for col in m["board"]]
        moves = m["moves"]

        def work():
            m["botPick"] = (moves, bot_move(snap, level))
        threading.Thread(target=work, daemon=True).start()
    return None


# ---------------------------------------------------------------- actions ---
def _find_turn(g, pid):
    for m in g["matches"]:
        if not m["result"] and dropper(m["sides"][m["turn"]]) == pid:
            return m
    return None


def act(g, players, pid, action, data):
    """Returns (error or None, extra dict)."""
    now = time.time()
    cfg = g["cfg"]
    if action == "c4Set":
        if g["phase"] == "playing":
            return "Finish the game before changing settings.", {}
        key, value = data.get("key"), data.get("value")
        if key == "format" and value in FORMATS:
            cfg["format"] = value
        elif key == "size" and value in SIZES:
            cfg["size"] = value
        elif key == "connect" and str(value) in map(str, CONNECTS):
            cfg["connect"] = int(value)
        elif key == "bot" and value in BOTS:
            cfg["bot"] = value
        elif key == "timer" and str(value) in map(str, TIMERS):
            cfg["timer"] = int(value)
        else:
            return "Unknown setting.", {}
        g["revision"] += 1
        return None, {}
    if action == "c4Team":
        if g["phase"] == "playing":
            return "Teams are set until this game ends.", {}
        team = data.get("team")
        if pid is None or team not in (1, 2):
            return "Pick red or yellow.", {}
        g["teamOf"][pid] = team
        g["revision"] += 1
        return None, {}
    if action == "c4Shuffle":
        if g["phase"] == "playing":
            return "Teams are set until this game ends.", {}
        pids = list(players)
        random.shuffle(pids)
        g["teamOf"] = {p: 1 + i % 2 for i, p in enumerate(pids)}
        g["revision"] += 1
        return None, {}
    if action == "c4Start":
        if not players:
            return "Wait for at least one player to join.", {}
        _start(g, players, now)
        return None, {}
    if action == "c4Drop":
        if g["phase"] != "playing":
            return "No game in progress.", {}
        m = _find_turn(g, pid)
        if not m:
            return "It's not your turn.", {}
        try:
            col = int(data.get("col"))
        except (TypeError, ValueError):
            return "Pick a column.", {}
        if col not in legal(m):
            return "That column is full.", {}
        m["auto"] = None
        _drop(g, players, m, col, now)
        return None, {}
    if action == "c4End":
        for m in g["matches"]:
            if not m["result"]:
                m["result"], m["endsAt"], m["botAt"] = "ended", 0, 0
        if g["matches"]:
            g["phase"] = "done"
        g["revision"] += 1
        return None, {}
    if action == "c4Lobby":
        act(g, players, pid, "c4End", {})
        g["phase"], g["matches"] = "lobby", []
        g["revision"] += 1
        return None, {}
    if action == "c4Reset":
        g["record"], g["met"] = {}, {}
        g["revision"] += 1
        return None, {}
    return "Unknown action.", {}


# ---------------------------------------------------------------- view ---
def _side_view(side, players, color):
    d = dropper(side)
    return {"kind": side["kind"], "name": side["name"], "color": color,
            "pids": side["pids"], "dropper": d,
            "members": [players[p]["name"] for p in side["pids"] if p in players]}


def standings(g, players):
    rows = []
    for p, r in g["record"].items():
        if p not in players:
            continue
        rows.append({"pid": p, "name": players[p]["name"], **r, "pts": 3 * r["w"] + r["d"]})
    rows.sort(key=lambda x: (-x["pts"], -x["w"], x["name"]))
    place = 0
    for i, row in enumerate(rows):
        if not i or (row["pts"], row["w"]) != (rows[i - 1]["pts"], rows[i - 1]["w"]):
            place = i + 1
        row["place"] = place
    return rows


def public(g, players, pid=None):
    mine = None
    matches = []
    for m in g["matches"]:
        side_i = next((i for i, s in enumerate(m["sides"]) if pid is not None and pid in s["pids"]), None)
        if side_i is not None:
            mine = {"match": m["id"], "side": side_i}
        cur = m["sides"][m["turn"]]
        matches.append({
            "id": m["id"], "cols": m["cols"], "rows": m["rows"], "need": m["need"],
            "board": m["board"], "turn": m["turn"], "moves": m["moves"], "last": m["last"],
            "line": m["line"], "winner": m["winner"], "result": m["result"],
            "endsAt": m["endsAt"], "thinking": bool(m["botAt"]) and not m["result"],
            "auto": m["auto"],
            "sides": [_side_view(s, players, COLORS[i]) for i, s in enumerate(m["sides"])],
            "yourTurn": pid is not None and not m["result"] and dropper(cur) == pid,
            "yourSide": side_i,
        })
    if mine:      # a phone sees its own board first
        matches.sort(key=lambda x: x["id"] != mine["match"])
    teams = {str(t): [{"pid": p, "name": players[p]["name"]} for p in sorted(players)
                      if g["teamOf"].get(p) == t] for t in (1, 2)}
    return {
        "phase": g["phase"], "cfg": g["cfg"], "round": g["round"], "revision": g["revision"],
        "options": {"formats": FORMATS, "sizes": list(SIZES), "connects": CONNECTS,
                    "bots": BOTS, "timers": TIMERS},
        "matches": matches, "me": mine,
        "myTeam": g["teamOf"].get(pid), "teams": teams,
        "standings": standings(g, players),
        "now": time.time(),
    }
