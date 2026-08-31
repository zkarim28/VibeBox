"""
BlackBox card data + deck helpers — the Apples-to-Apples / Cards-Against-
Humanity style game. Pure data and pure functions, no server/IO concerns.

Cards are the official Cards Against Humanity decks only, extracted from the
workbook by CAH_official/extract_official.py. Re-run that script to refresh
CAH_official/cah_official_cards.json when the source changes.

  WHITE : list[str]                       — response cards
  BLACK : list[{"text", "pick", "draw"}]  — prompt cards
          text  has "_____" where a white card goes (or none, for a question)
          pick  how many white cards a player submits (1, 2, or 3)
          draw  extra cards each non-czar draws before playing (0 unless pick 3)
"""

import json
import os
import random

HAND_SIZE = 7
BLANK = "_____"

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "CAH_official", "cah_official_cards.json")
with open(_PATH, encoding="utf-8") as _f:
    _DATA = json.load(_f)

# the official file stores each card as an object ({text, decks, …}); the game
# only needs the text for whites and text/pick/draw for blacks
WHITE = [c["text"] if isinstance(c, dict) else c for c in _DATA["white"]]
BLACK = [c if isinstance(c, dict) else {"text": c, "pick": 1, "draw": 0}
         for c in _DATA["black"]]


def shuffled_white_deck():
    d = list(range(len(WHITE)))
    random.shuffle(d)
    return d


def shuffled_black_deck():
    d = list(range(len(BLACK)))
    random.shuffle(d)
    return d


def white_text(idx):
    return WHITE[idx] if 0 <= idx < len(WHITE) else "?"


def fill_prompt(black_text, whites):
    """Render a black card with the chosen white card texts substituted in.
    Extra blanks (or a card with none) get the whites appended as a list."""
    parts = black_text.split(BLANK)
    out = parts[0]
    i = 0
    for seg in parts[1:]:
        val = whites[i] if i < len(whites) else BLANK
        # drop the trailing period of a white card when it lands mid-sentence
        if seg.strip():
            val = val.rstrip(".")
        out += _cap_after(val, out) + seg
        i += 1
    leftover = whites[i:]
    if leftover:
        out = out.rstrip()
        if not out.endswith((".", "!", "?", ":")):
            out += "."
        out += " " + " ".join(leftover)
    return out.strip()


def _cap_after(val, preceding):
    """Capitalise a substituted card if it starts the sentence."""
    p = preceding.rstrip()
    if p == "" or p.endswith((".", "!", "?")):
        return val[:1].upper() + val[1:]
    return val
