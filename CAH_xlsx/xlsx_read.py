"""Minimal stdlib-only .xlsx reader — enough to pull cell text out of every
sheet. No formulas, no styles, just values."""

import re
import zipfile
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _col_to_idx(ref):
    """'AB12' -> 27 (0-based column index)."""
    m = re.match(r"([A-Z]+)", ref)
    c = 0
    for ch in m.group(1):
        c = c * 26 + (ord(ch) - 64)
    return c - 1


def _si_text(si):
    """Text of one <si> shared-string element (may hold several <r><t> runs)."""
    out = []
    for t in si.iter(NS + "t"):
        out.append(t.text or "")
    return "".join(out)


class Workbook:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        self.shared = self._load_shared()
        self.sheets = self._load_sheet_index()

    def _load_shared(self):
        try:
            data = self.zip.read("xl/sharedStrings.xml")
        except KeyError:
            return []
        out = []
        for _, el in ET.iterparse(_bytes_io(data), events=("end",)):
            if el.tag == NS + "si":
                out.append(_si_text(el))
                el.clear()
        return out

    def _load_sheet_index(self):
        wb = ET.fromstring(self.zip.read("xl/workbook.xml"))
        rels = ET.fromstring(self.zip.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {}
        for r in rels:
            rid_to_target[r.get("Id")] = r.get("Target")
        sheets = []
        for s in wb.iter(NS + "sheet"):
            target = rid_to_target[s.get(RID)]
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            sheets.append({
                "name": s.get("name"),
                "state": s.get("state", "visible"),
                "path": target,
            })
        return sheets

    def rows(self, sheet_path):
        """Yield each row as a dict {col_idx: text}."""
        data = self.zip.read(sheet_path)
        for _, el in ET.iterparse(_bytes_io(data), events=("end",)):
            if el.tag != NS + "row":
                continue
            row = {}
            for c in el.findall(NS + "c"):
                ref = c.get("r")
                if not ref:
                    continue
                ci = _col_to_idx(ref)
                t = c.get("t")
                v = c.find(NS + "v")
                if t == "s":
                    if v is not None and v.text is not None:
                        row[ci] = self.shared[int(v.text)]
                elif t == "inlineStr":
                    is_el = c.find(NS + "is")
                    row[ci] = _si_text(is_el) if is_el is not None else ""
                elif v is not None:
                    row[ci] = v.text or ""
            if row:
                yield row
            el.clear()


def _bytes_io(data):
    import io
    return io.BytesIO(data)


if __name__ == "__main__":
    import sys
    wb = Workbook(sys.argv[1])
    print(f"{len(wb.shared)} shared strings, {len(wb.sheets)} sheets\n")
    for s in wb.sheets:
        print(f"  [{s['state']:7}] {s['name']}")
