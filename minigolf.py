"""
Mini golf (2D, top-down) for VibeBox. Call under the server state lock.

Rules (Smugglers Cove / Lost Treasure house rules)
  * Holes are played in order. Lowest total strokes wins.
  * Everyone tees off before anyone takes a second stroke. After that the ball
    farthest from the cup goes next (or closest, if the host picks that house
    rule). On later holes the best score on the previous hole tees off first.
  * Stroke limit (mercy rule, default 6): reach it without sinking and the
    ball is picked up and a 6 is recorded.
  * Water, sand traps, the kraken's mouth and out of bounds: +1 stroke and the
    ball goes back to where it left the playing surface.
  * A ball resting against a rail (or in a windmill's/drawbridge's path) is
    moved one clubhead away, no penalty.
  * Turn-taking groups hold at most 4-5 balls; groups play the same hole side
    by side. "Together" pace drops turn order so big rooms keep moving.

Formats: solo (everyone for themselves), best (teams, each player plays a
ball, a team's hole score is its best ball), alternate (teams share one ball
and take turns hitting it).

The server owns the physics. A shot is simulated here once, deterministically
(moving obstacles run off a shared clock), and the resulting path is sent to
every screen to animate. Phones only send an angle and a power.

Courses are generated: each hole is a fairway carved out of an 8x12 grid of
10-unit cells (straight, dogleg, zigzag, U, open arena), then dressed with
obstacles drawn from the kinds the host enabled. Every hole is checked to be
playable (a ball-sized path from tee to cup) before it's used.
"""

import math
import random
import secrets
import time
from collections import deque

import minigolf_trivia

# ----------------------------------------------------------------- course ---
W, H, C = 80, 120, 10
COLS, ROWS = W // C, H // C
R = 1.1                  # ball radius
CUP_R = 2.0
SINK_SPEED = 46          # faster than this and the ball lips over the cup
MAX_SPEED = 135
FRICTION = 26            # rolling resistance, units/s^2
DRAG = 0.22              # extra slowdown proportional to speed, per second
REST = 0.72              # rail bounce
DT = 1 / 240
FPS = 20                 # path samples sent to screens
MAX_SIM = 14
START_DELAY = 0.5        # a shot starts a beat after it is struck, so every screen (polling) sees it from the start
NUDGE = 4.0              # "one clubhead length" off a rail
HOLE_PAUSE = 7           # scorecard between holes, seconds
TRIVIA_STROKES = 3       # putts you get on a trivia hole to sink into an answer
TRIVIA_TIME = 75         # seconds before a trivia hole closes
TRIVIA_REVEAL = 7        # seconds showing the right answer afterwards
GRAVITY = 70             # pulls an airborne (trampolined) ball back down, units/s^2
RAIL_H = 2.5             # a ball higher than this sails over the rails

FORMATS = ["solo", "best", "alternate"]
THEMES = {
    "pirate": {"name": "Pirate Cove", "a": ["Smuggler's", "Kraken", "Rum Runner's", "Cutlass", "Barnacle", "Parrot", "Davy Jones'", "Shipwreck", "Skull", "Treasure"],
               "b": ["Cove", "Point", "Lagoon", "Reef", "Bay", "Hideout", "Landing", "Island", "Grotto", "Harbor"]},
    "neon": {"name": "Neon Nights", "a": ["Neon", "Disco", "Laser", "Glow", "Arcade", "Synth", "Cosmic", "Electric", "Jackpot", "Retro"],
             "b": ["Alley", "Lounge", "Strip", "Palace", "Arena", "Club", "District", "Boulevard", "Casino", "Rink"]},
    "candy": {"name": "Sugar Rush", "a": ["Gumdrop", "Lollipop", "Marshmallow", "Candy Cane", "Bubblegum", "Toffee", "Sprinkle", "Jellybean", "Fudge", "Taffy"],
              "b": ["Lane", "Hills", "Factory", "Valley", "Swirl", "Meadow", "Avenue", "Kingdom", "Falls", "Parlor"]},
    "jungle": {"name": "Lost Jungle", "a": ["Temple", "Monkey", "Vine", "Jaguar", "Tiki", "Banana", "Toucan", "Ruins", "Python", "Mango"],
               "b": ["Falls", "Trail", "Grove", "Canopy", "Basin", "Hollow", "Ridge", "Run", "Swamp", "Shrine"]},
    "space": {"name": "Lunar Links", "a": ["Lunar", "Nebula", "Comet", "Asteroid", "Saturn", "Galaxy", "Orbit", "Rocket", "Quasar", "Meteor"],
              "b": ["Crater", "Station", "Belt", "Rings", "Base", "Drift", "Outpost", "Way", "Dock", "Colony"]},
}
THEME_CHOICES = list(THEMES) + ["random", "mix"]
COIN_PTS = 50
SCORE = {"bumper": 25, "coin": COIN_PTS, "portal": 30, "boost": 10, "tramp": 40, "spin": 30}
SINK_PTS = {1: 500, -3: 400, -2: 300, -1: 150, 0: 75}      # hole in one / albatross / eagle / birdie / par
MAX_MULT = 5
PACES = ["turns", "together"]
ORDERS = ["far", "near"]
AMOUNTS = {"few": 1, "some": 2, "lots": 3}
LIMITS = [6, 8, 10]
HOLE_COUNTS = [9, 18]
TEAM_NAMES = ["Red Parrots", "Blue Sharks", "Gold Doubloons", "Green Krakens"]
TEAM_COLORS = ["#ff5d73", "#4cc9f0", "#ffc94d", "#5be38c"]

OBSTACLES = [
    {"id": "windmill", "name": "Ye Olde Windmill", "emoji": "\N{WIND BLOWING FACE}", "blurb": "Spinning blades guard the way. Time your putt."},
    {"id": "water", "name": "Shark Lagoon", "emoji": "\N{SHARK}", "blurb": "Water hazard: +1 stroke, back to where you went in."},
    {"id": "sand", "name": "Sandy Bottoms", "emoji": "\N{BEACH WITH UMBRELLA}", "blurb": "Sand trap: +1 stroke, back to where you went in."},
    {"id": "bumpers", "name": "Boing Barrels", "emoji": "\N{OIL DRUM}", "blurb": "Bouncy barrels that kick the ball away faster."},
    {"id": "slope", "name": "Sneaky Slopes", "emoji": "\N{MOUNTAIN}", "blurb": "Hills that bend your putt downhill."},
    {"id": "portal", "name": "Treasure Tunnels", "emoji": "\N{HOLE}", "blurb": "Drop into one hole, pop out the other."},
    {"id": "boost", "name": "Turbo Arrows", "emoji": "\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE}", "blurb": "Speed pads fling the ball the way they point."},
    {"id": "gate", "name": "Drawbridge", "emoji": "\N{DOOR}", "blurb": "A sliding gate that keeps opening and closing."},
    {"id": "ice", "name": "Slip 'n' Slide", "emoji": "\N{ICE CUBE}", "blurb": "Ice: the ball barely slows down."},
    {"id": "kraken", "name": "Kraken Whirlpool", "emoji": "\N{OCTOPUS}", "blurb": "Pulls the ball in; its mouth swallows it (+1)."},
    {"id": "tramp", "name": "Boing-Boing Trampoline", "emoji": "\N{UPWARDS BLACK ARROW}", "blurb": "Launches the ball into the air, over hazards (and maybe the rails)."},
    {"id": "spin", "name": "Spin Cycle", "emoji": "\N{CYCLONE}", "blurb": "A roulette wheel that flings the ball wherever its arrow points."},
]
OBS_IDS = [o["id"] for o in OBSTACLES]
HAZARDS = {"water", "sand"}



def _dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def _closest(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L))
    return x0 + t * dx, y0 + t * dy


def _carve(pts, w):
    cells = set()
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        for x in range(min(ax, bx), max(ax, bx) + 1):
            for y in range(min(ay, by), max(ay, by) + 1):
                for i in range(w):
                    for j in range(w):
                        cells.add((x + i, y + j))
    return cells


def _layout(rng):
    """(cells, tee, cup, width, shape) for one fairway."""
    shape = rng.choice(["straight", "dogleg", "dogleg", "zigzag", "zigzag", "u", "arena",
                        "snake", "stairs", "ring", "ring", "cross"])
    if shape == "ring":
        # a loop round a central block: two ways to the cup
        aw, ah = rng.randint(5, 8), rng.randint(7, 12)
        ax, ay = rng.randint(0, COLS - aw), rng.randint(0, ROWS - ah)
        t = 2 if aw >= 6 and rng.random() < .6 else 1
        if aw - 2 * t < 1:
            t = 1
        cells = {(x, y) for x in range(ax, ax + aw) for y in range(ay, ay + ah)}
        cells -= {(x, y) for x in range(ax + t + (aw >= 7), ax + aw - t) for y in range(ay + 2, ay + ah - 2)}
        mid = ax + aw / 2
        tee = (mid * C + rng.uniform(-3, 3), (ay + ah - .9) * C)
        cup = (mid * C + rng.uniform(-6, 6), (ay + 1) * C)
        return cells, tee, cup, 2, shape
    if shape == "cross":
        # a plus sign: tee on one arm, cup on another, dead-end arms to tempt you
        cx, cy = rng.randint(2, COLS - 4), rng.randint(3, ROWS - 5)
        cells = _carve([(cx, ROWS - 2), (cx, 0)], 2) | _carve([(0, cy), (COLS - 2, cy)], 2)
        arms = [((cx + 1) * C, (ROWS - .9) * C), ((cx + 1) * C, 1 * C), (1 * C, (cy + 1) * C), ((COLS - 1) * C, (cy + 1) * C)]
        tee, cup = rng.sample(arms, 2)
        return cells, tee, cup, 2, shape
    if shape == "arena":
        aw, ah = rng.randint(4, 6), rng.randint(7, 11)
        ax, ay = rng.randint(0, COLS - aw), rng.randint(0, ROWS - ah)
        cells = {(x, y) for x in range(ax, ax + aw) for y in range(ay, ay + ah)}
        tee = ((ax + rng.uniform(1, aw - 1)) * C, (ay + ah - 1) * C)
        cup = ((ax + rng.uniform(1, aw - 1)) * C, (ay + 1.2) * C)
        # a few rock islands in the middle (never on the tee or cup)
        inner = [(x, y) for x in range(ax + 1, ax + aw - 1) for y in range(ay + 2, ay + ah - 2)]
        for cell in rng.sample(inner, min(len(inner), rng.randint(1, 3))):
            cells.discard(cell)
        return cells, tee, cup, 3, shape
    w = rng.choice([2, 2, 3])
    mx, my = COLS - w, ROWS - w
    x0 = rng.randint(0, mx)
    other = lambda x: rng.choice([v for v in range(mx + 1) if abs(v - x) >= 2] or [x])
    if shape == "snake":
        # back and forth across the course, climbing a row of corridors at a time
        w = 2
        mx, my = COLS - w, ROWS - w
        pts, y, left = [(0, my)], my, True
        pts = [(rng.randint(0, 1), my)]
        while y - 3 >= 0:
            x = mx if left else rng.randint(0, 1)
            pts.append((x, y))
            y -= 3
            pts.append((x, y))
            left = not left
        pts.append((mx if left else 0, y))
        cells = _carve(pts, w)
        tee = ((pts[0][0] + w / 2) * C, (pts[0][1] + w / 2) * C)
        cup = ((pts[-1][0] + w / 2) * C, (pts[-1][1] + w / 2) * C)
        return cells, tee, cup, w, shape
    if shape == "stairs":
        w = 2
        mx, my = COLS - w, ROWS - w
        step = rng.choice([1, 2])
        right = rng.random() < .5
        x, y = (0 if right else mx), my
        pts = [(x, y)]
        while y > 0:
            y = max(0, y - step)
            pts.append((x, y))
            nx = x + (step if right else -step)
            if not 0 <= nx <= mx:
                right = not right
                nx = x + (step if right else -step)
            x = nx
            pts.append((x, y))
        cells = _carve(pts, w)
        tee = ((pts[0][0] + w / 2) * C, (pts[0][1] + w / 2) * C)
        cup = ((pts[-1][0] + w / 2) * C, (pts[-1][1] + w / 2) * C)
        return cells, tee, cup, w, shape
    if shape == "straight":
        pts = [(x0, my), (x0, rng.randint(0, 2))]
        if rng.random() < .5:                         # a jog halfway up
            y1 = rng.randint(3, my - 3)
            x1 = other(x0)
            pts = [(x0, my), (x0, y1), (x1, y1), (x1, rng.randint(0, 1))]
    elif shape == "dogleg":
        y1 = rng.randint(0, max(0, my - 4))
        pts = [(x0, my), (x0, y1), (other(x0), y1)]
    elif shape == "zigzag":
        pts, x, y = [(x0, my)], x0, my
        while y > 2:
            y = max(0, y - rng.randint(2, 4))
            pts.append((x, y))
            x = other(x)
            pts.append((x, y))
    else:   # u
        x0 = rng.randint(0, max(0, (mx - w - 1) // 2))
        x1 = rng.randint(min(mx, x0 + w + 1), mx)
        y1 = rng.randint(0, 3)
        pts = [(x0, my), (x0, y1), (x1, y1), (x1, rng.randint(min(my, y1 + w + 2), my))]
    cells = _carve(pts, w)
    tee = ((pts[0][0] + w / 2) * C, (pts[0][1] + w / 2) * C)
    cup = ((pts[-1][0] + w / 2) * C, (pts[-1][1] + w / 2) * C)
    return cells, tee, cup, w, shape


def _walls(cells):
    """Merged rail segments (in units) around a set of cells."""
    h, v = {}, {}
    for x, y in cells:
        if (x, y - 1) not in cells:
            h.setdefault(y, []).append(x)
        if (x, y + 1) not in cells:
            h.setdefault(y + 1, []).append(x)
        if (x - 1, y) not in cells:
            v.setdefault(x, []).append(y)
        if (x + 1, y) not in cells:
            v.setdefault(x + 1, []).append(y)
    segs = []
    for line, runs, horiz in [(k, s, True) for k, s in h.items()] + [(k, s, False) for k, s in v.items()]:
        runs = sorted(set(runs))
        start = prev = runs[0]
        for r in runs[1:] + [None]:
            if r is not None and r == prev + 1:
                prev = r
                continue
            if horiz:
                segs.append((start * C, line * C, (prev + 1) * C, line * C))
            else:
                segs.append((line * C, start * C, line * C, (prev + 1) * C))
            if r is not None:
                start = prev = r
    return segs


def _index(walls):
    idx = {}
    for s in walls:
        x0, y0, x1, y1 = s
        for cx in range(int(min(x0, x1) // C) - 1, int(max(x0, x1) // C) + 1):
            for cy in range(int(min(y0, y1) // C) - 1, int(max(y0, y1) // C) + 1):
                idx.setdefault((cx, cy), []).append(s)
    return idx


def _in_rect(o, x, y, pad=0.0):
    return o["x"] - pad <= x <= o["x"] + o["w"] + pad and o["y"] - pad <= y <= o["y"] + o["h"] + pad


def _wall_clear(idx, x, y):
    best = 99.0
    for s in idx.get((int(x // C), int(y // C)), ()):
        px, py = _closest(x, y, *s)
        best = min(best, _dist(x, y, px, py))
    return best


def _blocked(o, x, y):
    """True if a resting/rolling ball can't sit here (for the playability check)."""
    k = o["k"]
    if k in HAZARDS:
        return _in_rect(o, x, y)
    if k == "bumpers":
        return any(_dist(x, y, b[0], b[1]) < b[2] + R for b in o["b"])
    if k == "kraken":
        return _dist(x, y, o["x"], o["y"]) < 2.6
    if k == "windmill":
        return _dist(x, y, o["x"], o["y"]) < 1.6 + R
    return False


def _footprint(o, x, y, pad):
    """True if (x, y) is on top of an obstacle (used to keep coins clear)."""
    k = o["k"]
    if "w" in o and "h" in o and k not in ("windmill", "gate"):
        return _in_rect(o, x, y, pad)
    if k == "bumpers":
        return any(_dist(x, y, b[0], b[1]) < b[2] + pad for b in o["b"])
    if k == "portal":
        return _dist(x, y, o["ax"], o["ay"]) < o["r"] + pad or _dist(x, y, o["bx"], o["by"]) < o["r"] + pad
    if k in ("kraken", "tramp", "spin"):
        return _dist(x, y, o["x"], o["y"]) < o["r"] + pad
    if k == "windmill":
        return _dist(x, y, o["x"], o["y"]) < o["L"] + pad
    if k == "gate":
        return _dist(x, y, o["x"], o["y"]) < o["amp"] + o["len"] / 2 + pad
    return False


def _progress(cells, start):
    """BFS distance (in cells) from the tee cell, for "along the path" choices."""
    d, q = {start: 0}, deque([start])
    while q:
        x, y = q.popleft()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n in cells and n not in d:
                d[n] = d[(x, y)] + 1
                q.append(n)
    return d


def _place(rng, kind, cells, tee, cup, taken, prog):
    def far(cx, cy):
        return _dist(cx, cy, *tee) > 14 and _dist(cx, cy, *cup) > 14

    singles = [c for c in cells if c not in taken and far((c[0] + .5) * C, (c[1] + .5) * C)]
    blocks = [(x, y) for (x, y) in cells
              if all(b in cells and b not in taken for b in ((x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)))
              and far((x + 1) * C, (y + 1) * C)]
    rng.shuffle(singles)
    rng.shuffle(blocks)
    T = lambda t: round(t, 2)

    if kind in ("water", "sand") and singles:
        x, y = singles[0]
        cover = [(x, y)]
        if rng.random() < .45:                     # sometimes a longer pond / bunker
            for n in ((x + 1, y), (x, y + 1)):
                if n in cells and n not in taken:
                    cover.append(n)
                    break
        xs, ys = [c[0] for c in cover], [c[1] for c in cover]
        o = {"k": kind, "x": min(xs) * C + 1.2, "y": min(ys) * C + 1.2,
             "w": (max(xs) - min(xs) + 1) * C - 2.4, "h": (max(ys) - min(ys) + 1) * C - 2.4}
        return o, cover
    if kind in ("ice", "slope") and blocks:
        x, y = blocks[0]
        o = {"k": kind, "x": x * C, "y": y * C, "w": 2 * C, "h": 2 * C}
        if kind == "slope":
            o["a"] = T(rng.randrange(8) * math.pi / 4)
        return o, [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
    if kind == "boost" and singles:
        x, y = singles[0]
        nxt = max(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)), key=lambda n: prog.get(n, -1))
        a = math.atan2(nxt[1] - y, nxt[0] - x) if prog.get(nxt, -1) > prog.get((x, y), 0) else rng.randrange(4) * math.pi / 2
        return {"k": "boost", "x": x * C + 2, "y": y * C + 2, "w": 6, "h": 6, "a": T(a)}, [(x, y)]
    if kind == "tramp" and singles:
        x, y = singles[0]
        return {"k": "tramp", "x": (x + .5) * C, "y": (y + .5) * C, "r": 3.2}, [(x, y)]
    if kind == "spin" and blocks:
        x, y = blocks[0]
        return ({"k": "spin", "x": (x + 1) * C, "y": (y + 1) * C, "r": 4.5,
                 "w": T(rng.choice([-1, 1]) * rng.uniform(2.2, 3.4)), "ph": T(rng.uniform(0, 6.28))},
                [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)])
    if kind == "bumpers" and blocks:
        x, y = blocks[0]
        b = []
        for _ in range(30):
            if len(b) >= rng.choice([2, 3]):
                break
            bx, by = x * C + rng.uniform(4, 16), y * C + rng.uniform(4, 16)
            if all(_dist(bx, by, p[0], p[1]) > 7.5 for p in b):
                b.append([round(bx, 1), round(by, 1), 2.3])
        return {"k": "bumpers", "b": b}, [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
    if kind == "portal" and len(singles) >= 2:
        a = singles[0]
        later = [c for c in singles[1:] if prog.get(c, 0) >= prog.get(a, 0) + 2] or singles[1:]
        b = rng.choice(later)
        return ({"k": "portal", "ax": (a[0] + .5) * C, "ay": (a[1] + .5) * C,
                 "bx": (b[0] + .5) * C, "by": (b[1] + .5) * C, "r": 2.4}, [a, b])
    if kind in ("kraken", "windmill", "gate") and blocks:
        x, y = blocks[0]
        cx, cy = (x + 1) * C, (y + 1) * C
        cover = [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
        if kind == "kraken":
            return {"k": "kraken", "x": cx, "y": cy, "r": 7.5}, cover
        if kind == "windmill":
            return {"k": "windmill", "x": cx, "y": cy, "L": 7.4,
                    "w": T(rng.choice([-1, 1]) * rng.uniform(1.1, 1.8)), "ph": T(rng.uniform(0, 3.14))}, cover
        # gate slides across the way the fairway runs
        vertical_run = (x, y - 1) in cells or (x, y + 2) in cells
        return {"k": "gate", "x": cx, "y": cy, "ax": 1 if vertical_run else 0, "len": 9.0, "amp": 5.0,
                "w": T(rng.uniform(1.0, 1.7)), "ph": T(rng.uniform(0, 6.28))}, cover
    return None, []


def _path_len(cells, idx, obs, tee, cup, step=2.0):
    """Length of the shortest ball-sized path tee→cup, or None if there isn't one."""
    def ok(i, j):
        x, y = (i + .5) * step, (j + .5) * step
        if (int(x // C), int(y // C)) not in cells or _wall_clear(idx, x, y) < R + 0.2:
            return False
        return not any(_blocked(o, x, y) for o in obs)

    nx, ny = int(W / step), int(H / step)
    start = (int(tee[0] / step), int(tee[1] / step))
    goal = (int(cup[0] / step), int(cup[1] / step))
    if not ok(*start) or not ok(*goal):
        return None
    seen, q = {start: 0}, deque([start])
    while q:
        i, j = q.popleft()
        if (i, j) == goal:
            return seen[(i, j)] * step
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                n = (i + di, j + dj)
                if n not in seen and 0 <= n[0] < nx and 0 <= n[1] < ny and ok(*n):
                    seen[n] = seen[(i, j)] + 1
                    q.append(n)
    return None


def _coins(rng, cells, idx, obs, tee, cup):
    """3-6 coins on open fairway, clear of the tee, cup and obstacles."""
    out, tries = [], 0
    want = rng.randint(3, 6)
    cl = sorted(cells)
    while len(out) < want and tries < 200:
        tries += 1
        cx, cy = rng.choice(cl)
        x, y = cx * C + rng.uniform(2.5, 7.5), cy * C + rng.uniform(2.5, 7.5)
        if _dist(x, y, *tee) < 9 or _dist(x, y, *cup) < 6 or _wall_clear(idx, x, y) < 2.2:
            continue
        if any(_footprint(o, x, y, 2.0) or _blocked(o, x, y) for o in obs):
            continue
        if any(_dist(x, y, a, b) < 6 for a, b in out):
            continue
        out.append([round(x, 1), round(y, 1)])
    return out


def make_hole(rng, number, kinds, amount, limit, theme="pirate"):
    for _ in range(400):
        cells, tee, cup, w, shape = _layout(rng)
        if _dist(*tee, *cup) < 35:
            continue
        walls = _walls(cells)
        idx = _index(walls)
        prog = _progress(cells, (int(tee[0] // C), int(tee[1] // C)))
        obs, taken = [], set()
        want = AMOUNTS.get(amount, 2) + (rng.random() < .35)
        for kind in (rng.choice(kinds) for _ in range(want if kinds else 0)):
            o, cover = _place(rng, kind, cells, tee, cup, taken, prog)
            if o:
                obs.append(o)
                taken.update(cover)
        length = _path_len(cells, idx, obs, tee, cup)
        if length is None:
            continue
        par = 2 if length < 70 else 3 if length < 140 else 4
        if sum(o["k"] in HAZARDS or o["k"] in ("kraken", "windmill") for o in obs) >= 2:
            par += 1
        if shape in ("snake", "stairs") and length > 180:
            par += 1
        par = max(2, min(par, limit - 1))
        return {"n": number, "shape": shape, "par": par, "cells": sorted(cells), "theme": theme,
                "walls": walls, "tee": [round(tee[0], 1), round(tee[1], 1)],
                "cup": [round(cup[0], 1), round(cup[1], 1)], "obs": obs,
                "coins": _coins(rng, cells, idx, obs, tee, cup)}
    # never expected: a plain straight hole
    cells = _carve([(3, ROWS - 2), (3, 0)], 2)
    walls = _walls(cells)
    return {"n": number, "shape": "straight", "par": 2, "cells": sorted(cells), "walls": walls, "theme": theme,
            "tee": [40.0, 110.0], "cup": [40.0, 10.0], "obs": [], "coins": [[40.0, 60.0]]}


TRIVIA_SAFE = {"bumpers", "slope", "boost", "tramp", "spin", "windmill", "gate", "ice", "sand", "water"}


def make_trivia_hole(rng, kinds, theme, n_opts=4):
    """A wide open hole with one cup per answer, each at the top of its own
    lane, and a few obstacles from the host's list in the middle."""
    cells = {(x, y) for x in range(COLS) for y in range(ROWS)}
    lane = W / n_opts
    dividers = [(round(lane * i, 1), 0, round(lane * i, 1), 22) for i in range(1, n_opts)]
    walls = _walls(cells) + dividers
    idx = _index(walls)
    cups = [[round(lane * (i + .5), 1), 9.0] for i in range(n_opts)]
    tee = [40.0, 111.0]
    mid = {(x, y) for x in range(COLS) for y in range(4, 9)}
    pool = [k for k in kinds if k in TRIVIA_SAFE] or ["bumpers"]
    for _ in range(100):
        obs, taken = [], set()
        prog = _progress(cells, (4, 11))
        for kind in (rng.choice(pool) for _ in range(rng.randint(1, 3))):
            o, cover = _place(rng, kind, mid, tee, cups[0], taken, prog)
            if o and not any(_footprint(o, cx, cy, 6) for cx, cy in cups):
                obs.append(o)
                taken.update(cover)
        if all(_path_len(cells, idx, obs, tee, c) for c in cups):
            break
    else:
        obs = []
    return {"n": 0, "key": "t" + secrets.token_hex(3), "trivia": True, "shape": "trivia", "par": 2,
            "cells": sorted(cells), "walls": walls, "tee": tee, "cup": cups[0], "cups": cups, "obs": obs,
            "theme": theme, "coins": _coins(rng, mid, idx, obs, tee, cups[0])}


def make_course(cfg, seed=None):
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    kinds = list(OBS_IDS) if cfg["obsMode"] == "surprise" else [k for k in cfg["obstacles"] if k in OBS_IDS]
    theme = cfg.get("theme", "pirate")
    if theme == "random" or theme not in THEME_CHOICES:
        theme = rng.choice(list(THEMES))
    holes = []
    for n in range(cfg["holes"]):
        pick = kinds
        if cfg["obsMode"] == "surprise":
            pick = rng.sample(OBS_IDS, rng.randint(2, 4))
        hole_theme = rng.choice(list(THEMES)) if theme == "mix" else theme
        holes.append(make_hole(rng, n + 1, pick, cfg["amount"], cfg["limit"], hole_theme))
    if theme == "mix":
        return f"The Grand Tour ({cfg['holes']} worlds)", holes
    t = THEMES[theme]
    return f"{rng.choice(t['a'])} {t['b'][rng.randrange(len(t['b']))]}", holes


# ---------------------------------------------------------------- physics ---
_IDX = {}


def _hole_index(hole):
    key = id(hole)
    if key not in _IDX or _IDX[key][0] is not hole:
        if len(_IDX) > 64:
            _IDX.clear()
        _IDX[key] = (hole, _index([tuple(s) for s in hole["walls"]]), {tuple(c) for c in hole["cells"]})
    return _IDX[key][1], _IDX[key][2]


def _blades(o, T):
    th = o["ph"] + o["w"] * T
    out = []
    for a in (th, th + math.pi / 2):
        ux, uy = math.cos(a), math.sin(a)
        out.append((o["x"] - ux * o["L"], o["y"] - uy * o["L"], o["x"] + ux * o["L"], o["y"] + uy * o["L"]))
    return out


def _gate(o, T):
    """(segment, velocity) of a drawbridge at clock T."""
    sx, sy = (1, 0) if o["ax"] else (0, 1)
    off = o["amp"] * math.sin(o["w"] * T + o["ph"])
    vel = o["amp"] * o["w"] * math.cos(o["w"] * T + o["ph"])
    cx, cy = o["x"] + sx * off, o["y"] + sy * off
    h = o["len"] / 2
    return (cx - sx * h, cy - sy * h, cx + sx * h, cy + sy * h), (sx * vel, sy * vel)


def _bounce(x, y, vx, vy, px, py, e=REST, bvx=0.0, bvy=0.0, radius=R):
    """Push the ball out of contact point (px, py) and reflect its velocity
    relative to a surface moving at (bvx, bvy)."""
    d = _dist(x, y, px, py)
    if d >= radius or d == 0:
        return x, y, vx, vy, False
    nx, ny = (x - px) / d, (y - py) / d
    x, y = px + nx * radius, py + ny * radius
    rvx, rvy = vx - bvx, vy - bvy
    dot = rvx * nx + rvy * ny
    if dot < 0:
        vx -= (1 + e) * dot * nx
        vy -= (1 + e) * dot * ny
    return x, y, vx, vy, True


def _hazard_at(hole, cells, x, y):
    if (int(x // C), int(y // C)) not in cells or not (0 <= x <= W and 0 <= y <= H):
        return "oob"
    for o in hole["obs"]:
        if o["k"] in HAZARDS and _in_rect(o, x, y):
            return o["k"]
        if o["k"] == "kraken" and _dist(x, y, o["x"], o["y"]) < 1.8:
            return "kraken"
    return None


PENALTY_TEXT = {"water": "Splash! Into Shark Lagoon. +1 stroke", "sand": "Sand trap! +1 stroke",
                "kraken": "The kraken swallowed it! +1 stroke", "oob": "Out of bounds! +1 stroke"}


def simulate(hole, start, angle, power, clock, coins_left=None):
    """Roll one shot. `clock` is the obstacle clock (seconds) at the moment the
    ball starts moving; `coins_left` is [(index, x, y)] still on the hole.
    Returns dict(frames, cuts, end, dur, sunk, penalty, events). Frames are
    [x, y] or [x, y, height] while the ball is in the air."""
    idx, cells = _hole_index(hole)
    x, y = start
    sp = MAX_SPEED * max(0.03, min(1.0, power))
    vx, vy = math.cos(angle) * sp, math.sin(angle) * sp
    z = vz = 0.0
    frames, cuts, events = [[round(x, 1), round(y, 1)]], [], []
    coins = list(coins_left or [])
    safe = (x, y)
    t, step, per = 0.0, 0, round(1 / (DT * FPS))
    lock_portal, lipped, sunk, penalty = None, False, False, 0
    locks = {}                      # obstacle id -> True while the ball is still on it
    last_bump = {}
    cups = hole.get("cups") or [hole["cup"]]        # a trivia hole has one cup per answer
    cx, cy = cups[0]
    cup_i = 0
    still = 0.0

    def ev(kind, text=""):
        events.append({"t": round(t, 2), "k": kind, "text": text, "x": round(x, 1), "y": round(y, 1)})

    def frame():
        frames.append([round(x, 1), round(y, 1)] + ([round(z, 1)] if z > 0.05 else []))

    while t < MAX_SIM:
        t += DT
        step += 1
        T = clock + t
        air = z > 0
        fric, ax, ay = FRICTION, 0.0, 0.0
        drag = DRAG
        if air:
            fric, drag = 0.0, DRAG * 0.25
        else:
            for o in hole["obs"]:
                k = o["k"]
                if k == "ice" and _in_rect(o, x, y):
                    fric, drag = FRICTION * 0.15, DRAG * 0.3
                elif k == "slope" and _in_rect(o, x, y):
                    ax += math.cos(o["a"]) * 24
                    ay += math.sin(o["a"]) * 24
                elif k == "boost" and _in_rect(o, x, y):
                    ax += math.cos(o["a"]) * 240
                    ay += math.sin(o["a"]) * 240
                    if not locks.get(id(o)):
                        locks[id(o)] = True
                        ev("boost")
                elif k == "boost":
                    locks.pop(id(o), None)
                elif k == "kraken":
                    d = _dist(x, y, o["x"], o["y"])
                    if 0 < d < o["r"]:
                        pull = 12 + 58 * (1 - d / o["r"])
                        ax += (o["x"] - x) / d * pull
                        ay += (o["y"] - y) / d * pull
        speed = math.hypot(vx, vy)
        if len(cups) > 1:
            cup_i = min(range(len(cups)), key=lambda i: (x - cups[i][0]) ** 2 + (y - cups[i][1]) ** 2)
            cx, cy = cups[cup_i]
        dcup = _dist(x, y, cx, cy)
        if not air and dcup < CUP_R + 1.6 and speed < 22 and dcup > 0:      # the cup's lip draws a slow ball in
            ax += (cx - x) / dcup * 45
            ay += (cy - y) / dcup * 45
        vx += ax * DT
        vy += ay * DT
        speed = math.hypot(vx, vy)
        cap = MAX_SPEED * 1.15
        if speed > cap:
            vx, vy, speed = vx * cap / speed, vy * cap / speed, cap
        if speed > 0:
            ns = max(0.0, speed - (fric + drag * speed) * DT)
            vx, vy = vx * ns / speed, vy * ns / speed
        x += vx * DT
        y += vy * DT
        if air:
            vz -= GRAVITY * DT
            z += vz * DT
            if z <= 0:
                z = 0.0
                if vz < -14:                 # a little bounce on landing
                    vz = -vz * 0.35
                    z = 0.01
                else:
                    vz = 0.0
            air = z > 0

        # rails (a high ball sails over them)
        if z < RAIL_H:
            for s in idx.get((int(x // C), int(y // C)), ()):
                px, py = _closest(x, y, *s)
                x, y, vx, vy, _ = _bounce(x, y, vx, vy, px, py)
        # obstacles you can hit
        for o in hole["obs"]:
            k = o["k"]
            if z >= 2:
                break
            if k == "bumpers":
                for n, (bx, by, br) in enumerate(o["b"]):
                    d = _dist(x, y, bx, by)
                    if 0 < d < br + R:
                        nx, ny = (x - bx) / d, (y - by) / d
                        x, y = bx + nx * (br + R), by + ny * (br + R)
                        dot = vx * nx + vy * ny
                        if dot < 0:
                            vx -= 2 * dot * nx
                            vy -= 2 * dot * ny
                            vx += nx * 28
                            vy += ny * 28
                            key = (id(o), n)
                            if t - last_bump.get(key, -9) > 0.25:
                                last_bump[key] = t
                                ev("bumper")
            elif k == "windmill":
                hx, hy = o["x"], o["y"]
                if _dist(x, y, hx, hy) < o["L"] + R + 1:
                    x, y, vx, vy, _ = _bounce(x, y, vx, vy, *_closest(x, y, hx, hy, hx, hy), radius=1.6 + R)
                    for seg in _blades(o, T):
                        px, py = _closest(x, y, *seg)
                        rx, ry = px - hx, py - hy        # blade surface speed = w x r
                        x, y, vx, vy, _ = _bounce(x, y, vx, vy, px, py, 0.6, -o["w"] * ry, o["w"] * rx)
            elif k == "gate":
                seg, (gvx, gvy) = _gate(o, T)
                px, py = _closest(x, y, *seg)
                x, y, vx, vy, _ = _bounce(x, y, vx, vy, px, py, 0.6, gvx, gvy)
            elif air:
                continue
            elif k == "portal":
                for (ix, iy, ox, oy, name) in ((o["ax"], o["ay"], o["bx"], o["by"], "a"), (o["bx"], o["by"], o["ax"], o["ay"], "b")):
                    d = _dist(x, y, ix, iy)
                    if lock_portal == name and d > o["r"] + R:
                        lock_portal = None
                    if lock_portal is None and d < o["r"] and math.hypot(vx, vy) > 3:
                        frame()
                        cuts.append(len(frames))
                        x, y = ox, oy
                        frame()
                        lock_portal = "b" if name == "a" else "a"
                        ev("portal", "Through the tunnel!")
                        break
            elif k == "tramp":
                d = _dist(x, y, o["x"], o["y"])
                if d > o["r"] + R:
                    locks.pop(id(o), None)
                elif d < o["r"] and not locks.get(id(o)) and math.hypot(vx, vy) > 6:
                    locks[id(o)] = True
                    vz = min(48.0, 12 + 0.34 * math.hypot(vx, vy))
                    z = 0.01
                    ev("tramp", "Boing!")
            elif k == "spin":
                d = _dist(x, y, o["x"], o["y"])
                if d > o["r"] + R:
                    locks.pop(id(o), None)
                elif d < o["r"] * 0.6 and not locks.get(id(o)):
                    locks[id(o)] = True
                    a = o["ph"] + o["w"] * T
                    sp2 = min(MAX_SPEED, max(math.hypot(vx, vy), 30) + 12)
                    vx, vy = math.cos(a) * sp2, math.sin(a) * sp2
                    ev("spin", "Spin Cycle!")

        # coins: roll (or bounce low) over them
        if coins and z < 3:
            for c in list(coins):
                if _dist(x, y, c[1], c[2]) < R + 1.4:
                    coins.remove(c)
                    events.append({"t": round(t, 2), "k": "coin", "i": c[0], "text": "",
                                   "x": round(c[1], 1), "y": round(c[2], 1)})

        hz = None if air else _hazard_at(hole, cells, x, y)
        if hz:
            penalty += 1
            frame()
            sx, sy = safe
            sp_ = math.hypot(vx, vy) or 1
            bx, by = sx - vx / sp_ * 1.5, sy - vy / sp_ * 1.5       # a touch back from the edge
            if not _hazard_at(hole, cells, bx, by) and _wall_clear(idx, bx, by) > R:
                sx, sy = bx, by
            ev(hz, PENALTY_TEXT[hz])
            x, y, vx, vy, z, vz = sx, sy, 0.0, 0.0, 0.0, 0.0
            cuts.append(len(frames))
            frame()
            break
        if not air:
            safe = (x, y)

        dcup = _dist(x, y, cx, cy)
        speed = math.hypot(vx, vy)
        if not air and dcup < CUP_R:
            if speed < SINK_SPEED:
                sunk = True
                x, y = cx, cy
                frame()
                break
            if not lipped:
                lipped = True
                vx, vy = vx * 0.85, vy * 0.85
                ev("lip", "Lipped out! Too fast.")
        elif dcup > CUP_R + 2:
            lipped = False

        if not air:
            moving_force = math.hypot(ax, ay)
            if speed < 1.2 and moving_force < FRICTION * 0.6:
                break
            still = still + DT if speed < 2.5 else 0.0
            if still > 1.5:
                break
        if step % per == 0:
            frame()
    end = (x, y)
    if not sunk and not penalty:
        end, moved = _settle(hole, x, y, clock + t)
        if moved:
            ev("nudge", "Moved a clubhead off the rail (no penalty).")
    if frames[-1][:2] != [round(end[0], 1), round(end[1], 1)]:
        frames.append([round(end[0], 1), round(end[1], 1)])
    return {"frames": frames, "cuts": cuts, "end": [round(end[0], 2), round(end[1], 2)],
            "dur": round(max(t, len(frames) / FPS), 2), "sunk": sunk, "penalty": penalty, "events": events,
            "cup": cup_i if sunk else None}


def _valid_rest(hole, idx, cells, x, y):
    return (not _hazard_at(hole, cells, x, y) and _wall_clear(idx, x, y) > R + 0.1
            and not any(_blocked(o, x, y) for o in hole["obs"]))


def _settle(hole, x, y, T):
    """Rail rule: a ball resting against a rail or in a moving obstacle's path
    is moved one clubhead away. Returns ((x, y), moved?)."""
    idx, cells = _hole_index(hole)
    nx = ny = 0.0
    for s in idx.get((int(x // C), int(y // C)), ()):
        px, py = _closest(x, y, *s)
        d = _dist(x, y, px, py)
        if 0 < d < R + 0.6:
            nx += (x - px) / d
            ny += (y - py) / d
    pushes = []
    if nx or ny:
        n = math.hypot(nx, ny)
        pushes.append((nx / n, ny / n, NUDGE - R))
    for o in hole["obs"]:
        if o["k"] == "windmill":
            d = _dist(x, y, o["x"], o["y"])
            if d < o["L"] + R + 0.4:
                ux, uy = ((x - o["x"]) / d, (y - o["y"]) / d) if d else (0, 1)
                pushes.append((ux, uy, o["L"] + R + 0.8 - d))
        elif o["k"] == "gate":
            along = o["amp"] + o["len"] / 2 + R
            if o["ax"]:      # slides along x: band is horizontal
                if abs(x - o["x"]) < along and abs(y - o["y"]) < R + 0.5:
                    s = 1 if y >= o["y"] else -1
                    pushes.append((0, s, R + 1.2 - abs(y - o["y"])))
            elif abs(y - o["y"]) < along and abs(x - o["x"]) < R + 0.5:
                s = 1 if x >= o["x"] else -1
                pushes.append((s, 0, R + 1.2 - abs(x - o["x"])))
    for ux, uy, amt in pushes:
        for frac in (1.0, 0.6, 0.35):
            cx, cy = x + ux * amt * frac, y + uy * amt * frac
            if _valid_rest(hole, idx, cells, cx, cy):
                x, y = cx, cy
                break
    moved = bool(pushes)
    return (x, y), moved


# ------------------------------------------------------------------- game ---
DEFAULTS = {"format": "solo", "holes": 9, "limit": 6, "pace": "turns", "order": "far",
            "amount": "some", "obsMode": "chosen", "obstacles": list(OBS_IDS), "teams": 2, "groupSize": 4,
            "theme": "random", "trivia": 3}


def fresh(cfg=None, team_of=None):
    return {"phase": "lobby", "cfg": dict(DEFAULTS, **(cfg or {})), "teamOf": dict(team_of or {}),
            "course": "", "holes": [], "hole": 0, "sides": {}, "order": [], "groups": [], "teeOrder": [],
            "epoch": 0.0, "nextAt": 0, "message": "", "log": [], "shotSeq": 0, "standings": None,
            "coinsTaken": {}, "trivia": None, "asked": [], "pointsStandings": None,
            "revision": random.randrange(1, 2 ** 48)}


def _present(players, pid):
    p = players.get(pid)
    return bool(p) and p.get("connected", True)


def _side_of(g, pid):
    for s in g["sides"].values():
        if pid in s["members"]:
            return s
    return None


def _hitter(g, players, side):
    m = side["members"]
    for k in range(len(m)):
        pid = m[(side["hitter"] + k) % len(m)]
        if _present(players, pid):
            return pid
    return None


def _side_present(players, side):
    return any(_present(players, p) for p in side["members"])


def _balance(g, players, here):
    n = max(2, min(4, g["cfg"]["teams"]))
    counts = [0] * n
    for p in here:
        t = g["teamOf"].get(p)
        if t is not None and t < n:
            counts[t] += 1
    for p in here:
        t = g["teamOf"].get(p)
        if t is None or t >= n:
            t = counts.index(min(counts))
            g["teamOf"][p] = t
            counts[t] += 1
    # never leave a team empty while another has two or more
    while min(counts) == 0 and max(counts) > 1:
        src, dst = counts.index(max(counts)), counts.index(0)
        mover = next(p for p in here if g["teamOf"][p] == src)
        g["teamOf"][mover] = dst
        counts[src] -= 1
        counts[dst] += 1


def _new_side(sid, name, color, members, team, n_done, limit):
    return {"id": sid, "name": name, "color": color, "members": members, "team": team, "hitter": 0,
            "scores": [limit] * n_done, "missed": n_done, "strokes": 0, "state": "tee", "ball": [0, 0], "points": 0,
            "shot": None, "until": 0.0}


def _log(g, text):
    g["log"].append({"t": time.time(), "text": text})
    del g["log"][:-8]


def _begin_hole(g, i):
    g["hole"], g["phase"], g["nextAt"] = i, "playing", 0
    g["coinsTaken"], g["trivia"] = {}, None
    hole = g["holes"][i]
    for s in g["sides"].values():
        s.update(strokes=0, state="tee", ball=list(hole["tee"]), shot=None, until=0.0)
    if i == 0:
        g["teeOrder"] = list(g["order"])
    else:   # honor: best score on the last hole tees off first
        prev = {sid: g["sides"][sid]["scores"][i - 1] if len(g["sides"][sid]["scores"]) >= i else 99
                for sid in g["order"]}
        g["teeOrder"] = sorted(g["teeOrder"], key=lambda sid: prev.get(sid, 99))
        g["teeOrder"] += [sid for sid in g["order"] if sid not in g["teeOrder"]]
    g["revision"] += 1


def _groups(order, size):
    n = max(1, math.ceil(len(order) / size))
    groups = [[] for _ in range(n)]
    for k, sid in enumerate(order):
        groups[k % n].append(sid)
    return groups


def _group_of(g, sid):
    for grp in g["groups"]:
        if sid in grp:
            return grp
    return [sid]


def _turn(g, players, grp, now):
    """Side id whose turn it is in a group, 'busy' while a ball rolls, or None."""
    sides = g["sides"]
    if any(sides[s]["until"] > now for s in grp):
        return "busy"
    active = [s for s in grp if sides[s]["state"] in ("tee", "play") and _side_present(players, sides[s])]
    tee = [s for s in g["teeOrder"] if s in active and sides[s]["state"] == "tee"]
    if tee:
        return tee[0]
    play = [s for s in active if sides[s]["state"] == "play"]
    if not play:
        return None
    cup = g["holes"][g["hole"]]["cup"]
    key = lambda s: _dist(*sides[s]["ball"], *cup)
    return max(play, key=key) if g["cfg"]["order"] == "far" else min(play, key=key)


def _start(g, players):
    here = sorted(p for p, v in players.items() if v.get("connected", True))
    if not here:
        return "Players need to join first."
    cfg = g["cfg"]
    if cfg["format"] != "solo" and len(here) < 2:
        return "Team play needs at least 2 players. Switch to Solo or wait for more."
    name, holes = make_course(cfg)
    keep_team = dict(g["teamOf"])
    g.clear()
    g.update(fresh(cfg, keep_team))
    g.update(course=name, holes=holes, epoch=time.time())
    limit = cfg["limit"]
    if cfg["format"] == "solo":
        for p in here:
            sid = f"p{p}"
            g["sides"][sid] = _new_side(sid, players[p]["name"], players[p].get("color", "#fff"), [p], None, 0, limit)
    else:
        _balance(g, players, here)
        if cfg["format"] == "best":
            for p in here:
                sid = f"p{p}"
                g["sides"][sid] = _new_side(sid, players[p]["name"], players[p].get("color", "#fff"), [p],
                                            g["teamOf"][p], 0, limit)
        else:
            for t in range(cfg["teams"]):
                members = [p for p in here if g["teamOf"].get(p) == t]
                if members:
                    sid = f"t{t}"
                    g["sides"][sid] = _new_side(sid, TEAM_NAMES[t], TEAM_COLORS[t], members, t, 0, limit)
                    g["sides"][sid]["memberNames"] = [players[p]["name"] for p in members]
    g["order"] = list(g["sides"])
    random.shuffle(g["order"])               # who putts first on hole 1
    g["groups"] = _groups(g["order"], cfg["groupSize"])
    _begin_hole(g, 0)
    _log(g, f"Welcome to {name}! {cfg['holes']} holes, stroke limit {limit}.")
    return None


def _team_totals(g):
    """{team: (total, per-hole list)} for best-ball."""
    out = {}
    n = len(g["holes"])
    for t in range(g["cfg"]["teams"]):
        members = [s for s in g["sides"].values() if s["team"] == t]
        if not members:
            continue
        per = []
        for h in range(n):
            vals = [s["scores"][h] for s in members if len(s["scores"]) > h]
            if vals:
                per.append(min(vals))
        out[t] = (sum(per), per)
    return out


def standings(g):
    """Ranked rows (lowest first): name, total, toPar, members, color, place."""
    played_par = lambda k: sum(h["par"] for h in g["holes"][:k])
    rows = []
    if g["cfg"]["format"] == "best":
        for t, (total, per) in _team_totals(g).items():
            rows.append({"name": TEAM_NAMES[t], "color": TEAM_COLORS[t], "total": total, "holes": len(per),
                         "toPar": total - played_par(len(per)), "team": t,
                         "members": [s["name"] for s in g["sides"].values() if s["team"] == t]})
    else:
        for sid in g["order"]:
            s = g["sides"][sid]
            rows.append({"name": s["name"], "color": s["color"], "total": sum(s["scores"]),
                         "holes": len(s["scores"]), "toPar": sum(s["scores"]) - played_par(len(s["scores"])),
                         "team": s["team"], "side": sid,
                         "members": [] if g["cfg"]["format"] == "solo" else list(s.get("memberNames", []))})
    rows.sort(key=lambda r: (-r["holes"], r["total"]))
    place = 0
    for k, r in enumerate(rows):
        if k == 0 or (r["total"], r["holes"]) != (rows[k - 1]["total"], rows[k - 1]["holes"]):
            place = k + 1
        r["place"] = place
    return rows


def points_standings(g):
    """Most points first (coins, pinball scoring, trivia)."""
    rows = []
    if g["cfg"]["format"] == "best":
        for t in range(g["cfg"]["teams"]):
            members = [s for s in g["sides"].values() if s["team"] == t]
            if members:
                rows.append({"name": TEAM_NAMES[t], "color": TEAM_COLORS[t], "points": sum(s["points"] for s in members),
                             "team": t, "members": [s["name"] for s in members]})
    else:
        for sid in g["order"]:
            s = g["sides"][sid]
            rows.append({"name": s["name"], "color": s["color"], "points": s["points"], "team": s["team"], "side": sid,
                         "members": [] if g["cfg"]["format"] == "solo" else list(s.get("memberNames", []))})
    rows.sort(key=lambda r: -r["points"])
    place = 0
    for k, r in enumerate(rows):
        if k == 0 or r["points"] != rows[k - 1]["points"]:
            place = k + 1
        r["place"] = place
    return rows


def _award(g, players, rows, per_place):
    n = len(rows)
    for r in rows:
        pts = per_place * (n - r["place"] + 1)
        if "side" in r:
            pids = g["sides"][r["side"]]["members"]
        else:
            pids = [p for s in g["sides"].values() if s["team"] == r["team"] for p in s["members"]]
        for p in pids:
            if p in players:
                players[p]["score"] += pts


def _finish(g, players):
    g["phase"], g["nextAt"], g["trivia"] = "done", 0, None
    rows = standings(g)
    prow = points_standings(g)
    g["standings"], g["pointsStandings"] = rows, prow
    _award(g, players, rows, 100)       # fewest strokes
    _award(g, players, [r for r in prow if r["points"] > 0], 50)        # most points (nobody wins on 0)
    if rows:
        _log(g, f"\N{TROPHY} {rows[0]['name']} wins with {rows[0]['total']} strokes!")
    if prow and prow[0]["points"]:
        _log(g, f"\N{SPORTS MEDAL} Most points: {prow[0]['name']} with {prow[0]['points']}.")
    g["revision"] += 1


def _cur_hole(g):
    """The hole being played: a trivia hole between regular ones, else the course hole."""
    if g["phase"] in ("trivia", "treveal") and g.get("trivia"):
        return g["trivia"]["hole"]
    return g["holes"][min(g["hole"], len(g["holes"]) - 1)]


def _trivia_due(g):
    every = int(g["cfg"].get("trivia") or 0)
    return every > 0 and (g["hole"] + 1) % every == 0


def _start_trivia(g, now):
    """Bonus trivia hole: putt into the cup with the right answer."""
    pool = [i for i in range(len(minigolf_trivia.QUESTIONS)) if i not in g["asked"]]
    if not pool:
        g["asked"] = []
        pool = list(range(len(minigolf_trivia.QUESTIONS)))
    qi = random.choice(pool)
    g["asked"].append(qi)
    q, right, wrong = minigolf_trivia.QUESTIONS[qi]
    options = [right] + list(wrong)
    random.shuffle(options)
    cfg = g["cfg"]
    kinds = list(OBS_IDS) if cfg["obsMode"] == "surprise" else cfg["obstacles"]
    theme = g["holes"][g["hole"]].get("theme", "pirate")
    hole = make_trivia_hole(random.Random(), kinds, theme, len(options))
    g["trivia"] = {"id": qi, "q": q, "options": options, "correct": options.index(right), "hole": hole,
                   "endsAt": now + TRIVIA_TIME, "results": {}, "revealed": False}
    g["coinsTaken"] = {}
    for s_ in g["sides"].values():
        s_.update(strokes=0, state="tee", ball=list(hole["tee"]), shot=None, until=0.0)
    g["phase"], g["nextAt"] = "trivia", 0
    _log(g, "Bonus trivia hole! Putt into the cup with the right answer.")
    g["revision"] += 1


def trivia_points(strokes):
    return 150 + 50 * max(0, TRIVIA_STROKES - strokes)      # 250 / 200 / 150


def _reveal_trivia(g, now):
    tv = g["trivia"]
    if tv["revealed"]:
        return
    tv["revealed"] = True
    right = []
    for sid, r in tv["results"].items():
        side = g["sides"].get(sid)
        if side and r.get("choice") == tv["correct"]:
            r["pts"] = trivia_points(r["strokes"])
            side["points"] += r["pts"]
            right.append(f"{side['name']} +{r['pts']}")
    letter = "ABCDEFGH"[tv["correct"]]
    _log(g, f"Trivia answer: {letter}. {tv['options'][tv['correct']]}. " + (", ".join(right) if right else "Nobody got it!"))
    g["phase"], g["nextAt"] = "treveal", now + TRIVIA_REVEAL
    g["revision"] += 1


def _after_break(g, players):
    if g["hole"] + 1 < len(g["holes"]):
        _begin_hole(g, g["hole"] + 1)
    else:
        _finish(g, players)


def tick(g, players):
    """Janitor hook (and called after actions). Returns True if anything changed."""
    now = time.time()
    if g["phase"] == "hole" and g["nextAt"] and now >= g["nextAt"]:
        if _trivia_due(g):
            _start_trivia(g, now)
        else:
            _after_break(g, players)
        return True
    if g["phase"] == "treveal" and g["nextAt"] and now >= g["nextAt"]:
        _after_break(g, players)
        return True
    if g["phase"] == "trivia":
        sides = list(g["sides"].values())
        open_ = [s_ for s_ in sides if s_["state"] in ("tee", "play") and _side_present(players, s_)]
        last = max((s_["until"] for s_ in sides), default=0)
        if (not open_ and now >= last + 0.8) or now >= g["trivia"]["endsAt"]:
            _reveal_trivia(g, now)
            return True
        return False
    if g["phase"] != "playing":
        return False
    sides = list(g["sides"].values())
    open_ = [s for s in sides if s["state"] in ("tee", "play")]
    changed = False
    # balls of players who left: pick them up once everyone else is done
    if open_ and all(not _side_present(players, s) for s in open_) and all(s["until"] <= now for s in sides):
        limit = g["cfg"]["limit"]
        for s in open_:
            s["state"], s["strokes"] = "picked", limit
            s["scores"].append(limit)
            _log(g, f"{s['name']} left: picked up for {limit}.")
        changed = True
        open_ = []
    if not open_:
        last = max((s["until"] for s in sides), default=0)
        if now >= last + 0.8:
            g["phase"], g["nextAt"] = "hole", now + HOLE_PAUSE
            g["revision"] += 1
            return True
    if changed:
        g["revision"] += 1
    return changed


def _shot(g, players, pid, data):
    trivia = g["phase"] == "trivia"
    if g["phase"] != "playing" and not trivia:
        return "Wait for the next hole.", {}
    side = _side_of(g, pid)
    if not side:
        return "You're watching this game. Tap \"Join in\" to play from this hole.", {}
    if side["state"] not in ("tee", "play"):
        return "Your ball is already done on this hole.", {}
    now = time.time()
    if side["until"] > now:
        return "Wait for your ball to stop.", {}
    if g["cfg"]["format"] == "alternate":
        h = _hitter(g, players, side)
        if h != pid:
            return f"It's {players[h]['name'] if h in players else 'a teammate'}'s shot for your team.", {}
    if g["cfg"]["pace"] == "turns" and not trivia:         # trivia holes: everyone at once
        t = _turn(g, players, _group_of(g, side["id"]), now)
        if t == "busy":
            return "Wait for the ball to stop.", {}
        if t != side["id"]:
            return f"Not your turn: {g['sides'][t]['name'] if t in g['sides'] else 'someone else'} is up.", {}
    try:
        angle, power = float(data.get("angle")), float(data.get("power"))
    except (TypeError, ValueError):
        return "Bad shot.", {}
    if not (math.isfinite(angle) and math.isfinite(power)):
        return "Bad shot.", {}
    power = max(0.03, min(1.0, power))
    hole = _cur_hole(g)
    t0 = now + START_DELAY
    coins_left = [(i, c[0], c[1]) for i, c in enumerate(hole.get("coins", [])) if str(i) not in g["coinsTaken"]]
    res = simulate(hole, side["ball"], angle, power, t0 - g["epoch"], coins_left)
    limit = g["cfg"]["limit"]
    side["strokes"] += 1 + res["penalty"]
    side["ball"] = res["end"]
    side["until"] = t0 + res["dur"]
    g["shotSeq"] += 1
    who = players[pid]["name"]
    events = list(res["events"])
    # pinball scoring: every trick in one shot raises the combo multiplier
    mult, gained = 1, 0
    for e in events:
        base = SCORE.get(e["k"])
        if not base:
            continue
        e["pts"], e["mult"] = base * mult, mult
        gained += base * mult
        mult = min(MAX_MULT, mult + 1)
        if e["k"] == "coin":
            g["coinsTaken"][str(e["i"])] = {"by": side["id"], "at": round(t0 + e["t"], 2)}
    if trivia:
        tv = g["trivia"]
        if res["sunk"]:
            side["state"] = "in"
            letter = "ABCDEFGH"[res["cup"]]
            tv["results"][side["id"]] = {"choice": res["cup"], "strokes": side["strokes"], "by": who}
            cx_, cy_ = hole["cups"][res["cup"]]
            events.append({"t": res["dur"], "k": "answer", "text": f"Locked in {letter}!", "x": cx_, "y": cy_})
            _log(g, f"{who} locked in an answer.")
        elif side["strokes"] >= TRIVIA_STROKES:
            side["state"] = "picked"
            tv["results"][side["id"]] = {"choice": None, "strokes": side["strokes"], "by": who}
            events.append({"t": res["dur"], "k": "picked", "text": "Out of putts: no answer this time."})
        else:
            side["state"] = "play"
    elif res["sunk"]:
        side["state"] = "in"
        score = min(side["strokes"], limit)
        side["scores"].append(score)
        par = hole["par"]
        label = ("HOLE IN ONE!" if score == 1 else {-3: "Albatross!", -2: "Eagle!", -1: "Birdie!", 0: "Par.",
                                                   1: "Bogey.", 2: "Double bogey."}.get(score - par, f"{score} strokes."))
        bonus = SINK_PTS.get(1 if score == 1 else score - par, 25) * mult
        gained += bonus
        cx_, cy_ = hole["cup"]
        events.append({"t": res["dur"], "k": "in", "text": f"In! {label}", "pts": bonus, "mult": mult,
                       "x": cx_, "y": cy_})
        _log(g, f"{who} sank it in {score}. {label}")
    elif side["strokes"] >= limit:
        side["state"] = "picked"
        side["scores"].append(limit)
        events.append({"t": res["dur"], "k": "picked", "text": f"Stroke limit: picked up, {limit} recorded."})
        _log(g, f"{who} hit the {limit}-stroke limit and picked up.")
    else:
        side["state"] = "play"
        for e in res["events"]:
            if e["k"] in PENALTY_TEXT:
                _log(g, f"{who}: {e['text']}")
    side["points"] += gained
    if mult >= 3:
        _log(g, f"{who}: x{mult} combo, +{gained} points!")
    if g["cfg"]["format"] == "alternate":
        side["hitter"] = (side["members"].index(pid) + 1) % len(side["members"])
    side["shot"] = {"id": g["shotSeq"], "t0": t0, "dur": res["dur"], "frames": res["frames"],
                    "cuts": res["cuts"], "events": events, "by": who, "pid": pid}
    g["revision"] += 1
    tick(g, players)
    return None, {"shot": side["shot"], "side": side["id"]}


def _join(g, players, pid):
    if g["phase"] not in ("playing", "hole", "trivia", "treveal"):
        return "Join from the lobby.", {}
    if _side_of(g, pid):
        return None, {}
    cfg, done = g["cfg"], g["hole"] + (1 if g["phase"] != "playing" else 0)
    limit = cfg["limit"]
    p = players[pid]
    if cfg["format"] == "alternate":
        teams = [s for s in g["sides"].values()]
        if not teams:
            return "No teams to join.", {}
        side = min(teams, key=lambda s: len(s["members"]))
        side["members"].append(pid)
        side.setdefault("memberNames", []).append(p["name"])
        g["teamOf"][pid] = side["team"]
    else:
        team = None
        if cfg["format"] == "best":
            counts = {}
            for s in g["sides"].values():
                counts[s["team"]] = counts.get(s["team"], 0) + 1
            team = min(counts, key=counts.get) if counts else 0
            g["teamOf"][pid] = team
        sid = f"p{pid}"
        side = _new_side(sid, p["name"], p.get("color", "#fff"), [pid], team, min(done, len(g["holes"])), limit)
        if g["phase"] == "playing":
            side["ball"] = list(g["holes"][g["hole"]]["tee"])
        else:
            side["state"] = "in"          # waits for the next hole to start
        g["sides"][sid] = side
        g["order"].append(sid)
        g["teeOrder"].append(sid)
        grp = min(g["groups"], key=len) if g["groups"] else None
        if grp is not None and len(grp) < cfg["groupSize"]:
            grp.append(sid)
        else:
            g["groups"].append([sid])
        if done:
            _log(g, f"{p['name']} joined at hole {done + 1} ({done} missed holes count {limit} each).")
    g["revision"] += 1
    return None, {}


def act(g, players, pid, action, data):
    """Returns (error or None, extra reply fields)."""
    cfg = g["cfg"]
    if action == "mgSet":
        if g["phase"] not in ("lobby", "done"):
            return "Change settings between games.", {}
        key, value = data.get("key"), data.get("value")
        if isinstance(key, str) and key.startswith("obs:") and key[4:] in OBS_IDS:
            on = set(cfg["obstacles"])
            (on.add if value else on.discard)(key[4:])
            cfg["obstacles"] = [k for k in OBS_IDS if k in on]
        elif key == "format" and value in FORMATS:
            cfg["format"] = value
        elif key == "pace" and value in PACES:
            cfg["pace"] = value
        elif key == "order" and value in ORDERS:
            cfg["order"] = value
        elif key == "amount" and value in AMOUNTS:
            cfg["amount"] = value
        elif key == "obsMode" and value in ("chosen", "surprise"):
            cfg["obsMode"] = value
        elif key == "theme" and value in THEME_CHOICES:
            cfg["theme"] = value
        elif key == "trivia":
            try:
                v = int(value)
            except (TypeError, ValueError):
                return "Unknown setting.", {}
            if v not in (0, 1, 2, 3):
                return "Unknown setting.", {}
            cfg["trivia"] = v
        elif key in ("holes", "limit", "teams", "groupSize"):
            try:
                v = int(value)
            except (TypeError, ValueError):
                return "Unknown setting.", {}
            allowed = {"holes": HOLE_COUNTS, "limit": LIMITS, "teams": [2, 3, 4], "groupSize": [4, 5]}[key]
            if v not in allowed:
                return "Unknown setting.", {}
            cfg[key] = v
        else:
            return "Unknown setting.", {}
        g["revision"] += 1
        return None, {}

    if action == "mgRandomObs":
        if g["phase"] not in ("lobby", "done"):
            return "Change settings between games.", {}
        cfg["obstacles"] = sorted(random.sample(OBS_IDS, random.randint(3, 6)), key=OBS_IDS.index)
        cfg["obsMode"] = "chosen"
        g["revision"] += 1
        return None, {}

    if action == "mgShuffle":
        here = [p for p, v in players.items() if v.get("connected", True)]
        random.shuffle(here)
        n = cfg["teams"]
        g["teamOf"] = {p: k % n for k, p in enumerate(here)}
        g["revision"] += 1
        return None, {}

    if action == "mgStart":
        if g["phase"] in ("playing", "hole"):
            return "A game is already running.", {}
        return _start(g, players), {}

    if action == "mgNext":
        if g["phase"] in ("hole", "treveal"):
            g["nextAt"] = time.time()
            tick(g, players)
        elif g["phase"] == "trivia":
            _reveal_trivia(g, time.time())
        return None, {}

    if action == "mgSkip":       # host picks up a slow or missing player's ball
        if g["phase"] != "playing":
            return None, {}
        sid = data.get("side")
        if sid not in g["sides"]:
            t = _turn(g, players, g["groups"][0] if g["groups"] else [], time.time()) if cfg["pace"] == "turns" else None
            sid = t if t in g["sides"] else None
        if not sid:
            return "Nobody to skip right now.", {}
        s = g["sides"][sid]
        if s["state"] in ("tee", "play"):
            s["state"], s["strokes"] = "picked", cfg["limit"]
            s["scores"].append(cfg["limit"])
            _log(g, f"Host picked up {s['name']}'s ball ({cfg['limit']}).")
            g["revision"] += 1
            tick(g, players)
        return None, {}

    if action == "mgEnd":
        if g["phase"] in ("playing", "hole", "trivia", "treveal"):
            # count only the holes everyone finished
            k = min((len(s["scores"]) for s in g["sides"].values()), default=0)
            for s in g["sides"].values():
                del s["scores"][k:]
            g["holes"] = g["holes"][:max(k, 0)] or g["holes"][:0]
            _finish(g, players)
        return None, {}

    if action == "mgLobby":
        keep = (dict(cfg), dict(g["teamOf"]))
        g.clear()
        g.update(fresh(*keep))
        return None, {}

    # ---- player actions ----
    if pid not in players:
        return "Join the game first.", {}
    if action == "mgTeam":
        if g["phase"] not in ("lobby", "done"):
            return "Pick teams between games.", {}
        try:
            t = int(data.get("team"))
        except (TypeError, ValueError):
            return "Pick a team.", {}
        if not 0 <= t < cfg["teams"]:
            return "Pick a team.", {}
        g["teamOf"][pid] = t
        g["revision"] += 1
        return None, {}
    if action == "mgShot":
        return _shot(g, players, pid, data)
    if action == "mgJoin":
        return _join(g, players, pid)
    return "Unknown mini golf action.", {}


# ----------------------------------------------------------------- public ---
def public(g, players, pid=None):
    now = time.time()
    cfg = g["cfg"]
    out = {"phase": g["phase"], "cfg": cfg, "course": g["course"], "serverNow": now, "epoch": g["epoch"],
           "revision": g["revision"], "message": g["message"], "obstacles": OBSTACLES,
           "teamNames": TEAM_NAMES[:cfg["teams"]], "teamColors": TEAM_COLORS[:cfg["teams"]],
           "log": [e["text"] for e in g["log"]][-6:], "nextAt": g["nextAt"],
           "consts": {"W": W, "H": H, "C": C, "R": R, "cupR": CUP_R, "fps": FPS}}
    if g["phase"] == "lobby" or (g["phase"] == "done" and cfg["format"] != "solo"):
        here = [p for p, v in players.items() if v.get("connected", True)]
        out["teams"] = [[{"pid": p, "name": players[p]["name"]} for p in here if g["teamOf"].get(p) == t]
                        for t in range(cfg["teams"])]
        out["myTeam"] = g["teamOf"].get(pid)
    if not g["holes"]:
        return out
    k = min(g["hole"], len(g["holes"]) - 1)
    hole = _cur_hole(g)
    out["hole"] = {key: hole.get(key) for key in ("n", "par", "cells", "walls", "tee", "cup", "obs", "shape", "theme",
                                                  "coins", "cups", "key")}
    out["coinsTaken"] = {i: c["at"] for i, c in g["coinsTaken"].items()}
    out["scoreTable"] = SCORE
    out["holeIndex"] = k
    out["holeCount"] = len(g["holes"])
    out["pars"] = [h["par"] for h in g["holes"]]
    names = {p: v["name"] for p, v in players.items()}
    sides = []
    turns = {}
    if g["phase"] == "playing" and cfg["pace"] == "turns":
        for grp in g["groups"]:
            t = _turn(g, players, grp, now)
            for sid in grp:
                turns[sid] = t
    for sid in g["order"]:
        s = g["sides"][sid]
        row = {"id": sid, "name": s["name"], "color": s["color"], "team": s["team"], "state": s["state"],
               "strokes": s["strokes"], "scores": s["scores"], "total": sum(s["scores"]), "ball": s["ball"],
               "missed": s.get("missed", 0), "moving": s["until"] > now, "points": s["points"],
               "present": _side_present(players, s),
               "members": [{"pid": p, "name": names.get(p, "(left)")} for p in s["members"]]}
        if cfg["format"] == "alternate":
            row["hitter"] = _hitter(g, players, s)
        if s["shot"] and now < s["until"] + 1.5:
            row["shot"] = s["shot"]
        elif s["shot"]:
            row["shot"] = {k2: s["shot"][k2] for k2 in ("id", "t0", "dur", "by")}
        if sid in turns:
            row["up"] = turns[sid] == sid
        sides.append(row)
    out["sides"] = sides
    mine = _side_of(g, pid) if pid is not None else None
    me = {"side": mine["id"] if mine else None, "canShoot": False, "status": ""}
    tv = g.get("trivia")
    if g["phase"] == "trivia" and mine:
        res = tv["results"].get(mine["id"])
        left = TRIVIA_STROKES - mine["strokes"]
        if mine["until"] > now:
            me["status"] = "Rolling…"
        elif res and res["choice"] is not None:
            me["status"] = f"Locked in {'ABCDEFGH'[res['choice']]}. Waiting for the others…"
        elif mine["state"] == "picked":
            me["status"] = "Out of putts. Waiting for the answer…"
        elif cfg["format"] == "alternate" and _hitter(g, players, mine) != pid:
            me["status"] = f"{names.get(_hitter(g, players, mine), 'Your teammate')} putts for your team."
        else:
            me["canShoot"] = True
            me["status"] = f"Putt into the cup with the right answer! {left} putt{'s' if left != 1 else ''} left."
    elif g["phase"] == "playing" and mine:
        if mine["state"] in ("in", "picked"):
            me["status"] = (f"In with {mine['strokes']}." if mine["state"] == "in" else "Picked up.") + " Waiting for the others…"
        elif mine["until"] > now:
            me["status"] = "Rolling…"
        else:
            ok, why = True, ""
            if cfg["format"] == "alternate":
                h = _hitter(g, players, mine)
                if h != pid:
                    ok, why = False, f"{names.get(h, 'Your teammate')} takes this shot for your team."
            if ok and cfg["pace"] == "turns":
                t = turns.get(mine["id"])
                if t == "busy":
                    ok, why = False, "Watching the shot…"
                elif t != mine["id"]:
                    ok, why = False, f"Waiting for {g['sides'][t]['name']}…" if t in g["sides"] else "Waiting…"
            me["canShoot"] = ok
            me["status"] = ("Tee off! " if mine["state"] == "tee" else "Your shot! ") + "Drag back from anywhere, let go to putt." if ok else why
    elif g["phase"] == "playing" and pid is not None:
        me["status"] = "You're watching. Join in to play from here (missed holes count as the stroke limit)."
    out["me"] = me
    if g["phase"] in ("hole", "treveal", "done"):
        out["standings"] = g["standings"] if g["phase"] == "done" and g["standings"] is not None else standings(g)
        out["pointsStandings"] = (g["pointsStandings"] if g["phase"] == "done" and g["pointsStandings"] is not None
                                  else points_standings(g))
    if g["phase"] in ("trivia", "treveal") and tv:
        reveal = g["phase"] == "treveal"
        results = []
        for sid, r in tv["results"].items():
            if sid in g["sides"]:
                results.append({"name": g["sides"][sid]["name"], "color": g["sides"][sid]["color"],
                                "choice": r["choice"] if reveal else None, "locked": r["choice"] is not None,
                                "pts": r.get("pts", 0)})
        out["trivia"] = {"q": tv["q"], "options": tv["options"], "endsAt": tv["endsAt"], "strokes": TRIVIA_STROKES,
                         "correct": tv["correct"] if reveal else None, "results": results,
                         "points": [trivia_points(n) for n in range(1, TRIVIA_STROKES + 1)]}
    if cfg["format"] == "best":
        out["teamTotals"] = {t: v[0] for t, v in _team_totals(g).items()}
    return out
