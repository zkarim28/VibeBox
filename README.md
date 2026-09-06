# Party Games — laptop screen + phone controllers

A tiny local party-game console. The **laptop** shows a menu and the game
screen; **phones** on the same WiFi join and act as controllers.

Currently playable: **Tap Race**, **Scattergories**, **Taboo**, **BlackBox**,
**Codenames**, **Wii Sandbox**, **Imposter**, and **Mafia**. More games are
stubbed on the menu as "Coming soon".

No dependencies — just Python 3 (stdlib only).

## Running it

**macOS** — double-click **`Start Party Games.command`** in Finder
(right-click → Open the first time).

**Linux** — from a terminal in this folder:

```
./start-party-games.sh
```

Add a menu/desktop entry with `./start-party-games.sh install-shortcut`.

**Any OS** — `python3 server.py` also works directly. Flags/env: `--no-gui`
skips the control window; `MODE=local` / `MODE=public` skips the prompt;
`NO_HTTPS=1` disables the local HTTPS listener. The control window sizes itself
to your display; if it looks cramped on a scaled Linux desktop, set
`VIBEBOX_UI_SCALE=1.5` (or whatever fits).

Optional extras: **`cloudflared`** for Public (over-the-internet) mode, a
distro **Tkinter** package for the control window (Fedora:
`sudo dnf install python3-tkinter`, Debian/Ubuntu: `sudo apt install python3-tk`).
Without them the server still runs — Local mode, terminal only.

### Hosting from another machine

The host screen (menu + game view) is protected by a **password**, so you can
open the host link from any device — your laptop, a friend's, a phone — and
unlock it by typing the password (default `Brownnation1!`, override with
`HOST_PASSWORD`). Your own `?host=…` bookmark still unlocks in one click.

### Get the public link emailed / texted to you

Every time a new Public (Cloudflare) link is created, the server can send you
the host + player links. Copy `.env.example` to `.env` and fill in either SMTP
details (e.g. a Gmail app-password) or a free `NOTIFY_WEBHOOK` (ntfy.sh). The
launcher loads `.env` automatically. Nothing is sent unless you configure it.

## Taboo

Two teams, **one phone each**. A card (guess word + 5 forbidden "taboo" words)
shows on the big screen.

Before every turn there's a 5-second **"get ready"** countdown (big screen shows
the team that's up; each phone tells its player their role). The host can tap
**Start now** to skip it. Then the turn runs for 60 seconds:

- The **clue-giving team's phone** shows the card with **✓ Got it** (+1 point)
  and **Skip** buttons.
- The **other team's phone** shows the same card with a **🔔 Taboo!** buzzer —
  tap it if the clue-giver says a listed word.
- Any of Got it / Skip / Taboo advances the big screen to the next card. A
  running turn log (✅ / ⏭ / 🔔) builds up.
- When time runs out the turn ends; the host taps **Next turn** and the other
  team goes. **End game** shows the winner.

Host lobby settings: seconds per turn, team names, and *a correct buzz gives the
other team a point* (off by default). Players are auto-split across the two
teams on join and can switch on their phone in the lobby.

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

## Mafia

The playing-card party classic, dealt over phones. One player is picked as the
**moderator** in the lobby — they hold no card and run the night/day cycle from
their phone (the laptop screen is just the shared "town square" and can stand in
if the moderator's phone drops).

- Host lobby: set how many **Mafia / Sheriffs / Doctors** there are (everyone
  else is a Civilian), tap a player to make them the **moderator**, then
  **Deal cards & start** (needs 4+ card-holders, and the town must outnumber the
  Mafia).
- Each phone shows its secret role. Mafia phones also list their team-mates.
- **Night:** everyone closes their eyes for real; the moderator's phone shows the
  full role list and a tap-list to record who the Mafia killed (or "nobody" if
  the Doctor saved them). Sheriff / Doctor checks happen in the room — the
  moderator nods or shakes.
- **Day:** the big screen announces who died (role revealed). Living
  card-holders vote on their phones; the vote auto-resolves once everyone's in,
  or the moderator can reveal early, call **no lynch**, or eliminate someone
  directly. The lynched player's role is revealed.
- **Town wins** when every Mafia member is out. **Mafia win** once they equal or
  outnumber the remaining town.

## Imposter

Social deduction. Everyone sees the **category**; the crew also sees a secret
**word**, but the randomly chosen **imposter(s)** just see "YOU'RE THE
IMPOSTER". Set how many imposters in the lobby (needs 3+ players).

- Each round every player takes a turn on their phone to type **one clue word**
  hinting at the secret word — imposter included, bluffing. The big screen shows
  whose turn it is and the clues as they land.
- After everyone's clued, it's **discussion** time. From here the host can start
  another round (every clue from every past round stays on screen, labelled by
  who said it), **call a vote**, or reveal & end.
- A vote: everyone taps who they suspect. The top pick is revealed — if they're
  an imposter they're **out**; if not, that crew member is out anyway.
- Any imposter who thinks they've cracked the word can hit **guess** on their
  phone. Every screen shows who's guessing; they say the word out loud and the
  host taps Correct / Wrong. **Wrong → they're out. Correct → the imposters
  win.**
- **Crew win** when every imposter is out. **Imposters win** on a correct guess
  or if every crew member gets eliminated.

## Run

```bash
python3 server.py
```

A small **control window** pops up (needs Tkinter — standard on the python.org
build). It asks **Local or Public**, then shows:

- the **host link** with an *Open in browser* button (one click to unlock the
  laptop screen),
- the **players-join link** + **room code**,
- the join **QR**,
- a **Quit** button.

The same details are also printed to the terminal. Close the window or press
**Ctrl+C** to stop everything (and the tunnel, if running).

No window: run with `--no-gui`, `GUI=0`, or set `MODE=local|public` — then it
falls back to the terminal prompt.

### The flow

1. **Open the host link** (button in the window, or the terminal link). It sets
   a cookie — that browser is now the "host". Any browser without the cookie
   sees a "screen locked" page, so a stray player can't hijack the game.
2. Players open **/play** (or scan the QR — it carries the code), type a name +
   the **room code**, tap **Join**.
3. On the laptop, pick a game. Players' phones switch to that controller.
4. In Tap Race: **Space** starts / restarts, **R** returns to the lobby.

### Playing over the internet

Pick **Public** (window button, or `P` at the terminal prompt) and it runs
`cloudflared` for you, grabs the `https://…trycloudflare.com` URL, and rewrites
the QR / join link / host link to use it. Quitting stops the tunnel too. Needs
`cloudflared` on PATH (`brew install cloudflared`); if it's missing or the
tunnel fails, the window offers "Run Local instead".

Skip the choice with `MODE=local` or `MODE=public`. Or point at a tunnel you
started yourself with `PUBLIC_URL=https://… python3 server.py`.

Anyone joining needs **both** the link **and** the room code; the laptop screens
still require the host cookie. Read the security notes below before doing this.

Env knobs: `MODE` (local|public), `GUI` (0 to disable the window), `PUBLIC_URL`,
`ROOM_CODE` (default random), `HOST_TOKEN` (default random — set it for a stable
host bookmark), `MAX_PLAYERS` (12), `JOIN_MAX` (15 join attempts / IP / minute).

## How it works

| Piece | Role |
|-------|------|
| `server.py` | stdlib HTTP server. Holds all game state in memory. |
| `gui.py` | The Tkinter control window (`server.py` calls `gui.run(ctx)` on the main thread; no import back into `server`). Skipped with `--no-gui` / `GUI=0`. |
| `qr.py` | Dependency-free QR-code generator (verified against the `qrcode` package + decoded back with OpenCV). |
| `scattergories.py` | Category pool, letter set, and the pure scoring / alliteration helpers. |
| `taboo.py` | The ~145-card Taboo deck + a shuffle helper. |
| `GET /` → `static/menu.html` | The games menu. Accessible: keyboard-navigable, screen-reader labelled, respects reduced-motion. Lists games from `GET /games`. |
| `GET /host` → `static/host.html` | The game screen. One shell, a view per game, switched by `state["game"]`. Polls `/state` ~2×/second for updates (SSE didn't survive the Cloudflare tunnel). |
| `GET /play` → `static/controller.html` | The phone controller. One shell: "waiting" screen, then the current game's controls. |
| `GET /?host=<token>` | Sets the host cookie (302 → clean path). Any laptop screen needs this cookie or it shows "locked". |
| `GET /amihost` | `{host: bool, code}` from the request's cookie — the menu/host pages use it to lock themselves. |
| `GET /games` | The game catalog (`GAMES` in `server.py`). |
| `GET /whoami` | *(host only)* The public/LAN phone URL + room code. |
| `GET /phoneQR.png` | *(host only)* QR of the join URL (carries `?code=`). Regenerates when the address changes. |
| `POST /select` | *(host only)* `{game}` — pick a game (or `null` for the menu). |
| `POST /join` | `{name, code}` — code must match; rate-limited per IP; capped at `MAX_PLAYERS`. Returns `pid` + color or `{error}`. |
| `POST /input` | `{pid, action, ...}`. Player actions (`tap`, `scatAnswers`, `scatVote`, `tabooGot/Skip/Buzz`, `tabooTeam`, `leave`, `ping`) are open. Host-only actions (`start`, `reset`, `scatStart/Set/EndRound/Next/Lobby/ResetScores`, `tabooStart/Set/BeginTurn/EndTurn/NextTurn/EndGame/NewGame`) require the host cookie — see `HOST_ONLY` in `server.py`. |
| `GET /state?pid=<id>` | Latest state. Needs the host cookie **or** a valid `pid`; otherwise returns `{locked: true}`. A present `pid` also refreshes that player's heartbeat — the controller polls ~2×/second, so it doubles as the keep-alive. |
| `GET /events` | *(host only)* legacy SSE stream — unused now (the host polls). |

**Disconnects:** the controller's `/state?pid=` poll is the heartbeat. A player
that goes silent for `PLAYER_TIMEOUT` (6s) is dropped by `janitor()` (checked
every second). Closing / navigating the controller page fires a `leave` beacon
so a deliberate exit is near-instant. State served to clients is in
`public_state()`; per-game blocks hang off it (`scat`, `taboo`).

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
- Taboo cards: `_CARDS` in `taboo.py` (guess word + 5 taboo words per tuple).
- Port: `PORT` in `server.py`.
- Player colors: `COLORS` in `server.py`.

## Notes

- On LAN, phone and laptop must be on the **same network** and it must allow
  device-to-device traffic (some guest WiFi blocks this — a phone hotspot works).
- macOS may pop a firewall prompt the first time — click **Allow**.

## Security (matters if you expose it via a tunnel)

What's protected:

- **Laptop screens** (`/`, `/host`, `/whoami`, QR, `/events`, `/select`, all
  host game controls) need the host cookie, set only by opening the printed
  `?host=<token>` link. A random visitor gets a "locked" page.
- **Joining** needs the room code. `/state` gives nothing useful without the
  cookie or a live `pid`, so a stray link-holder can't even spectate.
- Rate limit on join attempts (slows room-code guessing) and a hard player cap.

What's still true:

- The tunnel provider terminates TLS and can see traffic.
- The room code is 4 chars — fine against casual guessing, not a real secret.
  Share the link + code only with people you want playing.
- It's a hobby `ThreadingHTTPServer`. Don't leave the tunnel up when you're not
  actively playing, and don't treat this as hardened infrastructure.
- `HOST_TOKEN` regenerates every run unless you set it — so does the room code
  unless you set `ROOM_CODE`.
