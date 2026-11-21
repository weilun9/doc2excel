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
import statistics
import threading
from pathlib import Path

from PIL import Image, ImageOps

from . import config, llm

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
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = []
        total = 0
        for i in range(min(len(pdf), MAX_PDF_PAGES)):
            text = pdf[i].get_textpage().get_text_range()
            total += len(text.strip())
            rows = []
            for line in text.replace("\r", "").split("\n"):
                cells = [c for c in _split_spaces(line) if c]
                if cells:
                    rows.append(cells)
            pages.append(rows)
        return pages if total > 40 else None
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


def rapid_rows(img: Image.Image) -> list[list[str]]:
    import numpy as np
    img = _shrink(img, MAX_OCR_SIDE)
    result, _ = _rapid()(np.array(img))
    if not result:
        return []
    items = []
    for box, text, score in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        text = str(text).strip()
        if not text:
            continue
        items.append({
            "x0": min(xs), "x1": max(xs), "yc": (min(ys) + max(ys)) / 2,
            "h": max(ys) - min(ys), "t": text,
            "cw": (max(xs) - min(xs)) / max(len(text), 1),
        })
    if not items:
        return []
    med_h = statistics.median(i["h"] for i in items) or 10
    items.sort(key=lambda i: i["yc"])
    lines: list[list[dict]] = []
    for it in items:
        if lines:
            cur = lines[-1]
            mean_y = sum(i["yc"] for i in cur) / len(cur)
            if abs(it["yc"] - mean_y) < 0.6 * med_h:
                cur.append(it)
                continue
        lines.append([it])
    rows = []
    for line in lines:
        line.sort(key=lambda i: i["x0"])
        cells = [line[0]["t"]]
        prev = line[0]
        for it in line[1:]:
            gap = it["x0"] - prev["x1"]
            cw = max((prev["cw"] + it["cw"]) / 2, 1)
            if gap < 1.6 * cw:
                cells[-1] = cells[-1] + ("" if _cjk(cells[-1][-1:]) else " ") + it["t"]
            else:
                cells.append(it["t"])
            prev = it
        rows.append([_respace(c) for c in cells])
    return rows


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


def _cjk(ch: str) -> bool:
    return bool(ch) and "　" <= ch <= "鿿"


VISION_PROMPT = (
    "Transcribe ALL text in this document image exactly as printed, top to bottom. "
    "Keep each printed line on its own output line. When a line has separate columns "
    "(tables, label/value pairs, line items), separate the columns with ' | '. "
    "Keep numbers, currency symbols, dates and codes exactly as printed. "
    "Output only the transcription, no comments, no markdown."
)


def vision_rows(img: Image.Image) -> list[list[str]]:
    img = _shrink(img, 2000)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    content = llm.chat(
        config.get("VISION_BASE_URL"), config.get("VISION_API_KEY"), config.get("VISION_MODEL"),
        [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        timeout=180,
    )
    content = content.strip().strip("`")
    rows = []
    for line in content.splitlines():
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    return rows


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
        rows = pdf_text_rows(path)
        if rows:
            return {"engine": "pdf-text", "pages": rows, "text": rows_to_text(rows)}
    engine = choose_engine()
    out_pages = []
    for i, img in enumerate(pages):
        if progress:
            progress(i + 1, len(pages), engine)
        if engine == "vision":
            try:
                out_pages.append(vision_rows(img))
                continue
            except llm.LLMError:
                engine = "rapidocr"  # fall back for this and the remaining pages
        out_pages.append(rapid_rows(img))
    return {"engine": engine, "pages": out_pages, "text": rows_to_text(out_pages)}
