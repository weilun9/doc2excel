"""Tests for the SmartAcc DynaMod export (app/dynamod.py). Builds a small
stand-in 进单整理模板 in a temp folder, so no business data is needed.

    .venv\\Scripts\\python tests\\dynamod_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="d2x_dm_"))
os.environ["LOCALAPPDATA"] = str(TMP / "appdata")
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook, load_workbook  # noqa: E402

from app import dynamod as dm  # noqa: E402

FAILS = 0
if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a legacy code page
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name: str, ok: bool, detail=""):
    global FAILS
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    FAILS += not ok


# ---------- a stand-in template with the same sheets / columns ----------
tpl = TMP / "模板.xlsx"
wb = Workbook()
h = wb.active; h.title = "表头"
h.append(["单据ID", "类型", "供应商名称", "Supplier ID", "供应商单号", "日期", "Terms", "小计", "SST",
          "Rounding", "总额", "明细合计", "差额", "Cent Adj", "状态", "备注"])
h.append(["EX-1", "Invoice", "EXAMPLE", "=IF(C2=\"\",\"\",\"X\")", "1", None, None, 1, 0, 0, 1,
          "=IF(A2=\"\",\"\",SUMIF(明细!$F:$F,A2,明细!$Q:$Q))", None, None, "例子", "demo"])
d = wb.create_sheet("明细")
d.append(["Supplier ID", "Location ID", "Barcode", "Unit Price", "Quantity", "单据ID", "行号", "货号", "品名",
          "印刷条码", "手写条码", "系统条码", "单位", "数量", "每单位PCS", "PCS", "行金额", "售价", "标记", "检查"])
d.append(["=IF(F2=\"\",\"\",\"S\")", "=IF(F2=\"\",\"\",\"HQ\")", None, None, None, "EX-1", 1, None, "demo item",
          None, None, None, "PCS", 1, None, "=IF(N2=\"\",\"\",N2*IF(O2=\"\",1,O2))", 1])
s = wb.create_sheet("供应商")
s.append(["关键字", "Supplier ID"])
for kw, sid in [("SINAR JAYA SDN", "SJS1"), ("SINAR JAYA TRADING", "SJT1"), ("HUA FENG", "HF01")]:
    s.append([kw, sid])
b = wb.create_sheet("条码对照")
b.append(["KEY", "Supplier ID", "货号", "单据品名", "系统条码", "系统品名", "每单位PCS", "备注"])
b.append([None, "SJT1", "45301", "Melon Milk 250ml", "955000000011", None, 24])
b.append([None, "HF01", "G47", "扣肉罐头", "955000000022", None, 24])
b.append([None, "HF01", "OKD03", "生抽 370ml", "955000000033", None, 1])
b.append([None, "CV01", "FISH ROLL", "Fish Roll (5's)", "190011", None, 5])
wb.save(tpl)

lk = dm.Lookups.from_workbook(tpl)
check("lookups loaded", len(lk.suppliers) == 3 and len(lk.barcodes) == 4, (lk.suppliers, list(lk.barcodes)))
check("longest supplier keyword wins", lk.supplier("SINAR JAYA TRADING (M) SDN BHD")[0] == "SJT1")
check("supplier match ignores dropped spaces", lk.supplier("PERNIAGAANHUAFENG")[0] == "HF01")
check("unknown supplier", lk.supplier("ACME SDN BHD")[0] == "")


def doc(text, fields, columns, rows, name="x.jpg"):
    return {"id": "d1", "name": name, "text": text,
            "table": {"title": "", "fields": [{"key": k, "value": v} for k, v in fields],
                      "columns": columns, "rows": rows}}


# ---------- bill building ----------
bill = dm.bill_from_doc(doc(
    "SINAR JAYA TRADING (M) SDN BHD\nTAX INVOICE",
    [("Invoice No", "IV 881"), ("Date", "2026-11-12"), ("Terms", "30 DAYS"), ("Sub Total", "160.92"),
     ("SST", "0.00"), ("Total", "160.92")],
    ["Item Code", "Description", "UOM", "Qty", "FOC", "Unit Price", "Gross Amount", "Nett Amount"],
    [["45301", "Melon Milk 250ml", "CTN", "2", "", "40.80", "81.60", "77.52"],
     ["", "Curry Chicken 145G 9550000000440", "PCS", "24", "2", "3.53", "84.72", "83.40"]]), lk)
l1, l2 = bill["lines"]
check("doc id = SupplierID-InvoiceNo", bill["doc_id"] == "SJT1-IV881", bill["doc_id"])
check("ISO date parsed", str(bill["date"]) == "2026-11-12", bill["date"])
check("Nett Amount chosen over gross", l1["amount"] == 77.52, l1["amount"])
check("CTN size from barcode table", l1["ppu"] == 24 and l1["pcs"] == 48, (l1["ppu"], l1["pcs"]))
check("unit price = nett / PCS, 4 dp", l1["unit_price"] == round(77.52 / 48, 4), l1["unit_price"])
check("system barcode used", l1["barcode"] == "955000000011")
check("EAN in description -> first 12 digits", l2["barcode"] == "955000000044" and "9556" not in l2["name"],
      (l2["barcode"], l2["name"]))
check("FOC column added to qty", l2["pcs"] == 26, l2["pcs"])
check("差额 0 -> ready", bill["diff"] == 0 and bill["ready"], bill["problems"])
check("CN detected", dm.bill_from_doc(doc("ACME\nCREDIT NOTE", [], ["Description", "Qty", "Amount"],
                                          [["A", "1", "1.00"]]), lk)["type"] == "CN")

# carton without a known size must not be imported as 1 piece
b2 = dm.bill_from_doc(doc("PERNIAGAAN HUA FENG", [("Invoice No", "T1"), ("Total", "156.00")],
                          ["Code", "Description", "UOM", "Qty", "Amount"],
                          [["G47", "扣肉罐头", "CTN", "1", "96.00"],
                           ["OKD03", "生抽 370ml", "CTN", "2", "60.00"]]), lk)
check("table size 24 for G47", b2["lines"][0]["pcs"] == 24, b2["lines"][0])
check("CTN with table size 1 -> must fill in", b2["lines"][1]["ppu"] is None and not b2["ready"],
      b2["lines"][1])

# FOC line of the same item merges; 差额 caught
b3 = dm.bill_from_doc(doc("SINAR JAYA TRADING", [("Invoice No", "F1"), ("Sub Total", "90.00"), ("Total", "90.00")],
                          ["Item Code", "Description", "UOM", "Qty", "Amount"],
                          [["45301", "Melon Milk 250ml", "CTN", "1", "40.80"],
                           ["45301", "Melon Milk 250ml (FOC)", "CTN", "1", "0.00"]]), lk)
check("FOC line merged into paid line", len(b3["lines"]) == 1 and b3["lines"][0]["pcs"] == 48, b3["lines"])
check("差额 reported", b3["diff"] == 49.2 and not b3["ready"], (b3["diff"], b3["problems"]))
check("dd/mm/yyyy", str(dm.parse_date("03/10/2026")) == "2026-10-03")
check("3-Oct-26", str(dm.parse_date("3-Oct-26")) == "2026-10-03")

# ---------- import file ----------
out = TMP / "out" / "T.xlsx"
dm.write_import_file([("SJT1", "HQ", "0955123", 1.615, 48), ("SJT1", "HQ", "34-16-033-0", 2.5, 3)], "Invoice", out)
w = load_workbook(out)
ws = w.active
rows = list(ws.iter_rows(values_only=True))
check("one sheet, one table", w.sheetnames == ["Sheet1"] and list(ws.tables) == ["Table1"])
check("header kept", rows[0] == ("Supplier ID", "Location ID", "Barcode", "Unit Price", "Quantity"), rows[0])
check("barcode stays text (leading 0, Stock ID)", rows[1][2] == "0955123" and rows[2][2] == "34-16-033-0", rows)
check("price/qty numbers", rows[1][3] == 1.615 and rows[1][4] == 48, rows[1])
with zipfile.ZipFile(out) as z:
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
check("no cells past the data", "A4" not in sheet and 'dimension ref="A1:E3"' in sheet)
check("template unchanged otherwise", z.namelist() == zipfile.ZipFile(dm.template_path("Invoice")).namelist())

res = dm.write_import_files([bill, b2], TMP / "opp")
check("only ready bills get a file", res[0]["ok"] and not res[1]["ok"] and len(list((TMP / "opp").iterdir())) == 1,
      res)

# ---------- 整理表 ----------
zl = dm.write_workbook([bill, b2], tpl, TMP / "整理.xlsx")
w = load_workbook(zl)
h, d = w["表头"], w["明细"]
check("example bill removed", h["A2"].value == "SJT1-IV881", h["A2"].value)
check("header formulas kept", str(h["D2"].value).startswith("="), h["D2"].value)
check("formulas copied to new rows", str(h["L3"].value).startswith("=") and "A3" in h["L3"].value, h["L3"].value)
check("detail inputs written", d["F2"].value == "SJT1-IV881" and d["Q2"].value == 77.52 and d["O2"].value == 24,
      [d["F2"].value, d["Q2"].value, d["O2"].value])
check("detail formulas kept/extended", str(d["P4"].value).startswith("=") and "N4" in d["P4"].value, d["P4"].value)
check("problem flagged in 标记", "PCS" in str(d["S5"].value), d["S5"].value)
check("unknown CTN size left blank + yellow", d["O5"].value is None and d["O5"].fill.fgColor.rgb.endswith("FFF2CC"))

# reading back: formulas never calculated -> refuse with a note
bills, notes = dm.bills_from_workbook(zl)
check("uncalculated work sheet -> note, no files", notes and not any(b["ready"] for b in bills), (notes, bills))

# a work sheet with stored results (as Excel saves it)
calc = Workbook()
h2 = calc.active; h2.title = "表头"
h2.append(["单据ID", "类型"] + [None] * 14)
h2.append(["SJT1-9", "CN"] + [None] * 10 + [0, 0, "OK", None])
h2.append(["HF01-9", "Invoice"] + [None] * 10 + [0, 0, "", None])
d2 = calc.create_sheet("明细")
d2.append(["h"] * 20)
d2.append(["SJT1", "HQ", 955000000044.0, 3.2585, 26, "SJT1-9", 1, None, "Curry", None, None, None, "PCS", 26, None,
           26, 84.72, None, None, "OK"])
d2.append(["HF01", "HQ", "955000000033", 30, 2, "HF01-9", 1, "OKD03", "x", None, None, "955000000033", "CTN", 2,
           None, 2, 60, None, None, "OK"])
calc.save(TMP / "calc.xlsx")
bills, notes = dm.bills_from_workbook(TMP / "calc.xlsx")
by = {b["doc_id"]: b for b in bills}
check("numeric barcode read back as text digits", by["SJT1-9"]["rows"][0][2] == "955000000044", by["SJT1-9"]["rows"])
check("CN type from 表头", by["SJT1-9"]["type"] == "CN" and by["SJT1-9"]["ready"])
check("CTN with blank size blocked even if 检查 says OK", not by["HF01-9"]["ready"], by["HF01-9"]["problems"])

print("\nALL PASSED" if not FAILS else f"\n{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
