#!/usr/bin/env python3
"""
Local party-game server. Zero dependencies (Python 3 stdlib only).

  Laptop screen  ->  http://<this-machine>:8000/         (the game display)
  Phones         ->  http://<this-machine>:8000/play     (the controller)

Run:  python3 server.py
"""

import json
import queue
import socket
import threading
import time
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import qr             # local, dependency-free QR-code generator
import scattergories  # local, Scattergories data + rules
import taboo          # local, Taboo card deck

HOST = "0.0.0.0"
PORT = 8000
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

GOAL = 40                     # taps needed to win
# Controllers touch the server ~2x/second via /state polling, so a player that
# goes quiet for this long has really gone. Kept a little above the slowest
# poll interval (~0.7s) times a handful of missed polls, to ride out WiFi blips.
PLAYER_TIMEOUT = 6            # seconds without a heartbeat -> drop player
COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#eab308",
          "#a855f7", "#ec4899", "#14b8a6", "#f97316"]

# The game catalog shown on the menu screen. Add an entry here (and, for a
# playable one, the game logic) to grow the collection.
GAMES = [
    {
        "id": "tap-race",
        "name": "Tap Race",
        "tagline": "Mash the button. First to 40 taps wins.",
        "players": "2-8 players",
        "status": "ready",
        "emoji": "\N{CHEQUERED FLAG}",
    },
    {
        "id": "scattergories",
        "name": "Scattergories",
        "tagline": "One letter, 12 categories, 2 minutes. Vote on each other's answers.",
        "players": "2-8 players",
        "status": "ready",
        "emoji": "\N{INPUT SYMBOL FOR LATIN LETTERS}",
    },
    {
        "id": "taboo",
        "name": "Taboo",
        "tagline": "Get your team to say the word — without saying the 5 forbidden ones.",
        "players": "2 teams · 1 phone each",
        "status": "ready",
        "emoji": "\N{ZIPPER-MOUTH FACE}",
    },
    {
        "id": "doodle-dash",
        "name": "Doodle Dash",
        "tagline": "Draw the prompt on your phone; everyone guesses.",
        "players": "3-8 players",
        "status": "soon",
        "emoji": "\N{ARTIST PALETTE}",
    },
    {
        "id": "trivia-rumble",
        "name": "Trivia Rumble",
        "tagline": "Fast-fire quiz. Answer quickest for the most points.",
        "players": "2-8 players",
        "status": "soon",
        "emoji": "\N{BRAIN}",
    },
    {
        "id": "word-bluff",
        "name": "Word Bluff",
        "tagline": "Invent a convincing fake definition and fool the room.",
        "players": "3-8 players",
        "status": "soon",
        "emoji": "\N{SPEECH BALLOON}",
    },
    {
        "id": "reaction-royale",
        "name": "Reaction Royale",
        "tagline": "Tap the instant the screen flips. Too early, you're out.",
        "players": "2-8 players",
        "status": "soon",
        "emoji": "\N{HIGH VOLTAGE SIGN}",
    },
]
PLAYABLE = {g["id"] for g in GAMES if g["status"] == "ready"}


def lan_ip():
    """Best guess at this machine's address on the local network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def play_url():
    return f"http://{lan_ip()}:{PORT}/play"


# QR image is regenerated only when the play URL changes (e.g. new WiFi).
_qr_cache = {"url": None, "png": None}


def qr_png_for_current_url():
    url = play_url()
    if _qr_cache["url"] != url:
        _qr_cache["url"] = url
        _qr_cache["png"] = qr.png(qr.encode(url, "M"), scale=12, border=4)
    return _qr_cache["png"], url

# ---------------------------------------------------------------- game state ---
_lock = threading.Lock()
_subscribers = set()          # set[queue.Queue]  (one per open host display)

def fresh_scat():
    """A Scattergories sub-state in its lobby (pre-round) form."""
    return {
        "phase": "lobby",        # lobby | playing | review | done
        "time": 120,             # seconds per round
        "showNames": False,      # show who wrote each answer during review
        "alliterationBonus": False,
        "letter": None,
        "categories": [],
        "endsAt": None,          # epoch seconds
        # pid -> {"name", "color", "cells": {catIndex(str) -> text}}
        "entries": {},
        "reviewIndex": 0,
        # catIndex(int) -> { targetPid(int) -> { voterPid(int) -> bool } }
        "votes": {},
        "roundScores": {},       # pid(int) -> points earned this round
    }


TABOO_READY_SECONDS = 5   # "get ready" countdown before each turn


def fresh_taboo():
    """A Taboo sub-state in its lobby form."""
    return {
        "phase": "lobby",        # lobby | ready | turn | turnend | gameover
        "turnSeconds": 60,
        "buzzScoresPoint": False, # a correct buzz gives the buzzing team a point
        "teamNames": {"1": "Team 1", "2": "Team 2"},
        "scores": {"1": 0, "2": 0},
        "activeTeam": 1,         # the team giving clues this turn
        "startTeam": 1,          # who takes the first turn of a game
        "deck": [],              # shuffled card indices
        "deckPos": 0,
        "card": None,            # {"word", "taboo": [...], "seq"}
        "cardSeq": 0,
        "readyUntil": None,      # epoch seconds — end of the pre-turn countdown
        "endsAt": None,          # epoch seconds
        "turnPoints": {"1": 0, "2": 0},   # points scored during the live turn
        "turnLog": [],           # [{"word", "result"}]  result: got|skip|buzz
        "lastAction": None,      # {"type", "team", "seq"}  -> big-screen flash
    }


state = {
    "game": None,             # None = menu screen, else a GAMES id
    "phase": "lobby",         # tap-race phase: lobby | playing | over
    "goal": GOAL,
    "winner": None,           # tap-race winner pid
    "players": {},            # pid -> {name, color, taps, score, team, last_seen}
    "scat": fresh_scat(),
    "taboo": fresh_taboo(),
}
_next_pid = [1]


def _scat_public():
    scat = state["scat"]
    present = state["players"]
    out = {
        "phase": scat["phase"],
        "time": scat["time"],
        "showNames": scat["showNames"],
        "alliterationBonus": scat["alliterationBonus"],
        "letter": scat["letter"],
        "categories": scat["categories"],
        "endsAt": scat["endsAt"],
        "serverNow": time.time(),
        "playerCount": len(present),
        "submittedCount": sum(
            1 for e in scat["entries"].values()
            if any(v.strip() for v in e["cells"].values())
        ),
    }

    if scat["phase"] in ("review", "done"):
        out["reviewIndex"] = scat["reviewIndex"]
        out["reviewTotal"] = len(scat["categories"])

    if scat["phase"] == "review" and scat["categories"]:
        idx = scat["reviewIndex"]
        out["category"] = scat["categories"][idx] if idx < len(scat["categories"]) else None
        out["answers"] = _scat_category_answers(idx)
        # a voter is "done" once they've voted on every answer they may vote on
        votable = [a for a in out["answers"] if not a["eliminated"]]
        needed, done = 0, 0
        for pid in present:
            targets = [a for a in votable if a["pid"] != pid]
            if not targets:
                continue
            needed += 1
            votes = scat["votes"].get(idx, {})
            if all(pid in votes.get(a["pid"], {}) for a in targets):
                done += 1
        out["votersNeeded"] = needed
        out["votersDone"] = done

    if scat["phase"] == "done":
        rows = []
        for pid, p in present.items():
            rows.append({
                "pid": pid, "name": p["name"], "color": p["color"],
                "points": scat["roundScores"].get(pid, 0), "total": p["score"],
            })
        rows.sort(key=lambda r: (-r["total"], r["name"].lower()))
        out["roundScores"] = rows

    return out


def _scat_category_answers(idx):
    """All answers for one category, with duplicate elimination + vote tallies."""
    scat = state["scat"]
    key = str(idx)
    raw = []
    for pid, e in scat["entries"].items():
        text = (e["cells"].get(key) or "").strip()
        if text:
            raw.append((pid, e, text))

    norm_counts = {}
    for _, _, text in raw:
        n = scattergories.normalize(text)
        norm_counts[n] = norm_counts.get(n, 0) + 1

    votes_for_cat = scat["votes"].get(idx, {})
    answers = []
    for pid, e, text in sorted(raw, key=lambda r: r[0]):
        eliminated = norm_counts[scattergories.normalize(text)] > 1
        vmap = votes_for_cat.get(pid, {})
        row = {
            "id": f"{idx}:{pid}",
            "pid": pid,
            "color": e["color"],
            "text": text,
            "eliminated": eliminated,
            "yes": sum(1 for v in vmap.values() if v),
            "no": sum(1 for v in vmap.values() if not v),
            "votes": {str(vp): vv for vp, vv in vmap.items()},
        }
        if scat["showNames"]:
            row["name"] = e["name"]
        answers.append(row)
    return answers


def _taboo_public():
    tb = state["taboo"]
    counts = {"1": 0, "2": 0}
    for p in state["players"].values():
        if p.get("team") in (1, 2):
            counts[str(p["team"])] += 1
    out = {
        "phase": tb["phase"],
        "turnSeconds": tb["turnSeconds"],
        "buzzScoresPoint": tb["buzzScoresPoint"],
        "teamNames": tb["teamNames"],
        "scores": tb["scores"],
        "activeTeam": tb["activeTeam"],
        "teamCounts": counts,
        "endsAt": tb["endsAt"],
        "serverNow": time.time(),
        "turnPoints": tb["turnPoints"],
        "turnLog": tb["turnLog"],
        "lastAction": tb["lastAction"],
        "deckSize": len(taboo.CARDS),
    }
    if tb["phase"] == "ready":
        out["readyUntil"] = tb["readyUntil"]
    if tb["phase"] == "turn":
        out["card"] = tb["card"]
    if tb["phase"] in ("turnend", "gameover"):
        out["lastTurnTeam"] = tb["activeTeam"]
    if tb["phase"] == "gameover":
        s1, s2 = tb["scores"]["1"], tb["scores"]["2"]
        out["winner"] = 1 if s1 > s2 else (2 if s2 > s1 else 0)
    return out


def public_state():
    """State shaped for the clients (players as a sorted list, no timestamps)."""
    players = [
        {"pid": pid, "name": p["name"], "color": p["color"],
         "taps": p["taps"], "score": p["score"], "team": p.get("team")}
        for pid, p in state["players"].items()
    ]
    players.sort(key=lambda p: p["pid"])
    winner = state["players"].get(state["winner"], {}).get("name") if state["winner"] else None
    return {
        "game": state["game"],
        "phase": state["phase"],
        "goal": state["goal"],
        "winner": winner,
        "winnerPid": state["winner"],
        "players": players,
        "scat": _scat_public() if state["game"] == "scattergories" else None,
        "taboo": _taboo_public() if state["game"] == "taboo" else None,
    }


def broadcast():
    data = json.dumps(public_state())
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(data)
        except queue.Full:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


def reap_players():
    """Drop players whose controller has gone silent. Caller holds _lock."""
    now = time.time()
    stale = [pid for pid, p in state["players"].items()
             if now - p["last_seen"] > PLAYER_TIMEOUT]
    for pid in stale:
        del state["players"][pid]
    return bool(stale)


def touch_player(pid):
    """Mark a player alive from any request that carries their id."""
    try:
        p = state["players"].get(int(pid))
    except (TypeError, ValueError):
        return
    if p:
        p["last_seen"] = time.time()


def janitor():
    while True:
        time.sleep(1)
        with _lock:
            changed = False
            # end a Scattergories round when its timer runs out
            scat = state["scat"]
            if (state["game"] == "scattergories" and scat["phase"] == "playing"
                    and scat["endsAt"] and time.time() >= scat["endsAt"]):
                scat["phase"] = "review"
                scat["reviewIndex"] = 0
                changed = True

            # Taboo: run the pre-turn countdown, and end a turn on time-out
            tb = state["taboo"]
            if (state["game"] == "taboo" and tb["phase"] == "ready"
                    and tb["readyUntil"] and time.time() >= tb["readyUntil"]):
                _taboo_start_turn()
                changed = True
            if (state["game"] == "taboo" and tb["phase"] == "turn"
                    and tb["endsAt"] and time.time() >= tb["endsAt"]):
                tb["phase"] = "turnend"
                tb["card"] = None
                changed = True
            if reap_players():
                changed = True
            if changed:
                broadcast()


# ------------------------------------------------------------------- actions ---
def do_join(name):
    with _lock:
        pid = _next_pid[0]
        _next_pid[0] += 1
        color = COLORS[(pid - 1) % len(COLORS)]
        name = (name or "").strip()[:14] or f"Player {pid}"
        team = None
        if state["game"] == "taboo":
            # auto-split arrivals across the two teams (they can switch in the lobby)
            c1 = sum(1 for p in state["players"].values() if p.get("team") == 1)
            c2 = sum(1 for p in state["players"].values() if p.get("team") == 2)
            team = 1 if c1 <= c2 else 2
        state["players"][pid] = {
            "name": name, "color": color, "taps": 0, "score": 0, "team": team,
            "last_seen": time.time(),
        }
        broadcast()
        return {"pid": pid, "color": color, "goal": state["goal"]}


def do_select(game):
    """Host picks a game from the menu (game=None returns to the menu)."""
    with _lock:
        if game is not None and game not in PLAYABLE:
            return {"ok": False, "error": "unknown_game"}
        state["game"] = game
        state["phase"] = "lobby"
        state["winner"] = None
        for pl in state["players"].values():
            pl["taps"] = 0
        # keep the chosen game's settings, drop any in-progress round
        keep = state["scat"]
        state["scat"] = fresh_scat()
        state["scat"]["time"] = keep["time"]
        state["scat"]["showNames"] = keep["showNames"]
        state["scat"]["alliterationBonus"] = keep["alliterationBonus"]

        keptb = state["taboo"]
        state["taboo"] = fresh_taboo()
        state["taboo"]["turnSeconds"] = keptb["turnSeconds"]
        state["taboo"]["buzzScoresPoint"] = keptb["buzzScoresPoint"]
        state["taboo"]["teamNames"] = keptb["teamNames"]
        if game == "taboo":
            _taboo_autoteam()
        else:
            for pl in state["players"].values():
                pl["team"] = None
        broadcast()
        return {"ok": True, "game": game}


def _taboo_autoteam():
    """Spread everyone across the two teams as evenly as we can."""
    pids = sorted(state["players"])
    for i, pid in enumerate(pids):
        state["players"][pid]["team"] = 1 if i % 2 == 0 else 2


# ------------------------------------------------------- scattergories actions ---
def _scat_start_round():
    scat = state["scat"]
    r = scattergories.new_round()
    scat["letter"] = r["letter"]
    scat["categories"] = r["categories"]
    scat["entries"] = {}
    scat["votes"] = {}
    scat["roundScores"] = {}
    scat["reviewIndex"] = 0
    scat["endsAt"] = time.time() + max(15, int(scat["time"]))
    scat["phase"] = "playing"


def _scat_score_current_category():
    """Tally the category the host is leaving and bank the points."""
    scat = state["scat"]
    idx = scat["reviewIndex"]
    if idx >= len(scat["categories"]):
        return
    present = state["players"]
    for a in _scat_category_answers(idx):
        if a["eliminated"]:
            continue
        author = a["pid"]
        eligible = [pid for pid in present if pid != author]
        if not eligible:
            approved = True
        else:
            approved = a["yes"] > a["no"]
        pts = scattergories.score_answer(
            approved, a["text"], scat["letter"], scat["alliterationBonus"])
        if pts:
            scat["roundScores"][author] = scat["roundScores"].get(author, 0) + pts


def do_scat(pid, action, data):
    """Handle a scat* action. Caller holds _lock. pid may be None for host acts."""
    scat = state["scat"]

    if action == "scatSet":
        key, value = data.get("key"), data.get("value")
        if key == "time":
            try:
                scat["time"] = min(600, max(15, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad_value"}
        elif key in ("showNames", "alliterationBonus"):
            scat[key] = bool(value)
        else:
            return {"ok": False, "error": "bad_key"}
        broadcast()
        return {"ok": True}

    if action == "scatStart":
        if scat["phase"] in ("lobby", "done", "review"):
            _scat_start_round()
            broadcast()
        return {"ok": True}

    if action == "scatEndRound":
        if scat["phase"] == "playing":
            scat["phase"] = "review"
            scat["reviewIndex"] = 0
            broadcast()
        return {"ok": True}

    if action == "scatNext":
        if scat["phase"] != "review":
            return {"ok": True}
        _scat_score_current_category()
        scat["reviewIndex"] += 1
        if scat["reviewIndex"] >= len(scat["categories"]):
            for p, pts in scat["roundScores"].items():
                if p in state["players"]:
                    state["players"][p]["score"] += pts
            scat["phase"] = "done"
        broadcast()
        return {"ok": True}

    if action == "scatLobby":
        scat["phase"] = "lobby"
        broadcast()
        return {"ok": True}

    if action == "scatResetScores":
        for pl in state["players"].values():
            pl["score"] = 0
        broadcast()
        return {"ok": True}

    # -- player actions below need a valid pid --
    p = state["players"].get(pid)
    if not p:
        return {"ok": False, "error": "not_joined"}
    p["last_seen"] = time.time()

    if action == "scatAnswers":
        if scat["phase"] != "playing":
            return {"ok": True}
        cells = data.get("answers") or {}
        clean = {}
        for k, v in cells.items():
            try:
                ki = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= ki < len(scat["categories"]) and isinstance(v, str):
                clean[str(ki)] = v[:80]
        scat["entries"][pid] = {"name": p["name"], "color": p["color"], "cells": clean}
        broadcast()
        return {"ok": True}

    if action == "scatVote":
        if scat["phase"] != "review":
            return {"ok": True}
        idx = scat["reviewIndex"]
        target = data.get("target")
        value = data.get("value")
        try:
            target = int(target)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_target"}
        if target == pid:
            return {"ok": False, "error": "no_self_vote"}
        answers = {a["pid"]: a for a in _scat_category_answers(idx)}
        if target not in answers or answers[target]["eliminated"]:
            return {"ok": False, "error": "not_votable"}
        scat["votes"].setdefault(idx, {}).setdefault(target, {})[pid] = bool(value)
        broadcast()
        return {"ok": True}

    return {"ok": False, "error": "unknown_action"}


# --------------------------------------------------------------- taboo actions ---
def _taboo_deal_card():
    tb = state["taboo"]
    if not tb["deck"] or tb["deckPos"] >= len(tb["deck"]):
        tb["deck"] = taboo.shuffled_deck()
        tb["deckPos"] = 0
    idx = tb["deck"][tb["deckPos"]]
    tb["deckPos"] += 1
    tb["cardSeq"] += 1
    c = taboo.CARDS[idx]
    tb["card"] = {"word": c["word"], "taboo": c["taboo"], "seq": tb["cardSeq"]}


def _taboo_begin_ready():
    """Show the 'get ready' countdown before a turn actually starts."""
    tb = state["taboo"]
    tb["phase"] = "ready"
    tb["readyUntil"] = time.time() + TABOO_READY_SECONDS
    tb["card"] = None


def _taboo_start_turn():
    tb = state["taboo"]
    tb["phase"] = "turn"
    tb["readyUntil"] = None
    tb["endsAt"] = time.time() + max(15, int(tb["turnSeconds"]))
    tb["turnPoints"] = {"1": 0, "2": 0}
    tb["turnLog"] = []
    tb["lastAction"] = None
    _taboo_deal_card()


def _taboo_advance(result, scoring_team):
    """Log the card just resolved, award any point, deal the next one."""
    tb = state["taboo"]
    tb["turnLog"].append({"word": tb["card"]["word"], "result": result})
    if scoring_team:
        k = str(scoring_team)
        tb["scores"][k] += 1
        tb["turnPoints"][k] += 1
    tb["lastAction"] = {"type": result, "team": scoring_team, "seq": tb["cardSeq"]}
    _taboo_deal_card()


def do_taboo(pid, action, data):
    """Handle a taboo* action. Caller holds _lock. pid may be None for host acts."""
    tb = state["taboo"]

    if action == "tabooSet":
        key, value = data.get("key"), data.get("value")
        if key == "turnSeconds":
            try:
                tb["turnSeconds"] = min(300, max(15, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad_value"}
        elif key == "buzzScoresPoint":
            tb["buzzScoresPoint"] = bool(value)
        elif key in ("team1Name", "team2Name"):
            slot = "1" if key == "team1Name" else "2"
            tb["teamNames"][slot] = (str(value or "").strip()[:16]
                                     or f"Team {slot}")
        else:
            return {"ok": False, "error": "bad_key"}
        broadcast()
        return {"ok": True}

    if action == "tabooStart":
        if tb["phase"] in ("lobby", "gameover"):
            tb["scores"] = {"1": 0, "2": 0}
            tb["activeTeam"] = tb["startTeam"]
            tb["deck"] = taboo.shuffled_deck()
            tb["deckPos"] = 0
            _taboo_begin_ready()
            broadcast()
        return {"ok": True}

    if action == "tabooNextTurn":
        if tb["phase"] == "turnend":
            tb["activeTeam"] = 2 if tb["activeTeam"] == 1 else 1
            _taboo_begin_ready()
            broadcast()
        return {"ok": True}

    if action == "tabooBeginTurn":     # host skips the countdown
        if tb["phase"] == "ready":
            _taboo_start_turn()
            broadcast()
        return {"ok": True}

    if action == "tabooEndTurn":     # timer ran out (or host cut it short)
        if tb["phase"] == "turn":
            tb["phase"] = "turnend"
            tb["card"] = None
            broadcast()
        return {"ok": True}

    if action == "tabooEndGame":
        if tb["phase"] in ("turn", "turnend"):
            tb["phase"] = "gameover"
            tb["card"] = None
            broadcast()
        return {"ok": True}

    if action == "tabooNewGame":
        if tb["phase"] == "gameover":
            tb["startTeam"] = 2 if tb["startTeam"] == 1 else 1
            tb["scores"] = {"1": 0, "2": 0}
            tb["phase"] = "lobby"
            broadcast()
        return {"ok": True}

    # -- player actions need a valid pid + team --
    p = state["players"].get(pid)
    if not p:
        return {"ok": False, "error": "not_joined"}
    p["last_seen"] = time.time()

    if action == "tabooTeam":
        if tb["phase"] in ("lobby", "turnend", "gameover"):
            t = data.get("team")
            if t in (1, 2):
                p["team"] = t
                broadcast()
        return {"ok": True}

    if action in ("tabooGot", "tabooSkip", "tabooBuzz"):
        if tb["phase"] != "turn" or not tb["card"]:
            return {"ok": True}
        # ignore a tap aimed at a card we've already moved past
        if data.get("seq") != tb["card"]["seq"]:
            return {"ok": True, "stale": True}
        team = p.get("team")
        if team not in (1, 2):
            return {"ok": False, "error": "no_team"}
        if action == "tabooBuzz":
            if team == tb["activeTeam"]:
                return {"ok": False, "error": "cant_buzz_own"}
            _taboo_advance("buzz", team if tb["buzzScoresPoint"] else None)
        else:
            if team != tb["activeTeam"]:
                return {"ok": False, "error": "not_your_turn"}
            if action == "tabooGot":
                _taboo_advance("got", tb["activeTeam"])
            else:
                _taboo_advance("skip", None)
        broadcast()
        return {"ok": True}

    return {"ok": False, "error": "unknown_action"}


def do_input(data):
    pid, action = data.get("pid"), data.get("action")
    with _lock:
        if action == "ping":
            touch_player(pid)
            return {"ok": True}

        if action == "leave":
            # sent as a beacon when a controller page is closed / navigated away
            if pid in state["players"]:
                del state["players"][pid]
                broadcast()
            return {"ok": True}

        if isinstance(action, str) and action.startswith("scat"):
            return do_scat(pid, action, data)

        if isinstance(action, str) and action.startswith("taboo"):
            return do_taboo(pid, action, data)

        # start / reset are game-wide controls — the laptop screen or any
        # phone can trigger them, so they don't require a valid player id.
        if action == "start":
            if state["phase"] != "playing":
                for pl in state["players"].values():
                    pl["taps"] = 0
                state["phase"] = "playing"
                state["winner"] = None
                broadcast()
            return {"ok": True}

        if action == "reset":
            state["phase"] = "lobby"
            state["winner"] = None
            for pl in state["players"].values():
                pl["taps"] = 0
            broadcast()
            return {"ok": True}

        p = state["players"].get(pid)
        if not p:
            return {"ok": False, "error": "not_joined"}
        p["last_seen"] = time.time()

        if action == "ping":
            return {"ok": True}

        if action == "tap":
            if state["phase"] != "playing":
                return {"ok": True, "taps": p["taps"]}
            p["taps"] += 1
            if p["taps"] >= state["goal"]:
                state["phase"] = "over"
                state["winner"] = pid
            broadcast()
            return {"ok": True, "taps": p["taps"]}

        return {"ok": False, "error": "unknown_action"}


# -------------------------------------------------------------------- server ---
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # keep the console quiet
        pass

    # -- helpers --
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name, ctype):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes --
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_file("menu.html", "text/html; charset=utf-8")
        elif path in ("/host", "/host/"):
            self._send_file("host.html", "text/html; charset=utf-8")
        elif path in ("/play", "/play/"):
            self._send_file("controller.html", "text/html; charset=utf-8")
        elif path == "/games":
            self._send_json({"games": GAMES})
        elif path == "/phoneQR.png":
            png, _ = qr_png_for_current_url()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(png)
        elif path == "/whoami":
            self._send_json({"ip": lan_ip(), "port": PORT, "playUrl": play_url()})
        elif path == "/state":
            pid = parse_qs(urlparse(self.path).query).get("pid", [None])[0]
            with _lock:
                if pid is not None:
                    touch_player(pid)   # the poll doubles as the heartbeat
                self._send_json(public_state())
        elif path == "/events":
            self._stream_events()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_json()
        if path == "/join":
            self._send_json(do_join(data.get("name")))
        elif path == "/input":
            self._send_json(do_input(data))
        elif path == "/select":
            self._send_json(do_select(data.get("game")))
        else:
            self.send_error(404)

    # -- server-sent events stream to a host display --
    def _stream_events(self):
        q = queue.Queue(maxsize=64)
        _subscribers.add(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            with _lock:
                first = json.dumps(public_state())
            self.wfile.write(f"data: {first}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")   # heartbeat
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            _subscribers.discard(q)


def main():
    threading.Thread(target=janitor, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    ip = lan_ip()
    print("\n  party-game server running\n")
    print(f"  laptop / games menu :  http://localhost:{PORT}/")
    print(f"  phones / controller :  http://{ip}:{PORT}/play")
    print("\n  (phone must be on the same WiFi.  Ctrl+C to stop.)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye\n")
        srv.shutdown()


if __name__ == "__main__":
    main()
