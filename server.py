#!/usr/bin/env python3
"""
Local party-game server. Zero dependencies (Python 3 stdlib only).

  Laptop screen  ->  http://<this-machine>:8000/         (the game display)
  Phones         ->  http://<this-machine>:8000/play     (the controller)

Run:  python3 server.py
"""

import http.cookies
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import qr             # local, dependency-free QR-code generator
import scattergories  # local, Scattergories data + rules
import taboo          # local, Taboo card deck

HOST = "0.0.0.0"
PORT = 8000
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

GOAL = 40                     # taps needed to win
# Controllers touch the server ~2x/second via /state polling. Two stages, so a
# WiFi blip or a phone locking doesn't cost you your spot:
#   no heartbeat for PLAYER_TIMEOUT  -> marked "reconnecting" (kept in the game)
#   ...still gone after PLAYER_GRACE  -> record removed for good
# A returning phone re-attaches with its saved id+token (see do_resume) and
# picks up its old name, colour, score and team.
PLAYER_TIMEOUT = 6            # seconds without a heartbeat -> show as reconnecting
PLAYER_GRACE = 90            # ...and this long -> really drop them
COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#eab308",
          "#a855f7", "#ec4899", "#14b8a6", "#f97316"]

# --- access control (matters most when exposed via a tunnel) -------------------
# Set PUBLIC_URL to the https URL your tunnel prints, e.g.
#   PUBLIC_URL=https://foo-bar-baz.trycloudflare.com python3 server.py
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
MAX_PLAYERS = int(os.environ.get("MAX_PLAYERS", "12"))
# Room code players must supply to join. The QR carries it; typed-in players
# read it off the host screen. Override with ROOM_CODE=WXYZ to keep it stable.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O/1/I/L
ROOM_CODE = (os.environ.get("ROOM_CODE", "").strip().upper()
             or "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)))
# Secret that unlocks the laptop screens (menu + game screen). Printed on start.
# Override with HOST_TOKEN=... to keep your host bookmark stable across restarts.
HOST_TOKEN = os.environ.get("HOST_TOKEN", "").strip() or secrets.token_urlsafe(18)
JOIN_WINDOW = 60             # seconds
# join attempts per client IP per window — mainly to slow room-code guessing.
# Real players rejoin a handful of times at most; bump it if you're behind a
# proxy that collapses everyone to one IP.
JOIN_MAX = int(os.environ.get("JOIN_MAX", "15"))
_join_hits = {}             # ip -> [timestamps]

# Actions only the host screen may trigger.
HOST_ONLY = {
    "start", "reset",
    "scatStart", "scatSet", "scatEndRound", "scatNext", "scatLobby", "scatResetScores",
    "tabooStart", "tabooSet", "tabooBeginTurn", "tabooEndTurn", "tabooNextTurn",
    "tabooEndGame", "tabooNewGame",
}

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


def base_url():
    return PUBLIC_URL or f"http://{lan_ip()}:{PORT}"


def play_url():
    """The address a phone joins at — carries the room code so a QR scan is
    zero-friction."""
    return f"{base_url()}/play?code={ROOM_CODE}"


def host_url():
    return f"{base_url()}/?host={HOST_TOKEN}"


def rate_ok(ip):
    """Simple sliding-window limiter for /join. Caller holds _lock."""
    now = time.time()
    hits = [t for t in _join_hits.get(ip, []) if now - t < JOIN_WINDOW]
    if len(hits) >= JOIN_MAX:
        _join_hits[ip] = hits
        return False
    hits.append(now)
    _join_hits[ip] = hits
    return True


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


def _scat_public(for_pid=None):
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

    # hand a reconnecting phone back its own in-progress answers so a mid-round
    # disconnect (or a reload) doesn't wipe what it already typed
    if scat["phase"] == "playing" and for_pid is not None:
        e = scat["entries"].get(for_pid)
        out["mine"] = dict(e["cells"]) if e else {}

    if scat["phase"] in ("review", "done"):
        out["reviewIndex"] = scat["reviewIndex"]
        out["reviewTotal"] = len(scat["categories"])

    if scat["phase"] == "review" and scat["categories"]:
        idx = scat["reviewIndex"]
        out["category"] = scat["categories"][idx] if idx < len(scat["categories"]) else None
        out["answers"] = _scat_category_answers(idx)
        # a voter is "done" once they've voted on every answer they may vote on.
        # a phone that's mid-reconnect doesn't hold up the round.
        votable = [a for a in out["answers"] if not a["eliminated"]]
        needed, done = 0, 0
        for pid, p in present.items():
            if not p.get("connected", True):
                continue
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


def public_state(for_pid=None):
    """State shaped for the clients (players as a sorted list, no timestamps).
    `for_pid` (set on a /state?pid= poll) adds that player's own scat answers."""
    players = [
        {"pid": pid, "name": p["name"], "color": p["color"],
         "taps": p["taps"], "score": p["score"], "team": p.get("team"),
         "connected": p.get("connected", True)}
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
        "scat": _scat_public(for_pid) if state["game"] == "scattergories" else None,
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
    """Two-stage cleanup for silent controllers. Caller holds _lock.
    Silent past PLAYER_TIMEOUT -> flagged disconnected but kept (so they can
    reconnect); silent past PLAYER_GRACE -> removed. Returns True if anything
    changed so the caller re-broadcasts."""
    now = time.time()
    changed = False
    for pid, p in list(state["players"].items()):
        gone = now - p["last_seen"]
        if gone > PLAYER_GRACE:
            del state["players"][pid]
            changed = True
        elif gone > PLAYER_TIMEOUT and p.get("connected", True):
            p["connected"] = False
            changed = True
    return changed


def touch_player(pid):
    """Mark a player alive from any request that carries their id. Returns True
    when this flips them from disconnected back to connected (caller broadcasts)."""
    try:
        p = state["players"].get(int(pid))
    except (TypeError, ValueError):
        return False
    if not p:
        return False
    p["last_seen"] = time.time()
    if not p.get("connected", True):
        p["connected"] = True
        return True
    return False


def _valid_pid(pid):
    try:
        return int(pid) in state["players"]
    except (TypeError, ValueError):
        return False


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
def do_join(name, code, ip):
    with _lock:
        if not rate_ok(ip):
            return {"ok": False, "error": "rate_limited"}
        if (code or "").strip().upper() != ROOM_CODE:
            return {"ok": False, "error": "bad_code"}
        if len(state["players"]) >= MAX_PLAYERS:
            return {"ok": False, "error": "full"}
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
        token = secrets.token_urlsafe(9)   # phone keeps this to reconnect as itself
        state["players"][pid] = {
            "name": name, "color": color, "taps": 0, "score": 0, "team": team,
            "last_seen": time.time(), "token": token, "connected": True,
        }
        broadcast()
        return {"pid": pid, "token": token, "color": color, "goal": state["goal"]}


def do_resume(pid, token):
    """A phone that dropped comes back with its saved id + token. Caller-free
    (takes the lock itself)."""
    with _lock:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return {"ok": False, "error": "unknown"}
        p = state["players"].get(pid)
        if not p or not token or not p.get("token") \
                or not secrets.compare_digest(str(token), p["token"]):
            return {"ok": False, "error": "unknown"}
        p["last_seen"] = time.time()
        p["connected"] = True
        broadcast()
        return {"ok": True, "pid": pid, "name": p["name"], "color": p["color"],
                "team": p.get("team"), "goal": state["goal"]}


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


def do_input(data, is_host=False):
    pid, action = data.get("pid"), data.get("action")
    if action in HOST_ONLY and not is_host:
        return {"ok": False, "error": "not_host"}
    with _lock:
        if action == "ping":
            touch_player(pid)
            return {"ok": True}

        if action == "leave":
            # beacon when a controller page closes / navigates away. Don't delete
            # — the page might just be reloading, or the phone crashed and is
            # coming back. Flag them disconnected; reap_players() clears them
            # after PLAYER_GRACE if they never return.
            p = state["players"].get(pid)
            if p and p.get("connected", True):
                p["connected"] = False
                p["last_seen"] = time.time() - PLAYER_TIMEOUT
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
    def _maybe_set_host_cookie(self):
        """Re-issue the host cookie on every response to a valid ?host= link, so
        reloading/bookmarking that link always unlocks — no fragile redirect."""
        if getattr(self, "_grant_host", False):
            self.send_header(
                "Set-Cookie",
                f"hosttoken={HOST_TOKEN}; Path=/; Max-Age=43200; SameSite=Lax")

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._maybe_set_host_cookie()
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
        self._maybe_set_host_cookie()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _is_host(self):
        raw = self.headers.get("Cookie", "")
        try:
            jar = http.cookies.SimpleCookie(raw)
        except http.cookies.CookieError:
            return False
        c = jar.get("hosttoken")
        return bool(c) and secrets.compare_digest(c.value, HOST_TOKEN)

    def _client_ip(self):
        for h in ("CF-Connecting-IP", "X-Forwarded-For"):
            v = self.headers.get(h)
            if v:
                return v.split(",")[0].strip()
        return self.client_address[0]

    # -- routes --
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # unlock the laptop screens: ?host=<token> tells _send_file/_send_json to
        # (re-)issue the host cookie on this response. No redirect — a redirect
        # over a still-warming tunnel would land the host on the lock screen.
        tok = qs.get("host", [None])[0]
        if tok is not None:
            if not secrets.compare_digest(tok, HOST_TOKEN):
                self.send_error(403, "bad host key")
                return
            self._grant_host = True

        host = self._grant_host if getattr(self, "_grant_host", False) \
            else self._is_host()

        if path == "/":
            self._send_file("menu.html", "text/html; charset=utf-8")
        elif path in ("/host", "/host/"):
            self._send_file("host.html", "text/html; charset=utf-8")
        elif path in ("/play", "/play/"):
            self._send_file("controller.html", "text/html; charset=utf-8")
        elif path == "/games":
            self._send_json({"games": GAMES})
        elif path == "/amihost":
            self._send_json({"host": host, "code": ROOM_CODE if host else None})
        elif path == "/phoneQR.png":
            if not host:
                self.send_error(403); return
            png, _ = qr_png_for_current_url()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(png)
        elif path == "/whoami":
            if not host:
                self.send_error(403); return
            self._send_json({"playUrl": play_url(), "code": ROOM_CODE,
                             "public": bool(PUBLIC_URL)})
        elif path == "/state":
            pid = qs.get("pid", [None])[0]
            with _lock:
                if not host and not _valid_pid(pid):
                    self._send_json({"locked": True, "game": state["game"],
                                     "players": []})
                    return
                if pid is not None and touch_player(pid):
                    broadcast()         # this phone just came back from "reconnecting"
                try:
                    mine = int(pid) if pid is not None else None
                except (TypeError, ValueError):
                    mine = None
                self._send_json(public_state(mine))
        elif path == "/events":
            if not host:
                self.send_error(403); return
            self._stream_events()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_json()
        if path == "/join":
            self._send_json(do_join(data.get("name"), data.get("code"),
                                    self._client_ip()))
        elif path == "/resume":
            self._send_json(do_resume(data.get("pid"), data.get("token")))
        elif path == "/input":
            self._send_json(do_input(data, self._is_host()))
        elif path == "/select":
            if not self._is_host():
                self._send_json({"ok": False, "error": "not_host"}, 403)
            else:
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


_TCF_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def _ask_mode():
    """Prompt for local vs public. MODE=local|public skips the prompt; falls
    back to local when there's no terminal to ask on."""
    env = os.environ.get("MODE", "").strip().lower()
    if env in ("local", "public"):
        return env
    if not sys.stdin.isatty():
        return "local"
    print("\n  How do you want to run?")
    print("    [L] Local  — phones on the same WiFi only            (default)")
    print("    [P] Public — anyone with the link + room code, via a Cloudflare tunnel")
    try:
        ans = input("\n  Choose L or P: ").strip().lower()
    except EOFError:
        return "local"
    return "public" if ans.startswith("p") else "local"


def _start_tunnel(port):
    """Launch `cloudflared` and return (proc, https_url), or None on failure."""
    exe = shutil.which("cloudflared")
    if not exe:
        print("\n  cloudflared isn't on your PATH. Install it with:")
        print("      brew install cloudflared")
        return None
    print("\n  starting a Cloudflare tunnel (a few seconds)…")
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)
    tail, url, deadline = [], None, time.time() + 40
    while time.time() < deadline:
        ln = proc.stdout.readline()
        if not ln:
            if proc.poll() is not None:
                break
            continue
        tail = (tail + [ln])[-12:]
        m = _TCF_RE.search(ln)
        if m:
            url = m.group(0)
            break
    if not url:
        print("  couldn't get a tunnel URL. cloudflared said:")
        for ln in tail:
            print("    " + ln.rstrip())
        proc.terminate()
        return None
    # keep draining output so cloudflared's pipe never fills; warn if it dies
    def _watch():
        for _ in proc.stdout:
            pass
        if proc.returncode not in (0, None):
            print("\n  ⚠  the Cloudflare tunnel stopped — remote players are cut off.")
    threading.Thread(target=_watch, daemon=True).start()

    # cloudflared prints the URL several seconds to a minute before Cloudflare's
    # edge can actually route to it (until then every request 530s). Wait for a
    # real reply so we never hand out a link that fails on the first click —
    # the host link is a one-shot ?token redirect and doesn't survive that.
    print("  got the URL — waiting for the tunnel to come up…")
    if _wait_url_live(url + "/games"):
        print("  tunnel is live.")
    else:
        print("  still no response — using the link anyway; "
              "give it a moment before opening, and reload if the first try fails.")
    return proc, url


def _wait_url_live(url, timeout=60):
    """Poll `url` until it answers with a non-5xx status, or give up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                if r.status < 500:
                    return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _banner(tunnel_on):
    line = "  " + "-" * 62
    print("\n  party-game server running\n")
    print(line)
    print("  HOST — open this to unlock the laptop screen (one click):")
    print(f"      {host_url()}")
    print(line)
    print(f"  Players join at:   {play_url()}")
    print(f"  Room code:         {ROOM_CODE}    (the QR already includes it)")
    print(line)
    if tunnel_on:
        print("  PUBLIC via Cloudflare tunnel — anyone with the link AND code can join.")
        print("  Ctrl+C stops the game and the tunnel.")
    elif PUBLIC_URL:
        print("  PUBLIC MODE (PUBLIC_URL set) — anyone with the link AND code can join.")
    else:
        print("  LAN only.  Local host link also works:")
        print(f"      http://localhost:{PORT}/?host={HOST_TOKEN}")
    print(line + "\n")


def _want_gui():
    """A window pops up by default in an interactive run. Skip it when GUI=0 /
    --no-gui, when MODE is preset (scripted), when there's no display, or when
    output is piped and GUI wasn't explicitly requested."""
    if os.environ.get("GUI", "").strip().lower() in ("0", "no", "false"):
        return False
    if "--no-gui" in sys.argv:
        return False
    if os.environ.get("MODE", "").strip().lower() in ("local", "public"):
        return False
    if not sys.stdout.isatty() and not os.environ.get("GUI"):
        return False
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


def main():
    global PUBLIC_URL
    try:
        sys.stdout.reconfigure(line_buffering=True)   # show output even when piped
    except Exception:
        pass
    # make sure Ctrl+C works even if a parent shell left SIGINT ignored
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        pass

    srv = ThreadingHTTPServer((HOST, PORT), Handler)   # binds + listens now
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()

    hold = {"tunnel": None}   # (proc, url), so cleanup always finds it

    def _set_public(url):
        global PUBLIC_URL
        PUBLIC_URL = url

    def _cleanup():
        srv.shutdown()
        t = hold["tunnel"]
        if t:
            t[0].terminate()
            try:
                t[0].wait(timeout=5)
            except subprocess.TimeoutExpired:
                t[0].kill()

    if _want_gui():
        try:
            import gui
        except Exception as exc:
            print(f"  (control window unavailable: {exc} — using the terminal)")
        else:
            print("  opening the control window…\n")
            ctx = {
                "room_code": ROOM_CODE,
                "already_public": bool(PUBLIC_URL),
                "host_url": host_url,
                "play_url": play_url,
                "qr_png": lambda: qr_png_for_current_url()[0],
                "start_tunnel": lambda: _start_tunnel(PORT),
                "set_public_url": _set_public,
                "set_tunnel": lambda t: hold.__setitem__("tunnel", t),
                "banner": _banner,
            }
            try:
                gui.run(ctx)
            finally:
                print("\n  bye\n")
                _cleanup()
            return

    # ---- terminal-only fallback ----
    if not PUBLIC_URL and _ask_mode() == "public":
        t = _start_tunnel(PORT)
        if t:
            PUBLIC_URL = t[1]
            hold["tunnel"] = t
        else:
            print("  → running LOCAL instead.")
    _banner(hold["tunnel"] is not None)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n  bye\n")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
