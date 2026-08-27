# Party Games — laptop screen + phone controllers

A tiny local party-game console. The **laptop** shows a menu and the game
screen; **phones** on the same WiFi join and act as controllers.

Currently playable: **Tap Race** (first to 40 taps wins). More games are
stubbed on the menu as "Coming soon".

No dependencies — just Python 3 (stdlib only).

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
| `GET /` → `static/menu.html` | The games menu. Accessible: keyboard-navigable, screen-reader labelled, respects reduced-motion. Lists games from `GET /games`. |
| `GET /host` → `static/host.html` | The Tap Race game screen. Live updates via Server-Sent Events (`/events`). |
| `GET /play` → `static/controller.html` | The phone controller. Shows a "waiting" screen until the host picks a game, then the game's controls. |
| `GET /games` | The game catalog (`GAMES` in `server.py`). |
| `GET /whoami` | This machine's current LAN IP + the phone URL. |
| `GET /phoneQR.png` | QR code for the controller page. **Generated live from the current LAN IP** — switch WiFi networks and the screen updates itself within ~10s, no restart. |
| `POST /select` | `{game: "<id>" | null}` — host picks a game (or `null` to return to the menu). |
| `POST /join` | Registers a player, returns a `pid` + color. |
| `POST /input` | `{pid, action}` where action is `tap`, `ping`, `start`, or `reset`. |

Players that stop pinging for 15s are dropped automatically.

## Adding a game

1. Add an entry to `GAMES` in `server.py` with `"status": "soon"` — it shows
   on the menu immediately as "Coming soon".
2. When you build it: set `"status": "ready"`, add its host screen + controller
   views, and branch on `state["game"]` in the server actions the way
   `tap-race` does today.

## Tweaks

- Win target: `GOAL` in `server.py`.
- Port: `PORT` in `server.py`.
- Player colors: `COLORS` in `server.py`.

## Notes

- Phone and laptop must be on the **same network**, and it must allow
  device-to-device traffic (some public/guest WiFi blocks this — a phone
  hotspot works as a fallback).
- macOS may pop a firewall prompt the first time — click **Allow**.
- Plain HTTP on your LAN, no auth. Fine for a living room, not the open internet.
