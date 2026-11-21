"""Write documents to a formatted .xlsx workbook."""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
HEAD_FONT = Font(bold=True, color="FFFFFF")
KEY_FILL = PatternFill("solid", fgColor="DDEBF7")
THIN = Side(style="thin", color="B4C6E7")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

_NUM_RE = re.compile(r"^\(?-?(RM|\$|USD|MYR|€|£|¥|S\$)?\s*-?\d{1,3}(,\d{3})*(\.\d+)?\)?$|^\(?-?\d+(\.\d+)?\)?$", re.I)


def to_cell_value(v: str):
    """Convert number-looking strings to numbers so Excel can sum them.
    Codes with leading zeros (e.g. 00123) stay text."""
    s = str(v).strip()
    if not s or not _NUM_RE.match(s.replace(" ", "")):
        return s
    neg = s.startswith("(") and s.endswith(")") or s.lstrip("(").startswith("-")
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits.count(".") > 1:
        return s
    if len(digits) > 1 and digits.startswith("0") and not digits.startswith("0."):
        return s
    if len(digits.replace(".", "")) > 15:
        return s
    if "." not in digits and "," not in s and len(digits) >= 10:
        return s  # account / phone / reference numbers stay text
    num = float(digits) if "." in digits else int(digits)
    return -num if neg else num


def _put(ws, row: int, col: int, value):
    """Write a cell. Text that looks like a formula (=, +, -, @ ...) from a
    scanned document must stay text, never become a live Excel formula."""
    c = ws.cell(row=row, column=col)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        c.value = value
        c.data_type = "s"
    else:
        c.value = value
    return c


def _safe_sheet(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "_", name).strip("'") or "Sheet"
    base = base[:31]
    cand, n = base, 2
    while cand.lower() in used:
        suffix = f" ({n})"
        cand = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(cand.lower())
    return cand


def _autowidth(ws, min_w=8, max_w=60):
    widths: dict[int, float] = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value is None:
                continue
            text = str(c.value)
            w = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
            widths[c.column] = max(widths.get(c.column, 0), w)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(min_w, min(max_w, w + 2))


def _write_table(ws, start_row: int, columns: list[str], rows: list[list[str]]) -> int:
    for j, name in enumerate(columns, 1):
        c = _put(ws, start_row, j, name)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BOX
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for i, r in enumerate(rows, 1):
        for j, v in enumerate(r, 1):
            val = to_cell_value(v)
            c = _put(ws, start_row + i, j, val)
            c.border = BOX
            if isinstance(val, float):
                c.number_format = "#,##0.00"
    return start_row + len(rows)


def _doc_sheet(ws, doc: dict) -> None:
    t = doc["table"]
    r = 1
    _put(ws, r, 1, t.get("title") or doc["name"]).font = Font(bold=True, size=14)
    r += 1
    ws.cell(row=r, column=1, value=f"Source: {doc['name']}").font = Font(italic=True, color="808080")
    r += 2
    if t["fields"]:
        for f in t["fields"]:
            k = _put(ws, r, 1, f["key"])
            k.font, k.fill, k.border = Font(bold=True), KEY_FILL, BOX
            v = _put(ws, r, 2, to_cell_value(f["value"]))
            v.border = BOX
            r += 1
        r += 1
    header_row = r
    last = _write_table(ws, header_row, t["columns"], t["rows"])
    if t["rows"]:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(t['columns']))}{last}"
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    _autowidth(ws)


def export(docs: list[dict], path: str | Path, mode: str = "per_doc") -> Path:
    """mode: per_doc (one sheet each) | combined (all rows in one sheet) | both."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()

    if mode in ("combined", "both"):
        ws = wb.create_sheet(_safe_sheet("All rows", used))
        cols: list[str] = []
        for d in docs:
            for c in d["table"]["columns"]:
                if c not in cols:
                    cols.append(c)
        out_rows = []
        for d in docs:
            t = d["table"]
            idx = {c: k for k, c in enumerate(t["columns"])}
            label = t.get("title") or d["name"]
            for r in t["rows"]:
                out_rows.append([label] + [r[idx[c]] if c in idx else "" for c in cols])
        last = _write_table(ws, 1, ["Document"] + cols, out_rows)
        if out_rows:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(cols) + 1)}{last}"
        ws.freeze_panes = "B2"
        _autowidth(ws)

        ws2 = wb.create_sheet(_safe_sheet("Summary", used))
        keys: list[str] = []
        for d in docs:
            for f in d["table"]["fields"]:
                if f["key"] not in keys:
                    keys.append(f["key"])
        srows = []
        for d in docs:
            fm = {f["key"]: f["value"] for f in d["table"]["fields"]}
            srows.append([d["table"].get("title") or d["name"], d["name"], d["table"].get("doc_type", "")]
                         + [fm.get(k, "") for k in keys])
        _write_table(ws2, 1, ["Document", "File", "Type"] + keys, srows)
        ws2.freeze_panes = "B2"
        _autowidth(ws2)

    if mode in ("per_doc", "both"):
        for d in docs:
            ws = wb.create_sheet(_safe_sheet(d["table"].get("title") or Path(d["name"]).stem, used))
            _doc_sheet(ws, d)

    if not wb.sheetnames:
        wb.create_sheet("Empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
