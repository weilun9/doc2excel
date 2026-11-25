"""Score the pipeline on REAL documents against hand-checked answers.

Documents live in tests/real/ (committed) or tests/private/ (git-ignored, for
ones that must not be public): <photo> + <name>.json (see
tests/real/cahaya_T02-160232.json). For every
document with its photo present, runs each combination of photo clean-up on/off
and OCR engine (local, plus vision when one is set up) and reports how many
lines got the right barcode, quantity, unit price and line total.

    .venv\\Scripts\\python tests\\real_docs_eval.py            # local OCR
    .venv\\Scripts\\python tests\\real_docs_eval.py --vision   # also the vision model in Settings
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRS = [ROOT / "tests" / "real", ROOT / "tests" / "private"]
os.environ.setdefault("LOCALAPPDATA", tempfile.mkdtemp(prefix="d2x_real_"))
os.environ["D2X_AI_MODE"] = "offline"   # score what OCR/vision itself gets right
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import config, llm, ocr  # noqa: E402

DIGITS = re.compile(r"\D")


def num(s: str) -> str:
    s = str(s).replace(",", "")
    try:
        return f"{float(re.sub(r'[^0-9.]', '', s)):.2f}"
    except ValueError:
        return ""


def score(lines_text: list[str], truth: dict) -> dict:
    """lines_text: what the engine read, one entry per printed/handwritten line."""
    digit_lines = [DIGITS.sub("", ln) for ln in lines_text]
    nums = [{num(t) for t in re.findall(r"\d[\d,]*\.\d{2}", ln)} for ln in lines_text]
    res = {"barcode": 0, "price": 0, "total": 0, "qty": 0}
    for gt in truth["lines"]:
        if any(gt["barcode"] in d for d in digit_lines):
            res["barcode"] += 1
        # price and line total printed on the same line
        hit = [i for i, ns in enumerate(nums) if num(gt["price"]) in ns and num(gt["total"]) in ns]
        if hit:
            res["price"] += 1
            res["total"] += 1
            if any(re.search(rf"(^|\D){gt['qty']}\s*UNIT", lines_text[i], re.I) for i in hit):
                res["qty"] += 1
        else:
            res["price"] += any(num(gt["price"]) in ns for ns in nums)
            res["total"] += any(num(gt["total"]) in ns for ns in nums)
    text = "\n".join(lines_text)
    res["header"] = sum(v.replace(" ", "") in text.replace(" ", "")
                        for v in truth["header"].values())
    return res


def read(path: Path, enhance_on: bool, engine: str) -> list[str]:
    pages = ocr.load_pages(path)
    if enhance_on:
        pages = ocr.enhance_pages(pages)[0]
    out = []
    for img in pages:
        if engine == "vision":
            rows, _ = ocr.vision_page(img)
        else:
            rows, _ = ocr.rapid_page(img)
        out += [" | ".join(r) for r in rows]
    return out


def main():
    want_vision = "--vision" in sys.argv
    config.load()
    engines = ["local"] + (["vision"] if want_vision and llm.vision_ready() else [])
    if want_vision and "vision" not in engines:
        print("(no vision model set up in Settings: local OCR only)")
    cases = sorted(js for d in DIRS if d.is_dir() for js in d.glob("*.json"))
    found = False
    for js in cases:
        truth = json.loads(js.read_text(encoding="utf-8"))
        photo = js.parent / truth["photo"]
        if not photo.exists():
            print(f"skip {js.name}: put the original photo at {photo}")
            continue
        found = True
        n = len(truth["lines"])
        print(f"\n{js.stem}: {n} lines, {len(truth['header'])} header fields")
        print(f"  {'engine':8} {'clean-up':9} {'barcode':>8} {'qty':>6} {'price':>6} {'total':>6} {'header':>7}")
        for eng in engines:
            for on in (False, True):
                try:
                    r = score(read(photo, on, eng), truth)
                except llm.LLMError as e:
                    print(f"  {eng:8} {'on' if on else 'off':9} failed: {e}")
                    continue
                print(f"  {eng:8} {'on' if on else 'off':9} {r['barcode']:>5}/{n} {r['qty']:>3}/{n} "
                      f"{r['price']:>3}/{n} {r['total']:>3}/{n} {r['header']:>4}/{len(truth['header'])}")
    if not found:
        print("\nNo real documents with photos yet.")


if __name__ == "__main__":
    main()
