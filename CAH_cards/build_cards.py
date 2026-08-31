#!/usr/bin/env python3
"""
Parse every white/black card file in this folder, clean + dedupe them, and
write cards.json.

NOTE: the BlackBox game now loads the official-only deck from
CAH_official/cah_official_cards.json, not this file. This script is kept as a
standalone parser for the raw *.txt card lists in this folder.

    python3 CAH_cards/build_cards.py

Source formats handled:
  * plain text, one card per line (blank lines ignored)      -> whiteCards*.txt
  * plain text black cards, blanks written as "______"       -> blackCards.txt
  * TSV  id \\t draw \\t pick \\t text \\t watermark              -> blackCardsPretend.txt
  * a Postgres COPY dump with a white_cards table            -> whiteCardsPretend.txt
"""

import html
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "cards.json")

BLANK = "_____"                      # the single canonical blank token
_BLANK_RUN = re.compile(r"_{2,}")     # 2+ underscores = one blank
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(s):
    """Strip HTML, unescape entities, collapse whitespace."""
    s = s.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace('""', '"')
    s = re.sub(r"\s+([.!?,;:])", r"\1", s)
    s = _WS.sub(" ", s).strip()
    return s


def norm_blanks(s):
    """Every run of underscores becomes one BLANK token, spaced only where a
    word butts against it (so "for _____." keeps the period tight)."""
    s = _BLANK_RUN.sub(BLANK, s)
    b = re.escape(BLANK)
    s = re.sub(r"(?<=\w)" + b, " " + BLANK, s)
    s = re.sub(b + r"(?=\w)", BLANK + " ", s)
    s = re.sub(r"\s+([.!?,;:])", r"\1", s)
    return _WS.sub(" ", s).strip()


# ------------------------------------------------------------------- white ---
def read_plain_lines(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            t = clean_text(line)
            if t:
                out.append(t)
    return out


def read_pg_white(path):
    """Lines of the white_cards COPY block: id \\t text \\t watermark."""
    out, inside = [], False
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("COPY white_cards"):
                inside = True
                continue
            if inside and (line.startswith("COPY ") or line.startswith("\\.")):
                break
            if not inside:
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1]:
                t = clean_text(parts[1])
                if t:
                    out.append(t)
    return out


def white_key(t):
    return _WS.sub(" ", t.lower()).strip().rstrip(".").strip()


def build_white():
    raw = []
    raw += read_plain_lines(os.path.join(HERE, "whiteCards.txt"))
    raw += read_plain_lines(os.path.join(HERE, "whiteCards2.txt"))
    raw += read_plain_lines(os.path.join(HERE, "whiteCardsOG.txt"))
    raw += read_pg_white(os.path.join(HERE, "whiteCardsPretend.txt"))

    seen, out = {}, []
    for t in raw:
        if BLANK in t or "_____" in t:      # a stray black card — skip
            continue
        if len(t) < 2 or len(t) > 120:
            continue
        k = white_key(t)
        if not k or k in seen:
            continue
        # CAH white cards read as a noun phrase ending in a period
        if t[-1].isalnum():
            t += "."
        seen[k] = True
        out.append(t)
    out.sort(key=str.lower)
    return out


# ------------------------------------------------------------------- black ---
def read_plain_black(path):
    """One card per line; blanks are runs of underscores. No blank => a
    question the answer completes (pick 1)."""
    out = []
    for t in read_plain_lines(path):
        blanks = len(_BLANK_RUN.findall(t))
        pick = max(1, blanks)
        draw = 2 if pick >= 3 else 0
        out.append((norm_blanks(t), pick, draw))
    return out


def read_tsv_black(path):
    """id \\t draw \\t pick \\t text \\t watermark"""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            try:
                draw, pick = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            text = norm_blanks(clean_text(parts[3]))
            if not text:
                continue
            pick = max(1, min(pick, 3))
            draw = max(0, min(draw, 3))
            out.append((text, pick, draw))
    return out


def black_key(t):
    t = t.lower().replace(BLANK, "_")
    t = re.sub(r"[^a-z0-9_]+", " ", t)      # ignore punctuation/spacing differences
    return _WS.sub(" ", t).strip().rstrip("_").strip()


def build_black():
    raw = []
    raw += read_plain_black(os.path.join(HERE, "blackCards.txt"))
    raw += read_tsv_black(os.path.join(HERE, "blackCardsPretend.txt"))

    seen, out = set(), []
    for text, pick, draw in raw:
        if len(text) < 4 or len(text) > 220:
            continue
        k = black_key(text)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append({"text": text, "pick": pick, "draw": draw})
    out.sort(key=lambda c: c["text"].lower())
    return out


def main():
    white = build_white()
    black = build_black()
    data = {"white": white, "black": black}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    picks = {}
    for c in black:
        picks[c["pick"]] = picks.get(c["pick"], 0) + 1
    print(f"wrote {os.path.relpath(OUT, HERE)}")
    print(f"  white cards: {len(white)}")
    print(f"  black cards: {len(black)}   by pick: "
          + ", ".join(f"{k}->{v}" for k, v in sorted(picks.items())))


if __name__ == "__main__":
    main()
