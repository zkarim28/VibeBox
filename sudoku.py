"""
Sudoku engine for VibeBox. Call under the server state lock.

Modes
  competitive  everyone races the same board on their own phone. Wrong digits
               are rejected and cost a 30 s penalty; fastest adjusted time wins.
  coop         one shared board everyone fills together.
  zen          no clock, no ranking. Each player picks a difficulty and gets
               their own board, as many as they like.

Boards come from the graded bank (sudoku_puzzles.json). Each round picks a
board none of the current players has played before, then deals a random
equivalent of it (relabelled digits, shuffled rows/columns) so it looks new.

Every play is appended to sudoku_history.json (by player name, since there
are no accounts):  python3 sudoku.py --history  lists who played which board.
"""

import json
import os
import random
import secrets
import sys
import threading
import time
from datetime import datetime

import sudoku_puzzles as sp

MODES = ["competitive", "coop", "zen"]
MISTAKE_PENALTY = 30          # seconds added per wrong digit (competitive)
HINT_PENALTY = 45             # seconds added per hint (competitive)
CHAT_KEEP = 80                # messages kept per table
CHAT_LEN = 200                # characters per message
HISTORY_PATH = os.environ.get("SUDOKU_HISTORY") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sudoku_history.json")

BANK = sp.load_bank()


# ----------------------------------------------------------------- history ---
_cache = {"key": None, "plays": []}
_write_lock = threading.Lock()    # party room and public tables both append


def load_history(path=None):
    """All recorded plays (cached until the file changes — lobby polls read it)."""
    path = path or HISTORY_PATH
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (path, st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        try:
            with open(path, encoding="utf-8") as f:
                plays = json.load(f).get("plays", [])
        except (OSError, ValueError):
            return []
        _cache.update(key=key, plays=plays)
    return list(_cache["plays"])


def _record(plays, path=None):
    """Append finished/abandoned plays. Never let a disk error break a game."""
    if not plays:
        return
    path = path or HISTORY_PATH
    with _write_lock:
        try:
            data = load_history(path) + plays
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"plays": data}, f, indent=1)
            os.replace(tmp, path)
        except OSError as exc:
            print(f"  (sudoku history not saved: {exc})")


def _key(name):
    return " ".join(str(name).lower().split())


def played_by(names, history=None):
    """{board id: last time (epoch)} for boards any of `names` has played."""
    keys = {_key(n) for n in names}
    seen = {}
    for p in history if history is not None else load_history():
        if _key(p.get("player", "")) in keys:
            seen[p["board"]] = max(seen.get(p["board"], 0), p.get("t", 0))
    return seen


def pick(level, names, rng=random, history=None):
    """(bank entry, fresh?) — a board none of `names` has played, else the
    one they played longest ago."""
    entries = BANK.get(level) or []
    if not entries:
        return None, False
    seen = played_by(names, history)
    fresh = [e for e in entries if e["id"] not in seen]
    if fresh:
        return rng.choice(fresh), True
    return min(entries, key=lambda e: seen[e["id"]]), False


def bank_info(names, history=None):
    seen = played_by(names, history)
    return {lvl: {"total": len(BANK[lvl]),
                  "fresh": sum(e["id"] not in seen for e in BANK[lvl])}
            for lvl in sp.LEVELS}


# -------------------------------------------------------------------- game ---
def fresh(mode="competitive", difficulty="medium"):
    return dict(phase="lobby", mode=mode, difficulty=difficulty, rid="",
                startedAt=0, endedAt=0, board=None, players={}, shared=None,
                order=[], message="", revision=random.randrange(1, 2**48),
                watchers={}, peeked={}, chat=[], chatSeq=0, lastChat={})


def _reset(g, mode, level):
    """Back to a clean round, keeping the table's chat."""
    keep = {k: g.get(k) for k in ("chat", "chatSeq", "lastChat")}
    g.clear()
    g.update(fresh(mode, level))
    g.update({k: v for k, v in keep.items() if v is not None})


def _deal(level, names):
    entry, is_fresh = pick(level, names)
    if not entry:
        return None
    p, s = sp.transform(entry["p"], entry["s"])
    return {"id": entry["id"], "level": level, "puzzle": p, "solution": s,
            "fresh": is_fresh}


def _new_seat(name, board=None):
    seat = {"name": name, "cells": None, "mistakes": 0, "placed": 0,
            "finishedAt": 0, "startedAt": time.time(), "board": board,
            "rid": secrets.token_hex(4), "recorded": False, "hints": 0}
    if board:
        seat["cells"] = list(board["puzzle"])
    return seat


def _filled(cells, puzzle):
    return sum(1 for c, g in zip(cells, puzzle) if c != "0" and g == "0")


def _blanks(puzzle):
    return puzzle.count("0")


def _play_row(g, seat, board, result, place=None, seconds=None):
    row = {"board": board["id"], "level": board["level"], "mode": g["mode"],
           "player": seat["name"], "result": result, "t": int(time.time()),
           "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "mistakes": seat["mistakes"], "hints": seat.get("hints", 0)}
    if seconds is not None:
        row["seconds"] = int(seconds)
    if place is not None:
        row["place"] = place
    return row


def _finish_round(g, players):
    """End a competitive/coop round: rank, award points, record history."""
    if g["phase"] != "playing":
        return
    g["phase"], g["endedAt"] = "done", time.time()
    board, rows = g["board"], []
    if g["mode"] == "competitive":
        ranked = sorted((pid for pid in g["order"] if g["players"][pid]["finishedAt"]),
                        key=lambda pid: _adjusted(g, g["players"][pid]))
        n = len(g["order"])
        for place, pid in enumerate(ranked, 1):
            seat = g["players"][pid]
            seat["place"] = place
            if pid in players:
                players[pid]["score"] += 100 * (n - place + 1)
            rows.append(_play_row(g, seat, board, "solved", place, _adjusted(g, seat)))
        for pid in g["order"]:
            seat = g["players"][pid]
            if not seat["finishedAt"]:
                rows.append(_play_row(g, seat, board, "unfinished"))
    else:
        solved = g["shared"]["cells"] == list(board["solution"])
        for pid in g["order"]:
            seat = g["players"][pid]
            rows.append(_play_row(g, seat, board, "solved" if solved else "unfinished",
                                  seconds=g["endedAt"] - g["startedAt"] if solved else None))
    _record(rows)
    g["revision"] += 1


def _adjusted(g, seat):
    return (seat["finishedAt"] - g["startedAt"] + MISTAKE_PENALTY * seat["mistakes"]
            + HINT_PENALTY * seat.get("hints", 0))


def _close_zen(g):
    rows = []
    for seat in g["players"].values():
        if seat["board"] and not seat["recorded"]:
            seat["recorded"] = True
            rows.append(_play_row(g, seat, seat["board"], "unfinished"))
    _record(rows)


def tick(g, players):
    """Janitor hook: a competitive round ends once every player still in the
    room has finished. Returns True if anything changed."""
    if g["phase"] != "playing" or g["mode"] != "competitive":
        return False
    left = [pid for pid in g["order"] if pid in players]
    if not left or all(g["players"][pid]["finishedAt"] for pid in left):
        _finish_round(g, players)
        return True
    return False


def act(g, players, pid, action, data):
    """Returns (error or None, extra reply fields)."""
    if action == "sdSet":
        if g["phase"] != "lobby":
            return "Change settings in the lobby.", {}
        key, value = data.get("key"), data.get("value")
        if key == "mode" and value in MODES:
            g["mode"] = value
        elif key == "difficulty" and value in sp.LEVELS:
            g["difficulty"] = value
        else:
            return "Unknown setting.", {}
        g["revision"] += 1
        return None, {}

    if action == "sdStart":
        if g["phase"] == "playing":
            return "A round is already running.", {}
        here = sorted(p for p, v in players.items() if v.get("connected", True))
        if not here:
            return "Players need to join first.", {}
        names = [players[p]["name"] for p in here]
        mode, level = g["mode"], g["difficulty"]
        if mode == "zen":
            deals = {p: _deal(level, [players[p]["name"]]) for p in here}
        else:
            shared_board = _deal(level, names)
            deals = {p: shared_board for p in here}
        if not all(deals.values()):
            return "The puzzle bank is empty — run sudoku_puzzles.py.", {}
        _reset(g, mode, level)
        g.update(phase="playing", startedAt=time.time(), order=here,
                 rid=secrets.token_hex(4))
        for p in here:
            g["players"][p] = _new_seat(players[p]["name"], deals[p] if mode != "coop" else None)
        if mode != "zen":
            board = g["board"] = deals[here[0]]
            if mode == "coop":
                g["shared"] = {"cells": list(board["puzzle"]), "by": [0] * 81, "mistakes": 0, "hints": 0}
            if not board["fresh"]:
                g["message"] = "Everyone here has played every board at this level — dealing a reshuffled repeat."
        return None, {}

    if action == "sdEnd":
        if g["phase"] != "playing":
            return None, {}
        if g["mode"] == "zen":
            _close_zen(g)
            g["phase"] = "done"
            g["revision"] += 1
        else:
            _finish_round(g, players)
        return None, {}

    if action == "sdLobby":
        if g["phase"] == "playing":
            if g["mode"] == "zen":
                _close_zen(g)
            else:
                _finish_round(g, players)
        _reset(g, g["mode"], g["difficulty"])
        return None, {}

    # ---- player actions ----
    if pid not in players:
        return "Join the game first.", {}

    if action == "sdZenNew":
        if g["phase"] != "playing" or g["mode"] != "zen":
            return "Zen mode isn't running.", {}
        level = data.get("difficulty")
        if level not in sp.LEVELS:
            return "Pick a difficulty.", {}
        old = g["players"].get(pid)
        if old and old["board"] and not old["recorded"]:
            _record([_play_row(g, old, old["board"], "unfinished")])
        board = _deal(level, [players[pid]["name"]])
        if not board:
            return "The puzzle bank is empty — run sudoku_puzzles.py.", {}
        g["players"][pid] = _new_seat(players[pid]["name"], board)
        if pid not in g["order"]:
            g["order"].append(pid)
        g["revision"] += 1
        return None, {}

    if action == "sdChat":
        return _chat(g, players, pid, data.get("text"))

    if action == "sdWatch":
        target = data.get("target")
        if target in (None, "", 0):
            g["watchers"].pop(pid, None)
            return None, {}
        try:
            target = int(target)
        except (TypeError, ValueError):
            return "Pick someone to watch.", {}
        why = _cant_watch(g, pid, target)
        if why:
            return why, {}
        g["watchers"][pid] = target
        g["peeked"][pid] = True
        g["revision"] += 1
        return None, {}

    if action == "sdHint":
        seat = g["players"].get(pid)
        if g["phase"] != "playing" or not seat or seat["finishedAt"]:
            return "Hints are for players still solving.", {}
        board = seat["board"] or g["board"]
        cells = g["shared"]["cells"] if g["mode"] == "coop" else seat["cells"]
        h = sp.hint(board["puzzle"], cells, board["solution"])
        if not h:
            return None, {}
        seat["hints"] += 1
        if g["mode"] == "coop":
            g["shared"]["hints"] += 1
        g["revision"] += 1
        return None, {"hint": h}

    if action == "sdJoin":         # late arrival hops into a co-op board
        if g["peeked"].get(pid):
            return "You've seen the solution for this board, so you can only watch this round.", {}
        if g["phase"] == "playing" and g["mode"] == "coop" and pid not in g["players"]:
            g["players"][pid] = _new_seat(players[pid]["name"])
            g["order"].append(pid)
        return None, {}

    if action == "sdPlace":
        if g["phase"] != "playing":
            return "The round is over.", {}
        try:
            i, d = int(data.get("i")), int(data.get("d"))
        except (TypeError, ValueError):
            return "Bad move.", {}
        if not (0 <= i < 81 and 0 <= d <= 9):
            return "Bad move.", {}
        seat = g["players"].get(pid)
        if not seat:
            return "You're watching this round.", {}
        if g["mode"] == "coop":
            return _place_coop(g, players, pid, seat, i, d)
        return _place_own(g, players, pid, seat, i, d)

    return "Unknown Sudoku action.", {}


def _solving(g, pid):
    """Still working on a board this round (spoilers stay hidden from them)."""
    seat = g["players"].get(pid)
    return g["phase"] == "playing" and bool(seat) and not seat["finishedAt"]


def _cant_watch(g, pid, target):
    if g["phase"] != "playing":
        return "Nothing to watch right now."
    if target == pid or target not in g["players"]:
        return "Pick someone else to watch."
    mine = g["players"].get(pid)
    if g["mode"] == "competitive" and mine and not mine["finishedAt"]:
        return "Finish your own board before watching — spectators see the solution."
    if g["mode"] == "coop" and mine:
        return "You're on this board — spectators see the solution."
    return None


def _chat(g, players, pid, text):
    text = " ".join(str(text or "").split())[:CHAT_LEN]
    if not text:
        return None, {}
    now = time.time()
    if now - g["lastChat"].get(pid, 0) < 0.7:
        return "Slow down a little.", {}
    g["lastChat"][pid] = now
    seat = g["players"].get(pid)
    knows = g["peeked"].get(pid) or (g["mode"] == "competitive" and seat and seat["finishedAt"])
    g["chatSeq"] += 1
    g["chat"].append({"id": g["chatSeq"], "pid": pid, "name": players[pid]["name"],
                      "color": players[pid].get("color", "#888"), "text": text, "t": now,
                      "spoiler": bool(knows and g["phase"] == "playing"), "rid": g["rid"]})
    del g["chat"][:-CHAT_KEEP]
    g["revision"] += 1
    return None, {}


def _place_own(g, players, pid, seat, i, d):
    board = seat["board"]
    if seat["finishedAt"]:
        return None, {}
    if board["puzzle"][i] != "0":
        return "That's a given.", {}
    cells = seat["cells"]
    if g["mode"] == "competitive":
        if cells[i] != "0":
            return None, {}            # already solved cells are locked
        if d == 0:
            return None, {}
        if str(d) != board["solution"][i]:
            seat["mistakes"] += 1
            g["revision"] += 1
            return None, {"correct": False}
    cells[i] = str(d)
    seat["placed"] += 1
    if cells == list(board["solution"]):
        seat["finishedAt"] = time.time()
        if g["mode"] == "zen":
            seat["recorded"] = True
            _record([_play_row(g, seat, board, "solved",
                               seconds=seat["finishedAt"] - seat["startedAt"])])
        else:
            tick(g, players)
    g["revision"] += 1
    return None, {"correct": True}


def _place_coop(g, players, pid, seat, i, d):
    sh, board = g["shared"], g["board"]
    if board["puzzle"][i] != "0" or sh["cells"][i] != "0" or d == 0:
        return None, {}
    if str(d) != board["solution"][i]:
        seat["mistakes"] += 1
        sh["mistakes"] += 1
        g["revision"] += 1
        return None, {"correct": False}
    sh["cells"][i] = str(d)
    sh["by"][i] = pid
    seat["placed"] += 1
    if sh["cells"] == list(board["solution"]):
        _finish_round(g, players)
    g["revision"] += 1
    return None, {"correct": True}


# ------------------------------------------------------------------ public ---
def public(g, players, pid=None):
    """Never includes a solution."""
    now = time.time()
    out = {k: g[k] for k in ("phase", "mode", "difficulty", "rid", "startedAt",
                             "endedAt", "message", "revision")}
    out.update(serverNow=now, levels=sp.LEVELS, modes=MODES, penalty=MISTAKE_PENALTY,
               hintPenalty=HINT_PENALTY, inGame=pid in g["players"],
               peeked=bool(g["peeked"].get(pid)))

    solving = _solving(g, pid)
    out["chat"] = [{k: m[k] for k in ("id", "name", "color", "text", "t", "spoiler")}
                   for m in g["chat"]
                   if not (m["spoiler"] and solving and m["rid"] == g["rid"] and m["pid"] != pid)][-50:]
    out["spoilerHidden"] = solving

    target = g["watchers"].get(pid)
    if target is not None and _cant_watch(g, pid, target):
        g["watchers"].pop(pid, None)
        target = None
    if target is not None:
        tseat = g["players"][target]
        tb = tseat["board"] or g["board"]
        out["spectate"] = {"pid": target, "name": tseat["name"], "puzzle": tb["puzzle"],
                           "cells": "".join(g["shared"]["cells"] if g["mode"] == "coop" else tseat["cells"]),
                           "solution": tb["solution"], "level": tb["level"], "rid": tseat["rid"],
                           "finished": bool(tseat["finishedAt"])}

    names = [p["name"] for p in players.values()]
    if g["phase"] != "playing":
        hist = load_history()
        out["bank"] = bank_info(names, hist)
        out["recent"] = [{k: r.get(k) for k in ("board", "level", "mode", "player", "result",
                                                 "seconds", "date")} for r in hist[-8:][::-1]]

    roster = []
    for p in g["order"]:
        seat = g["players"].get(p)
        if not seat:
            continue
        b = seat["board"] or g["board"]
        cells = g["shared"]["cells"] if g["mode"] == "coop" else seat["cells"]
        row = {"pid": p, "name": seat["name"], "mistakes": seat["mistakes"],
               "placed": seat["placed"], "here": p in players, "hints": seat.get("hints", 0),
               "watchable": p != pid and not _cant_watch(g, pid, p) if pid is not None else False,
               "color": players.get(p, {}).get("color", "#888")}
        if b and cells:
            row.update(filled=_filled(cells, b["puzzle"]), blanks=_blanks(b["puzzle"]),
                       level=b["level"])
        if seat["finishedAt"]:
            row["seconds"] = int(seat["finishedAt"] - (g["startedAt"] if g["mode"] == "competitive" else seat["startedAt"]))
            if g["mode"] == "competitive":
                row["adjusted"] = int(_adjusted(g, seat))
        if "place" in seat:
            row["place"] = seat["place"]
        roster.append(row)
    if g["mode"] == "competitive":
        roster.sort(key=lambda r: (r.get("place") or 99, r.get("adjusted") or 10**9,
                                   -r.get("filled", 0), r["mistakes"]))
    out["roster"] = roster

    if g["mode"] == "coop" and g["board"]:
        sh = g["shared"]
        out.update(puzzle=g["board"]["puzzle"], cells="".join(sh["cells"]), by=sh["by"],
                   teamMistakes=sh["mistakes"], teamHints=sh.get("hints", 0), boardId=g["board"]["id"],
                   level=g["board"]["level"])
    seat = g["players"].get(pid)
    if seat and seat["board"]:
        out.update(puzzle=seat["board"]["puzzle"], cells="".join(seat["cells"]),
                   boardId=seat["board"]["id"], level=seat["board"]["level"],
                   seatRid=seat["rid"], myStart=seat["startedAt"])
    if seat:
        out["me"] = {"mistakes": seat["mistakes"], "finished": bool(seat["finishedAt"]),
                     "place": seat.get("place"), "hints": seat.get("hints", 0)}
    return out


# --------------------------------------------------------------------- cli ---
def _report():
    plays = load_history()
    if not plays:
        print("No Sudoku games recorded yet.")
        return
    by_board = {}
    for p in plays:
        by_board.setdefault((p["level"], p["board"]), []).append(p)
    order = {lvl: i for i, lvl in enumerate(sp.LEVELS)}
    for (lvl, bid), rows in sorted(by_board.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1])):
        print(f"{bid} ({lvl})")
        for r in rows:
            t = f"{r['seconds'] // 60}:{r['seconds'] % 60:02d}" if r.get("seconds") is not None else "—"
            place = f" #{r['place']}" if r.get("place") else ""
            print(f"    {r['date']}  {r['player']:<16} {r['mode']:<11} {r['result']:<10} "
                  f"{t:>6}  {r['mistakes']} mistakes{place}")
    print()
    for lvl in sp.LEVELS:
        played = {bid for (l, bid) in by_board if l == lvl}
        print(f"  {lvl:9s} {len(played):3d} of {len(BANK[lvl])} boards played")


if __name__ == "__main__":
    if "--history" in sys.argv:
        _report()
    else:
        print(__doc__)
