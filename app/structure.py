"""Turn OCR rows into a table: header fields + column headers + data rows.

Online: an OpenAI-compatible text LLM (DeepSeek by default).
Offline: layout rules over the OCR rows (label:value pairs + the largest block
of rows sharing the same column count)."""
from __future__ import annotations

import json
import re
from collections import Counter

from . import config, layout, llm

MAX_CHARS = 14000

SYSTEM_PROMPT_HEAD = """You convert OCR text of a scanned business document into a structured table.
The OCR text keeps the printed lines; ' | ' separates columns that were apart on the page.
OCR may contain small errors: fix obvious ones only when certain (e.g. O vs 0 in amounts),
and restore spaces OCR dropped between words ("CopierPaper" -> "Copier Paper",
"19/11/202614:32" -> "19/11/2026 14:32").

"""

# Output schema and rules, shared with the one-step vision prompt in ocr.py.
SCHEMA_RULES = """Return ONLY a JSON object with this shape:
{
  "doc_type": "invoice | receipt | statement | quotation | delivery_order | purchase_order | form | other",
  "title": "short title, e.g. 'Invoice INV-001 - ABC Sdn Bhd'",
  "fields": [{"key": "Invoice No", "value": "INV-001"}],
  "columns": ["Description", "Qty", "Unit Price", "Amount"],
  "rows": [["Item A", "2", "10.00", "20.00"]]
}
Rules:
- fields: document-level facts (supplier, customer, document no., dates, currency,
  subtotal, tax, discount, total, payment terms, account no., period...). Keep the
  original wording of labels; translate nothing.
- columns/rows: the repeating records (invoice line items, receipt items,
  statement transactions). Every row has exactly len(columns) cells; use "" when empty.
- Keep values as printed (numbers without thousands separators are fine; keep
  decimals). Dates as printed. Item codes and barcodes: every digit, as text.
- When printed, keep these as separate columns: item code, barcode, description,
  UOM (CTN/PCS/PKT...), quantity, free/FOC quantity, unit price, discount, and the
  line amount after discount (name that column "Nett Amount" if the document shows
  both a gross and a nett amount).
- Put subtotal, tax (SST), rounding and the grand total in fields as separate keys.
- If the document has no repeating records, put everything in fields and return
  columns ["Item", "Value"] with rows copied from fields.
- Never invent data that is not in the text."""

SYSTEM_PROMPT = SYSTEM_PROMPT_HEAD + SCHEMA_RULES


def choose_mode() -> str:
    mode = config.get("AI_MODE").lower()
    if mode == "offline":
        return "offline"
    if not llm.text_llm_ready():
        return "offline"
    if mode == "online":
        return "online"
    return "online" if llm.is_reachable(config.get("LLM_BASE_URL")) else "offline"


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))


def normalize(table: dict) -> dict:
    cols = [str(c).strip() for c in (table.get("columns") or []) if str(c).strip() != ""] or ["Text"]
    rows = []
    for r in table.get("rows") or []:
        if not isinstance(r, list):
            r = list(r.values()) if isinstance(r, dict) else [r]
        cells = ["" if c is None else str(c).strip() for c in r]
        if len(cells) < len(cols):
            cells += [""] * (len(cols) - len(cells))
        elif len(cells) > len(cols):
            cells = cells[: len(cols) - 1] + [" ".join(cells[len(cols) - 1:])]
        if any(cells):
            rows.append(cells)
    fields = []
    for f in table.get("fields") or []:
        if isinstance(f, dict):
            k, v = str(f.get("key", "")).strip(), f.get("value", "")
        elif isinstance(f, (list, tuple)) and len(f) >= 2:
            k, v = str(f[0]).strip(), f[1]
        else:
            continue
        if k:
            fields.append({"key": k, "value": "" if v is None else str(v).strip()})
    return {
        "doc_type": str(table.get("doc_type") or "other"),
        "title": str(table.get("title") or "").strip(),
        "fields": fields,
        "columns": cols,
        "rows": rows,
    }


def _chunks(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split OCR text into pieces under `limit`, cutting only at line ends
    (page markers are lines too), so long statements are not truncated."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        line = line[:limit]
        if cur and len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = cur + "\n" + line if cur else line
    if cur:
        out.append(cur)
    return out


def merge_tables(parts: list[dict]) -> dict:
    """Join tables from consecutive chunks/pages of one document. Columns come
    from the first part that has rows; later rows are matched by column name
    (or by position when the names differ but the count is the same)."""
    parts = [normalize(p) for p in parts if p]
    if not parts:
        return normalize({})
    base = next((p for p in parts if p["rows"]), parts[0])
    cols = base["columns"]
    fields, seen = [], set()
    rows = []
    for p in parts:
        for f in p["fields"]:
            if f["key"].lower() not in seen:
                seen.add(f["key"].lower())
                fields.append(f)
        if not p["rows"]:
            continue
        if p["columns"] == cols or len(p["columns"]) == len(cols):
            rows.extend(p["rows"])
            continue
        idx = {c.lower(): k for k, c in enumerate(p["columns"])}
        for r in p["rows"]:
            rows.append([r[idx[c.lower()]] if c.lower() in idx else "" for c in cols])
    first = parts[0]
    return normalize({"doc_type": first["doc_type"], "title": first["title"] or base["title"],
                      "fields": fields, "columns": cols, "rows": rows})


def ai_structure(text: str) -> dict:
    chunks = _chunks(text)
    parts: list[dict] = []
    for n, chunk in enumerate(chunks, 1):
        user = "OCR text:\n" + chunk
        if len(chunks) > 1:
            note = f"This is part {n} of {len(chunks)} of ONE document."
            if parts and parts[0]["rows"]:
                note += (" If its records continue the same table, use exactly these columns: "
                         + json.dumps(parts[0]["columns"], ensure_ascii=False) + ".")
            user = note + "\n" + user
        reply = llm.chat(
            config.get("LLM_BASE_URL"), config.get("LLM_API_KEY"), config.get("LLM_MODEL"),
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user}],
            json_mode=True, max_tokens=8000,
        )
        parts.append(normalize(_extract_json(reply)))
    return merge_tables(parts)


_KV = re.compile(r"^\s*([^:：|]{1,30}?)\s*[:：]\s*(.+?)\s*$")
_NUM = re.compile(r"[-+]?\(?[$€£¥RM]*\s*\d[\d,]*(\.\d+)?\)?%?$")


def layout_structure(pages: list[list[list[str]]], boxes: list[list[dict]]) -> dict | None:
    """Offline table from OCR box positions (see layout.py). Tables on several
    pages with the same columns (e.g. a long statement) are joined. Returns
    None when no header row is found, so the caller can fall back."""
    fields = _kv_fields([r for page in pages for r in page])[0]
    columns, rows = None, []
    for items in boxes:
        if not items:
            continue
        t = layout.build_table(items)
        if not t:
            continue
        if columns is None:
            columns = t["columns"]
        elif len(t["columns"]) != len(columns):
            continue
        rows.extend(t["rows"])
    if columns is None:
        return None
    return normalize({"doc_type": "other", "fields": fields, "columns": columns, "rows": rows})


def _kv_fields(rows: list[list[str]]) -> tuple[list[dict], set]:
    fields: list[dict] = []
    used = set()
    # Label: value pairs, either in one cell ("Date: 1/1") or two cells.
    for i, r in enumerate(rows):
        if len(r) == 1:
            m = _KV.match(r[0])
            if m:
                fields.append({"key": m.group(1).strip(), "value": m.group(2).strip()})
                used.add(i)
        elif len(r) == 2 and (r[0].rstrip().endswith((":", "：")) or (
                len(r[0]) <= 25 and not _NUM.match(r[0]) and _NUM.match(r[1].replace(" ", ""))
                and re.search(r"total|tax|sst|gst|subtotal|amount|合计|总计|税|金额|小计", r[0], re.I))):
            fields.append({"key": r[0].rstrip(":： "), "value": r[1]})
            used.add(i)
    return fields, used


def rule_structure(pages: list[list[list[str]]]) -> dict:
    rows = [r for page in pages for r in page]
    fields, used = _kv_fields(rows)

    # Largest block of consecutive rows with the same (>=3) column count.
    best: tuple[int, int, int] | None = None  # (start, end, ncols)
    i = 0
    while i < len(rows):
        n = len(rows[i])
        j = i
        while j + 1 < len(rows) and len(rows[j + 1]) == n:
            j += 1
        if n >= 3 and (best is None or (j - i) > (best[1] - best[0])):
            best = (i, j, n)
        i = j + 1
    if best is None:
        counts = Counter(len(r) for r in rows if len(r) >= 2)
        if counts:
            n = counts.most_common(1)[0][0]
            block = [r for r in rows if len(r) == n]
            return normalize({"doc_type": "other", "fields": fields,
                              "columns": [f"Col {k + 1}" for k in range(n)], "rows": block})
        return normalize({"doc_type": "other", "fields": fields, "columns": ["Text"],
                          "rows": [[" ".join(r)] for k, r in enumerate(rows) if k not in used]})
    s, e, n = best
    header = rows[s]
    has_header = sum(1 for c in header if _NUM.match(c.replace(" ", ""))) == 0 and e > s
    cols = header if has_header else [f"Col {k + 1}" for k in range(n)]
    body = rows[s + 1: e + 1] if has_header else rows[s: e + 1]
    # Loose rows right after the block (wrapped descriptions etc.) are kept in fields
    # only if they look like key/value; otherwise ignored to keep the table clean.
    return normalize({"doc_type": "other", "fields": fields, "columns": cols, "rows": body})


def offline(ocr_result: dict) -> dict:
    boxes = ocr_result.get("boxes")
    if boxes:
        t = layout_structure(ocr_result["pages"], boxes)
        if t is not None:
            return t
    return rule_structure(ocr_result["pages"])


def run(ocr_result: dict) -> tuple[dict, str, str | None]:
    """Returns (table, mode_used, warning).
    ocr_result: {"text", "pages", optional "boxes" (positions per page) and
    "vision_tables" (tables the vision model already built, one per page)}."""
    vt = ocr_result.get("vision_tables")
    if vt and all(vt):
        return merge_tables(vt), "ai", None
    mode = choose_mode()
    if mode == "online":
        try:
            return ai_structure(ocr_result["text"]), "ai", None
        except (llm.LLMError, ValueError, json.JSONDecodeError) as e:
            return offline(ocr_result), "rules", f"AI failed, used offline rules: {e}"
    return offline(ocr_result), "rules", None
