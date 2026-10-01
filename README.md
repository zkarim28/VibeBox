# VibeBox — phone-only or shared-screen game night

A small party-game console. Play entirely on phones with one **host-player**,
or use a **laptop/TV** as a shared display. Join over local WiFi or a public link.

Currently playable: **Scattergories**, **Taboo**, **BlackBox**,
**Codenames**, **Wii Sandbox**, **Imposter**, **Mafia**, **Ludo**,
**Word Hunt**, and **BS**. More games are stubbed on the menu as "Coming soon".

No dependencies — just Python 3 (stdlib only).

## Host and play on a phone

1. Start the server as usual, using Public mode for an internet-accessible room.
   The Python server still runs on the hosting computer; no TV or open host
   browser is needed during play.
2. Open `<public-or-LAN-address>/host/play` on your phone and enter the host
   password. The normal host menu also has **Host & play on this phone**;
   the Launcher offers **Host & play on phone** for each running server.
   Startup prints a private one-click phone-host link too.
3. Enter your player name. **Play** is your normal player view, including only
   your own private cards/role. **Host controls** lets you select games, change
   settings, start/end rounds, advance reviews, manage players, and share the
   ordinary player link and QR. Other players use that ordinary link.
4. Reloading resumes your same player seat. Host access uses the existing host
   cookie/password; player requests authenticate with their saved player token.

Phone mode applies to the room. Finish the active round or return to its lobby
before changing modes. Open **Room setup & mode** on the host display, then
**Use shared-screen mode** on the menu to return to the original setup.

Phone-only game adaptations:

- **Taboo:** at least two players per team. A rotating clue-giver and the
  opposing team see the card; guessing teammates never receive it.
- **Codenames:** uses the online rules, with a private key for each spymaster.
- **Imposter:** typed guesses are checked automatically (ignoring case,
  punctuation, and extra spacing). The host can be an imposter too.
- **Mafia:** one phone belongs to the moderator, who receives no role. Choose
  someone else as moderator if the host wants to hold a card.
- **Target Practice:** the Wii Sandbox becomes a touch-target race in phone
  mode. Motion activities remain available in shared-screen mode.
- **Word Hunt:** the server ends the timer without relying on any host display.
- **Scattergories, BlackBox, and Ludo:** boards, answers, judging, results, and
  round controls are available on phones.

Games with spoken clues or discussion still use conversation in the room, or a
separate voice call for remote players. VibeBox does not provide voice chat.

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

### Short join links

Players don't need the long `/play?code=WXYZ` URL. The **room code is the whole
path** now:

| Link | Goes to |
|------|---------|
| `<addr>/WXYZ` | join screen with the code filled in |
| `<addr>/j/WXYZ` | same (explicit form) |
| `<addr>/join` or just `<addr>/` | join screen, type the code yourself |
| `<addr>/host` | the laptop screen (then the host password) |

The join QR encodes the short form too. `<addr>` is your LAN address, the
Cloudflare URL, or your own domain (below).

### Your own domain (e.g. `vibebox.tv`)

Point a domain at the server and players just type **`vibebox.tv/WXYZ`**:

1. Add the domain to a free **Cloudflare** account and create a **named tunnel**
   (`cloudflared tunnel create vibebox`), route it
   (`cloudflared tunnel route dns vibebox vibebox.tv`), and run it
   (`cloudflared tunnel run vibebox`, ideally as a service).
2. Start VibeBox pointed at that hostname:

   ```
   PUBLIC_URL=https://vibebox.tv python3 server.py
   ```

   (skip the built-in quick-tunnel — `PUBLIC_URL` being set already does that).
   Every printed link, the QR, and the emailed/texted links now use
   `vibebox.tv`. For a memorable one-click host link also set a short
   `HOST_TOKEN` (e.g. `HOST_TOKEN=letmein` → `vibebox.tv/?host=letmein`).

### Hosting from another machine

The host screen (menu + game view) is protected by a **password**, so you can
open the host link from any device — your laptop, a friend's, a phone — and
unlock it by typing the password. Set your own with `HOST_PASSWORD=...` (a
git-ignored `.env` file is the easy way — see below); leave it unset and the
server generates a random one each start and prints it in the startup banner.
Your own `?host=…` bookmark still unlocks in one click.

### Get startup links on your phone with ntfy

Set `NOTIFY_WEBHOOK=https://ntfy.sh/your-long-random-topic` in the git-ignored
`.env` file (see `.env.example`). Subscribe to that same topic in the ntfy app
on your phone and allow notifications. Keep the randomly named topic private.

Every server startup sends the **Host & play** link, guest join link, room code,
and computer name. Tap the notification to open the phone host; your host
password is still required. Push messages never include the host unlock token.
This works from the terminal, desktop window, and launcher, including a fixed
`PUBLIC_URL`. Local starts send a link labelled for the same WiFi network;
choose Public mode for links people can open remotely.

Use the **same `NOTIFY_WEBHOOK` setting on every computer** running VibeBox.
The server loads `.env` automatically; the topic is not stored in Git.
Optional `NOTIFY_TOKEN` supplies a bearer token for protected ntfy topics.
Test the setup without starting a game:

```sh
python3 notify.py --test-push
```

The test is clearly labelled and contains example links. Sending happens in
the background with retries for temporary failures. Optional SMTP email/SMS
settings are also supported; `python3 notify.py --test` tests those too.

### Start games from your phone — the Launcher

`launcher.py` is a tiny always-on control panel: from your phone you tap
**Start a public game** and it spins up a `server.py` in public mode on the
laptop, shows the join link + QR + live player count, and gives you a **Stop**
button. No SSH. Run several at once; each gets its own Cloudflare tunnel.

It listens on `127.0.0.1:8790` only — you expose it to your phone with
**Tailscale Funnel** (or a Cloudflare tunnel on your own domain).

```
./setup-launcher.sh          # installs a systemd --user service, starts it
```

Then, one time, run the commands it prints:

```
sudo loginctl enable-linger $USER          # runs at boot / while logged out
sudo tailscale set --operator=$USER        # lets your user manage Tailscale
tailscale funnel --bg 8790                 # put the launcher on the internet
```

If `tailscale funnel` says Funnel isn't enabled for your tailnet, open the link
it prints and enable it, then re-run. Now bookmark
`https://<machine>.<tailnet>.ts.net/` on your phone.

Password: set `LAUNCHER_PASSWORD` in `.env`, or use the auto-generated one in
`~/.vibebox-launcher/password` (also printed to
`journalctl --user -u vibebox-launcher`). A launcher restart re-attaches to
games already running — it never kills a game in progress.

Manage the service: `systemctl --user {status,restart,stop} vibebox-launcher`.

## Taboo

Two teams. Everyone can join with their own phone (players are auto-split; switch
in the lobby). In shared-screen mode, a card (guess word + 5 forbidden words) shows on the big
screen. In phone mode, a rotating clue-giver sees it on their phone while
their teammates guess; the opposing team can see it to check for taboo words.

Before every turn there's a 5-second **"get ready"** countdown (big screen shows
the team that's up; each phone tells its player their role). The host can tap
**Start now** to skip it. Then the turn runs for 60 seconds:

- **Every phone on the clue-giving team** in shared-screen mode shows the card with **✓ Got it**
  (+1 point) and **Skip** buttons.
- **Every phone on the other team** shows the same card with a **🔔 Taboo!**
  buzzer — tap it if the clue-giver says the word or a listed word. The buzzer
  sounds a loud buzz on the big screen (and every phone), so the clue-giver
  hears the moment they slip.
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

## Ludo

The classic race game for **2–4 players, one phone each**. The first four
players to join take the green / yellow / blue / red seats (in that clockwise
order); anyone after that spectates. The laptop draws the shared board.

- Turn order is fixed — green, yellow, blue, red — and the first seat rolls
  first (no "highest roll goes first" phase). On your turn the phone shows a
  **Roll** button; the die lands on the big screen.
- You need a **6** to move a token out of your base onto the board — so a 6 is
  also what "starts" you. A 6 always earns another roll; **three 6s in a row**
  forfeits the whole turn.
- After rolling, your movable tokens **glow on the phone's mini-board** — tap
  one to move it. If there's only one legal move it's played automatically; if
  there's none, the turn passes.
- Land exactly on an opponent (off a safe square) to **capture** it — straight
  back to their base. **Star squares and every colour's start square are safe.**
- Two of your own tokens on one square form a **block**: opponents can't land
  on, pass, or capture it.
- Reaching the final home needs an **exact** roll. **First player to get all
  four tokens home wins.** The host can **End game** early (furthest-along
  player wins) or start a **New game** with the same seats.

## Word Hunt

A Boggle-style word search for **1–20 players**, each on their own phone.
Everyone races the same shared 4×4 letter grid for 90 seconds (configurable);
whoever scores the most wins — no turns, no teams, just swipe.

- Trace a word by dragging across **adjacent letters** (including diagonals);
  lift your finger to submit. You can't reuse a tile within one word.
- Scoring is by length, not letters used: 3 → 100, 4 → 400, 5 → 800,
  6 → 1,400, 7 → 1,800, 8+ → 2,200. Finding `CAT`, then `CATS`, then `CATER`
  on the same cluster of letters stacks fast.
- Every valid find scores instantly — no waiting for round end — and the
  **same word can't be scored twice** by one player, even via a different
  path. Everyone's own word list stays private until the round ends; only
  running scores are visible live, then the full recap is revealed.
- Words are checked against a bundled ~170k-word dictionary
  (`wordhunt_words.txt`, derived from the public-domain ENABLE word list).
  Boards are letter-frequency weighted with 5–7 guaranteed vowels so every
  grid is actually playable.
- The host can set the round length, **Reset scores** to start a fresh
  tally, or run back-to-back rounds — the leaderboard's lifetime totals
  persist across rounds until reset.

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
2. Players scan the QR, or type the short link **`<addr>/<room code>`** (the code
   is the path), then a name and **Join**. Plain **`<addr>/`** works too — they
   just enter the code by hand.
3. On the laptop, pick a game. Players' phones switch to that controller.
4. For phone-only play, choose **Host & play on this phone** and use the
   **Play / Host controls** tabs.

The laptop always shows a **👥 Players** control (top-right on the game screen,
under the join QR on the menu). Tap **Kick** next to a name to remove that
player — their phone drops straight back to the name + code screen (they can
rejoin if you let them).

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
`PORT` (8000), `ROOM_CODE` (default random), `HOST_TOKEN` (default random — set it
for a stable host bookmark), `MAX_PLAYERS` (12), `JOIN_MAX` (15 join attempts / IP
/ minute).

## How it works

| Piece | Role |
|-------|------|
| `server.py` | stdlib HTTP server. Holds all game state in memory. |
| `launcher.py` | Separate always-on service (systemd `--user`, `setup-launcher.sh`). Spawns/monitors/stops `server.py` public instances from a phone-friendly page; state in `~/.vibebox-launcher/`. |
| `gui.py` | The Tkinter control window (`server.py` calls `gui.run(ctx)` on the main thread; no import back into `server`). Skipped with `--no-gui` / `GUI=0`. |
| `qr.py` | Dependency-free QR-code generator (verified against the `qrcode` package + decoded back with OpenCV). |
| `scattergories.py` | Category pool, letter set, and the pure scoring / alliteration helpers. |
| `taboo.py` | The ~145-card Taboo deck + a shuffle helper. |
| `GET /` → `static/menu.html` | The games menu (host only). A non-host request 302s to `/play`, so a player can type the bare domain. Lists games from `GET /games`. |
| `GET /<code>`, `/j/<code>`, `/join` | 302 → `/play?code=<code>` — the short player links. `<code>` must match the room code (a mismatch 404s). |
| `GET /host` → `static/host.html` | The game screen. One shell, a view per game, switched by `state["game"]`. Polls `/state` ~2×/second for updates (SSE didn't survive the Cloudflare tunnel). |
| `GET /host/play` → `static/controller.html` + `static/phone-host.js` | Password-protected host-player setup, private player view, and a separate host-controls tab. |
| `GET /play` → `static/controller.html` | The phone controller. One shell: "waiting" screen, then the current game's controls. |
| `GET /?host=<token>` | Sets the host cookie (302 → clean path). Any laptop screen needs this cookie or it shows "locked". |
| `GET /amihost` | `{host: bool, code}` from the request's cookie — the menu/host pages use it to lock themselves. |
| `GET /games` | The game catalog (`GAMES` in `server.py`). |
| `GET /whoami` | *(host only)* The public/LAN phone URL + room code. |
| `GET /phoneQR.png` | *(host only)* QR of the join URL (carries `?code=`). Regenerates when the address changes. |
| `POST /select` | *(host only)* `{game}` — pick a game (or `null` for the menu). |
| `POST /join` | `{name, code}` — code must match; rate-limited per IP; capped at `MAX_PLAYERS`. Returns `pid` + color or `{error}`. |
| `POST /input` | `{pid, token, action, ...}` for player actions. Host controls require the host cookie (`HOST_ONLY` and role-aware game handlers). `hostMode` changes the room setup between `phone` and `screen`. |
| `GET /state?pid=<id>` | Latest private player state, authenticated with `X-Player-Token`; host-cookie requests can read the shared view without a player ID. Polling also refreshes the player's heartbeat. |
| `GET /events` | *(host only)* legacy SSE stream — unused now (the host polls). |

**Disconnects:** the controller's `/state?pid=` poll is the heartbeat. A player
that goes silent for `PLAYER_TIMEOUT` (6s) is marked reconnecting. They retain
their seat for `PLAYER_GRACE` (90s). Closing/navigating the controller sends a
`leave` beacon; the player can still resume with their saved token. State served to clients is in
`public_state()`; per-game blocks hang off it (`scat`, `taboo`).

## Joining and phone results

The host game screen keeps a shared joining QR, room code, and link visible
throughout every game phase. It uses a side panel on large displays and a
sticky banner on smaller screens.

Phones show ranked results after Scattergories rounds, Taboo turns
and games, BlackBox rounds and games, and Word Hunt rounds. Round points and
cumulative totals are labelled separately, ties share a rank, and your own
row is highlighted. Codenames includes final team results; Wii Target Practice
shows finish order as players complete their targets.

The score-to-results mapping is in `static/results.js`. Run its regression
checks with `node --test tests/results.test.cjs` (Node is only needed for these
checks, not for running VibeBox). Phone-only game and authorization tests run
with `python3 -m unittest discover -s tests`.

## Visual theme

`static/theme.css` supplies the shared midnight-indigo, lavender, and mint theme
for the menu, host display, phone controllers, and launcher. Page-specific
layouts stay in their HTML files; team colors and game feedback retain their
meaning. Hover effects apply to mouse/trackpad devices, keyboard focus is
visible, and reduced-motion preferences disable decorative animation. The
desktop control window uses the matching palette in `gui.py`.

## Adding a game

1. Add an entry to `GAMES` in `server.py` with `"status": "soon"` — it shows
   on the menu immediately as "Coming soon".
2. When you build it: set `"status": "ready"`, give it a sub-state (like
   `state["scat"]`), a `_<game>_public()` block in `public_state()`, an action
   handler dispatched from `do_input`, a `#view-<game>` in `host.html`, and a
   view in `controller.html` keyed on `s.game`. `janitor()` is where per-tick
   logic (timers) lives.

## Tweaks

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
  cookie or a valid player ID and token, so a stray link-holder can't even spectate.
- Rate limit on join attempts (slows room-code guessing) and a hard player cap.

What's still true:

- The tunnel provider terminates TLS and can see traffic.
- The room code is 4 chars — fine against casual guessing, not a real secret.
  Share the link + code only with people you want playing.
- It's a hobby `ThreadingHTTPServer`. Don't leave the tunnel up when you're not
  actively playing, and don't treat this as hardened infrastructure.
- `HOST_TOKEN` regenerates every run unless you set it — so does the room code
  unless you set `ROOM_CODE`.

## BS (Bullshit / Cheat)

For 2–10 players (best with 3–6). Choose one or two standard decks in the
lobby; every card is dealt evenly and the Ace of Spades starts. Each player
selects 1–4 cards (up to 8 with two decks) privately on their phone, claiming
the current rank: Aces through Kings, then back to Aces. Any cards may be
played, so bluffing is allowed.

Other players have **8 seconds** to tap **Call BS!**. Only the latest play is
revealed: a liar picks up the entire pile, otherwise the challenger does.
After a five-second reveal, the next player takes the next rank. Without a
challenge, the pile stays face-down and play advances automatically. The
first empty hand wins only after its final challenge window resolves.

Works in phone-host and computer-host modes; all phones show claims,
challenges, card counts, and the winner. The shared display never sees hands.
Players joining mid-game watch until the next deal. A temporarily disconnected
player keeps their hand; the host can end the deal from Host controls if
someone cannot return. A removed or expired player ends the deal without
awarding a winner, so the host can deal again.
