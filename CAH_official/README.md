# CAH_official — first-party Cards Against Humanity cards only

Separate from `CAH_cards/`, `CAH_xlsx/`, and the game's `blackbox_cards.json`.
**Not wired into the game.**

## What "official" means here

`extract_official.py` reads `../CAH_cards/Cards Against Humanity.xlsx` but only
looks at the **11 sheets whose name contains "CAH"** — the first-party decks:

```
CAH Main Deck          CAH Main Deck - Black
CAH Family Edition     CAH Expansions - Black
CAH Expansions         CAH Expansions - White
CAH Packs              CAH Expansions Old
CAH - Holiday Specials CAH Intl - Black
                       CAH Intl - White
```

Every community / third-party / fan / "Stand Alone" sheet is ignored, and the
`Master Cards List` (which mixes everything) is **not** used.

## Card typing

* `Prompt` cells → **black** cards
* `Response` cells → **white** cards
* `CAH - Holiday Specials` uses `Black` / `White` in its type column instead

Text is deduped by normalised form (case / punctuation / whitespace, underscore
runs collapsed to `_____`, trailing `(Pax East 2014)`-style notes dropped).
Each card lists **every official deck** it appears in; the
`Cards Against Humanity: ` name prefix is stripped so a deck's long-form and
short-form names merge.

## Run

```
python3 extract_official.py
```

## Output

| file | contents |
|---|---|
| `cah_official_cards.json` | `{"black":[{text,pick,draw,decks}], "white":[{text,decks}]}` |
| `black_cards_official.txt` | `text ⇥ [pick N] ⇥ deck; deck` |
| `white_cards_official.txt` | `text ⇥ deck; deck` |

## Last run

- **~1,358** black (prompt) cards
- **~7,060** white (response) cards
- **113** official decks (Main Deck, the six numbered Expansions, the coloured
  Box Expansions, Family Edition, Card Lab, Bōks, every retail/PAX/holiday pack,
  UK/AU/International editions, …)
