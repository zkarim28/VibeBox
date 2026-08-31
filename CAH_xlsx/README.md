# CAH_xlsx — cards extracted from `Cards Against Humanity.xlsx`

Separate from `CAH_cards/` and the game's `blackbox_cards.json`. **Not wired into
the game.** This is just the parsed dump of the workbook.

## Source

`../CAH_cards/Cards Against Humanity.xlsx` — the community "Card Listing
Spreadsheet" (49 sheets, ~73k unique strings).

## Run

```
python3 extract.py
```

Reads the workbook with the tiny stdlib-only reader in `xlsx_read.py`
(no openpyxl/pandas on this machine). `peek.py` dumps the first rows of any
sheet for inspection.

## How cards are found

1. **`Master Cards List` sheet** — the maintained consolidation. Prompt text in
   column A with its deck in column C; response text in column G with its deck
   in column H.
2. **Every sheet with `Prompt` / `Response` label cells** — the card text is the
   cell to the right; the deck is the nearest `Set` name above it in that
   column (falls back to the sheet name).
3. **Hidden `… - Black` / `… - White` list sheets** — text in one column under
   deck-header rows; type comes from the sheet name.

Cards are deduped by normalised text (case/punctuation/whitespace-insensitive;
underscore runs collapsed). Each card keeps **every** deck it was seen under, so
one card can list several decks.

## Output

| file | contents |
|---|---|
| `blackbox_xlsx_cards.json` | `{"black":[{text,pick,draw,decks}], "white":[{text,decks}]}` |
| `black_cards_xlsx.txt` | one prompt per line — `text \t [pick N] \t deck; deck` |
| `white_cards_xlsx.txt` | one response per line — `text \t deck; deck` |

`pick` / `draw` for prompts come from the sheet's "Special" column
(`PICK 2`, `DRAW 2, PICK 3`) or the count of `_____` blanks.

## Last run

- **14,835** prompt (black) cards
- **47,223** response (white) cards
- **526** distinct decks
- every card has at least one deck label
