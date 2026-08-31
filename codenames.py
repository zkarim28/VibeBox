"""
Codenames word list + board dealer. Pure data, no server concerns.

A board is 25 words plus a 25-slot key. The team that goes first has 9 words,
the other has 8, then 7 innocent bystanders and 1 assassin (9+8+7+1 = 25).
"""

import os
import random

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "Codenames", "cards.txt")
with open(_PATH, encoding="utf-8") as _f:
    WORDS = [w.strip().upper() for w in _f if w.strip()]

BOARD_SIZE = 25
STARTER_COUNT = 9
SECOND_COUNT = 8
INNOCENT_COUNT = 7
ASSASSIN_COUNT = 1


def deal_board(rng=random):
    """Return (words, key, starter). `key[i]` is one of
    'red' | 'blue' | 'innocent' | 'assassin'."""
    words = rng.sample(WORDS, BOARD_SIZE)
    starter = rng.choice(["red", "blue"])
    other = "blue" if starter == "red" else "red"
    key = ([starter] * STARTER_COUNT + [other] * SECOND_COUNT
           + ["innocent"] * INNOCENT_COUNT + ["assassin"] * ASSASSIN_COUNT)
    rng.shuffle(key)
    return words, key, starter
