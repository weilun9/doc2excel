"""SmartAcc DynaMod supplier bills: OCR table -> 进单整理表 + import files.

DynaMod's Supplier Invoice / Supplier Credit Note import takes ONE table with
five columns: Supplier ID | Location ID | Barcode | Unit Price | Quantity.
Quantity is in PCS and Unit Price = line nett amount / PCS (max 4 decimals).

This module
  * reads the user's own 进单整理模板 (sheets 供应商 + 条码对照) for supplier
    codes and the barcode/PCS-per-unit lookup. That workbook holds business
    data, so it stays on the user's PC (path in settings), never in the app;
  * turns a confirmed doc2excel table into a bill (header + lines) with the
    same rules as the template's formulas: barcode = system > handwritten >
    first 12 digits of the printed EAN, PCS = qty x PCS-per-unit, price =
    ROUND(nett / PCS, 4), and checks 差额 / Cent Adjustment;
  * fills a copy of the template (formulas kept) so the user reviews the
    bills the way they already do;
  * writes one import file per bill into a copy of DynaMod's own blank
    template, cells only (no whole-column formats, no extra sheets);
  * reads an edited 整理表 back and writes the import files from its green
    A:E columns, replacing the manual filter / copy / paste-values step.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import config

DEFAULT_LOCATION = "HQ"

# PCS per unit when the barcode table has no entry. None = must be filled in.
UNIT_PCS: dict[str, float | None] = {
    "": 1, "PCS": 1, "PC": 1, "PCE": 1, "UNIT": 1, "UNITS": 1, "UNT": 1, "EA": 1, "EACH": 1,
    "PKT": 1, "PK": 1, "PACK": 1, "PAC": 1, "BAG": 1, "BTL": 1, "BOTTLE": 1, "TIN": 1, "CAN": 1,
    "JAR": 1, "BOX": None, "KG": 1, "KGS": 1,
    "DOZ": 12, "DOZEN": 12, "DZ": 12, "DZN": 12,
    "CTN": None, "CARTON": None, "CTNS": None, "OTR": None, "OUTER": None, "BD": None, "BDL": None,
    "BUNDLE": None, "CASE": None, "CS": None, "IKT": None, "IKAT": None, "BAL": None,
    "包": 1, "瓶": 1, "罐": 1, "个": 1, "条": 1, "箱": None, "打": 12,
}

_EAN = re.compile(r"(?<!\d)(\d{12,14})(?!\d)")
_NUMBER = re.compile(r"-?\d[\d,]*(\.\d+)?")


# ---------------------------------------------------------------- helpers
def norm_key(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def loose_key(s) -> str:
    """For matching names: OCR often drops or adds spaces."""
    return re.sub(r"\s+", "", str(s or "")).upper()


def to_num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(" ", "")
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    m = _NUMBER.search(s.replace("RM", "").replace("$", ""))
    if not m:
        return None
    n = float(m.group(0).replace(",", "").lstrip("-"))
    return -n if neg else n


def text_barcode(v) -> str:
    """Barcode/Stock ID as text. A number Excel stored (e.g. 955643988207.0)
    becomes its digits again; text is kept exactly."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return str(v).strip()


def parse_date(s) -> dt.date | None:
    if isinstance(s, dt.datetime):
        return s.date()
    if isinstance(s, dt.date):
        return s
    s = str(s or "").strip()
    m = re.search(r"(?<!\d)(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})", s)  # yyyy-mm-dd first
    if m:
        try:
            return dt.date(*(int(g) for g in m.groups()))
        except ValueError:
            return None
    m = re.search(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})(?!\d)", s)  # dd/mm/yyyy (Malaysia)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        y += 2000 if y < 100 else 0
        try:
            return dt.date(y, mo, d)
        except ValueError:
            return None
    months = {k: i for i, k in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
    m = re.search(r"(\d{1,2})[\s\-/]*([A-Za-z]{3})[A-Za-z]*[\s\-/,]*(\d{2,4})", s)
    if m and m.group(2).lower() in months:
        y = int(m.group(3)); y += 2000 if y < 100 else 0
        try:
            return dt.date(y, months[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- lookups
class Lookups:
    """Supplier keywords and the barcode table from the user's 整理模板."""

    def __init__(self, suppliers=None, barcodes=None):
        self.suppliers: list[tuple[str, str]] = suppliers or []          # (KEYWORD, ID)
        self.barcodes: dict[str, dict] = barcodes or {}                   # "ID|KEY" -> entry
        self.by_name: dict[str, str] = {}                                 # "ID|NAME" -> "ID|KEY"
        for k, e in self.barcodes.items():
            if e.get("name"):
                self.by_name.setdefault(e["sid"] + "|" + loose_key(e["name"]), k)

    @classmethod
    def from_workbook(cls, path: str | Path | None) -> "Lookups":
        if not path or not Path(path).exists():
            return cls()
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True, read_only=True)
        sup, bcs = [], {}
        if "供应商" in wb.sheetnames:
            for r in wb["供应商"].iter_rows(min_row=2, values_only=True):
                if r and r[0] and len(r) > 1 and r[1]:
                    sup.append((norm_key(r[0]), str(r[1]).strip()))
        if "条码对照" in wb.sheetnames:
            for r in wb["条码对照"].iter_rows(min_row=2, values_only=True):
                r = list(r) + [None] * 8
                sid, code, name, bc, ppu = r[1], r[2], r[3], r[4], r[6]
                if not sid or not (code or name) or bc in (None, ""):
                    continue
                sid = str(sid).strip()
                entry = {"sid": sid, "code": text_barcode(code) if code is not None else "", "name": name or "",
                         "barcode": text_barcode(bc), "ppu": to_num(ppu)}
                key = sid + "|" + norm_key(entry["code"] or name)
                bcs[key] = entry
        wb.close()
        return cls(sup, bcs)

    def supplier(self, text: str) -> tuple[str, str]:
        """(Supplier ID, matched keyword) - the longest keyword found wins, so
        'ABC TRADING' beats 'ABC'."""
        up = norm_key(text).replace(" ", "")  # OCR often drops spaces
        best = ("", "")
        for kw, sid in self.suppliers:
            k = kw.replace(" ", "")
            if k and k in up and len(k) > len(best[1].replace(" ", "")):
                best = (sid, kw)
        return best

    def find(self, sid: str, code: str, name: str, printed: str) -> tuple[str, dict] | None:
        """(mapping key as written in 条码对照 col C, entry) or None."""
        for k in (code, printed, name):
            if k:
                e = self.barcodes.get(sid + "|" + norm_key(k))
                if e:
                    return e["code"] or str(e["name"]), e
        if name:
            k = self.by_name.get(sid + "|" + loose_key(name))
            if k:
                e = self.barcodes[k]
                return e["code"] or str(e["name"]), e
        return None


# ---------------------------------------------------------------- doc -> bill
_COL_RULES = [  # (field, regex on the column name); first match per column wins
    ("barcode_hw", re.compile(r"hand.*(bar|code)|手写.*条码", re.I)),
    ("price_hw", re.compile(r"hand.*(price|sell)|手写.*(售价|价)", re.I)),
    ("mark", re.compile(r"^(mark|remark|note|标记|备注)s?$", re.I)),
    ("barcode", re.compile(r"bar\s*code|barcode|ean|upc|条码", re.I)),
    ("foc", re.compile(r"\bfoc\b|free|bonus|赠|免费", re.I)),
    ("unit", re.compile(r"^(uom|u/?m|unit|units|单位|pack(ing)?)$", re.I)),
    ("qty", re.compile(r"qty|quantity|数量|q'?ty", re.I)),
    ("nett", re.compile(r"\bnett?(\b|\s*(amt|amount|value))|折后", re.I)),
    ("amount", re.compile(r"amount|amt|total|value|金额|总额", re.I)),
    ("code", re.compile(r"code|item\s*no|art(icle)?\s*no|sku|stock|货号|编号|代码|^no\.?\s*barang", re.I)),
    ("name", re.compile(r"desc|particular|item|product|品名|名称|货品|商品|description", re.I)),
]
_SKIP_COL = re.compile(r"unit\s*price|price|rate|disc|tax|sst|gst|单价|折扣|税", re.I)

_FIELD_RULES = [
    ("invoice_no", re.compile(r"(invoice|inv|bill|credit\s*note|c/?n|doc(ument)?|receipt)\s*(no|number|#)|单号|发票号|^no\.?$", re.I)),
    ("date", re.compile(r"^(invoice\s*|doc(ument)?\s*|c/?n\s*)?date$|^日期$|^date\b(?!.*due)", re.I)),
    ("terms", re.compile(r"term|付款", re.I)),
    ("subtotal", re.compile(r"sub\s*-?\s*total|total\s*(excl|before|b4)|小计|gross\s*total|^total\s*amount\s*excl", re.I)),
    ("sst", re.compile(r"\bsst\b|service\s*tax|sales\s*tax|\bgst\b|\btax\b|税", re.I)),
    ("rounding", re.compile(r"round", re.I)),
    ("total", re.compile(r"total|amount\s*(due|payable)|合计|总计|总额|应付", re.I)),
]


def _classify_columns(columns: list[str], rows: list[list[str]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for j, c in enumerate(columns):
        name = str(c).strip()
        for field, rx in _COL_RULES:
            if field in found:
                continue
            if field in ("amount", "nett", "qty", "name", "code", "unit") and _SKIP_COL.search(name) \
                    and not re.search(r"nett?\b|total|amount", name, re.I):
                continue
            if field == "amount" and re.search(r"unit|price", name, re.I):
                continue
            if rx.search(name):
                found[field] = j
                break
    if "nett" in found:
        found["amount"] = found.pop("nett")
    elif "amount" not in found and rows:
        # No amount heading: the rightmost mostly-numeric column is the line amount.
        for j in range(len(columns) - 1, -1, -1):
            vals = [r[j] for r in rows if j < len(r) and str(r[j]).strip()]
            if vals and sum(to_num(v) is not None for v in vals) >= 0.8 * len(vals) and j not in found.values():
                found["amount"] = j
                break
    return found


def _split_qty_unit(v: str) -> tuple[float | None, str]:
    m = re.match(r"^\s*(-?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z一-鿿]*)", str(v or ""))
    if not m:
        return to_num(v), ""
    return float(m.group(1).replace(",", "")), m.group(2).upper()


def _fields(table: dict) -> dict:
    out: dict = {}
    for f in table.get("fields", []):
        k, v = str(f.get("key", "")), f.get("value", "")
        for field, rx in _FIELD_RULES:
            if field == "total" and re.search(r"sub|excl|before|小计", k, re.I):
                continue
            if field == "date" and re.search(r"due|到期", k, re.I):
                continue
            if rx.search(k) and field not in out:
                out[field] = v
                break
    return out


def doc_type(text: str) -> str:
    return "CN" if re.search(r"credit\s*note|\bC/?N\s*(no|number)|贷项|退货单", text or "", re.I) else "Invoice"


def bill_from_doc(doc: dict, lk: Lookups, location: str = DEFAULT_LOCATION) -> dict:
    table = doc.get("table") or {}
    text = "\n".join([doc.get("text") or "", table.get("title") or ""] +
                     [f"{f.get('key')}: {f.get('value')}" for f in table.get("fields", [])])
    sid, kw = lk.supplier(text)
    sup_name = ""
    if kw:
        for line in text.splitlines():
            if kw.replace(" ", "") in norm_key(line).replace(" ", ""):
                sup_name = line.split("|")[0].strip()
                if kw not in norm_key(sup_name):  # keep the template's SEARCH() working
                    sup_name = f"{kw} / {sup_name}"
                break
    f = _fields(table)
    inv_no = re.sub(r"\s+", "", str(f.get("invoice_no", "")))
    cols = _classify_columns(table.get("columns", []), table.get("rows", []))

    def cell(row, key):
        j = cols.get(key)
        return row[j] if j is not None and j < len(row) else ""

    raw = []
    for row in table.get("rows", []):
        name, code = str(cell(row, "name")).strip(), str(cell(row, "code")).strip()
        printed = str(cell(row, "barcode")).strip()
        if not printed:  # an EAN printed inside the code/description cell
            for src in (code, name):
                m = _EAN.search(src)
                if m:
                    printed = m.group(1)
                    if src is name:
                        name = (name[:m.start()] + name[m.end():]).strip(" -")
                    break
        if printed and code == printed:
            code = ""
        qty, unit = _split_qty_unit(cell(row, "qty"))
        if cell(row, "unit"):
            unit = str(cell(row, "unit")).strip().upper()
        foc = to_num(cell(row, "foc")) or 0
        amount = to_num(cell(row, "amount"))
        if qty is None and amount is None and not (name or code):
            continue
        digits = re.sub(r"\D", "", printed)
        if 12 <= len(digits) <= 14:
            printed = digits
        raw.append({"code": code, "name": name, "printed": printed, "hand": str(cell(row, "barcode_hw")).strip(),
            "unit": unit, "qty": (qty or 0) + foc, "amount": amount,
            "sell": to_num(cell(row, "price_hw")), "mark": str(cell(row, "mark")).strip()})

    # FOC lines (amount 0) for an item that also has a paid line: add the
    # quantity to the paid line, amount unchanged.
    lines = []
    for r in raw:
        if (r["amount"] or 0) == 0 and r["qty"]:
            twin = next((x for x in lines if (x["amount"] or 0) != 0 and (
                (r["code"] and x["code"] == r["code"]) or (r["printed"] and x["printed"] == r["printed"]) or
                (r["name"] and norm_key(x["name"]) == norm_key(r["name"])))), None)
            if twin:
                twin["qty"] += r["qty"]
                twin["mark"] = (twin["mark"] + " FOC+" + f"{r['qty']:g}").strip()
                continue
        lines.append(r)

    out_lines = []
    for n, r in enumerate(lines, 1):
        hit = lk.find(sid, r["code"], r["name"], r["printed"]) if sid else None
        key, entry = hit if hit else (r["code"], None)
        system = entry["barcode"] if entry else ""
        # PCS per unit: the barcode table's value, else the unit's usual size.
        # A carton-type unit (CTN/OTR/BOX...) with no size > 1 in the table must
        # be filled in by hand - guessing 1 would import a carton as one piece.
        unit_ppu = UNIT_PCS.get(r["unit"])
        ppu = entry["ppu"] if entry and entry.get("ppu") else unit_ppu
        if unit_ppu is None and (ppu or 0) <= 1:
            ppu = None
        printed = r["printed"]
        barcode = system or r["hand"] or (printed[:12] if len(printed) == 13 and printed.isdigit() else printed)
        pcs = r["qty"] * ppu if (ppu and r["qty"]) else None
        price = round(r["amount"] / pcs, 4) if (pcs and r["amount"] is not None) else None
        problems = []
        if not barcode:
            problems.append("缺条码")
        if ppu is None:
            problems.append(f"{r['unit'] or '单位'} 要填每单位几 PCS")
        if r["amount"] is None:
            problems.append("缺金额")
        if not r["qty"]:
            problems.append("缺数量")
        if (r["amount"] or 0) == 0 and r["qty"]:
            problems.append("FOC 没有对应的货品")
        out_lines.append({**r, "line": n, "key": key, "system": system, "ppu": ppu, "barcode": barcode,
                          "pcs": pcs, "unit_price": price, "problems": problems})

    lines_total = round(sum(l["amount"] or 0 for l in out_lines), 2)
    subtotal, sst, rnd, total = (to_num(f.get(k)) for k in ("subtotal", "sst", "rounding", "total"))
    if subtotal is None and total is not None:
        subtotal = round(total - (sst or 0) - (rnd or 0), 2)
    diff = round(subtotal - lines_total, 2) if subtotal is not None else None
    cent = round(total - lines_total, 2) if total is not None else None
    problems = []
    if not sid:
        problems.append("找不到供应商（在 整理模板 的 供应商 表加关键字）")
    if not inv_no:
        problems.append("缺供应商单号")
    if diff is None:
        problems.append("单上没有读到小计/总额，无法对账")
    elif abs(diff) >= 0.01:
        problems.append(f"差额 {diff:.2f}：明细跟纸本小计对不上（漏行或读错）")
    if any(l["problems"] for l in out_lines):
        problems.append(f"{sum(1 for l in out_lines if l['problems'])} 行要检查")
    if not out_lines:
        problems.append("没有明细行")
    return {
        "doc_id": f"{sid or 'XXXX'}-{inv_no or doc.get('id', '')}", "type": doc_type(text),
        "supplier_name": sup_name, "supplier_id": sid, "invoice_no": inv_no,
        "date": parse_date(f.get("date")), "date_text": str(f.get("date", "")), "terms": str(f.get("terms", "")),
        "subtotal": subtotal, "sst": sst, "rounding": rnd, "total": total,
        "lines": out_lines, "lines_total": lines_total, "diff": diff, "cent_adj": cent,
        "location": location, "problems": problems, "ready": not problems, "source": doc.get("name", ""),
    }


def import_rows(bill: dict) -> list[tuple[str, str, str, float, float]]:
    return [(bill["supplier_id"], bill["location"], l["barcode"], l["unit_price"], l["pcs"])
            for l in bill["lines"]]


# ---------------------------------------------------------------- import file
# The blank DynaMod template (Sheet1 + Table1 A1:E1048576, header only). When
# the user's own copy is found in DYNAMOD_TEMPLATE_DIR it is used instead.
ASSET_DIR = config.RES_DIR / "assets" / "dynamod"


def template_path(kind: str) -> Path:
    name = "SupplierCreditNote.xlsx" if kind == "CN" else "SupplierInvoice.xlsx"
    user_dir = config.get("DYNAMOD_TEMPLATE_DIR")
    if user_dir and (Path(user_dir) / name).exists():
        return Path(user_dir) / name
    return ASSET_DIR / name


def write_import_file(rows, kind: str, out_path: str | Path) -> Path:
    """Copy the blank template and put the rows into Sheet1 from A2, the way
    "paste values" would: values only, text cells for the three ID columns,
    the template's own styles for price/qty. Nothing else in the file changes."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src = template_path(kind)
    with zipfile.ZipFile(src) as zin:
        files = {n: zin.read(n) for n in zin.namelist()}
    sheet_name = _first_sheet_path(files)
    sheet = files[sheet_name].decode("utf-8")
    sst = files["xl/sharedStrings.xml"].decode("utf-8")
    strings = re.findall(r"<si>(.*?)</si>", sst, re.S)
    index = {s: i for i, s in enumerate(strings)}
    count = int(re.search(r'count="(\d+)"', sst).group(1))
    styles = _column_styles(sheet)

    def sidx(text: str) -> int:
        item = f"<t xml:space=\"preserve\">{escape(text)}</t>" if text != text.strip() else f"<t>{escape(text)}</t>"
        if item not in index:
            index[item] = len(strings)
            strings.append(item)
        return index[item]

    xml_rows = []
    for i, (sid, loc, bc, price, qty) in enumerate(rows, start=2):
        cells = []
        for col, val in zip("ABC", (sid, loc, bc)):
            cells.append(f'<c r="{col}{i}" s="{styles.get(col, 1)}" t="s"><v>{sidx(str(val))}</v></c>')
            count += 1
        cells.append(f'<c r="D{i}" s="{styles.get("D", 0)}"><v>{_num(price)}</v></c>')
        cells.append(f'<c r="E{i}" s="{styles.get("E", 0)}"><v>{_num(qty)}</v></c>')
        xml_rows.append(f'<row r="{i}" spans="1:5">{"".join(cells)}</row>')
    sheet = re.sub(r"(<sheetData>.*?)(</sheetData>)", lambda m: m.group(1) + "".join(xml_rows) + m.group(2),
                   sheet, count=1, flags=re.S)
    sheet = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:E{max(1, len(rows) + 1)}"/>', sheet, count=1)
    files[sheet_name] = sheet.encode("utf-8")
    sst = re.sub(r"<si>.*</si>", lambda m: "".join(f"<si>{s}</si>" for s in strings), sst, count=1, flags=re.S)
    sst = re.sub(r'count="\d+"', f'count="{count}"', sst, count=1)
    sst = re.sub(r'uniqueCount="\d+"', f'uniqueCount="{len(strings)}"', sst, count=1)
    files["xl/sharedStrings.xml"] = sst.encode("utf-8")
    tmp = out_path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, data in files.items():
            zout.writestr(n, data)
    tmp.replace(out_path)
    return out_path


def _num(v) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(round(v, 4))


def _first_sheet_path(files: dict) -> str:
    rels = files["xl/_rels/workbook.xml.rels"].decode("utf-8")
    wbx = files["xl/workbook.xml"].decode("utf-8")
    rid = re.search(r'<sheet [^>]*r:id="([^"]+)"', wbx).group(1)
    target = re.search(rf'<Relationship [^>]*Id="{rid}"[^>]*Target="([^"]+)"', rels) or \
        re.search(rf'<Relationship [^>]*Target="([^"]+)"[^>]*Id="{rid}"', rels)
    t = target.group(1).lstrip("/")
    return t if t.startswith("xl/") else "xl/" + t


def _column_styles(sheet: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r'<col [^>]*min="(\d+)"[^>]*max="(\d+)"[^>]*style="(\d+)"', sheet):
        for c in range(int(m.group(1)), int(m.group(2)) + 1):
            if 1 <= c <= 5:
                out["ABCDE"[c - 1]] = m.group(3)
    return out


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", s).strip(" .") or "bill"


# ---------------------------------------------------------------- 整理表
def _set(ws, ref: str, value, text: bool = False):
    c = ws[ref]
    c.value = value
    if text and value not in (None, ""):
        c.data_type = "s"
        c.number_format = "@"


def _ensure_formula_rows(ws, upto: int, formula_cols: str):
    """The template has formulas down to a fixed row; copy row 2's formulas
    further down if the bills need more rows."""
    from openpyxl.formula.translate import Translator
    for col in formula_cols:
        src = ws[f"{col}2"]
        if not (isinstance(src.value, str) and src.value.startswith("=")):
            continue
        for r in range(3, upto + 1):
            c = ws[f"{col}{r}"]
            if c.value in (None, ""):
                c.value = Translator(src.value, origin=f"{col}2").translate_formula(f"{col}{r}")
                c.number_format = src.number_format


def write_workbook(bills: list[dict], template: str | Path, out_path: str | Path) -> Path:
    """Copy the user's 进单整理模板 and fill 表头 / 明细 input cells only; the
    template's formulas (Supplier ID, system barcode, PCS, price, 差额,
    Cent Adjustment, 检查) do the rest when Excel opens it."""
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, out_path)
    wb = load_workbook(out_path)
    head, det = wb["表头"], wb["明细"]
    yellow = PatternFill("solid", fgColor="FFF2CC")

    # Drop the template's example bill (状态 = 例子) - input cells only.
    examples = set()
    for r in range(2, head.max_row + 1):
        if str(head[f"O{r}"].value or "").strip() == "例子":
            examples.add(head[f"A{r}"].value)
            for col in "ABCEFGHIJKOP":
                head[f"{col}{r}"].value = None
    for r in range(2, det.max_row + 1):
        if det[f"F{r}"].value in examples:
            for col in "FGHIJKMNOQRS":
                det[f"{col}{r}"].value = None

    def next_free(ws, col):
        r = 2
        while ws[f"{col}{r}"].value not in (None, ""):
            r += 1
        return r

    hr, dr = next_free(head, "A"), next_free(det, "F")
    _ensure_formula_rows(head, hr + len(bills), "DLMN")
    _ensure_formula_rows(det, dr + sum(len(b["lines"]) for b in bills), "ABCDELPT")
    for b in bills:
        _set(head, f"A{hr}", b["doc_id"])
        _set(head, f"B{hr}", b["type"])
        _set(head, f"C{hr}", b["supplier_name"] or b["supplier_id"])
        if not b["supplier_id"]:
            head[f"D{hr}"].fill = yellow
        _set(head, f"E{hr}", b["invoice_no"], text=True)
        if b["date"]:
            _set(head, f"F{hr}", b["date"])
            head[f"F{hr}"].number_format = "dd/mm/yyyy"
        else:
            _set(head, f"F{hr}", b["date_text"])
        _set(head, f"G{hr}", b["terms"])
        for col, k in zip("HIJK", ("subtotal", "sst", "rounding", "total")):
            _set(head, f"{col}{hr}", b[k])
            head[f"{col}{hr}"].number_format = "#,##0.00"
        _set(head, f"O{hr}", "可以导入" if b["ready"] else "要检查")
        _set(head, f"P{hr}", "；".join([f"来源 {b['source']}"] + b["problems"]))
        hr += 1
        for l in b["lines"]:
            _set(det, f"F{dr}", b["doc_id"])
            _set(det, f"G{dr}", l["line"])
            _set(det, f"H{dr}", l["key"] if l["system"] else l["code"], text=True)
            _set(det, f"I{dr}", l["name"], text=True)
            _set(det, f"J{dr}", l["printed"], text=True)
            _set(det, f"K{dr}", l["hand"], text=True)
            _set(det, f"M{dr}", l["unit"])
            _set(det, f"N{dr}", l["qty"])
            if l["ppu"] is not None and l["ppu"] != 1:
                _set(det, f"O{dr}", l["ppu"])
            elif l["ppu"] is None:
                det[f"O{dr}"].fill = yellow  # template treats blank as 1: must be filled
            _set(det, f"Q{dr}", l["amount"])
            det[f"Q{dr}"].number_format = "#,##0.00"
            _set(det, f"R{dr}", l["sell"])
            _set(det, f"S{dr}", "；".join(x for x in [l["mark"]] + l["problems"] if x))
            dr += 1
    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)
    return out_path


def bills_from_workbook(path: str | Path) -> tuple[list[dict], list[str]]:
    """Read an edited 整理表 (saved by Excel, so formula results are stored)
    and return bills built from 明细 A:E. Returns (bills, notes)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    head, det = wb["表头"], wb["明细"]
    info = {}
    for r in head.iter_rows(min_row=2, values_only=True):
        if r[0]:
            info[str(r[0]).strip()] = {"type": str(r[1] or "Invoice"), "diff": r[12], "status": r[14]}
    bills: dict[str, dict] = {}
    notes: list[str] = []
    uncached = 0
    for r in det.iter_rows(min_row=2, values_only=True):
        r = list(r) + [None] * 20
        doc_id = str(r[5]).strip() if r[5] else ""
        if not doc_id:
            continue
        if r[0] is None and r[2] is None and r[13] not in (None, ""):
            uncached += 1
        b = bills.setdefault(doc_id, {"doc_id": doc_id, "type": info.get(doc_id, {}).get("type", "Invoice"),
                                      "rows": [], "problems": []})
        sid, loc, bc, price, qty, check = r[0], r[1], text_barcode(r[2]), to_num(r[3]), to_num(r[4]), r[19]
        line = r[6] or len(b["rows"]) + 1
        if not sid or not loc or not bc or price is None or not qty:
            b["problems"].append(f"第 {line} 行：{check or '五栏不完整'}")
            continue
        unit = norm_key(r[12])
        if unit and UNIT_PCS.get(unit, None) is None and (to_num(r[14]) or 0) <= 1:
            # the template's PCS formula counts a blank size as 1
            b["problems"].append(f"第 {line} 行：{unit} 没有填每单位几 PCS")
            continue
        if isinstance(check, str) and check not in ("OK",) and not check.startswith("四舍五入差"):
            b["problems"].append(f"第 {line} 行：{check}")
        b["rows"].append((str(sid).strip(), str(loc).strip(), bc, round(price, 4), qty))
    if uncached:
        notes.append("这个文件的公式还没有算出结果：请先用 Excel 打开、保存，再生成导入文件。")
    for doc_id, b in bills.items():
        d = info.get(doc_id, {}).get("diff")
        if isinstance(d, (int, float)) and abs(d) >= 0.01:
            b["problems"].append(f"差额 {d:.2f}")
        if doc_id not in info:
            b["problems"].append("表头 没有这张单")
        b["ready"] = not b["problems"] and bool(b["rows"])
    return list(bills.values()), notes


def write_import_files(bills: list[dict], out_dir: str | Path) -> list[dict]:
    """One file per ready bill: <单据ID>.xlsx. Returns a result per bill."""
    out_dir = Path(out_dir)
    results = []
    for b in bills:
        rows = b.get("rows") if "rows" in b else (import_rows(b) if b.get("ready") else [])
        if not b.get("ready"):
            results.append({"doc_id": b["doc_id"], "ok": False, "problems": b["problems"]})
            continue
        path = write_import_file(rows, b["type"], out_dir / f"{safe_name(b['doc_id'])}.xlsx")
        results.append({"doc_id": b["doc_id"], "ok": True, "path": str(path), "lines": len(rows)})
    return results
