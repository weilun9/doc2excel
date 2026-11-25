"""Geometry-based table building from positioned OCR text.

The old offline path only saw rows of cell strings, so it could not tell an
empty cell from a missing column, and a wrapped description became a separate
"row". Here every OCR box keeps its position, which lets us:

  * straighten the page (phone photos): find the paper outline and warp it flat
  * cancel small rotations by measuring the text-box angle
  * use the table's header row as column anchors, so a blank cell stays blank
    instead of the next value sliding left
  * glue wrapped description lines back onto their row

Everything here is plain numpy/OpenCV (already pulled in by RapidOCR): no new
model and no new dependency.
"""
from __future__ import annotations

import math
import re
import statistics

import numpy as np

# Words that mark a table header. A header needs a few non-numeric cells; one of
# these words makes the choice much more reliable.
_HEAD_WORDS = re.compile(
    r"\b(no|item|items|code|description|desc|particulars|details|qty|quantity|unit|price|"
    r"rate|amount|amt|total|disc|discount|tax|uom|date|debit|credit|balance|ref|reference|"
    r"brand|model|sku)\b|品名|名称|数量|单价|金额|日期|摘要|借方|贷方|余额|规格|单位|编号|序号",
    re.I)
_STOP_WORDS = re.compile(
    r"^(sub\s*total|total|grand\s*total|net\s*total|amount\s*due|balance\s*due|tax|sst|gst|vat|"
    r"rounding|合计|总计|小计|应付|税)", re.I)
_NUM = re.compile(r"^[-+(]?\s*(RM|\$|USD|MYR|S\$|€|£|¥)?\s*[\d][\d,]*(\.\d+)?\)?%?$", re.I)


def is_num(text: str) -> bool:
    return bool(_NUM.match(text.replace(" ", "")))


# ---------------------------------------------------------------- page warp
def flatten_page(img: np.ndarray) -> np.ndarray:
    """If the photo shows a sheet of paper on a darker background, warp the
    sheet to a flat rectangle. Returns the input unchanged when no clear
    four-cornered page is found (scans, screenshots, PDFs)."""
    q = bright_page_quad(img)
    return img if q is None else warp_quad(img, q)


def bright_page_quad(img: np.ndarray):
    """Corners of the large bright region (paper on a darker desk), or None."""
    try:
        import cv2
    except ImportError:
        return None
    h, w = img.shape[:2]
    scale = 1000 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img.copy()
    if scale >= 1:
        scale = 1.0
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    # Paper is the large bright region; Otsu separates it from the background.
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    sh, sw = small.shape[:2]
    if area < 0.25 * sh * sw or area > 0.97 * sh * sw:
        return None  # no page edge visible (already a scan) or page is tiny
    approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
    if len(approx) != 4 or not cv2.isContourConvex(approx):
        return None
    return approx.reshape(4, 2).astype("float32") / scale


def plausible_page(q: np.ndarray) -> bool:
    """A photographed sheet: four distinct corners (tl, tr, br, bl), every
    corner roughly square (perspective allowed) and opposite sides of similar
    length. Rejects outlines that would twist the page when warped."""
    if len({tuple(np.round(p, 1)) for p in q}) < 4:
        return False
    sides = [np.linalg.norm(q[(i + 1) % 4] - q[i]) for i in range(4)]
    if min(sides) < 1 or min(sides[0], sides[2]) / max(sides[0], sides[2]) < 0.6 \
            or min(sides[1], sides[3]) / max(sides[1], sides[3]) < 0.6:
        return False
    for i in range(4):
        a, b = q[i - 1] - q[i], q[(i + 1) % 4] - q[i]
        cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        if not -0.45 < cos < 0.45:  # corner angle outside ~63..117 degrees
            return False
    return True


def warp_quad(img: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Warp the four-cornered region `pts` (any order, image coordinates) to a
    straight rectangle. Returns `img` unchanged for implausible shapes."""
    import cv2
    pts = np.asarray(pts, dtype="float32").reshape(4, 2)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
    tr, bl = pts[np.argmin(d)], pts[np.argmax(d)]
    if not plausible_page(np.array([tl, tr, br, bl])):
        return img
    wid = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    hei = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if wid < 200 or hei < 200 or not (0.2 < wid / hei < 5):
        return img
    m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype="float32"),
                                    np.array([[0, 0], [wid - 1, 0], [wid - 1, hei - 1], [0, hei - 1]],
                                             dtype="float32"))
    return cv2.warpPerspective(img, m, (wid, hei), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


# ---------------------------------------------------------------- OCR boxes
def items_from_ocr(result) -> list[dict]:
    """RapidOCR result [(quad, text, score)] -> boxes in page coordinates with
    the page's average text angle removed."""
    raw = []
    angles = []
    for quad, text, score in result or []:
        text = str(text).strip()
        if not text:
            continue
        q = np.array(quad, dtype=float)
        raw.append((q, text, float(score)))
        dx, dy = q[1][0] - q[0][0], q[1][1] - q[0][1]
        if dx > 3 * abs(q[3][1] - q[0][1]):  # only wide boxes give a stable angle
            angles.append(math.atan2(dy, dx))
    theta = statistics.median(angles) if len(angles) >= 3 else 0.0
    if abs(theta) < math.radians(0.15):
        theta = 0.0
    c, s = math.cos(-theta), math.sin(-theta)
    items = []
    for q, text, score in raw:
        xs = q[:, 0] * c - q[:, 1] * s
        ys = q[:, 0] * s + q[:, 1] * c
        items.append({"x0": float(xs.min()), "x1": float(xs.max()), "y0": float(ys.min()),
                      "y1": float(ys.max()), "t": text, "s": round(score, 3)})
    return items


def group_lines(items: list[dict]) -> list[list[dict]]:
    """Cluster boxes into printed lines (sorted top to bottom, left to right)."""
    if not items:
        return []
    med_h = statistics.median(i["y1"] - i["y0"] for i in items) or 10
    its = sorted(items, key=lambda i: (i["y0"] + i["y1"]) / 2)
    lines: list[list[dict]] = []
    for it in its:
        yc = (it["y0"] + it["y1"]) / 2
        if lines:
            cur = lines[-1]
            mean_y = sum((i["y0"] + i["y1"]) / 2 for i in cur) / len(cur)
            if abs(yc - mean_y) < 0.6 * med_h:
                cur.append(it)
                continue
        lines.append([it])
    for ln in lines:
        ln.sort(key=lambda i: i["x0"])
    return lines


def _yc(line: list[dict]) -> float:
    return sum((i["y0"] + i["y1"]) / 2 for i in line) / len(line)


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


# ---------------------------------------------------------------- table
def _header_score(lines, k) -> float:
    ln = lines[k]
    if len(ln) < 3 or any(is_num(i["t"]) for i in ln):
        return -1
    words = sum(1 for i in ln if _HEAD_WORDS.search(i["t"]))
    if words == 0:
        return -1
    # how many following lines have >=2 boxes that sit under header boxes
    spans = [(i["x0"], i["x1"]) for i in ln]
    aligned = 0
    for nxt in lines[k + 1: k + 30]:
        hits = sum(1 for it in nxt if any(_overlap(it["x0"], it["x1"], a, b) > 0 for a, b in spans))
        if hits >= 2:
            aligned += 1
    return words * 2 + aligned


def _assign(line, cols) -> list[list[dict]]:
    """Put each box of a line under the header column it overlaps most."""
    cells: list[list[dict]] = [[] for _ in cols]
    for it in line:
        best, best_ov = None, 0.0
        for j, (a, b) in enumerate(cols):
            ov = _overlap(it["x0"], it["x1"], a, b)
            if ov > best_ov:
                best, best_ov = j, ov
        if best is None:  # no overlap: nearest column centre
            xc = (it["x0"] + it["x1"]) / 2
            best = min(range(len(cols)), key=lambda j: abs(xc - (cols[j][0] + cols[j][1]) / 2))
        cells[best].append(it)
    return cells


def _join(boxes: list[dict]) -> str:
    """Join boxes in the order given (line by line, left to right)."""
    out = ""
    for b in boxes:
        t = b["t"]
        if out and not (_cjk(out[-1]) and _cjk(t[:1])):
            out += " "
        out += t
    return out


def _cjk(ch: str) -> bool:
    return bool(ch) and "　" <= ch <= "鿿"


def build_table(items: list[dict]) -> dict | None:
    """Return {"columns", "rows", "start", "end"} for the best
    header-anchored table on this page, or None if no header row is found."""
    lines = group_lines(items)
    if len(lines) < 3:
        return None
    scores = [(_header_score(lines, k), k) for k in range(len(lines))]
    best, hk = max(scores)
    if best < 3:
        return None
    # Multi-word headings ("Unit Price") may come back as separate boxes:
    # glue boxes that are closer than a couple of characters.
    header = _header_cols(lines[hk], lines[hk + 1: hk + 15])
    if len(header) < 3:
        return None
    # Column spans: header box widened to meet its neighbours half-way into
    # the gap, so right-aligned numbers and long descriptions still overlap.
    hs = [(i["x0"], i["x1"]) for i in header]
    cols = []
    for j, (a, b) in enumerate(hs):
        left = a if j == 0 else (hs[j - 1][1] + a) / 2
        right = b if j == len(hs) - 1 else (b + hs[j + 1][0]) / 2
        if j == 0:
            left = a - (right - a)
        if j == len(hs) - 1:
            right = b + (b - left)
        cols.append((left, right))
    columns = [i["t"] for i in header]

    gaps = [_yc(lines[k + 1]) - _yc(lines[k]) for k in range(hk + 1, min(len(lines) - 1, hk + 40))]
    med_gap = statistics.median(gaps) if gaps else 40
    rows: list[list[list[dict]]] = []
    kinds: list[str] = []  # "row" or "cont"
    ys: list[float] = []
    end = hk
    prev_y = _yc(header)
    for k in range(hk + 1, len(lines)):
        ln = lines[k]
        y = _yc(ln)
        if y - prev_y > 3.2 * med_gap:
            break  # big vertical gap: table is over
        first = ln[0]["t"]
        if _STOP_WORDS.match(first) and len(ln) <= 3:
            break
        if not any(_overlap(it["x0"], it["x1"], cols[0][0], cols[-1][1]) > 0 for it in ln):
            break
        cells = _assign(ln, cols)
        has_num = any(is_num(it["t"]) for it in ln)
        filled = sum(1 for c in cells if c)
        kind = "row" if has_num or filled >= max(2, (len(cols) + 1) // 2) else "cont"
        rows.append(cells)
        kinds.append(kind)
        ys.append(y)
        prev_y = y
        end = k
    if not rows or kinds.count("row") == 0:
        return None

    # Merge continuation lines into the row above (or below, for lines that
    # come before the first real row or sit clearly closer to the next row).
    merged: list[list[list[dict]]] = []
    pending: list[list[list[dict]]] = []
    for idx, (cells, kind) in enumerate(zip(rows, kinds)):
        if kind == "row":
            row = [list(c) for c in cells]
            for p in pending:
                for j, c in enumerate(p):
                    row[j] = c + row[j]
            pending = []
            merged.append(row)
            continue
        gap_up = ys[idx] - ys[idx - 1] if idx > 0 else 1e9
        gap_dn = ys[idx + 1] - ys[idx] if idx + 1 < len(ys) else 1e9
        if merged and gap_up <= gap_dn * 1.15:
            for j, c in enumerate(cells):
                merged[-1][j].extend(c)
        else:
            pending.append(cells)

    out_rows = [[_join(_reading_order(c)) for c in cells] for cells in merged]
    return {"columns": columns, "rows": out_rows, "start": hk, "end": end}


def _close_groups(line: list[dict]) -> list[list[dict]]:
    """Split a line (sorted left to right) where the gap between boxes is
    wider than ~1.6 characters: those gaps are column breaks."""
    groups = [[line[0]]]
    for it in line[1:]:
        prev = groups[-1][-1]
        cw = max(((prev["x1"] - prev["x0"]) / max(len(prev["t"]), 1)
                  + (it["x1"] - it["x0"]) / max(len(it["t"]), 1)) / 2, 1)
        if it["x0"] - prev["x1"] < 1.6 * cw:
            groups[-1].append(it)
        else:
            groups.append([it])
    return groups


def _header_cols(head: list[dict], below: list[list[dict]]) -> list[dict]:
    """Header boxes -> one box per column. Two close boxes are one heading
    ("Unit" + "Price") unless the rows below show separate values under each
    of them (e.g. "序号" and "品名" printed close together)."""
    def separate(a, b) -> bool:
        # Evidence from the rows below: separate values under each heading on
        # the same line mean two columns; one value spanning both means one.
        apart = span = 0
        for ln in below:
            ua = {id(i) for i in ln if _overlap(i["x0"], i["x1"], a["x0"], a["x1"]) > 0}
            ub = {id(i) for i in ln if _overlap(i["x0"], i["x1"], b["x0"], b["x1"]) > 0}
            if ua & ub:
                span += 1
            elif ua and ub:
                apart += 1
        return apart >= 1 and apart >= span

    cols: list[list[dict]] = []
    for g in _close_groups(head):
        cur = [g[0]]
        for it in g[1:]:
            if separate(_merge_boxes(cur), it):
                cols.append(cur)
                cur = [it]
            else:
                cur.append(it)
        cols.append(cur)
    return [_merge_boxes(c) for c in cols]


def _reading_order(boxes: list[dict]) -> list[dict]:
    """Top-to-bottom, then left-to-right, for boxes that ended up in one cell
    from different printed lines."""
    out: list[list[dict]] = []
    for b in sorted(boxes, key=lambda i: (i["y0"] + i["y1"]) / 2):
        yc, h = (b["y0"] + b["y1"]) / 2, b["y1"] - b["y0"]
        if out:
            last = out[-1][-1]
            if abs(yc - (last["y0"] + last["y1"]) / 2) < 0.5 * max(h, last["y1"] - last["y0"]):
                out[-1].append(b)
                continue
        out.append([b])
    return [b for grp in out for b in sorted(grp, key=lambda i: i["x0"])]


def _merge_boxes(g: list[dict]) -> dict:
    return {"x0": min(i["x0"] for i in g), "x1": max(i["x1"] for i in g),
            "y0": min(i["y0"] for i in g), "y1": max(i["y1"] for i in g),
            "t": _join(g), "s": min(i["s"] for i in g)}


def lines_to_rows(items: list[dict]) -> list[list[str]]:
    """Plain rows of cells (for the AI prompt and the OCR text tab)."""
    return [[_join(g) for g in _close_groups(ln)] for ln in group_lines(items)]
