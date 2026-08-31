import sys
from xlsx_read import Workbook

wb = Workbook("../CAH_cards/Cards Against Humanity.xlsx")
want = sys.argv[1:] or None
for s in wb.sheets:
    if want and s["name"] not in want:
        continue
    print("\n" + "=" * 80)
    print(f"SHEET: {s['name']}  ({s['state']})")
    print("=" * 80)
    n = 0
    for row in wb.rows(s["path"]):
        cells = {k: (v[:60] if isinstance(v, str) else v) for k, v in sorted(row.items())}
        print(f"  r{n}: {cells}")
        n += 1
        if n >= 18:
            break
