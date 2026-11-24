"""OCR: turn an image or PDF into page images, then into text rows.

Engines:
  * rapidocr  - local PaddleOCR models via ONNX Runtime (offline, default)
  * vision    - an OpenAI-compatible vision LLM (online, optional)
  * pdf-text  - a PDF's own text layer, when it has one (no OCR needed)

Every engine returns rows of cells, so the offline table builder and the AI
prompt both see the document's line/column layout."""
from __future__ import annotations

import base64
import io
import re
import threading
from pathlib import Path

from PIL import Image, ImageOps

from . import config, layout, llm, structure

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
try:  # iPhone photos
    from pillow_heif import register_heif_opener
    register_heif_opener()
    IMAGE_EXT |= {".heic", ".heif"}
except ImportError:
    pass
PDF_EXT = {".pdf"}
SUPPORTED_EXT = IMAGE_EXT | PDF_EXT

MAX_OCR_SIDE = 2600
MAX_PDF_PAGES = 30

_engine = None
_engine_lock = threading.Lock()


def _rapid():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        return _engine


def warm_up() -> None:
    try:
        _rapid()
    except Exception:
        pass


def load_pages(path: Path) -> list[Image.Image]:
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        pages = []
        try:
            for i in range(min(len(pdf), MAX_PDF_PAGES)):
                pages.append(pdf[i].render(scale=200 / 72).to_pil().convert("RGB"))
        finally:
            pdf.close()
        return pages
    img = Image.open(path)
    frames = []
    for i in range(getattr(img, "n_frames", 1)):  # multi-page TIFF from scanners
        img.seek(i)
        frames.append(ImageOps.exif_transpose(img.copy()).convert("RGB"))
    return frames


def pdf_text_rows(path: Path) -> list[list[list[str]]] | None:
    """Rows per page from a PDF text layer, or None if the PDF is a scan."""
    res = pdf_text(path)
    return res[0] if res else None


def pdf_text(path: Path) -> tuple[list, list] | None:
    """(rows per page, positioned boxes per page) from a PDF text layer, or
    None if the PDF is a scan. Uses pdfium's text-run rectangles, so columns
    come from real x positions instead of counting spaces."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages, boxes = [], []
        total = 0
        for i in range(min(len(pdf), MAX_PDF_PAGES)):
            page = pdf[i]
            tp = page.get_textpage()
            text = tp.get_text_range()
            total += len(text.strip())
            _, ph = page.get_size()
            items = []
            for r in range(tp.count_rects()):
                left, bottom, right, top = tp.get_rect(r)
                t = tp.get_text_bounded(left, bottom, right, top).strip()
                if t:
                    items.append({"x0": round(left, 1), "x1": round(right, 1), "y0": round(ph - top, 1),
                                  "y1": round(ph - bottom, 1), "t": " ".join(t.split()), "s": 1.0})
            if items:
                rows = layout.lines_to_rows(items)
            else:
                rows = []
                for line in text.replace("\r", "").split("\n"):
                    cells = [c for c in _split_spaces(line) if c]
                    if cells:
                        rows.append(cells)
            pages.append(rows)
            boxes.append(items)
        return (pages, boxes) if total > 40 else None
    finally:
        pdf.close()


def _split_spaces(line: str) -> list[str]:
    """Split on runs of 2+ spaces or tabs (how PDF text layers align columns)."""
    import re
    return [c.strip() for c in re.split(r"\t+| {2,}", line.strip())]


def _shrink(img: Image.Image, side: int) -> Image.Image:
    w, h = img.size
    s = max(w, h)
    if s <= side:
        return img
    r = side / s
    return img.resize((int(w * r), int(h * r)), Image.LANCZOS)


def rapid_page(img: Image.Image) -> tuple[list[list[str]], list[dict]]:
    """OCR one page. Returns (rows of cells, positioned boxes for layout.py).
    Phone photos are flattened first (paper outline -> straight rectangle)."""
    import numpy as np
    arr = layout.flatten_page(np.array(_shrink(img, MAX_OCR_SIDE)))
    result, _ = _rapid()(arr)
    if not result:
        return [], []
    items = layout.items_from_ocr(result)
    for it in items:
        it["t"] = _respace(it["t"])
    rows = layout.lines_to_rows(items)
    for it in items:  # keep stored state small
        for k in ("x0", "x1", "y0", "y1"):
            it[k] = round(it[k], 1)
    return rows, items


_CJK_LAT = re.compile(r"(?<=[㐀-鿿])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[㐀-鿿])")
_LOW_UP = re.compile(r"(?<=[a-z])(?=[A-Z][a-z])")
_PAREN = re.compile(r"(?<=[A-Za-z0-9])(?=\()")


def _respace(text: str) -> str:
    """The recogniser sometimes drops spaces ("A4CopierPaper80gsm(box)").
    Restore the obvious ones: CJK/Latin boundaries, and camel-case joins or
    "word(" inside long space-free runs."""
    text = _CJK_LAT.sub(" ", text)
    out = []
    for tok in text.split(" "):
        if len(tok) >= 12:
            tok = _PAREN.sub(" ", _LOW_UP.sub(" ", tok))
        out.append(tok)
    return " ".join(out)


VISION_TABLE_PROMPT = (
    "This image is one page of a scanned or photographed business document. Read it "
    "and build the table directly from what you SEE: use the printed column positions, "
    "keep a blank cell blank, and keep a description that wraps over several printed "
    "lines in ONE row.\n"
    "Also return \"lines\": every printed line top to bottom, columns separated by ' | ', "
    "numbers/dates/codes exactly as printed.\n"
) + structure.SCHEMA_RULES.replace(
    'Return ONLY a JSON object with this shape:\n{',
    'Return ONLY a JSON object with this shape:\n{\n  "lines": ["Invoice No: | INV-001"],', 1)


def vision_page(img: Image.Image) -> tuple[list[list[str]], dict]:
    """One call per page: the vision model returns the transcription AND the
    table, so the layout it sees is not lost in a text-only second step."""
    img = _shrink(img, 2000)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    content = llm.chat(
        config.get("VISION_BASE_URL"), config.get("VISION_API_KEY"), config.get("VISION_MODEL"),
        [{"role": "user", "content": [
            {"type": "text", "text": VISION_TABLE_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        timeout=240, max_tokens=8000,
    )
    try:
        data = structure._extract_json(content)
    except ValueError as e:  # includes json.JSONDecodeError
        raise llm.LLMError(f"vision reply was not JSON: {e}") from e
    rows = []
    for line in data.get("lines") or []:
        cells = [c.strip() for c in str(line).split("|")]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    return rows, structure.normalize(data)


def choose_engine() -> str:
    mode = config.get("OCR_MODE").lower()
    if mode == "local":
        return "rapidocr"
    if mode == "vision":
        return "vision" if llm.vision_ready() else "rapidocr"
    if llm.vision_ready() and llm.is_reachable(config.get("VISION_BASE_URL")):
        return "vision"
    return "rapidocr"


def rows_to_text(pages: list[list[list[str]]]) -> str:
    parts = []
    for n, rows in enumerate(pages, 1):
        if len(pages) > 1:
            parts.append(f"--- page {n} ---")
        parts.extend(" | ".join(r) for r in rows)
    return "\n".join(parts)


def run(path: Path, pages: list[Image.Image], progress=None) -> dict:
    """OCR a document. Returns {engine, pages: [[cells]...], text}."""
    if path.suffix.lower() in PDF_EXT:
        got = pdf_text(path)
        if got:
            rows, boxes = got
            res = {"engine": "pdf-text", "pages": rows, "text": rows_to_text(rows)}
            if any(boxes):
                res["boxes"] = boxes
            return res
    engine = choose_engine()
    out_pages, boxes, tables = [], [], []
    for i, img in enumerate(pages):
        if progress:
            progress(i + 1, len(pages), engine)
        if engine == "vision":
            try:
                rows, table = vision_page(img)
                out_pages.append(rows)
                boxes.append([])
                tables.append(table)
                continue
            except llm.LLMError:
                engine = "rapidocr"  # fall back for this and the remaining pages
        rows, items = rapid_page(img)
        out_pages.append(rows)
        boxes.append(items)
        tables.append(None)
    res = {"engine": engine, "pages": out_pages, "text": rows_to_text(out_pages)}
    if any(boxes):
        res["boxes"] = boxes
    if tables and all(tables):
        res["vision_tables"] = tables
    return res
