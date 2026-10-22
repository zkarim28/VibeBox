# How VibeBox is wired

A map of how the pieces connect: processes, the server's state machine, the
pages, and the conventions each game follows. The README covers *using* it;
this file covers *changing* it.

---

## 1. The big picture

```
                 boot (systemd --user, linger on)
                 ├── vibebox-online.service ──► start-online.sh ──► server.py  (MODE=public, port 8000)
                 │                                                   └── cloudflared quick tunnel
                 └── vibebox-launcher.service ─► launcher.py (port 8790, via Tailscale Funnel)
                                                     └── spawns more server.py's on demand (ports 8000-8019)

  server.py (one room, one game at a time)
     │  notify.py ──► ntfy push (NOTIFY_WEBHOOK) [+ optional SMTP / email-to-SMS]
     │  qr.py     ──► join QR codes
     ▼
  HTTP (stdlib ThreadingHTTPServer, JSON over fetch)
     ├── /           menu.html        game picker (laptop / TV)
     ├── /host       host.html        shared screen for the current game
     ├── /play       controller.html  each player's phone
     ├── /host/play  controller.html + phone-host.js   host who is also a player (no TV)
     └── /sudoku     sudoku-home.html OPEN Sudoku page: no password / room code,
                                      its own tables (sudoku_tables.py), independent of the room
```

No dependencies: Python 3 stdlib on the server, plain HTML/JS/CSS on the pages
(no build step, no framework).

---

## 2. Processes and boot

| Piece | What it does |
|---|---|
| `start-party-games.sh` | Interactive start. Loads `.env`, frees ports 8000/8443, runs `server.py`, which asks Local or Public (or opens the Tk window in `gui.py`). |
| `start-online.sh` | Non-interactive start for boot. Forces `MODE=public GUI=0 NO_HTTPS=1`, waits up to 3 min for the internet, runs `server.py`. If the tunnel fails, the server would fall back to LAN-only. The script watches for that ("running LOCAL instead"), kills the server and exits 1 so systemd retries. |
| `deploy/vibebox-online.service` | systemd user unit for `start-online.sh` (`Restart=always`, 20 s back-off, no start limit). Installed copy: `~/.config/systemd/user/`. |
| `deploy/vibebox-watchdog.{sh,service,timer}` | Every 2 min, checks `127.0.0.1:8000/sudoku` and the current tunnel link (read from this run's journal). 3 bad checks in a row (e.g. Cloudflare ERROR 1033, tunnel lost) → `systemctl --user restart vibebox-online`, which opens a new tunnel and pushes the new link to ntfy. Skips the first 4 min after a start and does nothing while the internet itself is down. Logs: `journalctl --user -u vibebox-watchdog -o cat`. |
| `deploy/publish-link.sh` | Permanent address: `https://zkarim28.github.io/vibebox/` (a page in the portfolio repo) reads `vibebox/link.json`, checks `<tunnel>/ping` (CORS-enabled route in server.py) and redirects; `#/sudoku` etc. passes a path through. With `PUBLISH_LINK=1` in .env, `_notify_startup` runs this script on every new public URL. It uses its own clone (`~/.local/share/vibebox/site-bot`, over SSH), does `fetch` + `reset --hard origin/main`, rewrites only link.json, commits and pushes, and on a rejected push refetches and retries, so merge conflicts can't happen. GitHub Pages takes ~1 min to deploy; the page keeps retrying meanwhile. |
| `launcher.py` + `setup-launcher.sh` | Password-gated control panel on `127.0.0.1:8790`, exposed with `tailscale funnel`. Each "start" spawns `server.py` with `MODE=public`, a free port from 8000-8019, its own room code and host token. Tracks them in `~/.vibebox-launcher/servers.json`; logs in `~/.vibebox-launcher/logs/`. |
| `gui.py` | Optional Tk control window for interactive local runs. |

**Startup sequence inside `server.py main()`:** bind HTTP port → start the
`janitor` thread → optional HTTPS twin on 8443 (self-signed, for phone motion
sensors; skipped with `NO_HTTPS=1`) → choose mode (GUI / `MODE` env / prompt)
→ `_start_tunnel()` launches `cloudflared` and scrapes the
`*.trycloudflare.com` URL → `_banner()` prints links → `_notify_startup()`
sends the ntfy push once per URL.

**Logs and the current link:** `journalctl --user -u vibebox-online -o cat`.
A restart always makes a new tunnel URL and a new push.

### Environment (`.env`, git-ignored)

`.env` is read twice: `bash` sources it in the start scripts (so quote values
with `!`, `$`, spaces), and `notify.load_env_file()` reads it in Python
**without overriding** variables already set (so an empty `NOTIFY_WEBHOOK=` on
the command line really disables pushes).

| Var | Effect |
|---|---|
| `NOTIFY_WEBHOOK`, `NOTIFY_TOKEN` | ntfy topic URL (+ auth) for startup pushes. |
| `SMTP_*`, `NOTIFY_SMS`, `SMS_GATEWAY`, `NOTIFY_EMAIL` | Optional email / text copy of the link. |
| `HOST_PASSWORD` | Fixed host password. Unset means a random one per start, printed in the banner. |
| `MODE` | `local` / `public`; skips the prompt and the GUI. |
| `PUBLIC_URL` | Use an existing URL instead of a quick tunnel. |
| `PORT`, `HTTPS_PORT`, `NO_HTTPS`, `ROOM_CODE`, `HOST_TOKEN`, `MAX_PLAYERS`, `JOIN_MAX` | Server knobs (the launcher sets several per instance). |
| `ONLINE_PORT` | Port used by `start-online.sh` (default 8000). |
| `SUDOKU_HISTORY` | Where the Sudoku play log lives. |
| `LAUNCHER_PASSWORD`, `LAUNCHER_PORT`, `LAUNCHER_PORT_BASE`, `LAUNCHER_MAX` | Launcher knobs. |

Port note: the boot server holds 8000 and the launcher's range also starts at
8000. That's fine, because the launcher test-binds each port and skips busy ones.

---

## 3. Inside `server.py`

### One global state, one lock

```python
state = {
  "game": None | "<GAMES id>",   # None = menu
  "phoneMode": bool,             # host is on a phone (no shared screen)
  "players": {pid: {name, color, score, team, token, last_seen, connected}},
  "scat": {...}, "taboo": {...}, ..., "wordhunt": {...},   # one sub-state per game
  "bs": bs.fresh(), "sudoku": sudoku.fresh(),
}
_lock    # every read-modify-write of `state` happens under this
```

Every game keeps its sub-state alive the whole time. `do_select(game)` resets
them all to lobby form (keeping settings like round length) when the host
switches games.

### The request → state → response loop

```
phone tap ──POST /input {pid, token, action, ...}──► Handler.do_POST
    auth: host cookie, OR action in HOST_ONLY (then rejected), OR pid+token match
    └─► do_input(data, is_host)
          ├── HOST_ONLY check (host-cookie-only actions)
          ├── room-wide actions: hostMode, ping, leave, kick
          └── dispatch by ACTION PREFIX:
                bs*    → bs.act(...)            (module engine)
                sd*    → sudoku.act(...)        (module engine)
                scat*  → do_scat   taboo* → do_taboo   blackbox* → do_blackbox
                cn*    → do_codenames   wii* → do_wii   imp* → do_imposter
                mafia* → do_mafia   ludo* → do_ludo   wh* → do_wordhunt
          └─► broadcast()   (pushes JSON to /events subscribers)

every page ──GET /state?pid=N──► public_state(for_pid)
    {game, phoneMode, players[], scat|taboo|...|sudoku: <only the active game's view>}
```

* **Prefix routing matters.** A new game's actions need a unique prefix, and
  the prefix check order in `do_input` is significant (`bs` before `scat`, and
  so on).
* **`public_state(for_pid)`** is the only thing clients ever see. Per-game
  `_x_public()` / `module.public()` builds a view for that player. Private
  information (hands, roles, spymaster keys, Sudoku solutions) is filtered
  here, never on the client.
* **`janitor()`** runs every second under the lock: timers (Scattergories,
  Taboo, BlackBox, Word Hunt, BS challenge windows), `sudoku.tick()` (ends a
  race once everyone left has finished), and `reap_players()`.
* **Players** stay marked `connected` while their `/state?pid=` polls keep
  arriving. After `PLAYER_TIMEOUT` (6 s) they show as reconnecting; after
  `PLAYER_GRACE` (90 s) `_purge_player()` scrubs them from every sub-state.
  Kicked pids go into `_kicked` so their next poll tells them to leave.
* **Room-mode switch** (`hostMode`) is refused mid-round. It looks up the
  active game's sub-state through a small id→key map
  (`{"scattergories": "scat", "wii-sandbox": "wii", "word-hunt": "wordhunt"}`)
  and allows only `lobby / gameover / done / review / turnend` phases.

### HTTP routes

| GET | |
|---|---|
| `/` | menu (host) |
| `/host` | shared screen; `/host/play`, `/play` → controller |
| `/theme.css`, `/phone-host.js`, `/bs.js`, `/sudoku.js`, `/results.js` | static. **Each file needs its own explicit route**; there is no static-directory catch-all. |
| `/games` | the `GAMES` catalog |
| `/state[?pid=]` | `public_state()`; also the player heartbeat |
| `/events` | server-sent events stream of public state (clients currently poll `/state` instead) |
| `/whoami`, `/config`, `/amihost`, `/phoneQR.png` | links, host check, QR |
| `/<CODE>` | short join link → `/play?code=…` |
| `/sudoku[?t=<table>]` | the open Sudoku landing page |

| POST | |
|---|---|
| `/join` | name → `{pid, token}` (no room code since Oct 2026) (rate-limited per IP) |
| `/resume` | pid + token → rejoin after reload |
| `/unlock` | host password → sets host cookie (rate-limited) |
| `/input` | every game action |
| `/select` | host picks a game (or `null` for the menu) |
| `/sd/hello · tables · create · enter · leave · input · state` | open Sudoku page API (see §6b). Everything is a POST so tokens stay out of URLs. |

### Auth model

* **Host:** cookie `hosttoken=<HOST_TOKEN>`. Set either by opening a
  `?host=<token>` link (the banner's one-click link) or by POSTing the
  password to `/unlock`. `HOST_ONLY` lists the actions that need it.
* **Player:** `pid` + per-player `token` returned by `/join`, kept in the
  phone's storage, sent on every `/input`.
* **Open Sudoku visitor:** `mid` + `token` from `/sd/hello` (name only),
  kept in `localStorage` (`sd-home`). Completely separate from room players;
  it can only reach `/sd/*`, never `/input` or `/select`.
* ntfy pushes **never** include the host token. The "Host and play" link
  still asks for the password.

---

## 4. The pages

| File | Role |
|---|---|
| `static/menu.html` | Game grid from `/games`; host picks → `/select` → navigates to `/host`. Polls state every 2 s. |
| `static/host.html` | One `#view-<game>` per game, toggled by `s.game`. `render(s)` dispatches to `renderScat`, `renderTaboo`, … Unknown game → back to `/`. Polls with `pollHost()`. |
| `static/controller.html` | Join form, then one container per game (`#scat`, `#bs`, `#sudoku`, …). `setView(game)` toggles them; `pollState()` (also the heartbeat) dispatches to the game's renderer; `s_poll_delay()` sets per-game poll speed (300-700 ms). `send(action, extra)` adds `pid` and `token`. |
| `static/phone-host.js` | Only active on `/host/play`. Adds the "Host controls" tab: unlock gate, game picker, per-game **settings** (`settings()` uses the `number` / `toggle` / `choice` / `textSetting` helpers) and **buttons** (`controls()`), roster with kick. Settings forms post `{action, key, value}`. |
| `static/results.js` | `VibeResults.fromState()`: shared leaderboard panel for phones. |
| `static/bs.js`, `static/sudoku.js`, `static/minigolf.js`, `static/connect4.js` | **Self-contained game views** (see §5). |
| `static/theme.css` | Shared look plus the newer games' styles (`.bs-*`, `.sd-*`). Older games keep their CSS inline in host/controller HTML. |

---

## 5. Two styles of game

**Older, inline games** (Scattergories, Taboo, BlackBox, Codenames, Wii,
Imposter, Mafia, Ludo, Word Hunt): `fresh_x()`, `_x_public()`, `do_x()`
live inside `server.py`; markup, CSS and JS live inside `host.html` and
`controller.html`.

**Newer, modular games** (BS, Sudoku, Mini Golf, Connect 4): this is the pattern to copy.

```
game.py               fresh() · act(g, players, pid, action, data) · public(g, players, pid) · tick/expire(g)
                      pure engine; the caller already holds the lock
static/game.js        globalThis.GameView.render(root, state, pid, send, isHost)
                      draws the phone AND the shared screen (isHost=true adds the host controls)
server.py             import · HOST_ONLY · GAMES entry · state[...] · public_state · janitor hook
                      · do_select reset · do_input prefix dispatch · static route for game.js
controller.html       <div id="game"> · <script src> · setView toggle · waiting-list · render call · poll delay
host.html             <div id="view-game"> · <script src> · game-list check · render call
phone-host.js         settings() case · controls() case
tests/test_game.py    engine tests + a couple through server.do_input
```

Checklist for a new modular game: every bullet above. Search for `sudoku`
across the repo to see each touch point.

---

## 6. Sudoku specifics

```
sudoku_puzzles.py   solver (backtracking, counts solutions up to 2)
                    grader: replays human techniques, records the hardest one needed
                      singles → easy/medium (by clue count) · pointing/claiming → advanced
                      pairs/triples → hard · X-wing/swordfish/XY-wing → expert
                    generator: random full grid → dig symmetric pairs while unique & ≤ target level
                    transform(): digit relabel + row/col/band/stack shuffles + transpose
                    CLI: --per-level N (grow bank) · --stats
sudoku_puzzles.json committed bank: {level: [{id, p, s}]}, 150 per level
sudoku.py           engine: modes competitive / coop / zen, actions sd*
                    history: sudoku_history.json (git-ignored), append-only plays
                    pick(): a board none of the current names has played, else the oldest; then transform()
                    CLI: --history
sudoku_tables.py    open /sudoku page: visitors, tables, owners, cleanup (§6b)
static/sudoku.js    board UI, notes, skins, hints, spectating, chat, keyboard
static/sudoku-home.html  the open landing page
```

* **Never send the solution.** `sudoku.public()` sends `puzzle` (givens) and
  `cells` only. Competitive and co-op moves are checked on the server; a wrong
  digit returns `{correct: false}` and is never stored.
* **Zen** stores any digit, checks nothing until the board equals the
  solution, and highlights repeats on the client.
* **History** is keyed by player **name** (case and spacing ignored), since
  players have no accounts. Rows are written when a round ends, when the host
  ends or leaves a round, when the host switches games (`do_select` calls
  `sdLobby` first), or when a Zen player solves or abandons a board.
* `BANK` loads once at import, so a regenerated bank needs a server restart.
* **Phone-only state:** pencil-mark notes (`sd:<seat rid>`) and the chosen
  board skin (`sd-skin`) live in `localStorage`. Skins are CSS-variable sets
  on `[data-skin=…]` in `theme.css`. Notes mode adds `.sd-noting` to the
  grid, which swaps to the `--sd-nm-*` variables (grey board, white notes).

---

## 6b. The open Sudoku page

```
sudoku-home.html ──POST /sd/*──► Handler._sudoku_tables ──► TABLES (sudoku_tables.Tables, own lock)
                                                              ├── visitors {mid: name, token, color}
                                                              ├── tables {tid: owner, players, seen, g = sudoku.fresh()}
                                                              └── same sudoku.act / public / tick as the party room
```

* A table's `players` dict is passed to the engine as its "room", so every
  engine rule (modes, hints, chat, spectating, history) is shared with the
  party room.
* Owner-only actions: `TABLE_ONLY = {sdSet, sdStart, sdEnd, sdLobby, sdPause}` (+
  `sdListed`). Ownership passes to the next member when the owner leaves.
* Cleanup is lazy (on requests): a member who stops polling for 60 s
  leaves; an empty table logs any open boards and is deleted. A page reload
  does **not** leave (so a solo game survives it); the `?t=` link rejoins.
* Limits: 40 tables, 30 per table, 500 visitors, `/sd/hello` 10/min per IP,
  table creation 8/min per visitor, chat 1 message / 0.7 s, 200 chars.
* The page calls `SudokuView.render(el, {sudoku}, mid, send, false,
  {controls: isOwner})`. `controls` shows round controls on a phone; the 5th
  arg (`isHost`) still means "shared laptop/TV display".

### Rules, pause (engine, both contexts)

* `sdSet` keys: `mode`, `difficulty`, `boards` (`same`|`each`, competitive),
  `lives` (`0`|`3`). `boards`/`lives` survive `_reset` like the chat does.
* `boards=each`: `sdStart` deals per player (like Zen); everything already
  reads `seat["board"] or g["board"]`, so ranking, hints, spectating and
  history just work.
* `lives=3`: a racer's 3rd wrong digit sets `seat["out"]` and `finishedAt`
  (so `tick` treats them as done; they're not ranked and may spectate). Co-op:
  `shared["out"]` ends the round. With lives on, mistakes cost no time.
* `sdPause {value}` (host / owner): sets `pausedAt`; while set `public()` omits
  every board/solution, and `sdPlace`/`sdHint`/`sdZenNew` are refused.
  `_resume` shifts `startedAt` and every seat's `startedAt`/`finishedAt` by the
  pause length, so durations never count it. `sdEnd`/`sdLobby` resume first.
* Phone host panel (`phone-host.js`): settings save on `change` (no Save
  button); actions queue instead of being dropped, only the tapped control is
  marked busy, and the settings form isn't rebuilt while a save is in flight
  or a field has focus.

### Hints, spectating, chat (engine, both contexts)

* `sdHint` → `sudoku_puzzles.hint(puzzle, cells, solution)`: flags a wrong
  digit first, else finds a hidden/naked single, applying elimination
  techniques (pointing, pairs, X-wing…) until one appears, and explains it.
  Counted per seat (`hints`), +`HINT_PENALTY` s in competitive.
* `sdWatch {target}` puts the viewer in `g.watchers` and `g.peeked`;
  `public()` then adds `spectate` (target's cells + **solution**). Allowed
  only for people without an unfinished seat on that board. Co-op peekers
  can't `sdJoin`.
* `sdChat`: messages from anyone who knows the solution (peeked, or finished
  a race) are flagged `spoiler` and filtered out of `public()` for viewers
  still solving, until the round ends. Chat survives `_reset()` between
  rounds.

## 6c. Mini golf specifics

* `minigolf.py` (prefix `mg`, game id and state key both `minigolf`). Phases
  `lobby → playing ⇄ hole → done`. Settings live in `g["cfg"]` and survive
  `mgLobby`/`do_select`; teams in `g["teamOf"]`.
* **Course:** 80×120 units, 8×12 grid of 10-unit cells. `_layout` carves a
  fairway (straight/dogleg/zigzag/U/arena), `_walls` merges cell edges into
  rails, `_place` drops obstacles on free cells/2×2 blocks away from tee and
  cup, `_path_len` BFS (2-unit grid, ball clearance) rejects unplayable holes
  and sets par. 18 holes ≈ 0.1 s.
* **Physics is server-side and deterministic:** `simulate(hole, start, angle,
  power, clock)` at 240 Hz → path sampled at 20 fps (`frames`, `cuts` mark
  teleports/penalty resets, `events` for toasts). Moving obstacles (windmill,
  drawbridge, kraken swirl) are functions of the obstacle clock
  `t - g["epoch"]`, so screens draw them in sync with the sim. A shot starts
  `START_DELAY` (0.5 s) after it's struck so polling screens see it from the
  start. Phones only send `mgShot {angle, power}`.
* **Sides** are what own a ball: a player (solo, best ball) or a team
  (alternate shot, `hitter` rotates). Turn order: `_turn` per group (tee order,
  then farthest/closest); `pace=together` skips it. `tick` ends a hole when all
  balls are in/picked up and stopped (players who left get the limit), shows
  the scorecard `HOLE_PAUSE` s, then the next hole or `_finish` (lowest total,
  room points 100×(n−place+1)).
* **Trivia holes:** phases `hole → trivia → treveal → next hole` when
  `cfg.trivia` (0/1/2/3 = off / every Nth hole) says so. `make_trivia_hole`
  builds a full-width hole with one cup per answer (`hole["cups"]`, lane
  dividers as extra wall segments); `simulate` sinks into the nearest cup and
  reports `cup`. `_cur_hole(g)` returns the trivia hole during those phases.
  Everyone putts at once (`TRIVIA_STROKES` each, `TRIVIA_TIME` limit); choices
  stay hidden in `public()` until `_reveal_trivia` scores them
  (`trivia_points`). Questions: `minigolf_trivia.py`. Coins
  live in `hole["coins"]`; `g["coinsTaken"][i] = {by, at}` (at = the moment the
  ball reaches it on screen). Pinball points: `simulate` emits events with a
  position, `_shot` scores them with a combo multiplier (`SCORE`, `SINK_PTS`,
  `MAX_MULT`) into `side["points"]`; `points_standings()` is the second board.
  Trampolines give the ball height `z` (frames become `[x, y, z]`): airborne
  balls skip friction/hazards/obstacles and clear rails above `RAIL_H`.
  Themes (`THEMES`, `cfg.theme`, per-hole `hole["theme"]`) are cosmetic.
* `public()` sends only the current hole's cells/walls/obstacles, and shot
  frames only while that shot is animating (≈ 3–8 KB per poll).
* View: `GolfView.render(el, state, pid, send, isHost)`; a cached static layer
  (sea, fairway, rails, cup, static obstacles) plus a per-frame dynamic layer,
  cropped to each hole's bounding box. A 3D renderer could reuse the engine
  unchanged (the sim is 2D; heights would be cosmetic).

## 6d. Connect 4 specifics

* `connect4.py` (prefix `c4`, game id and state key `connect4`). Phases
  `lobby → playing → done`; `c4Start` again is a rematch. `cfg` (format,
  size, connect, bot, timer), `teamOf`, `record` (W/D/L) and `met` (pairing
  history) survive rematches; `do_select` keeps cfg + teams, drops the rest.
* **Formats:** `duels` pairs everyone at once (fewest previous meetings first,
  odd one out vs the computer), `teams` is one Red-vs-Yellow match with
  droppers rotating per side, `bot` gives every player their own match.
  Each match: `board[col][row]` bottom-up (0/1 red/2 yellow), `sides[0]` is
  red and moves first (sides are shuffled at deal).
* **The computer** is a bitboard negamax with alpha-beta, a transposition
  table and iterative deepening (`_Solver`), capped at `BOT_BUDGET` seconds.
  It always takes a win / blocks a four first; Easy/Medium then play randomly
  some of the time (`BOT_SLOPPY`). The search runs on a **thread** with a
  board snapshot (`_bot_pick`) so the server lock isn't held while it thinks;
  `tick()` applies the answer. `/state` polls call `connect4.tick()` too, so a
  bot reply lands within one poll (~0.9 s total incl. `BOT_DELAY`). Tests set
  `SYNC_BOT = True`.
* `tick()` also runs the move clock (expired → an Easy-bot move for that
  player, `m["auto"]` explains it), hands a departed player's seat to the
  computer, and seats late joiners on the smaller team in team games.
* Room points: win 100, draw 40, beating the computer 50/100/200 by level.
* View: `C4View.render(el, state, pid, send, isHost)`. Each match has its own
  wrapper and each part (status / players / board) its own cached markup, so
  the clock ticking or a move on another board never rebuilds a board, which
  would replay the CSS drop animation (`.c4-drop`, `--fall`).

## 7. Persistence (everything else is in memory)

| File | Written by |
|---|---|
| `.env` | you |
| `sudoku_history.json` | `sudoku.py` |
| `.certs/` | `ensure_self_signed_cert()` for the HTTPS twin |
| `~/.vibebox-launcher/{password,servers.json,logs/}` | `launcher.py` |

Restarting `server.py` clears players, scores and rounds. The Sudoku history
survives restarts.

---

## 8. Tests

```
python3 -m unittest discover -s tests
```

`test_phone_mode.py` (room and host flows through `server.do_input`),
`test_bs.py`, `test_sudoku.py` (bank validity, every mode, no solution leaks,
board picking), `test_notify.py`, plus `results.test.cjs` (needs Node).
Tests reset `server.state` from a deep copy in `setUp`. Sudoku tests point
`sudoku.HISTORY_PATH` at a temp file.

---

## 9. Gotchas found along the way

* `server.py` silently **falls back to local** if the tunnel fails; only
  `start-online.sh` treats that as an error.
* The HTTPS twin (8443) collides between instances; anything scripted should
  set `NO_HTTPS=1`, as the launcher and `start-online.sh` do.
* Static files need explicit routes in `Handler.do_GET`.
* Never compare `node.innerHTML !== html` to decide whether to re-render: the
  browser re-serialises markup (`&#39;` → `'`), so it never matches and the
  node is rebuilt every poll, which snaps open `<select>`s shut. Cache the
  string you wrote (`setHTML` in sudoku.js / minigolf.js / connect4.js does this, and also
  skips a rebuild while a field inside has focus).
* Game lists are repeated in several places: `controller.html`
  `setView()`'s "waiting" list, `host.html`'s redirect guard, and the
  `hostMode` key map. Miss one and the page bounces to the menu or shows
  "waiting".
* `HOST_PASSWORD` unset means a new password every restart.
* To run a throwaway test server without touching the live one or your ntfy:
  `PORT=8123 NO_HTTPS=1 MODE=local NOTIFY_WEBHOOK= SMTP_HOST= SUDOKU_HISTORY=/tmp/h.json python3 server.py --no-gui`
