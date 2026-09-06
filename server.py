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
import random
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import blackbox       # local, BlackBox (CAH-style) card decks
import codenames      # local, Codenames word list + board dealer
import imposter       # local, Imposter word list
import mafia          # local, Mafia role dealer
import notify         # local, emails/texts the owner when a public link opens
import qr             # local, dependency-free QR-code generator
import scattergories  # local, Scattergories data + rules
import taboo          # local, Taboo card deck

HOST = "0.0.0.0"
PORT = 8000
# A second listener that serves the exact same site over HTTPS with a throwaway
# self-signed cert. Its only reason to exist: iOS Safari refuses to hand a page
# the gyroscope / motion sensors unless the page is a "secure context", and a
# plain LAN http:// link never is. Phones that need motion (the Wii Sandbox)
# upgrade themselves to this port and click past the one-time cert warning — no
# Cloudflare tunnel, no internet round-trip, no lag. Disabled automatically if
# `openssl` isn't on PATH or the port is taken.
HTTPS_PORT = int(os.environ.get("HTTPS_PORT", "8443"))
NO_HTTPS = os.environ.get("NO_HTTPS", "").strip() not in ("", "0", "false", "no")
HTTPS_OK = False            # set True once the TLS listener is actually up
_CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".certs")
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
# Password the owner types on the lock screen to unlock host control from ANY
# machine (the ?host=<token> link still works one-click from your own browser).
# Override with HOST_PASSWORD=... .
HOST_PASSWORD = os.environ.get("HOST_PASSWORD", "").strip() or "Brownnation1!"
_unlock_hits = {}           # ip -> [timestamps]  (throttle password guessing)
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
    "blackboxStart", "blackboxSet", "blackboxNewGame", "blackboxEndGame",
    "cnStart", "cnMode", "cnNewGame", "cnLobby", "cnAutoTeam",
    "wiiReset", "wiiSelect", "wiiOpen", "wiiSens", "wiiCapture",
    "impSet", "impStart", "impNextRound", "impVoteStart", "impVoteResolve",
    "impSkip", "impGuessJudge", "impEndGame", "impLobby",
    "mafiaSetModerator",
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
        "id": "blackbox",
        "name": "BlackBox",
        "tagline": "Fill in the blank with your funniest card. The Card Czar picks the winner.",
        "players": "3-8 players",
        "status": "ready",
        "emoji": "\N{BLACK LARGE SQUARE}",
    },
    {
        "id": "codenames",
        "name": "Codenames",
        "tagline": "Two spymasters give one-word clues; their teams race to find the right agents — and dodge the assassin.",
        "players": "4-8 players · 2 teams",
        "status": "ready",
        "emoji": "\N{SLEUTH OR SPY}",
    },
    {
        "id": "wii-sandbox",
        "name": "Wii Sandbox",
        "tagline": "Point your phone at the screen like a Wii remote. Experimental motion controls.",
        "players": "1-8 players",
        "status": "ready",
        "emoji": "\N{VIDEO GAME}",
    },
    {
        "id": "imposter",
        "name": "Imposter",
        "tagline": "Everyone gets the secret word — except the imposters. Give a clue, blend in, don't get caught.",
        "players": "4-10 players",
        "status": "ready",
        "emoji": "\N{PERFORMING ARTS}",
    },
    {
        "id": "mafia",
        "name": "Mafia",
        "tagline": "Get dealt a secret role. The Mafia pick off the town each night; by day the town votes back.",
        "players": "5-12 players + a moderator",
        "status": "ready",
        "emoji": "\N{PLAYING CARD BLACK JOKER}",
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


def local_ipv4s():
    """Every IPv4 address this machine answers to — the phone might be on a
    different interface than the default route."""
    ips = {"127.0.0.1", lan_ip()}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    return sorted(i for i in ips if i and not i.startswith("169.254."))


def ensure_self_signed_cert():
    """Make a fresh self-signed cert covering the current local IPs and return
    (cert_path, key_path). Regenerated every start so a new Wi-Fi network (new
    IP) is always covered. Returns None if `openssl` isn't available."""
    if not shutil.which("openssl"):
        return None
    os.makedirs(_CERT_DIR, exist_ok=True)
    cert = os.path.join(_CERT_DIR, "vibebox.crt")
    key = os.path.join(_CERT_DIR, "vibebox.key")
    sans = ["DNS:localhost"] + [f"IP:{ip}" for ip in local_ipv4s()]
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
             "-days", "3650", "-nodes", "-keyout", key, "-out", cert,
             "-subj", "/CN=VibeBox Local",
             "-addext", "subjectAltName=" + ",".join(sans)],
            check=True, capture_output=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return cert, key


def start_https_listener():
    """Bring up the TLS twin of the HTTP server on HTTPS_PORT. Best effort:
    any failure just leaves HTTPS_OK False and the site stays http-only."""
    global HTTPS_OK
    if NO_HTTPS:
        return None
    made = ensure_self_signed_cert()
    if not made:
        print("  (no HTTPS twin — `openssl` not found; phone motion controls "
              "will need Public mode)")
        return None
    cert, key = made
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv = ThreadingHTTPServer((HOST, HTTPS_PORT), Handler)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    except (OSError, ssl.SSLError) as exc:
        print(f"  (no HTTPS twin on :{HTTPS_PORT} — {exc})")
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    HTTPS_OK = True
    print(f"  HTTPS twin ready on :{HTTPS_PORT} "
          f"(self-signed — phones tap past the warning once)")
    return srv


def base_url():
    return PUBLIC_URL or f"http://{lan_ip()}:{PORT}"


def play_url():
    """The short address a phone joins at — the room code *is* the path, e.g.
    https://vibebox.tv/WXYZ . GET /<code> 302s to /play?code=<code>, so it's
    just as zero-friction for a QR scan as the old long form."""
    return f"{base_url()}/{ROOM_CODE}"


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


def unlock_rate_ok(ip):
    """At most 8 host-password tries per IP per 5 minutes."""
    now = time.time()
    hits = [t for t in _unlock_hits.get(ip, []) if now - t < 300]
    _unlock_hits[ip] = hits
    if len(hits) >= 8:
        return False
    hits.append(now)
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


def fresh_blackbox():
    """A BlackBox sub-state in its lobby form. pid-keyed dicts use int keys
    (JSON stringifies them on the way out)."""
    return {
        "phase": "lobby",       # lobby | select | judge | reveal | gameover
        "target": 5,            # black cards needed to win
        "selectSeconds": 60,
        "scores": {},           # pid -> points
        "czar": None,           # pid of this round's Card Czar
        "round": 0,
        "black": None,          # {"text", "pick", "draw"}
        "whiteDeck": [], "whitePos": 0,
        "blackDeck": [], "blackPos": 0,
        "hands": {},             # pid -> [whiteIdx, ...]  (private, 7 cards)
        "subs": {},              # pid -> [whiteIdx, ...]  (ordered, hidden play)
        "order": [],             # judging order: shuffled list of pids
        "flipped": 0,            # how many plays the czar has turned over
        "winner": None,          # pid who won the round (reveal phase)
        "endsAt": None,          # selection deadline (epoch seconds)
    }


def fresh_codenames():
    """A Codenames sub-state in its lobby form."""
    return {
        "phase": "lobby",       # lobby | clue | guess | play (party) | gameover
        "mode": "online",       # online | party
        "words": [],            # 25 codename words
        "key": [],              # 25 of red|blue|innocent|assassin  (secret)
        "covers": [None] * 25,   # 25 of None|red|blue|innocent|assassin (placed)
        "turn": "red",          # whose turn it is
        "starter": "red",       # who went first (has 9 words)
        "spymasters": {"red": None, "blue": None},   # pid per team
        "clue": None,           # {"word", "number", "team"}
        "guessesLeft": 0,
        "guessedThisTurn": 0,
        "log": [],              # [{"team", "kind", "text"}]
        "winner": None,         # "red" | "blue"
        "winReason": None,      # "words" | "assassin"
    }


WII_ITEMS = [
    {"id": "playground", "name": "Cursor Playground", "emoji": "\N{DIRECT HIT}"},
    {"id": "targets", "name": "Target Practice", "emoji": "\N{BULLSEYE}"},
    {"id": "paint", "name": "Motion Paint", "emoji": "\N{ARTIST PALETTE}"},
    {"id": "balance", "name": "Balance the Ball", "emoji": "\N{SOCCER BALL}"},
    {"id": "conductor", "name": "Air Conductor", "emoji": "\N{MUSICAL NOTE}"},
    {"id": "swat", "name": "Fly Swatter", "emoji": "\N{BUG}"},
]


def fresh_wii():
    """Wii Sandbox sub-state. Pointers are pid-keyed and updated at ~20 Hz by
    the phones; they are NOT broadcast (the host polls /state fast instead)."""
    return {
        "sens": 1.8,            # pointer sensitivity (higher = less tilt to reach an edge)
        "pointers": {},          # pid -> {x,y,a,b,aSeq,bSeq,stage,last,buf}
        "selection": None,       # {"pid","name","item","seq"} — last menu pick
        "selSeq": 0,
        "items": WII_ITEMS,
        "targets": None,         # Target Practice round, see _wii_targets_new()
    }


WII_TARGETS_PER_PLAYER = 6
_wii_target_seq = [0]


def _wii_targets_new():
    """Deal a fresh Target Practice round: every phone currently past
    calibration (stage 'ready') gets its own colour's set of targets, placed
    at random on the shared stage. Everyone races to capture only their own."""
    w = state["wii"]
    pids = [pid for pid, pt in w["pointers"].items() if pt.get("stage") == "ready"]

    def _mk_target():
        _wii_target_seq[0] += 1
        return {"id": _wii_target_seq[0],
                "x": round(random.uniform(0.08, 0.92), 3),
                "y": round(random.uniform(0.16, 0.92), 3),
                "hit": False}

    w["targets"] = {
        "pids": pids,
        "byPid": {pid: [_mk_target() for _ in range(WII_TARGETS_PER_PLAYER)] for pid in pids},
        "doneOrder": [],          # pids, in the order they cleared their targets
        "startedAt": time.time(),
    }


# aim samples are timestamped and buffered so the host can render the cursor a
# little in the past, interpolating between real samples for smooth motion
# instead of chasing one stale point. ~1 s of history is plenty.
WII_BUF_SECS = 1.0
WII_BUF_MAX = 48


def fresh_imposter():
    """Imposter sub-state in its lobby form. pid-keyed dicts use int keys."""
    return {
        "phase": "lobby",       # lobby | clue | review | vote | guess | gameover
        "imposterCount": 1,     # how many imposters this game
        "category": None,
        "word": None,           # secret word (never sent to imposters)
        "round": 0,
        "roles": {},            # pid -> "imposter" | "crew"
        "out": {},              # pid -> "guessed_wrong" | "voted"  (eliminated)
        "order": [],            # pids, turn order for the current clue round
        "turnIndex": 0,         # position in `order`
        "clues": [],            # [{"round","pid","name","color","text"}]  all rounds
        "votes": {},            # voterPid -> targetPid
        "voteResult": None,     # {"pid","name","wasImposter"} after a vote resolves
        "guesser": None,        # pid of the imposter currently guessing
        "guessText": None,      # what they typed / said
        "lastGuess": None,      # {"name","text","correct"} for the big screen
        "winner": None,         # "crew" | "imposters"
    }


def fresh_mafia():
    """Mafia sub-state in its lobby form. pid-keyed dicts use int keys.

    One player is the `moderator` — they hold no card and drive the night/day
    cycle from their phone (the laptop can stand in via the host cookie).
    """
    return {
        "phase": "lobby",     # lobby | reveal | night | day | gameover
        "counts": {"mafia": 1, "sheriff": 1, "doctor": 1},
        "moderator": None,    # pid
        "roles": {},          # pid -> "mafia" | "sheriff" | "doctor" | "civilian"
        "alive": {},          # pid -> bool  (carded players only)
        "round": 0,           # night number, 1-based once the game starts
        "lastNight": None,    # {"killed": [pid, ...]} — the most recent night
        "deaths": [],         # [{"pid","name","color","role","how","round"}]
        "votes": {},          # voterPid -> targetPid   (0 = abstain)
        "voteResult": None,   # {"tie":True} | {"nolynch":True}
                              #   | {"pid","name","color","role"}
        "winner": None,       # "town" | "mafia"
    }


state = {
    "game": None,             # None = menu screen, else a GAMES id
    "phase": "lobby",         # tap-race phase: lobby | playing | over
    "goal": GOAL,
    "winner": None,           # tap-race winner pid
    "players": {},            # pid -> {name, color, taps, score, team, last_seen}
    "scat": fresh_scat(),
    "taboo": fresh_taboo(),
    "blackbox": fresh_blackbox(),
    "codenames": fresh_codenames(),
    "wii": fresh_wii(),
    "imposter": fresh_imposter(),
    "mafia": fresh_mafia(),
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


# ------------------------------------------------------------------- BlackBox --
def _bb_ingame_pids():
    """Players in the current BlackBox game, in table (join) order."""
    return sorted(state["blackbox"]["scores"].keys())


def _bb_non_czar_pids():
    bb = state["blackbox"]
    return [p for p in _bb_ingame_pids() if p != bb["czar"]]


def _bb_draw_white(n):
    bb = state["blackbox"]
    out = []
    for _ in range(n):
        if bb["whitePos"] >= len(bb["whiteDeck"]):
            bb["whiteDeck"] = blackbox.shuffled_white_deck()
            bb["whitePos"] = 0
        out.append(bb["whiteDeck"][bb["whitePos"]])
        bb["whitePos"] += 1
    return out


def _bb_draw_black():
    bb = state["blackbox"]
    if bb["blackPos"] >= len(bb["blackDeck"]):
        bb["blackDeck"] = blackbox.shuffled_black_deck()
        bb["blackPos"] = 0
    idx = bb["blackDeck"][bb["blackPos"]]
    bb["blackPos"] += 1
    return dict(blackbox.BLACK[idx])


def _bb_ensure_player(pid):
    """Give a mid-game joiner a score slot and a fresh hand."""
    bb = state["blackbox"]
    if pid not in bb["scores"]:
        bb["scores"][pid] = 0
    if pid not in bb["hands"]:
        bb["hands"][pid] = _bb_draw_white(blackbox.HAND_SIZE)


def _bb_refill_hands():
    bb = state["blackbox"]
    for hand in bb["hands"].values():
        if len(hand) < blackbox.HAND_SIZE:
            hand.extend(_bb_draw_white(blackbox.HAND_SIZE - len(hand)))
        del hand[blackbox.HAND_SIZE:]      # trim any leftover "draw N" extras


def _bb_next_czar():
    bb = state["blackbox"]
    pids = _bb_ingame_pids()
    if not pids:
        return None
    if bb["czar"] not in pids:
        return pids[bb["round"] % len(pids)]
    return pids[(pids.index(bb["czar"]) + 1) % len(pids)]


def _bb_begin_round():
    bb = state["blackbox"]
    for pid in list(state["players"]):      # pull in anyone who joined
        _bb_ensure_player(pid)
    bb["round"] += 1
    bb["czar"] = _bb_next_czar()
    bb["black"] = _bb_draw_black()
    bb["subs"] = {}
    bb["order"] = []
    bb["flipped"] = 0
    bb["winner"] = None
    extra = bb["black"].get("draw", 0)
    if extra:
        for pid, hand in bb["hands"].items():
            if pid != bb["czar"]:
                hand.extend(_bb_draw_white(extra))
    bb["phase"] = "select"
    bb["endsAt"] = time.time() + bb["selectSeconds"]


def _bb_all_submitted():
    bb = state["blackbox"]
    need = [p for p in _bb_non_czar_pids()
            if state["players"].get(p, {}).get("connected", True)]
    return bool(need) and all(p in bb["subs"] for p in need)


def _bb_to_judge():
    """Move from selection to judging. False if there's nothing to judge."""
    bb = state["blackbox"]
    if len(bb["subs"]) < 1:
        return False
    order = list(bb["subs"].keys())
    random.shuffle(order)
    bb["order"] = order
    bb["flipped"] = 0
    bb["phase"] = "judge"
    bb["endsAt"] = None
    return True


def _bb_award(winner_pid):
    bb = state["blackbox"]
    bb["winner"] = winner_pid
    bb["scores"][winner_pid] = bb["scores"].get(winner_pid, 0) + 1
    for pid, cards in bb["subs"].items():
        hand = bb["hands"].get(pid, [])
        for c in cards:
            if c in hand:
                hand.remove(c)
    _bb_refill_hands()
    bb["phase"] = "gameover" if bb["scores"][winner_pid] >= bb["target"] else "reveal"


def _bb_fill_html(black_text, whites):
    """fill_prompt as HTML-safe text with the answer(s) wrapped in <b>."""
    raw = blackbox.fill_prompt(black_text, whites, mark=True)
    out, esc = [], {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
    for ch in raw:
        if ch == blackbox.MARK_A:
            out.append("<b>")
        elif ch == blackbox.MARK_B:
            out.append("</b>")
        else:
            out.append(esc.get(ch, ch))
    return "".join(out)


def _bb_plays(reveal_names):
    """The plays on the table for the judge / reveal screens."""
    bb = state["blackbox"]
    out = []
    for slot, pid in enumerate(bb["order"]):
        face_up = reveal_names or slot < bb["flipped"]
        whites = [blackbox.white_text(i) for i in bb["subs"].get(pid, [])]
        p = state["players"].get(pid, {})
        out.append({
            "slot": slot,
            "cards": whites if face_up else [],
            "filled": blackbox.fill_prompt(bb["black"]["text"], whites) if face_up else None,
            "filledHtml": _bb_fill_html(bb["black"]["text"], whites) if face_up else None,
            "name": p.get("name") if reveal_names else None,
            "color": p.get("color") if reveal_names else None,
            "connected": p.get("connected", True) if reveal_names else None,
            "win": reveal_names and pid == bb["winner"],
        })
    return out


def _blackbox_public(for_pid=None):
    bb = state["blackbox"]
    order = _bb_ingame_pids()
    roster = [{"pid": p, "name": state["players"].get(p, {}).get("name", "?"),
               "color": state["players"].get(p, {}).get("color", "#888"),
               "score": bb["scores"].get(p, 0),
               "connected": state["players"].get(p, {}).get("connected", True),
               "czar": p == bb["czar"]}
              for p in order]
    out = {
        "phase": bb["phase"],
        "target": bb["target"],
        "selectSeconds": bb["selectSeconds"],
        "round": bb["round"],
        "czar": bb["czar"],
        "players": roster,
        "playerCount": len(state["players"]),
        "black": bb["black"],
        "serverNow": time.time(),
        "endsAt": bb["endsAt"],
    }
    if bb["phase"] == "select":
        need = [p for p in _bb_non_czar_pids()
                if state["players"].get(p, {}).get("connected", True)]
        out["needed"] = len(need)
        out["submitted"] = sum(1 for p in need if p in bb["subs"])
        out["submittedPids"] = [p for p in bb["subs"]]
    if bb["phase"] == "judge":
        out["plays"] = _bb_plays(reveal_names=False)
        out["flipped"] = bb["flipped"]
        out["playCount"] = len(bb["order"])
    if bb["phase"] in ("reveal", "gameover"):
        out["plays"] = _bb_plays(reveal_names=True)
        out["winnerPid"] = bb["winner"]
        w = state["players"].get(bb["winner"], {})
        out["winnerName"] = w.get("name")
    if bb["phase"] == "gameover":
        top = max(bb["scores"].items(), key=lambda kv: kv[1], default=(None, 0))
        out["champPid"] = top[0]
        out["champName"] = state["players"].get(top[0], {}).get("name")
    # this phone's private view: its hand + what it has played
    if for_pid is not None and for_pid in bb["hands"]:
        out["hand"] = [{"i": slot, "text": blackbox.white_text(idx)}
                       for slot, idx in enumerate(bb["hands"][for_pid])]
        if for_pid in bb["subs"]:
            played = bb["subs"][for_pid]
            out["mySub"] = [blackbox.white_text(i) for i in played]
        out["isCzar"] = for_pid == bb["czar"]
    return out


# ------------------------------------------------------------------- Codenames --
def _cn_counts():
    """Team words still hidden."""
    cn = state["codenames"]
    left = {"red": 0, "blue": 0}
    for i, k in enumerate(cn["key"]):
        if k in ("red", "blue") and cn["covers"][i] is None:
            left[k] += 1
    return left


def _cn_target(team):
    return codenames.STARTER_COUNT if team == state["codenames"]["starter"] \
        else codenames.SECOND_COUNT


def _cn_is_spy(pid):
    sm = state["codenames"]["spymasters"]
    return pid is not None and pid in (sm["red"], sm["blue"])


def _cn_spy_team(pid):
    sm = state["codenames"]["spymasters"]
    return "red" if sm["red"] == pid else ("blue" if sm["blue"] == pid else None)


def _cn_end_turn():
    cn = state["codenames"]
    cn["turn"] = "blue" if cn["turn"] == "red" else "red"
    cn["clue"] = None
    cn["guessesLeft"] = 0
    cn["guessedThisTurn"] = 0
    cn["phase"] = "clue"


def _cn_check_win():
    """Word-count win. Returns True if the game just ended."""
    cn = state["codenames"]
    left = _cn_counts()
    for t in ("red", "blue"):
        if left[t] == 0:
            cn["winner"] = t
            cn["winReason"] = "words"
            cn["phase"] = "gameover"
            return True
    return False


def _cn_reveal(i, guessing, forced_cover=None):
    """Uncover card i. `guessing` is the team that touched it. In party mode the
    spymaster phone passes forced_cover (what was actually guessed at the table);
    online mode uses the real key colour."""
    cn = state["codenames"]
    if not (0 <= i < 25) or cn["covers"][i] is not None:
        return
    actual = forced_cover if forced_cover else cn["key"][i]
    cn["covers"][i] = actual
    word = cn["words"][i]
    cn["log"].append({"team": guessing, "kind": actual, "text": word})

    if actual == "assassin":
        cn["winner"] = "blue" if guessing == "red" else "red"
        cn["winReason"] = "assassin"
        cn["phase"] = "gameover"
        return
    if _cn_check_win():
        return
    if cn["mode"] == "party":
        return                        # party phone drives turns manually
    if actual == guessing:
        cn["guessedThisTurn"] += 1
        cn["guessesLeft"] -= 1
        if cn["guessesLeft"] <= 0:
            _cn_end_turn()
    else:
        _cn_end_turn()                # innocent or the other team -> turn over


def _codenames_public(for_pid=None):
    cn = state["codenames"]
    is_spy = _cn_is_spy(for_pid)
    show_key = bool(cn["key"]) and (cn["mode"] == "party" or is_spy
                                    or cn["phase"] == "gameover")
    teams = {"red": [], "blue": []}
    for pid, p in state["players"].items():
        t = p.get("team")
        if t in ("red", "blue"):
            teams[t].append({
                "pid": pid, "name": p["name"],
                "spymaster": cn["spymasters"][t] == pid,
                "connected": p.get("connected", True),
            })
    left = _cn_counts()
    out = {
        "phase": cn["phase"], "mode": cn["mode"],
        "words": cn["words"], "covers": cn["covers"],
        "turn": cn["turn"], "starter": cn["starter"],
        "clue": cn["clue"], "guessesLeft": cn["guessesLeft"],
        "guessedThisTurn": cn["guessedThisTurn"],
        "left": left,
        "target": {"red": _cn_target("red"), "blue": _cn_target("blue")} if cn["key"] else None,
        "teams": teams,
        "spymasters": {t: (state["players"].get(cn["spymasters"][t], {}).get("name")
                           if cn["spymasters"][t] else None) for t in ("red", "blue")},
        "log": cn["log"][-8:],
        "winner": cn["winner"], "winReason": cn["winReason"],
        "playerCount": len(state["players"]),
    }
    if show_key:
        out["key"] = cn["key"]
    if for_pid is not None:
        p = state["players"].get(for_pid, {})
        yt = p.get("team")
        out["youTeam"] = yt if yt in ("red", "blue") else None
        spy_team = _cn_spy_team(for_pid)
        out["youSpymaster"] = spy_team
        if cn["mode"] == "party":
            out["canControl"] = True
        else:
            out["canClue"] = cn["phase"] == "clue" and spy_team == cn["turn"]
            out["canGuess"] = (cn["phase"] == "guess" and yt == cn["turn"]
                               and not is_spy)
    return out


# ------------------------------------------------------------- Wii Sandbox --
def _wii_public(for_pid=None):
    w = state["wii"]
    now = time.time()
    pointers = []
    for pid, pt in w["pointers"].items():
        p = state["players"].get(pid, {})
        pointers.append({
            "pid": pid, "name": p.get("name", "?"), "color": p.get("color", "#888"),
            "x": pt.get("x", 0.5), "y": pt.get("y", 0.5),
            "a": pt.get("a", False), "b": pt.get("b", False),
            "aSeq": pt.get("aSeq", 0), "bSeq": pt.get("bSeq", 0),
            "stage": pt.get("stage", "verify"),
            "live": (now - pt.get("last", 0)) < 2.0,
            # timestamped aim history for host-side interpolation
            "samples": [[round(t, 3), round(sx, 4), round(sy, 4)]
                        for t, sx, sy in pt.get("buf", [])],
        })
    out = {
        "sens": w["sens"],
        "items": w["items"],
        "pointers": pointers,
        "selection": w["selection"],
        "playerCount": len(state["players"]),
        "now": now,             # server clock, so the host can build one timeline
    }
    if for_pid is not None:
        pt = w["pointers"].get(for_pid)
        out["youStage"] = pt.get("stage", "verify") if pt else "verify"

    tg = w.get("targets")
    if tg:
        def _row(pid):
            p = state["players"].get(pid, {})
            return {"pid": pid, "name": p.get("name", "?"), "color": p.get("color", "#888"),
                    "targets": tg["byPid"].get(pid, []), "done": pid in tg["doneOrder"]}
        out["targets"] = {
            "players": [_row(pid) for pid in tg["pids"]],
            "doneOrder": [_row(pid) for pid in tg["doneOrder"]],
        }
        if for_pid is not None and for_pid in tg["byPid"]:
            mine = tg["byPid"][for_pid]
            out["myTargets"] = {"left": sum(1 for t in mine if not t["hit"]),
                                 "total": len(mine), "done": for_pid in tg["doneOrder"]}
    return out


# ------------------------------------------------------------ imposter public ---
def _imp_active_pids():
    im = state["imposter"]
    return [pid for pid in im["roles"]
            if pid in state["players"] and pid not in im["out"]]


def _imp_public(for_pid=None):
    im = state["imposter"]
    players = state["players"]
    active = _imp_active_pids()
    imps_in = [pid for pid in active if im["roles"][pid] == "imposter"]

    roster = []
    for pid, p in players.items():
        role = im["roles"].get(pid)
        row = {
            "pid": pid, "name": p["name"], "color": p["color"],
            "connected": p.get("connected", True),
            "inGame": role is not None,
            "out": im["out"].get(pid),          # None | guessed_wrong | voted
        }
        if im["phase"] == "gameover" and role:
            row["role"] = role
        roster.append(row)
    roster.sort(key=lambda r: r["pid"])

    out = {
        "phase": im["phase"],
        "imposterCount": im["imposterCount"],
        "imposterMax": max(1, len(players) - 1),
        "category": im["category"],
        "round": im["round"],
        "roster": roster,
        "clues": im["clues"],                   # every round, chronological
        "playerCount": len(players),
        "activeCount": len(active),
        "impostersLeft": len(imps_in),
        "voteResult": im["voteResult"],
        "lastGuess": im["lastGuess"],
    }

    if im["phase"] == "clue":
        turn_pid = (im["order"][im["turnIndex"]]
                    if im["turnIndex"] < len(im["order"]) else None)
        tp = players.get(turn_pid, {})
        out["turnPid"] = turn_pid
        out["turnName"] = tp.get("name")
        out["turnColor"] = tp.get("color")
        out["cluedThisRound"] = sum(1 for c in im["clues"] if c["round"] == im["round"])
        out["clueTargetCount"] = len(im["order"])
        out["order"] = [{
            "pid": pid,
            "name": players.get(pid, {}).get("name", "?"),
            "color": players.get(pid, {}).get("color", "#888"),
            "done": any(c["round"] == im["round"] and c["pid"] == pid
                        for c in im["clues"]),
        } for pid in im["order"]]

    if im["phase"] == "vote":
        out["candidates"] = [
            {"pid": pid, "name": players[pid]["name"], "color": players[pid]["color"]}
            for pid in active]
        tally = {}
        for t in im["votes"].values():
            tally[t] = tally.get(t, 0) + 1
        out["tally"] = tally
        out["votesIn"] = len(im["votes"])
        out["votersNeeded"] = len(active)

    if im["phase"] == "guess":
        gp = players.get(im["guesser"], {})
        out["guesserPid"] = im["guesser"]
        out["guesserName"] = gp.get("name")
        out["guesserColor"] = gp.get("color")
        out["guessText"] = im["guessText"]

    if im["phase"] == "gameover":
        out["winner"] = im["winner"]
        out["word"] = im["word"]

    if for_pid is not None:
        role = im["roles"].get(for_pid)
        out["youInGame"] = role is not None
        out["youAreImposter"] = role == "imposter"
        out["youAreOut"] = im["out"].get(for_pid)
        out["yourWord"] = im["word"] if role == "crew" else None
        if im["phase"] == "clue":
            out["yourTurn"] = (
                role is not None and for_pid not in im["out"]
                and im["turnIndex"] < len(im["order"])
                and im["order"][im["turnIndex"]] == for_pid)
        if im["phase"] == "vote":
            out["yourVote"] = im["votes"].get(for_pid)
        if im["phase"] == "guess":
            out["youAreGuessing"] = im["guesser"] == for_pid
    return out


def public_state(for_pid=None):
    """State shaped for the clients (players as a sorted list, no timestamps).
    `for_pid` (set on a /state?pid= poll) adds that player's own private view."""
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
        "blackbox": _blackbox_public(for_pid) if state["game"] == "blackbox" else None,
        "codenames": _codenames_public(for_pid) if state["game"] == "codenames" else None,
        "wii": _wii_public(for_pid) if state["game"] == "wii-sandbox" else None,
        "imposter": _imp_public(for_pid) if state["game"] == "imposter" else None,
        "mafia": _mafia_public(for_pid) if state["game"] == "mafia" else None,
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

            # BlackBox: selection time is up -> judge with whatever's in, or
            # (nothing played) redeal the round.
            bb = state["blackbox"]
            if (state["game"] == "blackbox" and bb["phase"] == "select"
                    and bb["endsAt"] and time.time() >= bb["endsAt"]):
                if not _bb_to_judge():
                    _bb_begin_round()
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
        elif state["game"] == "codenames" and state["codenames"]["mode"] == "online":
            r = sum(1 for p in state["players"].values() if p.get("team") == "red")
            b = sum(1 for p in state["players"].values() if p.get("team") == "blue")
            team = "red" if r <= b else "blue"
        token = secrets.token_urlsafe(9)   # phone keeps this to reconnect as itself
        state["players"][pid] = {
            "name": name, "color": color, "taps": 0, "score": 0, "team": team,
            "last_seen": time.time(), "token": token, "connected": True,
        }
        if state["game"] == "blackbox" and state["blackbox"]["phase"] != "lobby":
            _bb_ensure_player(pid)      # deal a mid-game arrival straight in
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

        keepbb = state["blackbox"]
        state["blackbox"] = fresh_blackbox()
        state["blackbox"]["target"] = keepbb["target"]
        state["blackbox"]["selectSeconds"] = keepbb["selectSeconds"]

        keepcn = state["codenames"]
        state["codenames"] = fresh_codenames()
        state["codenames"]["mode"] = keepcn["mode"]

        keepw = state["wii"]
        state["wii"] = fresh_wii()
        state["wii"]["sens"] = keepw["sens"]

        keepim = state["imposter"]
        state["imposter"] = fresh_imposter()
        state["imposter"]["imposterCount"] = keepim["imposterCount"]

        keepmf = state["mafia"]
        state["mafia"] = fresh_mafia()
        state["mafia"]["counts"] = dict(keepmf["counts"])
        if keepmf["moderator"] in state["players"]:
            state["mafia"]["moderator"] = keepmf["moderator"]

        if game == "taboo":
            _taboo_autoteam()
        elif game == "codenames":
            _cn_autoteam()
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


def _cn_autoteam():
    """Split players between 'red' and 'blue' for Codenames (online mode)."""
    pids = sorted(state["players"])
    for i, pid in enumerate(pids):
        state["players"][pid]["team"] = "red" if i % 2 == 0 else "blue"


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


def do_blackbox(pid, action, data, is_host=False):
    """Handle a blackbox* action. Caller holds _lock."""
    bb = state["blackbox"]

    if action == "blackboxSet":
        key, value = data.get("key"), data.get("value")
        try:
            if key == "target":
                bb["target"] = min(15, max(3, int(value)))
            elif key == "selectSeconds":
                bb["selectSeconds"] = min(180, max(20, int(value)))
            else:
                return {"ok": False, "error": "bad_key"}
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_value"}
        broadcast()
        return {"ok": True}

    if action == "blackboxStart":
        if bb["phase"] not in ("lobby", "gameover"):
            return {"ok": True}
        pids = sorted(state["players"])
        if len(pids) < 3:
            return {"ok": False, "error": "need_3"}
        bb["scores"] = {p: 0 for p in pids}
        bb["hands"] = {}
        bb["whiteDeck"] = blackbox.shuffled_white_deck()
        bb["whitePos"] = 0
        bb["blackDeck"] = blackbox.shuffled_black_deck()
        bb["blackPos"] = 0
        for p in pids:
            bb["hands"][p] = _bb_draw_white(blackbox.HAND_SIZE)
        bb["czar"] = None
        bb["round"] = 0
        _bb_begin_round()
        broadcast()
        return {"ok": True}

    if action == "blackboxNewGame":
        if bb["phase"] == "gameover":
            bb["phase"] = "lobby"
            broadcast()
        return {"ok": True}

    if action == "blackboxEndGame":
        if bb["phase"] in ("select", "judge", "reveal"):
            bb["phase"] = "gameover"
            broadcast()
        return {"ok": True}

    # round-flow actions: the Card Czar drives them (host may step in too)
    may_run = is_host or pid == bb["czar"]

    if action == "blackboxNext":
        if bb["phase"] == "reveal" and may_run:
            _bb_begin_round()
            broadcast()
        return {"ok": True}

    if action == "blackboxSkip":
        if bb["phase"] in ("select", "judge") and may_run:
            _bb_begin_round()
            broadcast()
        return {"ok": True}

    if action == "blackboxFlip":
        if bb["phase"] == "judge" and may_run:
            bb["flipped"] = min(bb["flipped"] + 1, len(bb["order"]))
            broadcast()
        return {"ok": True}

    if action == "blackboxPick":
        if bb["phase"] != "judge" or not may_run:
            return {"ok": False, "error": "not_czar"}
        try:
            slot = int(data.get("choice"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_choice"}
        if not (0 <= slot < len(bb["order"])):
            return {"ok": False, "error": "bad_choice"}
        _bb_award(bb["order"][slot])
        broadcast()
        return {"ok": True}

    # -- remaining actions need a real player --
    p = state["players"].get(pid)
    if not p:
        return {"ok": False, "error": "not_joined"}
    p["last_seen"] = time.time()
    p["connected"] = True
    _bb_ensure_player(pid)

    if action == "blackboxPlay":
        if bb["phase"] != "select" or pid == bb["czar"]:
            return {"ok": False, "error": "cant_play"}
        want = (bb["black"] or {}).get("pick", 1)
        raw = data.get("cards")
        if not isinstance(raw, list) or len(raw) != want:
            return {"ok": False, "error": "bad_count"}
        hand = bb["hands"].get(pid, [])
        try:
            idxs = [int(x) for x in raw]
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_cards"}
        if len(set(idxs)) != len(idxs) or any(not (0 <= i < len(hand)) for i in idxs):
            return {"ok": False, "error": "bad_cards"}
        bb["subs"][pid] = [hand[i] for i in idxs]
        if _bb_all_submitted():
            _bb_to_judge()
        broadcast()
        return {"ok": True}

    if action == "blackboxUnplay":
        if bb["phase"] == "select" and pid in bb["subs"]:
            del bb["subs"][pid]
            broadcast()
        return {"ok": True}

    return {"ok": False, "error": "unknown_action"}


_CN_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]{0,23}$")


def do_codenames(pid, action, data, is_host=False):
    """Handle a cn* action. Caller holds _lock."""
    cn = state["codenames"]

    if action == "cnMode":
        if cn["phase"] == "lobby":
            m = data.get("mode")
            if m in ("online", "party"):
                cn["mode"] = m
                broadcast()
        return {"ok": True}

    if action == "cnAutoTeam":
        if cn["phase"] == "lobby":
            _cn_autoteam()
            broadcast()
        return {"ok": True}

    if action == "cnStart":
        if cn["phase"] not in ("lobby", "gameover"):
            return {"ok": True}
        if cn["mode"] == "online":
            reds = [p for p in state["players"].values() if p.get("team") == "red"]
            blues = [p for p in state["players"].values() if p.get("team") == "blue"]
            if len(reds) < 2 or len(blues) < 2:
                return {"ok": False, "error": "need_teams"}
            if cn["spymasters"]["red"] not in [pd for pd, p in state["players"].items() if p.get("team") == "red"]:
                return {"ok": False, "error": "need_spy_red"}
            if cn["spymasters"]["blue"] not in [pd for pd, p in state["players"].items() if p.get("team") == "blue"]:
                return {"ok": False, "error": "need_spy_blue"}
        words, key, starter = codenames.deal_board()
        cn["words"], cn["key"], cn["starter"], cn["turn"] = words, key, starter, starter
        cn["covers"] = [None] * 25
        cn["clue"] = None
        cn["guessesLeft"] = 0
        cn["guessedThisTurn"] = 0
        cn["log"] = []
        cn["winner"] = None
        cn["winReason"] = None
        cn["phase"] = "play" if cn["mode"] == "party" else "clue"
        broadcast()
        return {"ok": True}

    if action == "cnNewGame":
        if cn["phase"] == "gameover":
            words, key, starter = codenames.deal_board()
            cn["words"], cn["key"], cn["starter"], cn["turn"] = words, key, starter, starter
            cn["covers"] = [None] * 25
            cn["clue"] = None
            cn["guessesLeft"] = cn["guessedThisTurn"] = 0
            cn["log"] = []
            cn["winner"] = cn["winReason"] = None
            cn["phase"] = "play" if cn["mode"] == "party" else "clue"
            broadcast()
        return {"ok": True}

    if action == "cnLobby":
        st = fresh_codenames()
        st["mode"] = cn["mode"]
        state["codenames"] = st
        broadcast()
        return {"ok": True}

    # ---- player actions ----
    p = state["players"].get(pid)
    if not p:
        return {"ok": False, "error": "not_joined"}
    p["last_seen"] = time.time()
    p["connected"] = True

    if action == "cnTeam":
        if cn["phase"] in ("lobby", "gameover"):
            t = data.get("team")
            if t in ("red", "blue"):
                if cn["spymasters"].get(p.get("team")) == pid:
                    cn["spymasters"][p["team"]] = None       # leaving a spy seat
                p["team"] = t
                broadcast()
        return {"ok": True}

    if action == "cnSpymaster":
        if cn["phase"] in ("lobby", "gameover"):
            t = p.get("team")
            if t in ("red", "blue"):
                cur = cn["spymasters"][t]
                cn["spymasters"][t] = None if cur == pid else pid
                broadcast()
        return {"ok": True}

    if action == "cnClue":
        if cn["mode"] != "online" or cn["phase"] != "clue":
            return {"ok": False, "error": "not_now"}
        if _cn_spy_team(pid) != cn["turn"]:
            return {"ok": False, "error": "not_your_turn"}
        word = str(data.get("word", "")).strip()
        if not _CN_WORD_RE.match(word):
            return {"ok": False, "error": "bad_word"}
        try:
            num = int(data.get("number"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_number"}
        num = max(0, min(9, num))
        cn["clue"] = {"word": word.upper(), "number": num, "team": cn["turn"]}
        cn["guessesLeft"] = 25 if num == 0 else num + 1
        cn["guessedThisTurn"] = 0
        cn["phase"] = "guess"
        cn["log"].append({"team": cn["turn"], "kind": "clue",
                          "text": f"{word.upper()} {num}"})
        broadcast()
        return {"ok": True}

    if action == "cnGuess":
        try:
            i = int(data.get("i"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_card"}
        if cn["phase"] == "gameover":
            return {"ok": True}
        if cn["mode"] == "party":
            cover = data.get("cover")
            if cover not in ("red", "blue", "innocent", "assassin"):
                return {"ok": False, "error": "bad_cover"}
            # whose guess this was: the card's own team, else the active turn
            guessing = cover if cover in ("red", "blue") else cn["turn"]
            _cn_reveal(i, guessing, forced_cover=cover)
            broadcast()
            return {"ok": True}
        # online
        if cn["phase"] != "guess" or p.get("team") != cn["turn"] or _cn_is_spy(pid):
            return {"ok": False, "error": "cant_guess"}
        _cn_reveal(i, cn["turn"])
        broadcast()
        return {"ok": True}

    if action == "cnUncover":       # party phone fat-finger fix
        if cn["mode"] == "party":
            try:
                i = int(data.get("i"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad_card"}
            if 0 <= i < 25 and cn["covers"][i] is not None and cn["phase"] != "gameover":
                cn["covers"][i] = None
                broadcast()
        return {"ok": True}

    if action == "cnTurn":          # party phone: hand the turn to a team
        if cn["mode"] == "party" and cn["phase"] == "play":
            t = data.get("team")
            if t in ("red", "blue"):
                cn["turn"] = t
                broadcast()
        return {"ok": True}

    if action == "cnEndTurn":
        if cn["mode"] == "online" and cn["phase"] == "guess" \
                and p.get("team") == cn["turn"] and not _cn_is_spy(pid):
            if cn["guessedThisTurn"] < 1:
                return {"ok": False, "error": "guess_first"}
            _cn_end_turn()
            broadcast()
        return {"ok": True}

    return {"ok": False, "error": "unknown_action"}


def do_wii(pid, action, data, is_host=False):
    """Wii Sandbox. Caller holds _lock. wiiAim is high-frequency and does NOT
    broadcast — the host polls /state fast to pick pointers up."""
    w = state["wii"]

    if action == "wiiSens":
        try:
            w["sens"] = max(0.6, min(5.0, float(data.get("value"))))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad"}
        broadcast()
        return {"ok": True}

    if action == "wiiReset":
        for pt in w["pointers"].values():
            pt["stage"] = "verify"
        w["selection"] = None
        w["targets"] = None
        broadcast()
        return {"ok": True}

    if action == "wiiOpen":            # host: leave an item, back to the menu
        w["selection"] = None
        w["targets"] = None
        broadcast()
        return {"ok": True}

    if action == "wiiSelect":          # host detected an A-press over an item
        # (also (re)used by the host's in-activity "New targets" button, which
        # posts this again with the same pid/item to deal a fresh round)
        try:
            spid = int(data.get("pid"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad"}
        item = data.get("item")
        if any(it["id"] == item for it in w["items"]):
            w["selSeq"] += 1
            w["selection"] = {
                "pid": spid, "name": state["players"].get(spid, {}).get("name", "?"),
                "color": state["players"].get(spid, {}).get("color", "#888"),
                "item": item, "seq": w["selSeq"],
            }
            if item == "targets":
                _wii_targets_new()
            broadcast()
        return {"ok": True}

    if action == "wiiCapture":         # host detected an A-press over one of a player's own targets
        try:
            cpid = int(data.get("pid"))
            tid = int(data.get("id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad"}
        tg = w.get("targets")
        mine = tg["byPid"].get(cpid) if tg else None
        if mine:
            for t in mine:
                if t["id"] == tid:
                    t["hit"] = True
                    break
            if cpid not in tg["doneOrder"] and all(t["hit"] for t in mine):
                tg["doneOrder"].append(cpid)
            broadcast()
        return {"ok": True}

    # ---- per-phone actions ----
    p = state["players"].get(pid)
    if not p:
        return {"ok": False, "error": "not_joined"}
    pt = w["pointers"].setdefault(pid, {
        "x": 0.5, "y": 0.5, "a": False, "b": False,
        "aSeq": 0, "bSeq": 0, "stage": "verify", "last": 0.0, "buf": [],
    })
    p["last_seen"] = time.time()
    p["connected"] = True

    if action == "wiiStage":
        st = data.get("stage")
        if st in ("verify", "calibrate", "ready"):
            pt["stage"] = st
            broadcast()
        return {"ok": True}

    if action == "wiiAim":
        try:
            pt["x"] = max(0.0, min(1.0, float(data.get("x"))))
            pt["y"] = max(0.0, min(1.0, float(data.get("y"))))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad"}
        pt["a"] = bool(data.get("a"))
        pt["b"] = bool(data.get("b"))
        now = time.time()
        pt["last"] = now
        buf = pt.setdefault("buf", [])
        buf.append((now, pt["x"], pt["y"]))
        cutoff = now - WII_BUF_SECS
        while len(buf) > 2 and buf[0][0] < cutoff:
            buf.pop(0)
        if len(buf) > WII_BUF_MAX:
            del buf[:len(buf) - WII_BUF_MAX]
        return {"ok": True}          # deliberately no broadcast

    if action == "wiiBtn":
        btn, down = data.get("btn"), bool(data.get("down"))
        if btn in ("a", "b"):
            was = pt[btn]
            pt[btn] = down
            if down and not was:
                pt[btn + "Seq"] = pt.get(btn + "Seq", 0) + 1
            pt["last"] = time.time()
        return {"ok": True}          # no broadcast — host poll catches the seq bump

    return {"ok": False, "error": "unknown_action"}


# ------------------------------------------------------------- imposter actions ---
def _imp_fold_in_newcomers():
    """Anyone who joined after the deal comes in as crew for the next round."""
    im = state["imposter"]
    for pid in state["players"]:
        im["roles"].setdefault(pid, "crew")


def _imp_build_order():
    im = state["imposter"]
    pids = _imp_active_pids()
    random.shuffle(pids)
    im["order"] = pids
    im["turnIndex"] = 0


def _imp_start_game():
    im = state["imposter"]
    pids = list(state["players"])
    k = max(1, min(im["imposterCount"], max(1, len(pids) - 1)))
    im["imposterCount"] = k
    imps = set(random.sample(pids, k))
    im["roles"] = {pid: ("imposter" if pid in imps else "crew") for pid in pids}
    im["out"] = {}
    im["category"], im["word"] = imposter.pick_word()
    im["round"] = 1
    im["clues"] = []
    im["votes"] = {}
    im["voteResult"] = None
    im["guesser"] = None
    im["guessText"] = None
    im["lastGuess"] = None
    im["winner"] = None
    im["phase"] = "clue"
    _imp_build_order()


def _imp_advance_turn():
    im = state["imposter"]
    active = _imp_active_pids()
    im["turnIndex"] += 1
    while (im["turnIndex"] < len(im["order"])
           and im["order"][im["turnIndex"]] not in active):
        im["turnIndex"] += 1
    if im["turnIndex"] >= len(im["order"]):
        im["phase"] = "review"


def _imp_check_win():
    """End the game if it's decided. Returns True if it ended.

    Crew win once every imposter is out. Imposters win if every crew member is
    eliminated (or an imposter guesses the word — handled in impGuessJudge).
    """
    im = state["imposter"]
    active = _imp_active_pids()
    imps = [pid for pid in active if im["roles"][pid] == "imposter"]
    crew = [pid for pid in active if im["roles"][pid] == "crew"]
    if not imps:
        im["winner"], im["phase"] = "crew", "gameover"
        return True
    if not crew:
        im["winner"], im["phase"] = "imposters", "gameover"
        return True
    return False


def _imp_resolve_vote():
    im = state["imposter"]
    tally = {}
    for t in im["votes"].values():
        tally[t] = tally.get(t, 0) + 1
    if not tally:
        im["voteResult"] = None
        im["phase"] = "review"
        return
    top = max(tally.values())
    leaders = [pid for pid, c in tally.items() if c == top]
    if len(leaders) != 1:
        im["voteResult"] = {"tie": True}
        im["phase"] = "review"
        return
    victim = leaders[0]
    p = state["players"].get(victim, {})
    im["out"][victim] = "voted"
    im["voteResult"] = {
        "tie": False, "pid": victim, "name": p.get("name", "?"),
        "color": p.get("color", "#888"),
        "wasImposter": im["roles"].get(victim) == "imposter",
    }
    if not _imp_check_win():
        im["phase"] = "review"


def do_imposter(pid, action, data, is_host=False):
    """Imposter. Caller holds _lock. pid may be None for host actions."""
    im = state["imposter"]

    if action == "impSet":
        if data.get("key") == "imposterCount":
            try:
                im["imposterCount"] = max(1, min(int(data.get("value")), 5))
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad"}
            broadcast()
        return {"ok": True}

    if action == "impStart":
        if im["phase"] in ("lobby", "gameover"):
            if len(state["players"]) < 3:
                return {"ok": False, "error": "need_players"}
            _imp_start_game()
            broadcast()
        return {"ok": True}

    if action == "impLobby":
        keep = im["imposterCount"]
        state["imposter"] = fresh_imposter()
        state["imposter"]["imposterCount"] = keep
        broadcast()
        return {"ok": True}

    if action == "impEndGame":
        if im["winner"] is None and not _imp_check_win():
            im["winner"] = "imposters"    # host called it — imposters escaped
        im["phase"] = "gameover"
        broadcast()
        return {"ok": True}

    if action == "impNextRound":
        if im["phase"] == "review":
            _imp_fold_in_newcomers()
            im["round"] += 1
            im["voteResult"] = None
            im["phase"] = "clue"
            _imp_build_order()
            broadcast()
        return {"ok": True}

    if action == "impVoteStart":
        if im["phase"] == "review":
            im["phase"] = "vote"
            im["votes"] = {}
            im["voteResult"] = None
            broadcast()
        return {"ok": True}

    if action == "impVoteResolve":
        if im["phase"] == "vote":
            _imp_resolve_vote()
            broadcast()
        return {"ok": True}

    if action == "impSkip":
        if im["phase"] == "clue" and im["turnIndex"] < len(im["order"]):
            sk = im["order"][im["turnIndex"]]
            p = state["players"].get(sk, {})
            im["clues"].append({
                "round": im["round"], "pid": sk, "name": p.get("name", "?"),
                "color": p.get("color", "#888"), "text": "(skipped)",
            })
            _imp_advance_turn()
            broadcast()
        return {"ok": True}

    if action == "impGuessJudge":
        if im["phase"] != "guess" or im["guesser"] is None:
            return {"ok": True}
        correct = bool(data.get("correct"))
        gp = state["players"].get(im["guesser"], {})
        im["lastGuess"] = {"name": gp.get("name", "?"),
                           "text": im["guessText"], "correct": correct}
        if correct:
            im["winner"], im["phase"] = "imposters", "gameover"
        else:
            im["out"][im["guesser"]] = "guessed_wrong"
            im["guesser"] = None
            im["guessText"] = None
            if not _imp_check_win():
                im["phase"] = "review"
        broadcast()
        return {"ok": True}

    # ---- player actions ----
    p = state["players"].get(pid)
    if not p:
        return {"ok": False, "error": "not_joined"}
    p["last_seen"] = time.time()
    role = im["roles"].get(pid)
    if role is None or pid in im["out"]:
        return {"ok": False, "error": "not_in_game"}

    if action == "impClue":
        if im["phase"] != "clue":
            return {"ok": True}
        if (im["turnIndex"] >= len(im["order"])
                or im["order"][im["turnIndex"]] != pid):
            return {"ok": False, "error": "not_your_turn"}
        text = str(data.get("text", "")).strip()[:40]
        if not text:
            return {"ok": False, "error": "empty"}
        im["clues"].append({
            "round": im["round"], "pid": pid,
            "name": p["name"], "color": p["color"], "text": text,
        })
        _imp_advance_turn()
        broadcast()
        return {"ok": True}

    if action == "impVote":
        if im["phase"] != "vote":
            return {"ok": True}
        try:
            target = int(data.get("target"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_target"}
        if target == pid or target not in _imp_active_pids():
            return {"ok": False, "error": "bad_target"}
        im["votes"][pid] = target
        # auto-resolve once every still-in player has voted; otherwise the host
        # taps "Reveal the vote"
        if all(q in im["votes"] for q in _imp_active_pids()):
            _imp_resolve_vote()
        broadcast()
        return {"ok": True}

    if action == "impGuess":
        if im["phase"] != "review" or role != "imposter":
            return {"ok": False, "error": "cant_guess"}
        im["phase"] = "guess"
        im["guesser"] = pid
        im["guessText"] = None
        broadcast()
        return {"ok": True}

    if action == "impGuessSubmit":
        if im["phase"] != "guess" or im["guesser"] != pid:
            return {"ok": False, "error": "not_guessing"}
        im["guessText"] = str(data.get("text", "")).strip()[:40]
        broadcast()
        return {"ok": True}

    return {"ok": False, "error": "unknown_action"}


# --------------------------------------------------------------- mafia actions ---
def _mf_carded():
    """pids that were dealt a card and are still connected as players."""
    mf = state["mafia"]
    return [pid for pid in mf["roles"] if pid in state["players"]]


def _mf_alive():
    mf = state["mafia"]
    return [pid for pid in mf["roles"]
            if pid in state["players"] and mf["alive"].get(pid)]


def _mf_alive_counts():
    """(mafia still alive, town still alive)."""
    mf = state["mafia"]
    maf = town = 0
    for pid in _mf_alive():
        if mf["roles"][pid] == "mafia":
            maf += 1
        else:
            town += 1
    return maf, town


def _mf_check_win():
    """End the game if it's decided. Returns True when it just ended.

    Town wins once every Mafia is dead. Mafia win once they equal or outnumber
    the surviving town.
    """
    mf = state["mafia"]
    maf, town = _mf_alive_counts()
    if maf == 0:
        mf["winner"], mf["phase"] = "town", "gameover"
        return True
    if maf >= town:
        mf["winner"], mf["phase"] = "mafia", "gameover"
        return True
    return False


def _mf_kill(pid, how):
    """Mark a carded player dead and log it (their role becomes public)."""
    mf = state["mafia"]
    if not mf["alive"].get(pid):
        return
    mf["alive"][pid] = False
    p = state["players"].get(pid, {})
    mf["deaths"].append({
        "pid": pid, "name": p.get("name", "?"), "color": p.get("color", "#888"),
        "role": mf["roles"].get(pid), "how": how, "round": mf["round"],
    })


def _mf_resolve_vote():
    """Tally the day vote and eliminate the plurality pick (ties / an all-abstain
    room mean nobody is lynched)."""
    mf = state["mafia"]
    tally = {}
    for t in mf["votes"].values():
        if t:
            tally[t] = tally.get(t, 0) + 1
    if not tally:
        mf["voteResult"] = {"nolynch": True}
        return
    top = max(tally.values())
    leaders = [t for t, c in tally.items() if c == top]
    if len(leaders) != 1 or not mf["alive"].get(leaders[0]):
        mf["voteResult"] = {"tie": True}
        return
    victim = leaders[0]
    _mf_kill(victim, "voted")
    p = state["players"].get(victim, {})
    mf["voteResult"] = {"pid": victim, "name": p.get("name", "?"),
                        "color": p.get("color", "#888"),
                        "role": mf["roles"].get(victim)}
    _mf_check_win()


def _mf_start_game():
    mf = state["mafia"]
    carded = [pid for pid in state["players"] if pid != mf["moderator"]]
    mf["roles"] = mafia.deal_roles(carded, mf["counts"], random)
    mf["alive"] = {pid: True for pid in carded}
    mf["round"] = 0
    mf["lastNight"] = None
    mf["deaths"] = []
    mf["votes"] = {}
    mf["voteResult"] = None
    mf["winner"] = None
    mf["phase"] = "reveal"


def _mafia_public(for_pid=None):
    mf = state["mafia"]
    players = state["players"]
    mod = mf["moderator"]
    is_mod = for_pid is not None and for_pid == mod
    reveal_all = mf["phase"] == "gameover" or is_mod

    roster = []
    for pid, p in players.items():
        role = mf["roles"].get(pid)
        in_game = role is not None
        alive = mf["alive"].get(pid, False) if in_game else None
        row = {
            "pid": pid, "name": p["name"], "color": p["color"],
            "connected": p.get("connected", True),
            "isModerator": pid == mod,
            "inGame": in_game,
            "alive": alive,
        }
        # a role is public once its owner is dead, at game over, or to the mod
        if in_game and (reveal_all or alive is False):
            row["role"] = role
        roster.append(row)
    roster.sort(key=lambda r: r["pid"])

    out = {
        "phase": mf["phase"],
        "counts": mf["counts"],
        "moderatorPid": mod,
        "moderatorName": players.get(mod, {}).get("name") if mod else None,
        "round": mf["round"],
        "roster": roster,
        "playerCount": len(players),
        "cardedCount": len(_mf_carded()),
        "aliveCount": len(_mf_alive()),
        "deaths": [{"name": d["name"], "color": d["color"], "role": d["role"],
                    "how": d["how"], "round": d["round"]} for d in mf["deaths"]],
        "lastNight": None,
        "voteResult": mf["voteResult"],
    }

    if mf["lastNight"] is not None:
        killed = mf["lastNight"]["killed"]
        out["lastNight"] = {
            "names": [players.get(k, {}).get("name", "?") for k in killed],
            "roles": [mf["roles"].get(k) for k in killed],
        }

    if mf["phase"] == "day":
        alive = _mf_alive()
        out["votesIn"] = sum(1 for q in alive if q in mf["votes"])
        out["votersNeeded"] = len(alive)
        if reveal_all:
            tally = {}
            for t in mf["votes"].values():
                tally[str(t)] = tally.get(str(t), 0) + 1
            out["tally"] = tally

    if mf["phase"] == "gameover":
        out["winner"] = mf["winner"]

    if for_pid is not None:
        role = mf["roles"].get(for_pid)
        out["youInGame"] = role is not None
        out["youModerator"] = is_mod
        out["youRole"] = role
        out["youAlive"] = bool(role) and mf["alive"].get(for_pid, False)
        if role == "mafia":
            out["mafiaTeam"] = [players[q]["name"] for q in mf["roles"]
                                if mf["roles"][q] == "mafia" and q != for_pid
                                and q in players]
        if mf["phase"] == "day":
            out["yourVote"] = mf["votes"].get(for_pid)
            if mf["alive"].get(for_pid) and mf["voteResult"] is None:
                out["candidates"] = [
                    {"pid": q, "name": players[q]["name"], "color": players[q]["color"]}
                    for q in _mf_alive() if q != for_pid]
        if is_mod and mf["phase"] in ("night", "day"):
            out["nightTargets"] = [
                {"pid": q, "name": players[q]["name"], "color": players[q]["color"],
                 "role": mf["roles"].get(q)}
                for q in _mf_alive()]
    return out


def do_mafia(pid, action, data, is_host=False):
    """Mafia. Caller holds _lock. pid may be None for host-cookie actions.
    mafiaSetModerator is gated by HOST_ONLY (the laptop's moderator override);
    everything else accepts either the host cookie or the moderator's pid, so
    the moderator runs the whole game — setup included — from their phone, and
    the laptop can stand in."""
    mf = state["mafia"]
    may_run = is_host or (mf["moderator"] is not None and pid is not None
                          and pid == mf["moderator"])

    if action == "mafiaSet":
        if not may_run:
            return {"ok": False, "error": "not_allowed"}
        if mf["phase"] != "lobby":
            return {"ok": True}
        key = data.get("key")
        if key not in ("mafia", "sheriff", "doctor"):
            return {"ok": False, "error": "bad_key"}
        try:
            v = max(0, min(int(data.get("value")), 8))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_value"}
        if key == "mafia":
            v = max(1, v)
        mf["counts"][key] = v
        broadcast()
        return {"ok": True}

    if action == "mafiaSetModerator":
        if mf["phase"] != "lobby":
            return {"ok": True}
        try:
            m = int(data.get("pid"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad"}
        if m not in state["players"]:
            return {"ok": False, "error": "bad"}
        mf["moderator"] = None if mf["moderator"] == m else m
        broadcast()
        return {"ok": True}

    if action == "mafiaStart":
        if not may_run:
            return {"ok": False, "error": "not_allowed"}
        if mf["phase"] not in ("lobby", "gameover"):
            return {"ok": True}
        players = list(state["players"])
        if mf["moderator"] not in players:
            return {"ok": False, "error": "need_moderator"}
        carded = [p for p in players if p != mf["moderator"]]
        n = len(carded)
        if n < 4:
            return {"ok": False, "error": "need_players"}
        c = mf["counts"]
        if c["mafia"] < 1 or c["mafia"] + c["sheriff"] + c["doctor"] > n:
            return {"ok": False, "error": "bad_counts"}
        if c["mafia"] * 2 >= n:
            return {"ok": False, "error": "too_many_mafia"}
        _mf_start_game()
        broadcast()
        return {"ok": True}

    if action == "mafiaLobby":
        if not may_run:
            return {"ok": False, "error": "not_allowed"}
        keep = dict(mf["counts"])
        mod = mf["moderator"] if mf["moderator"] in state["players"] else None
        state["mafia"] = fresh_mafia()
        state["mafia"]["counts"] = keep
        state["mafia"]["moderator"] = mod
        broadcast()
        return {"ok": True}

    if action == "mafiaEndGame":
        if may_run and mf["phase"] in ("reveal", "night", "day"):
            if not _mf_check_win():
                mf["winner"] = None
                mf["phase"] = "gameover"
            broadcast()
        return {"ok": True}

    if action == "mafiaBeginNight":
        if may_run and mf["phase"] == "reveal":
            mf["round"] = 1
            mf["phase"] = "night"
            broadcast()
        return {"ok": True}

    if action == "mafiaNight":
        if not may_run or mf["phase"] != "night":
            return {"ok": True}
        try:
            tgt = int(data.get("target"))
        except (TypeError, ValueError):
            tgt = 0
        killed = []
        if tgt and mf["alive"].get(tgt):
            _mf_kill(tgt, "night")
            killed = [tgt]
        mf["lastNight"] = {"killed": killed}
        mf["votes"] = {}
        mf["voteResult"] = None
        if not _mf_check_win():
            mf["phase"] = "day"
        broadcast()
        return {"ok": True}

    if action == "mafiaVoteResolve":
        if may_run and mf["phase"] == "day" and mf["voteResult"] is None:
            _mf_resolve_vote()
            broadcast()
        return {"ok": True}

    if action == "mafiaNoLynch":
        if may_run and mf["phase"] == "day" and mf["voteResult"] is None:
            mf["voteResult"] = {"nolynch": True}
            broadcast()
        return {"ok": True}

    if action == "mafiaLynch":       # moderator overrides the vote
        if may_run and mf["phase"] == "day" and mf["voteResult"] is None:
            try:
                t = int(data.get("target"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad"}
            if mf["alive"].get(t):
                _mf_kill(t, "voted")
                p = state["players"].get(t, {})
                mf["voteResult"] = {"pid": t, "name": p.get("name", "?"),
                                    "color": p.get("color", "#888"),
                                    "role": mf["roles"].get(t)}
                _mf_check_win()
                broadcast()
        return {"ok": True}

    if action == "mafiaNextNight":
        if may_run and mf["phase"] == "day" and mf["voteResult"] is not None:
            mf["round"] += 1
            mf["votes"] = {}
            mf["voteResult"] = None
            mf["lastNight"] = None
            mf["phase"] = "night"
            broadcast()
        return {"ok": True}

    # ---- player actions ----
    p = state["players"].get(pid)
    if not p:
        return {"ok": False, "error": "not_joined"}
    p["last_seen"] = time.time()

    if action == "mafiaClaimModerator":
        # a player volunteers to run the game from their own phone. First come,
        # first served — you can only claim the seat while it's empty, and tap
        # again to step back down.
        if mf["phase"] != "lobby":
            return {"ok": True}
        if mf["moderator"] is None:
            mf["moderator"] = pid
        elif mf["moderator"] == pid:
            mf["moderator"] = None
        else:
            return {"ok": False, "error": "taken"}
        broadcast()
        return {"ok": True}

    if action == "mafiaVote":
        if mf["phase"] != "day" or mf["voteResult"] is not None:
            return {"ok": True}
        if not mf["alive"].get(pid):
            return {"ok": False, "error": "not_alive"}
        try:
            t = int(data.get("target"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad"}
        if t != 0 and (t == pid or not mf["alive"].get(t)):
            return {"ok": False, "error": "bad_target"}
        mf["votes"][pid] = t
        if all(q in mf["votes"] for q in _mf_alive()):
            _mf_resolve_vote()
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

        if isinstance(action, str) and action.startswith("blackbox"):
            return do_blackbox(pid, action, data, is_host)

        if isinstance(action, str) and action.startswith("cn"):
            return do_codenames(pid, action, data, is_host)

        if isinstance(action, str) and action.startswith("wii"):
            return do_wii(pid, action, data, is_host)

        if isinstance(action, str) and action.startswith("imp"):
            return do_imposter(pid, action, data, is_host)

        if isinstance(action, str) and action.startswith("mafia"):
            return do_mafia(pid, action, data, is_host)

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

    def _redirect(self, location, code=302):
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _short_link_target(self, path):
        """Map a short player link to the real controller path, or None.

          /<ROOM_CODE>   /<ROOM_CODE>/   /j/<code>   /join      -> /play?code=...
        so a phone can join by typing e.g.  vibebox.tv/WXYZ
        """
        p = path.rstrip("/")
        seg = p.lstrip("/")
        if p.startswith("/j/"):
            code = p[3:].strip()
            return "/play" + (f"?code={code.upper()}" if code else "")
        if seg.lower() in ("j", "join", "go"):
            return "/play"
        if re.fullmatch(r"[A-Za-z0-9]{3,12}", seg) and seg.upper() == ROOM_CODE:
            return f"/play?code={ROOM_CODE}"
        return None

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
            # a player who just typed the bare domain (vibebox.tv) lands here —
            # send them to the join screen. The host arrives via the ?host= link
            # (handled above) or already holds the cookie, so they still get the
            # menu.
            if not host:
                self._redirect("/play")
            else:
                self._send_file("menu.html", "text/html; charset=utf-8")
        elif path in ("/host", "/host/"):
            self._send_file("host.html", "text/html; charset=utf-8")
        elif path in ("/play", "/play/"):
            self._send_file("controller.html", "text/html; charset=utf-8")
        elif path == "/games":
            self._send_json({"games": GAMES})
        elif path == "/config":
            # public hints: the plain port, and the HTTPS twin port (if running)
            # so a phone can hop to https for motion sensors and back again.
            self._send_json({
                "httpPort": PORT,
                "httpsPort": HTTPS_PORT if HTTPS_OK else None,
            })
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
        elif self._short_link_target(path) is not None:
            self._redirect(self._short_link_target(path))
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
        elif path == "/unlock":
            # type the host password to unlock control from any machine
            ip = self._client_ip()
            with _lock:
                allowed = unlock_rate_ok(ip)
            if not allowed:
                self._send_json({"ok": False, "error": "rate_limited"}, 429)
            elif secrets.compare_digest(str(data.get("password", "")), HOST_PASSWORD):
                self._grant_host = True          # _send_json sets the cookie
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "bad_password"}, 403)
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


def _install_hint(tool):
    """A best-effort, OS-appropriate 'how to get <tool>' line."""
    if tool == "cloudflared":
        if sys.platform == "darwin":
            return "brew install cloudflared"
        if sys.platform.startswith("linux"):
            return ("grab the binary from "
                    "https://github.com/cloudflare/cloudflared/releases "
                    "(or add Cloudflare's package repo)")
        return "see https://github.com/cloudflare/cloudflared/releases"
    # package names differ per distro for a few tools
    names = {
        "python3-tkinter": {"apt": "python3-tk", "pacman": "tk", "brew": "python-tk",
                            "dnf": "python3-tkinter", "zypper": "python3-tk"},
    }.get(tool, {})
    if sys.platform == "darwin":
        return f"brew install {names.get('brew', tool)}"
    for mgr, cmd in (("dnf", "sudo dnf install"), ("apt", "sudo apt install"),
                     ("pacman", "sudo pacman -S"), ("zypper", "sudo zypper install")):
        if shutil.which(mgr):
            return f"{cmd} {names.get(mgr, tool)}"
    return f"install {tool} with your package manager"


def _start_tunnel(port):
    """Launch `cloudflared` and return (proc, https_url), or None on failure."""
    exe = shutil.which("cloudflared")
    if not exe:
        print("\n  cloudflared isn't on your PATH. Install it with:")
        print(f"      {_install_hint('cloudflared')}")
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

    # email / text the owner the fresh public links (no-op unless configured)
    notify.notify_public_link({
        "url": url,
        "code": ROOM_CODE,
        "host_you": f"{url}/host?host={HOST_TOKEN}",
        "host_other": f"{url}/host",
        "play": f"{url}/{ROOM_CODE}",
        "source": f"{sys.platform}",
    })
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
    print(f"  From another machine: {base_url()}/host  then the host password")
    print(line)
    print(f"  Players join at:   {play_url()}   (the code is in the link)")
    print(f"  ...or the long way: {base_url()}/play   with room code {ROOM_CODE}")
    print(line)
    if tunnel_on:
        print("  PUBLIC via Cloudflare tunnel — anyone with the link AND code can join.")
        print("  Ctrl+C stops the game and the tunnel.")
    elif PUBLIC_URL:
        print("  PUBLIC MODE (PUBLIC_URL set) — anyone with the link AND code can join.")
    else:
        print("  LAN only.  Local host link also works:")
        print(f"      http://localhost:{PORT}/?host={HOST_TOKEN}")
    if HTTPS_OK:
        print(line)
        print("  Phone motion controls (Wii Sandbox) need https — on this LAN use:")
        print(f"      https://{lan_ip()}:{HTTPS_PORT}/{ROOM_CODE}")
        print("  (accept the one-time self-signed cert warning on the phone)")
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
    # a headless Linux box (ssh, no X/Wayland) can't show a window
    if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import tkinter  # noqa: F401
    except Exception:
        print("  (no Tkinter — running in the terminal. Install it with "
              f"'{_install_hint('python3-tkinter')}' for the control window.)")
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

    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)   # binds + listens now
    except OSError as exc:
        if exc.errno in (48, 98):   # EADDRINUSE (macOS / Linux)
            kill = (f"lsof -ti tcp:{PORT} | xargs kill"
                    if shutil.which("lsof") else f"fuser -k {PORT}/tcp")
            print(f"\n  Port {PORT} is already in use — a party-game window is\n"
                  f"  probably still open somewhere. Close that window, or run:\n\n"
                  f"      {kill}\n\n"
                  f"  then start again.\n")
            sys.exit(1)
        raise
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    https_srv = start_https_listener()   # optional TLS twin for phone motion

    hold = {"tunnel": None}   # (proc, url), so cleanup always finds it

    def _set_public(url):
        global PUBLIC_URL
        PUBLIC_URL = url

    def _cleanup():
        srv.shutdown()
        if https_srv:
            https_srv.shutdown()
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
                "install_hint": _install_hint,
            }
            try:
                gui.run(ctx)
            except Exception as exc:
                # e.g. Tk can't open a display on this box — drop to the terminal
                print(f"  (control window couldn't start: {exc} — using the terminal)\n")
            else:
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
