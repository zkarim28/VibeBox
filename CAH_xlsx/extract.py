#!/usr/bin/env python3
"""
Pull every Prompt (black) and Response (white) card out of
../CAH_cards/Cards Against Humanity.xlsx, across all 49 sheets, and record
which deck(s) each card belongs to.

Two passes:
  1. "Master Cards List" sheet — a maintained consolidation where prompt text
     sits in col A with its deck in col C, and response text in col G with its
     deck in col H.
  2. Every other sheet — find cells literally reading "Prompt" / "Response"
     (card text is the cell to the right; the deck is the nearest "Set" name
     above it in that column), plus the hidden "… - Black" / "… - White" style
     sheets which list text in one column under deck-header rows.

Output (written next to this script, kept separate from CAH_cards/):
  blackbox_xlsx_cards.json   {"black":[{text,pick,decks}], "white":[{text,decks}]}
  black_cards_xlsx.txt / white_cards_xlsx.txt   one card per line, "  [decks…]"
"""

import html
import json
import os
import re
from collections import OrderedDict

from xlsx_read import Workbook

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(HERE, os.pardir, "CAH_cards", "Cards Against Humanity.xlsx")

_WS = re.compile(r"\s+")
_UNDERS = re.compile(r"_{2,}")
_TAG = re.compile(r"<[^>]+>")
BLANK = "_____"

# strings that mean "this is a header / link / note row, not a card"
_JUNK_EXACT = {
    "", "prompt", "response", "prompt cards", "response cards", "set", "special",
    "sheet", "source", "comments", "edition", "version comments",
    "duplicates", "total cards", "total", "totals", "#ref!", "n/a", "-", "—",
    "printed", "unprinted", "notes",
    "card listing spreadsheet card types:", "starting cell",
}
_SETNAME_HINT = re.compile(
    r"(deck|expansion|edition|volume\s*\d|version|pack\b|box\b|set\b|"
    r"kickstarter|against humanity|apples)", re.I)


def clean(t):
    if not isinstance(t, str):
        return ""
    t = t.replace("\xa0", " ").replace("​", "")
    t = t.replace("\\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = _TAG.sub("", t)
    t = html.unescape(t)
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+([.!?,;:])", r"\1", t)
    return _WS.sub(" ", t).strip()


def norm_blanks(t):
    t = _UNDERS.sub(BLANK, t)
    b = re.escape(BLANK)
    t = re.sub(r"(?<=\w)" + b, " " + BLANK, t)
    t = re.sub(b + r"(?=\w)", BLANK + " ", t)
    t = re.sub(r"\s+([.!?,;:])", r"\1", t)
    return _WS.sub(" ", t).strip()


def looks_like_card(t):
    if not t or len(t) < 3 or len(t) > 240:
        return False
    low = t.lower().strip()
    if low in _JUNK_EXACT:
        return False
    if low.startswith(("http://", "https://", "www.", "=hyperlink", "see ")):
        return False
    if re.fullmatch(r"v?\d+(\.\d+)*[a-z]?", low):        # version tags
        return False
    if re.fullmatch(r"(us|ca|uk|au|intl|ks|nz|eu|oc)( v?\d.*)?", low):
        return False
    return True


def pick_from(special, text):
    s = (special or "").upper()
    if "PICK 3" in s or "PICK3" in s:
        return 3
    if "PICK 2" in s or "PICK2" in s:
        return 2
    n = len(_UNDERS.findall(text))
    if n >= 3:
        return 3
    if n == 2:
        return 2
    return 1


def draw_from(special, pick):
    s = (special or "").upper()
    if "DRAW 3" in s:
        return 3
    if "DRAW 2" in s:
        return 2
    return 2 if pick >= 3 else 0


class Store:
    def __init__(self):
        self.black = OrderedDict()   # key -> {"text","pick","draw","decks":set}
        self.white = OrderedDict()

    def _key(self, kind, text):
        t = text.lower()
        if kind == "black":
            t = t.replace(BLANK, "_")
        t = re.sub(r"[^a-z0-9]+", " ", t).strip()
        return t

    def add_black(self, text, special, deck):
        text = norm_blanks(clean(text))
        if not looks_like_card(text):
            return
        k = self._key("black", text)
        if not k:
            return
        pick = pick_from(special, text)
        e = self.black.get(k)
        if e is None:
            self.black[k] = {"text": text, "pick": pick,
                             "draw": draw_from(special, pick), "decks": set()}
            e = self.black[k]
        if deck:
            e["decks"].add(deck)

    def add_white(self, text, deck):
        text = clean(text)
        if BLANK in text or _UNDERS.search(text):
            return
        if not looks_like_card(text):
            return
        if text[-1:].isalnum():
            text += "."
        k = self._key("white", text)
        if not k:
            return
        e = self.white.get(k)
        if e is None:
            self.white[k] = {"text": text, "decks": set()}
            e = self.white[k]
        if deck:
            e["decks"].add(deck)


def deck_clean(d):
    d = clean(d)
    return d if d and d.lower() not in _JUNK_EXACT else ""


# --------------------------------------------------------------- pass 1: master
def pass_master(wb, sp, store):
    rows = list(wb.rows(sp["Master Cards List"]))
    for r in rows[1:]:
        if r.get(0):
            store.add_black(r[0], r.get(1), deck_clean(r.get(2)) or "Master Cards List")
        if r.get(6):
            store.add_white(r[6], deck_clean(r.get(7)) or "Master Cards List")


# --------------------------------------------- pass 2a: "Prompt"/"Response" grids
def _deck_for_column(grid, upto_row, col, sheet_name):
    """Nearest 'Set' name at or above `upto_row` in this column."""
    for rr in range(upto_row, -1, -1):
        row = grid[rr]
        if str(row.get(col, "")).strip().lower() == "set":
            for off in (1, 2, 3):
                cand = deck_clean(row.get(col + off))
                if cand:
                    return cand
    # some sheets carry the deck name as the block's very first text cell
    for rr in range(upto_row, -1, -1):
        v = deck_clean(grid[rr].get(col + 1))
        if v and _SETNAME_HINT.search(v) and len(v) < 70:
            return v
    return sheet_name


def pass_labeled(wb, sp, store):
    for s in wb.sheets:
        name = s["name"].strip()
        if name == "Master Cards List":
            continue
        grid = list(wb.rows(sp[name]))
        has_label = any(
            str(v).strip().lower() in ("prompt", "response")
            for row in grid for v in row.values())
        if not has_label:
            continue
        for ri, row in enumerate(grid):
            for c, v in list(row.items()):
                lab = str(v).strip().lower()
                if lab not in ("prompt", "response"):
                    continue
                text = row.get(c + 1) or row.get(c - 1)
                if not text:
                    continue
                special = row.get(c + 2)
                deck = _deck_for_column(grid, ri, c, name)
                if lab == "prompt":
                    store.add_black(text, special, deck)
                else:
                    store.add_white(text, deck)


# ---------------------------------------- pass 2b: hidden "… - Black/White" lists
def _text_column(grid):
    from collections import Counter
    cc = Counter()
    for row in grid:
        for c, v in row.items():
            if isinstance(v, str) and len(v) > 12 and not v.lower().startswith("http"):
                cc[c] += 1
    return cc.most_common(1)[0][0] if cc else None


def pass_suffix(wb, sp, store):
    for s in wb.sheets:
        name = s["name"].strip()
        low = name.lower()
        if "- black" in low:
            kind = "black"
        elif "- white" in low:
            kind = "white"
        elif low in ("black 1.7s",):
            kind = "black"
        elif low in ("white 1.7s",):
            kind = "white"
        else:
            continue
        grid = list(wb.rows(sp[name]))
        # already handled if this sheet also has Prompt/Response labels
        if any(str(v).strip().lower() in ("prompt", "response")
               for row in grid for v in row.values()):
            continue
        tc = _text_column(grid)
        if tc is None:
            continue
        base_deck = re.sub(r"\s*-\s*(black|white)\s*$", "", name, flags=re.I).strip()
        base_deck = re.sub(r"\s*1\.7s$", "", base_deck, flags=re.I).strip() or name
        cur_deck = base_deck
        for row in grid:
            val = row.get(tc)
            if not isinstance(val, str):
                continue
            v = clean(val)
            sib = " ".join(str(x) for k, x in row.items() if k != tc)
            is_header = (
                "special" in sib.lower() or "comments" in sib.lower()
                or re.search(r"\bedition\b|\bvol\b", sib.lower())
                or (len(v) < 60 and _SETNAME_HINT.search(v) and
                    (v.endswith((":", "Edition", "Deck", "Set", "Version"))
                     or ":" in v or "Volume" in v)))
            if is_header and looks_like_setname(v):
                cur_deck = v
                continue
            special = row.get(tc + 1) or row.get(tc - 1)
            if kind == "black":
                store.add_black(v, special, cur_deck)
            else:
                store.add_white(v, cur_deck)


def looks_like_setname(v):
    if not v or len(v) > 80:
        return False
    return bool(_SETNAME_HINT.search(v)) or ":" in v


# ----------------------------------------------------------------------- output
def main():
    wb = Workbook(XLSX)
    sp = {s["name"].strip(): s["path"] for s in wb.sheets}
    store = Store()
    pass_master(wb, sp, store)
    pass_labeled(wb, sp, store)
    pass_suffix(wb, sp, store)

    black = [{"text": e["text"], "pick": e["pick"], "draw": e["draw"],
              "decks": sorted(e["decks"])} for e in store.black.values()]
    white = [{"text": e["text"], "decks": sorted(e["decks"])}
             for e in store.white.values()]
    black.sort(key=lambda c: c["text"].lower())
    white.sort(key=lambda c: c["text"].lower())

    out_json = os.path.join(HERE, "blackbox_xlsx_cards.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"black": black, "white": white}, f, ensure_ascii=False,
                  separators=(",", ":"))

    with open(os.path.join(HERE, "black_cards_xlsx.txt"), "w", encoding="utf-8") as f:
        for c in black:
            f.write(f"{c['text']}\t[pick {c['pick']}]\t{'; '.join(c['decks'])}\n")
    with open(os.path.join(HERE, "white_cards_xlsx.txt"), "w", encoding="utf-8") as f:
        for c in white:
            f.write(f"{c['text']}\t{'; '.join(c['decks'])}\n")

    decks = set()
    for c in black + white:
        decks.update(c["decks"])
    print(f"black (prompt) cards : {len(black)}")
    print(f"white (response) cards: {len(white)}")
    print(f"distinct decks       : {len(decks)}")
    print(f"wrote {os.path.relpath(out_json, HERE)}, black_cards_xlsx.txt, white_cards_xlsx.txt")


if __name__ == "__main__":
    main()
