# Party Games — laptop screen + phone controllers

A tiny local party-game console. The **laptop** shows a menu and the game
screen; **phones** on the same WiFi join and act as controllers.

Currently playable: **Tap Race** and **Scattergories**. More games are stubbed
on the menu as "Coming soon".

No dependencies — just Python 3 (stdlib only).

## Scattergories

One random letter, 12 random categories, a shared timer (default 2 min).
Everyone fills in an answer for each category on their phone. When the timer
ends the host walks the room through each category:

- Answers appear on the big screen (names hidden unless *Show usernames* is on).
- Every player **except the answer's author** votes yes / no. An answer scores
  **1 point if yes-votes beat no-votes** (a tie doesn't score; with no other
  players it auto-counts).
- If two or more players wrote the **same answer** for a category, all of those
  answers are **eliminated** (struck through, worth nothing).
- Optional *Alliteration bonus*: an answer whose words all start with the
  letter scores **2**.
- The host taps **Next** to move on. After the last category, round points are
  added to each player's running total on the scoreboard.

Host lobby settings: time per round, show-usernames, alliteration bonus, and a
**Reset scores** button. `Start Round` begins; `New Round` (after a round)
returns to the lobby.

## Run

```bash
python3 server.py
```

It prints two URLs, e.g.

```
laptop / games menu :  http://localhost:8000/
phones / controller :  http://192.168.1.24:8000/play
```

1. Open the **menu** URL on your laptop (full-screen it).
2. On each phone (same WiFi), open **/play**, type a name, tap **Join**.
3. On the laptop, pick a game. Players' phones switch to that controller.
4. In Tap Race: **Space** starts / restarts, **R** returns to the lobby,
   **‹ Games menu** (top-left) goes back to the menu.

## How it works

| Piece | Role |
|-------|------|
| `server.py` | stdlib HTTP server. Holds all game state in memory. |
| `qr.py` | Dependency-free QR-code generator (verified against the `qrcode` package + decoded back with OpenCV). |
| `scattergories.py` | Category pool, letter set, and the pure scoring / alliteration helpers. |
| `GET /` → `static/menu.html` | The games menu. Accessible: keyboard-navigable, screen-reader labelled, respects reduced-motion. Lists games from `GET /games`. |
| `GET /host` → `static/host.html` | The game screen. One shell, a view per game, switched by `state["game"]`. Live updates via Server-Sent Events (`/events`). |
| `GET /play` → `static/controller.html` | The phone controller. One shell: "waiting" screen, then the current game's controls. |
| `GET /games` | The game catalog (`GAMES` in `server.py`). |
| `GET /whoami` | This machine's current LAN IP + the phone URL. |
| `GET /phoneQR.png` | QR code for the controller page. **Generated live from the current LAN IP** — switch WiFi networks and the screen updates itself within ~10s, no restart. |
| `POST /select` | `{game: "<id>" | null}` — host picks a game (or `null` to return to the menu). |
| `POST /join` | Registers a player, returns a `pid` + color. |
| `POST /input` | `{pid, action, ...}`. Tap Race: `tap` / `start` / `reset`. Scattergories: `scatStart`, `scatSet`, `scatAnswers`, `scatEndRound`, `scatVote`, `scatNext`, `scatLobby`, `scatResetScores`. `ping` keeps a player alive. |

Players that stop pinging for 15s are dropped automatically. The state served
to clients is in `public_state()`; per-game blocks hang off it (`scat`).

## Adding a game

1. Add an entry to `GAMES` in `server.py` with `"status": "soon"` — it shows
   on the menu immediately as "Coming soon".
2. When you build it: set `"status": "ready"`, give it a sub-state (like
   `state["scat"]`), a `_<game>_public()` block in `public_state()`, an action
   handler dispatched from `do_input`, a `#view-<game>` in `host.html`, and a
   view in `controller.html` keyed on `s.game`. `janitor()` is where per-tick
   logic (timers) lives.

## Tweaks

- Tap Race win target: `GOAL` in `server.py`.
- Scattergories categories / letters: `CATEGORY_POOL`, `LETTERS` in `scattergories.py`.
- Port: `PORT` in `server.py`.
- Player colors: `COLORS` in `server.py`.

## Notes

- Phone and laptop must be on the **same network**, and it must allow
  device-to-device traffic (some public/guest WiFi blocks this — a phone
  hotspot works as a fallback).
- macOS may pop a firewall prompt the first time — click **Allow**.
- Plain HTTP on your LAN, no auth. Fine for a living room, not the open internet.
