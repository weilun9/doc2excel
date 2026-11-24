"""Tests for table building: header-anchored layout, long-text chunking and the
one-step vision path. No network: the LLM client is replaced by a fake.

    .venv\\Scripts\\python tests\\table_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="d2x_tab_")
os.environ["D2X_KEYSTORE"] = "off"  # never move keys out of the real .env
sys.path.insert(0, str(ROOT))

from app import config, layout, llm, ocr, structure  # noqa: E402

FAILS = 0
if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a legacy code page
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name: str, ok: bool, detail: str = ""):
    global FAILS
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    FAILS += not ok


def box(x0, y, t, w=None, h=30):
    w = w or 16 * len(t)
    return {"x0": x0, "x1": x0 + w, "y0": y, "y1": y + h, "t": t, "s": 0.99}


# ---------- layout.build_table ----------
items = [box(100, 50, "No"), box(200, 50, "Description"), box(700, 50, "Qty"),
         box(850, 50, "Unit", 60), box(915, 50, "Price", 80), box(1100, 50, "Amount"),
         box(100, 110, "1"), box(200, 110, "Network Switch 24-Port"), box(700, 110, "2"),
         box(850, 110, "850.00"), box(1100, 110, "1700.00"),
         box(200, 150, "Gigabit Managed"),                                    # wrapped line
         box(200, 210, "Delivery"), box(1100, 210, "80.00"),                  # blank qty/price
         box(100, 270, "3"), box(200, 270, "Cable"), box(700, 270, "4"),
         box(850, 270, "10.00"), box(1100, 270, "40.00"),
         box(850, 340, "Total:"), box(1100, 340, "1820.00")]
t = layout.build_table(items)
check("header found, 'Unit'+'Price' glued", t and t["columns"] == ["No", "Description", "Qty", "Unit Price", "Amount"],
      t and t["columns"])
check("wrapped line joins its row", t and t["rows"][0][1] == "Network Switch 24-Port Gigabit Managed", t and t["rows"])
check("blank cells stay blank", t and t["rows"][1] == ["", "Delivery", "", "", "80.00"], t and t["rows"])
check("stops at Total", t and len(t["rows"]) == 3, t and t["rows"])

# close CJK headings with separate values below stay separate columns
cjk = [box(100, 50, "序号", 60), box(175, 50, "品名", 60), box(500, 50, "数量", 60), box(700, 50, "金额", 60)]
for k, y in enumerate((110, 160, 210)):
    cjk += [box(105, y, str(k + 1), 15), box(175, y, "螺丝", 60), box(500, y, "2", 15), box(700, y, "1.00", 60)]
t = layout.build_table(cjk)
check("close CJK headings not glued", t and t["columns"] == ["序号", "品名", "数量", "金额"], t and t["columns"])

check("no header -> None", layout.build_table([box(100, 50 + 40 * i, f"line {i}") for i in range(6)]) is None)

# rotated page: boxes tilted by ~2 degrees still group into lines
import math  # noqa: E402
th = math.radians(2)
quad_res = []
for x, y, s in [(100, 100, "Item"), (500, 100, "Qty"), (800, 100, "Amount"),
                (100, 160, "Apple"), (500, 160, "3"), (800, 160, "6.00"),
                (100, 220, "Pear"), (500, 220, "1"), (800, 220, "2.50")]:
    w = 200
    pts = [(x, y), (x + w, y), (x + w, y + 30), (x, y + 30)]
    quad_res.append(([[px * math.cos(th) - py * math.sin(th), px * math.sin(th) + py * math.cos(th)]
                      for px, py in pts], s, 0.99))
t = layout.build_table(layout.items_from_ocr(quad_res))
check("deskew keeps rows together", t and t["rows"] == [["Apple", "3", "6.00"], ["Pear", "1", "2.50"]], t and t["rows"])

# ---------- structure: offline uses boxes, falls back without them ----------
pages = [layout.lines_to_rows(items)]
config.get = (lambda orig: (lambda k: {"AI_MODE": "offline"}.get(k, orig(k))))(config.get)
tab, mode, _ = structure.run({"text": ocr.rows_to_text(pages), "pages": pages, "boxes": [items]})
check("offline run uses layout table", mode == "rules" and len(tab["rows"]) == 3 and tab["columns"][3] == "Unit Price",
      tab)
check("offline run keeps Total field", any(f["value"] == "1820.00" for f in tab["fields"]), tab["fields"])
tab2, _, _ = structure.run({"text": ocr.rows_to_text(pages), "pages": pages})
check("no boxes -> old rules still work", isinstance(tab2["rows"], list))

# ---------- chunking + merge ----------
long_text = "\n".join(f"0{i % 9 + 1}/10/2026 | PAYMENT {i} | {i}.00" for i in range(2000))
chunks = structure._chunks(long_text, 14000)
check("long text split, nothing lost", len(chunks) > 1 and "\n".join(chunks) == long_text,
      [len(c) for c in chunks])
check("chunks under limit", all(len(c) <= 14000 for c in chunks))

calls = []


def fake_chat(base_url, api_key, model, messages, **kw):
    calls.append(messages)
    user = messages[-1]["content"]
    if isinstance(user, list):  # vision call
        return "```json\n" + json.dumps({
            "lines": ["Invoice No: | V-1", "Pen | 2 | 3.00"], "doc_type": "invoice", "title": "V-1",
            "fields": [{"key": "Invoice No", "value": "V-1"}],
            "columns": ["Item", "Qty", "Amount"], "rows": [["Pen", "2", "3.00"]]}) + "\n```"
    n = len(calls)
    return json.dumps({"doc_type": "statement", "title": "S",
                       "fields": [{"key": "Account", "value": "123"}, {"key": f"Page{n}", "value": str(n)}],
                       "columns": ["Date", "Description", "Amount"],
                       "rows": [[f"0{n}/10", f"row from part {n}", "1.00"]]})


llm.chat = fake_chat
ai = structure.ai_structure(long_text)
check("one LLM call per chunk", len(calls) == len(chunks), len(calls))
check("rows from every chunk merged", len(ai["rows"]) == len(chunks), ai["rows"])
check("later chunks told the columns", "Date" in calls[1][-1]["content"].split("OCR text:")[0])
check("duplicate fields dropped", sum(f["key"] == "Account" for f in ai["fields"]) == 1, ai["fields"])

m = structure.merge_tables([{"columns": ["A", "B"], "rows": [["1", "2"]]},
                            {"columns": ["B", "A", "C"], "rows": [["x", "y", "z"]]}])
check("merge maps columns by name", m["rows"][1] == ["y", "x"], m["rows"])

# ---------- vision: one call per page returns lines + table ----------
calls.clear()
from PIL import Image  # noqa: E402
rows, vt = ocr.vision_page(Image.new("RGB", (400, 300), "white"))
check("vision returns transcript rows", rows == [["Invoice No:", "V-1"], ["Pen", "2", "3.00"]], rows)
check("vision returns table", vt["rows"] == [["Pen", "2", "3.00"]] and vt["columns"][0] == "Item", vt)
check("vision is a single call", len(calls) == 1)
tab, mode, _ = structure.run({"text": "x", "pages": [rows], "vision_tables": [vt]})
check("structure uses vision table without text LLM", mode == "ai" and tab["rows"] == vt["rows"] and len(calls) == 1)
tab, mode, _ = structure.run({"text": "x", "pages": [rows, rows], "vision_tables": [vt, None]})
check("partial vision pages -> normal path (offline here)", mode == "rules", mode)


def bad_chat(*a, **k):
    return "sorry, I cannot read this"


llm.chat = bad_chat
try:
    ocr.vision_page(Image.new("RGB", (100, 100), "white"))
    check("non-JSON vision reply raises LLMError (-> local OCR fallback)", False)
except llm.LLMError:
    check("non-JSON vision reply raises LLMError (-> local OCR fallback)", True)

print("\nALL PASSED" if not FAILS else f"\n{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
