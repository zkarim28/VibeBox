"""
Word Hunt — Boggle-style word search. Pure data + board/word helpers.

  new_grid(rng)         -> 16 letters, row-major (4x4), this round's board
  word_for_path(grid, path) -> the word a swipe spells, or None if the swipe
                                itself is illegal (non-adjacent step, reused tile)
  WORDS                 -> the dictionary (uppercase) a found word must be in
  score(word)            -> points for a word, by length

Board generation is letter-frequency weighted rather than a fixed die set —
a handful of guaranteed vowels keeps every board playable without needing to
trust a memorized "official" Boggle die table.
"""

import os
import random

# Relative English letter frequency (%), used as sampling weights so boards
# come out looking like real Boggle boards (mostly common letters, the odd
# rare one) instead of uniform-random noise.
LETTER_FREQ = {
    "E": 12.7, "T": 9.1, "A": 8.2, "O": 7.5, "I": 7.0, "N": 6.7, "S": 6.3,
    "H": 6.1, "R": 6.0, "D": 4.3, "L": 4.0, "C": 2.8, "U": 2.8, "M": 2.4,
    "W": 2.4, "F": 2.2, "G": 2.0, "Y": 2.0, "P": 1.9, "B": 1.5, "V": 1.0,
    "K": 0.8, "J": 0.15, "X": 0.15, "Q": 0.10, "Z": 0.07,
}
_VOWELS = [c for c in LETTER_FREQ if c in "AEIOU"]
_VOWEL_W = [LETTER_FREQ[c] for c in _VOWELS]
_CONSONANTS = [c for c in LETTER_FREQ if c not in "AEIOU"]
_CONSONANT_W = [LETTER_FREQ[c] for c in _CONSONANTS]

GRID_SIZE = 4
GRID_CELLS = GRID_SIZE * GRID_SIZE

_WORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordhunt_words.txt")
with open(_WORDS_PATH, encoding="utf-8") as _f:
    WORDS = frozenset(w.strip().upper() for w in _f if w.strip())

POINTS = {3: 100, 4: 400, 5: 800, 6: 1400, 7: 1800}
MAX_POINTS = 2200   # 8+ letters


def score(word):
    n = len(word)
    if n < 3:
        return 0
    return POINTS.get(n, MAX_POINTS)


def new_grid(rng=random):
    """16 letters, row-major, with 5-7 of them vowels so the board is
    actually playable."""
    n_vowels = rng.randint(5, 7)
    letters = (rng.choices(_VOWELS, weights=_VOWEL_W, k=n_vowels)
               + rng.choices(_CONSONANTS, weights=_CONSONANT_W, k=GRID_CELLS - n_vowels))
    rng.shuffle(letters)
    return letters


def adjacent(a, b):
    """8-directional adjacency between two cell indices (0..15) on the grid."""
    ar, ac = divmod(a, GRID_SIZE)
    br, bc = divmod(b, GRID_SIZE)
    return a != b and abs(ar - br) <= 1 and abs(ac - bc) <= 1


def word_for_path(grid, path):
    """The word `path` (a list of cell indices) spells on `grid`, or None if
    the path itself isn't a legal swipe: each step must land on a new tile
    adjacent to the last, and the path needs at least 3 tiles."""
    if not isinstance(path, list) or not (3 <= len(path) <= GRID_CELLS):
        return None
    if len(set(path)) != len(path):
        return None
    if any(not isinstance(i, int) or not (0 <= i < GRID_CELLS) for i in path):
        return None
    for a, b in zip(path, path[1:]):
        if not adjacent(a, b):
            return None
    return "".join(grid[i] for i in path)
