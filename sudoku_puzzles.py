"""
Sudoku puzzles for VibeBox — solver, human-style difficulty grader, generator,
and the graded puzzle bank (sudoku_puzzles.json).

Puzzles are generated locally rather than scraped from websites or copied
from books (no licensing questions, no network needed). Every puzzle has
exactly one solution, and its difficulty is the hardest solving technique a
person needs, not just the clue count:

  easy      naked / hidden singles only, lots of clues
  medium    singles only, fewer clues
  advanced  + locked candidates (pointing / claiming)
  hard      + naked / hidden pairs and triples
  expert    + X-wing, swordfish, XY-wing

Boards are strings of 81 chars, row-major, "0" = empty.

  python3 sudoku_puzzles.py --per-level 40     grow the bank to 40 per level
  python3 sudoku_puzzles.py --stats            show what's in the bank
"""

import json
import os
import random
import sys
import time

LEVELS = ["easy", "medium", "advanced", "hard", "expert"]
BANK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sudoku_puzzles.json")

ROWS = [[r * 9 + c for c in range(9)] for r in range(9)]
COLS = [[r * 9 + c for r in range(9)] for c in range(9)]
BOXES = [[(br * 3 + r) * 9 + bc * 3 + c for r in range(3) for c in range(3)]
         for br in range(3) for bc in range(3)]
UNITS = ROWS + COLS + BOXES
PEERS = [sorted({p for u in UNITS if i in u for p in u} - {i}) for i in range(81)]
ALL = 0x3FE     # bits 1..9


def _digits(mask):
    return [d for d in range(1, 10) if mask >> d & 1]


# ------------------------------------------------------------------ solver ---
def count_solutions(board, limit=2):
    """Backtracking (fewest-candidates-first) count, stopping at `limit`."""
    cells = [int(ch) for ch in board]
    found = [0, None]

    def cand(i):
        m = ALL
        for p in PEERS[i]:
            m &= ~(1 << cells[p])
        return m

    def go():
        best, best_m, best_n = -1, 0, 10
        for i in range(81):
            if not cells[i]:
                m = cand(i)
                n = bin(m).count("1")
                if n < best_n:
                    best, best_m, best_n = i, m, n
                    if n <= 1:
                        break
        if best < 0:
            found[0] += 1
            if found[1] is None:
                found[1] = "".join(map(str, cells))
            return found[0] >= limit
        for d in _digits(best_m):
            cells[best] = d
            if go():
                return True
        cells[best] = 0
        return False

    for i in range(81):        # reject boards that break the rules outright
        if cells[i] and any(cells[p] == cells[i] for p in PEERS[i]):
            return 0, None
    go()
    return found[0], found[1]


def solve(board):
    n, sol = count_solutions(board, 2)
    return sol if n == 1 else None


# ------------------------------------------------------------------ grader ---
class _Grid:
    def __init__(self, board):
        self.v = [int(ch) for ch in board]
        self.c = [0 if x else ALL for x in self.v]
        for i, x in enumerate(self.v):
            if x:
                self._elim_peers(i, x)

    def _elim_peers(self, i, d):
        for p in PEERS[i]:
            self.c[p] &= ~(1 << d)

    def place(self, i, d):
        self.v[i], self.c[i] = d, 0
        self._elim_peers(i, d)

    def remove(self, cells, d):
        bit, changed = 1 << d, False
        for i in cells:
            if self.c[i] & bit:
                self.c[i] &= ~bit
                changed = True
        return changed

    def done(self):
        return all(self.v)

    def broken(self):
        return any(not x and not m for x, m in zip(self.v, self.c))


def _singles(g):
    for i in range(81):
        if not g.v[i] and bin(g.c[i]).count("1") == 1:
            g.place(i, _digits(g.c[i])[0])
            return True
    for u in UNITS:
        for d in range(1, 10):
            spots = [i for i in u if g.c[i] >> d & 1]
            if len(spots) == 1:
                g.place(spots[0], d)
                return True
    return False


def _locked(g):
    for box in BOXES:
        for d in range(1, 10):
            spots = [i for i in box if g.c[i] >> d & 1]
            if len(spots) < 2:
                continue
            for lines in (ROWS, COLS):
                line = next((ln for ln in lines if all(i in ln for i in spots)), None)
                if line and g.remove([i for i in line if i not in box], d):
                    return True
    for line in ROWS + COLS:
        for d in range(1, 10):
            spots = [i for i in line if g.c[i] >> d & 1]
            if len(spots) < 2:
                continue
            box = next((b for b in BOXES if all(i in b for i in spots)), None)
            if box and g.remove([i for i in box if i not in line], d):
                return True
    return False


def _subsets(g, size):
    from itertools import combinations
    for u in UNITS:
        empty = [i for i in u if not g.v[i]]
        if len(empty) <= size:
            continue
        for combo in combinations(empty, size):        # naked
            m = 0
            for i in combo:
                m |= g.c[i]
            if bin(m).count("1") == size:
                others = [i for i in empty if i not in combo]
                if any(g.remove(others, d) for d in _digits(m)):
                    return True
        open_digits = [d for d in range(1, 10) if any(g.c[i] >> d & 1 for i in empty)]
        for ds in combinations(open_digits, size):     # hidden
            spots = {i for i in empty for d in ds if g.c[i] >> d & 1}
            if len(spots) == size:
                keep = sum(1 << d for d in ds)
                changed = False
                for i in spots:
                    if g.c[i] & ~keep:
                        g.c[i] &= keep
                        changed = True
                if changed:
                    return True
    return False


def _fish(g, size):
    from itertools import combinations
    for base, cover, pos in ((ROWS, COLS, lambda i: i % 9), (COLS, ROWS, lambda i: i // 9)):
        for d in range(1, 10):
            lines = []
            for k, ln in enumerate(base):
                spots = {pos(i) for i in ln if g.c[i] >> d & 1}
                if 2 <= len(spots) <= size:
                    lines.append((k, spots))
            for combo in combinations(lines, size):
                union = set().union(*(s for _, s in combo))
                if len(union) != size:
                    continue
                used = {k for k, _ in combo}
                elim = [i for c in union for i in cover[c] if
                        (i // 9 if base is ROWS else i % 9) not in used]
                if g.remove(elim, d):
                    return True
    return False


def _xywing(g):
    pairs = [i for i in range(81) if bin(g.c[i]).count("1") == 2]
    for pivot in pairs:
        x, y = _digits(g.c[pivot])
        wings = [w for w in pairs if w in PEERS[pivot]]
        for a in wings:
            for b in wings:
                if a >= b:
                    continue
                ca, cb = g.c[a], g.c[b]
                for p, q in ((x, y), (y, x)):
                    if ca >> p & 1 and cb >> q & 1:
                        za, zb = ca & ~(1 << p), cb & ~(1 << q)
                        if za == zb and bin(za).count("1") == 1 and not za >> x & 1 and not za >> y & 1:
                            z = _digits(za)[0]
                            common = set(PEERS[a]) & set(PEERS[b])
                            if g.remove(common - {pivot}, z):
                                return True
    return False


# technique -> difficulty rank it implies (index into LEVELS, medium is
# singles-with-fewer-clues so no technique maps to it)
_TECHNIQUES = [
    (_singles, 0),
    (_locked, 2),
    (lambda g: _subsets(g, 2), 3),
    (lambda g: _subsets(g, 3), 3),
    (lambda g: _fish(g, 2), 4),
    (_xywing, 4),
    (lambda g: _fish(g, 3), 4),
]


def grade(board):
    """Hardest technique rank a person needs (0..4), or None when these
    techniques can't finish it (too hard for a party game)."""
    g = _Grid(board)
    hardest = 0
    while not g.done():
        if g.broken():
            return None
        for step, rank in _TECHNIQUES:
            if step(g):
                hardest = max(hardest, rank)
                break
        else:
            return None
    return hardest


def level_for(board):
    """Difficulty name for a puzzle, or None if it's out of range."""
    rank = grade(board)
    if rank is None:
        return None
    clues = sum(ch != "0" for ch in board)
    if rank == 0:
        return "easy" if clues >= 36 else "medium" if clues <= 32 else None
    return LEVELS[rank]


# -------------------------------------------------------------------- hints ---
_UNIT_NAMES = ([f"Row {r + 1}" for r in range(9)] + [f"Column {c + 1}" for c in range(9)]
               + [f"Box {b + 1}" for b in range(9)])
_ELIM_NAMES = [
    (_locked, "pointing / claiming (a digit locked to one line inside a box)"),
    (lambda g: _subsets(g, 2), "a pair"),
    (lambda g: _subsets(g, 3), "a triple"),
    (lambda g: _fish(g, 2), "an X-wing"),
    (_xywing, "an XY-wing"),
    (lambda g: _fish(g, 3), "a swordfish"),
]


def _find_single(g):
    """(cell, digit, reason) for the easiest single on the grid, or None.
    Hidden singles in a box read most naturally, so they're tried first."""
    for k in list(range(18, 27)) + list(range(18)):
        u = UNITS[k]
        for d in range(1, 10):
            spots = [i for i in u if g.c[i] >> d & 1]
            if len(spots) == 1:
                return spots[0], d, f"{_UNIT_NAMES[k]} has only one spot left for a {d}."
    for i in range(81):
        if not g.v[i] and bin(g.c[i]).count("1") == 1:
            d = _digits(g.c[i])[0]
            return i, d, (f"Row {i // 9 + 1}, column {i % 9 + 1} can only be {d}: every other digit "
                          "is already in its row, column or box.")
    return None


def hint(puzzle, cells, solution):
    """The next helpful move for a board in progress.

    {"kind": "wrong", "i"}               a filled cell that's incorrect
    {"kind": "single", "i", "d", "text"} a logical next digit and why
    {"kind": "reveal", "i", "d", "text"} no simple step found; just give one
    None                                 board already solved"""
    for i in range(81):
        if cells[i] != "0" and cells[i] != solution[i]:
            return {"kind": "wrong", "i": i,
                    "text": f"The {cells[i]} at row {i // 9 + 1}, column {i % 9 + 1} is wrong."}
    if "0" not in cells:
        return None
    g = _Grid("".join(cells))
    used = []
    for _ in range(60):
        found = _find_single(g)
        if found:
            i, d, why = found
            if used:
                why = f"After spotting {' and '.join(dict.fromkeys(used))}: {why[0].lower()}{why[1:]}"
            return {"kind": "single", "i": i, "d": d, "text": why}
        for step, name in _ELIM_NAMES:
            if step(g):
                used.append(name)
                break
        else:
            break
    i = next(k for k in range(81) if cells[k] == "0")
    return {"kind": "reveal", "i": i, "d": int(solution[i]),
            "text": f"This one needs advanced logic. Row {i // 9 + 1}, column {i % 9 + 1} is {solution[i]}."}


# --------------------------------------------------------------- generator ---
def _full_grid(rng):
    cells = [0] * 81

    def go(i):
        if i == 81:
            return True
        used = {cells[p] for p in PEERS[i]}
        ds = [d for d in range(1, 10) if d not in used]
        rng.shuffle(ds)
        for d in ds:
            cells[i] = d
            if go(i + 1):
                return True
        cells[i] = 0
        return False

    go(0)
    return "".join(map(str, cells))


def generate(rng=random, min_clues=17, max_rank=4):
    """One unique-solution puzzle, digging cells in symmetric pairs until no
    more can go. Stops early at `min_clues`. Returns (puzzle, solution)."""
    solution = _full_grid(rng)
    board = list(solution)
    order = list(range(41))       # 40 symmetric pairs + the centre
    rng.shuffle(order)
    for i in order:
        j = 80 - i
        if sum(ch != "0" for ch in board) - (1 if i == j else 2) < min_clues:
            break
        keep = board[i], board[j]
        board[i] = board[j] = "0"
        trial = "".join(board)
        if count_solutions(trial)[0] != 1:
            board[i], board[j] = keep
            continue
        rank = grade(trial)
        if rank is None or rank > max_rank:
            board[i], board[j] = keep
    return "".join(board), solution


def generate_for(level, rng=random, tries=4000):
    """A puzzle of exactly `level`, or None if `tries` runs out."""
    min_clues = {"easy": 36, "medium": 26}.get(level, 17)
    max_rank = {"easy": 0, "medium": 0}.get(level, LEVELS.index(level))
    for _ in range(tries):
        p, s = generate(rng, min_clues=min_clues, max_rank=max_rank)
        if level == "easy":
            p = _loosen(p, s, rng, target=rng.randint(36, 40))
        if level_for(p) == level:
            return p, s
    return None


def _loosen(puzzle, solution, rng, target):
    """Add givens back until `target` clues (easy puzzles stay friendly)."""
    board = list(puzzle)
    empty = [i for i, ch in enumerate(board) if ch == "0"]
    rng.shuffle(empty)
    while sum(ch != "0" for ch in board) < target and empty:
        i = empty.pop()
        board[i] = solution[i]
        board[80 - i] = solution[80 - i]
    return "".join(board)


# --------------------------------------------------------------- transform ---
def transform(puzzle, solution, rng=random):
    """A random equivalent of a puzzle: same logic and difficulty, different
    look. Relabels digits, shuffles rows within bands, bands, columns within
    stacks, stacks, and maybe transposes."""
    def order():
        bands = [0, 1, 2]
        rng.shuffle(bands)
        out = []
        for b in bands:
            inner = [0, 1, 2]
            rng.shuffle(inner)
            out += [b * 3 + r for r in inner]
        return out

    rows, cols = order(), order()
    flip = rng.random() < 0.5
    digits = list("123456789")
    rng.shuffle(digits)
    relabel = {"0": "0", **{str(i + 1): digits[i] for i in range(9)}}

    def apply(b):
        out = []
        for r in range(9):
            for c in range(9):
                rr, cc = (cols[c], rows[r]) if flip else (rows[r], cols[c])
                out.append(relabel[b[rr * 9 + cc]])
        return "".join(out)

    return apply(puzzle), apply(solution)


# -------------------------------------------------------------------- bank ---
def load_bank(path=BANK_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {lvl: data.get(lvl, []) for lvl in LEVELS}


def save_bank(bank, path=BANK_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=0)
    os.replace(tmp, path)


def grow_bank(per_level, path=BANK_PATH, rng=None, log=print):
    rng = rng or random.Random()
    bank = load_bank(path)
    for lvl in LEVELS:
        have = {e["p"] for e in bank[lvl]}
        while len(bank[lvl]) < per_level:
            t = time.time()
            got = generate_for(lvl, rng)
            if not got:
                log(f"  {lvl}: gave up after many tries")
                break
            p, s = got
            if p in have:
                continue
            have.add(p)
            n = len(bank[lvl]) + 1
            bank[lvl].append({"id": f"{lvl[0]}{n:04d}", "p": p, "s": s})
            log(f"  {lvl} #{n}: {sum(ch != '0' for ch in p)} clues ({time.time() - t:.1f}s)")
            save_bank(bank, path)
    return bank


if __name__ == "__main__":
    if "--stats" in sys.argv:
        for lvl, items in load_bank().items():
            clues = [sum(ch != "0" for ch in e["p"]) for e in items]
            rng_s = f"{min(clues)}-{max(clues)} clues" if clues else ""
            print(f"  {lvl:9s} {len(items):4d}  {rng_s}")
    elif "--per-level" in sys.argv:
        n = int(sys.argv[sys.argv.index("--per-level") + 1])
        grow_bank(n)
    else:
        print(__doc__)
