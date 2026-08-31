#!/usr/bin/env python3
"""
Official Cards Against Humanity cards only.

Reads ../CAH_cards/Cards Against Humanity.xlsx but looks at ONLY the sheets
whose name contains "CAH" — those hold the first-party decks (Main Deck,
Expansions, Packs, Family Edition, International editions, Holiday packs).
The community / third-party / fan sheets are ignored.

  Prompt / Black  -> black card
  Response / White -> white card
Each card is labelled with the deck(s) it appears in.

Output (this folder, separate from CAH_cards/ and CAH_xlsx/):
  cah_official_cards.json   {"black":[{text,pick,draw,decks}], "white":[{text,decks}]}
  black_cards_official.txt / white_cards_official.txt
"""

import html
import json
import os
import re
from collections import Counter, OrderedDict

from xlsx_read import Workbook

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(HERE, os.pardir, "CAH_cards", "Cards Against Humanity.xlsx")

_WS = re.compile(r"\s+")
_UNDERS = re.compile(r"_{2,}")
_TAG = re.compile(r"<[^>]+>")
BLANK = "_____"

_JUNK_EXACT = {
    "", "prompt", "response", "black", "white", "prompt cards", "response cards",
    "set", "special", "sheet", "source", "comments", "edition", "version",
    "version comments", "duplicates", "total cards", "total", "totals",
    "#ref!", "n/a", "-", "—", "printed", "unprinted", "notes", "day", "type",
    "title", "envelope", "starting cell",
}
_SETNAME_HINT = re.compile(
    r"(deck|expansion|edition|pack\b|box\b|version|promo|kit\b|against humanity|"
    r"holiday|nostalgia|geek|sci-?fi|movie night|world wide web|hidden compartment|"
    r"90s|college|fantasy|science|food|weed|2012|2013|2014|2015|2016|2017)", re.I)


def clean(t):
    if not isinstance(t, str):
        return ""
    t = t.replace("\xa0", " ").replace("​", "")
    t = t.replace("\\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = _TAG.sub("", t)
    t = html.unescape(t)
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+([.!?,;:])", r"\1", t)
    return _WS.sub(" ", t).strip()


_TRAIL_NOTE = re.compile(
    r"\s*\((?:[^()]*\b(?:pax|promo|prime \d|east \d|20\d\d|panel cards?)\b[^()]*)\)\s*$",
    re.I)


def strip_note(t):
    """Drop a trailing '(Pax East 2014)' / '(Promo Pack C)' annotation that the
    spreadsheet appends to some card texts."""
    return _TRAIL_NOTE.sub("", t).strip()


def norm_blanks(t):
    t = _UNDERS.sub(BLANK, t)
    b = re.escape(BLANK)
    t = re.sub(r"(?<=\w)" + b, " " + BLANK, t)
    t = re.sub(b + r"(?=\w)", BLANK + " ", t)
    t = re.sub(r"\s+([.!?,;:])", r"\1", t)
    return _WS.sub(" ", t).strip()


def is_card(t):
    if not t or len(t) < 2 or len(t) > 240:
        return False
    low = t.lower().strip()
    if low in _JUNK_EXACT:
        return False
    if low.startswith(("http://", "https://", "www.", "see ")):
        return False
    if re.fullmatch(r"v?\d+(\.\d+)*[a-z]?", low):
        return False
    if re.fullmatch(r"(us|ca|uk|au|intl|ks|nz|eu|oc)( v?\d.*)?", low):
        return False
    if "(?)" in t or low.startswith(("word on ", "picture of ", "[a picture",
                                     "[image", "image of ", "photo of ")):
        return False                      # image-pack description placeholders
    return True


def norm_deck(d):
    """The labelled sheets name a deck short-form ('Fantasy Pack'); the hidden
    list sheets name it long-form ('Cards Against Humanity: Fantasy Pack').
    Strip the prefix so the two merge."""
    d = clean(d)
    d = re.sub(r"^Cards Against Humanity\s*[:\-]\s*", "", d).strip()
    d = re.sub(r"\s*/.*$", "", d).strip()          # "Foo Pack/Bar Pack" -> "Foo Pack"
    return d


def deck_ok(d):
    d = clean(d)
    if not d or d.lower() in _JUNK_EXACT or d.lower().startswith("http"):
        return ""
    return d


def pick_of(special, text):
    s = (special or "").upper()
    if "PICK 3" in s:
        return 3
    if "PICK 2" in s:
        return 2
    n = len(_UNDERS.findall(text))
    return 3 if n >= 3 else (2 if n == 2 else 1)


def draw_of(special, pick):
    s = (special or "").upper()
    if "DRAW 3" in s:
        return 3
    if "DRAW 2" in s:
        return 2
    return 2 if pick >= 3 else 0


class Store:
    def __init__(self):
        self.black = OrderedDict()
        self.white = OrderedDict()

    def _key(self, kind, text):
        t = text.lower()
        if kind == "black":
            t = t.replace(BLANK, "_")
        return re.sub(r"[^a-z0-9]+", " ", t).strip()

    def add_black(self, text, special, deck):
        text = strip_note(norm_blanks(clean(text)))
        if not is_card(text):
            return
        k = self._key("black", text)
        if not k:
            return
        e = self.black.get(k)
        if e is None:
            pick = pick_of(special, text)
            self.black[k] = {"text": text, "pick": pick,
                             "draw": draw_of(special, pick), "decks": set()}
            e = self.black[k]
        if deck:
            _add_deck(e, deck)

    def add_white(self, text, deck):
        text = strip_note(clean(text))
        if BLANK in text or _UNDERS.search(text):
            return
        if not is_card(text):
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
            _add_deck(e, deck)


_DECK_CANON = {}
def _add_deck(entry, deck):
    d = norm_deck(deck)
    if not d:
        return
    key = d.lower()
    entry["decks"].add(_DECK_CANON.setdefault(key, d))


# ---- pass A: sheets that use "Prompt" / "Response" label cells --------------
def _deck_for_col(grid, upto, col, sheet):
    for rr in range(upto, -1, -1):
        if str(grid[rr].get(col, "")).strip().lower() == "set":
            for off in (1, 2, 3):
                d = deck_ok(grid[rr].get(col + off))
                if d:
                    return d
    for rr in range(upto, -1, -1):
        d = deck_ok(grid[rr].get(col + 1))
        if d and _SETNAME_HINT.search(d) and len(d) < 70:
            return d
    return sheet


def pass_labeled(grid, sheet, store):
    if not any(str(v).strip().lower() in ("prompt", "response")
               for row in grid for v in row.values()):
        return False
    for ri, row in enumerate(grid):
        for c, v in list(row.items()):
            lab = str(v).strip().lower()
            if lab not in ("prompt", "response"):
                continue
            text = row.get(c + 1) or row.get(c - 1)
            if not text:
                continue
            deck = _deck_for_col(grid, ri, c, sheet)
            if lab == "prompt":
                store.add_black(text, row.get(c + 2), deck)
            else:
                store.add_white(text, deck)
    return True


# ---- pass B: hidden list sheets (text column under deck-header rows) --------
def _text_col(grid):
    cc = Counter()
    for row in grid:
        for c, v in row.items():
            if isinstance(v, str) and len(v) > 10 and not v.lower().startswith("http"):
                cc[c] += 1
    return cc.most_common(1)[0][0] if cc else None


def pass_list(grid, sheet, store):
    low = sheet.lower()
    if "- black" in low or low.endswith("black"):
        kind = "black"
    elif "- white" in low or low.endswith("white"):
        kind = "white"
    else:
        return False
    tc = _text_col(grid)
    if tc is None:
        return True
    base = re.sub(r"\s*-\s*(black|white)\s*$", "", sheet, flags=re.I).strip() or sheet
    cur = base
    for row in grid:
        val = row.get(tc)
        if not isinstance(val, str):
            continue
        v = clean(val)
        sib = " ".join(str(x) for k, x in row.items() if k != tc).lower()
        header = (
            "special" in sib or "comments" in sib or "edition" in sib
            or (len(v) < 70 and _SETNAME_HINT.search(v)
                and (":" in v or v.lower().endswith(("edition", "version",
                     "pack", "kit", "deck", "expansion")))))
        if header and (_SETNAME_HINT.search(v) or ":" in v):
            cur = v
            continue
        if kind == "black":
            store.add_black(v, row.get(tc + 1) or row.get(tc - 1), cur)
        else:
            store.add_white(v, cur)
    return True


# ---- pass C: the Holiday Specials sheet (col: Black/White | text | ... | deck)
def pass_holiday(grid, sheet, store):
    title = ""
    for row in grid:
        for v in row.values():
            if isinstance(v, str) and "holiday" in v.lower():
                title = clean(v)
                break
        if title:
            break
    for row in grid:
        typ = None
        for c, v in row.items():
            if isinstance(v, str) and v.strip().lower() in ("black", "white"):
                typ = v.strip().lower()
                tcol = c
                break
        if not typ:
            continue
        text = row.get(tcol + 1)
        if not text:
            continue
        deck = ""
        for c in range(tcol + 2, tcol + 8):
            d = deck_ok(row.get(c))
            if d and _SETNAME_HINT.search(d):
                deck = d
                break
        deck = deck or title or sheet
        if typ == "black":
            store.add_black(text, row.get(tcol + 2), deck)
        else:
            store.add_white(text, deck)
    return True


def main():
    wb = Workbook(XLSX)
    store = Store()
    used = []
    for s in wb.sheets:
        name = s["name"].strip()
        if "cah" not in name.lower():
            continue
        used.append(name)
        grid = list(wb.rows(s["path"]))
        if "holiday" in name.lower():
            pass_holiday(grid, name, store)
        elif pass_labeled(grid, name, store):
            pass
        else:
            pass_list(grid, name, store)

    black = sorted(({"text": e["text"], "pick": e["pick"], "draw": e["draw"],
                     "decks": sorted(e["decks"])} for e in store.black.values()),
                   key=lambda c: c["text"].lower())
    white = sorted(({"text": e["text"], "decks": sorted(e["decks"])}
                    for e in store.white.values()), key=lambda c: c["text"].lower())

    with open(os.path.join(HERE, "cah_official_cards.json"), "w", encoding="utf-8") as f:
        json.dump({"black": black, "white": white}, f, ensure_ascii=False,
                  separators=(",", ":"))
    with open(os.path.join(HERE, "black_cards_official.txt"), "w", encoding="utf-8") as f:
        for c in black:
            f.write(f"{c['text']}\t[pick {c['pick']}]\t{'; '.join(c['decks'])}\n")
    with open(os.path.join(HERE, "white_cards_official.txt"), "w", encoding="utf-8") as f:
        for c in white:
            f.write(f"{c['text']}\t{'; '.join(c['decks'])}\n")

    decks = Counter(d for c in black + white for d in c["decks"])
    print("sheets used:", ", ".join(used))
    print(f"\nblack (prompt): {len(black)}")
    print(f"white (response): {len(white)}")
    print(f"decks: {len(decks)}")
    for d, n in decks.most_common():
        print(f"  {n:5}  {d}")


if __name__ == "__main__":
    main()
