"""Photo clean-up before OCR / AI, in the spirit of document-scanner apps:

  crop     find the sheet of paper and warp it flat (brightness and edge
           detectors; the largest plausible outline wins)
  light    remove shadows, uneven lighting and yellow paper: estimate the
           background per colour channel and divide it out (paper -> white,
           ink keeps its colour, so red stamps stay red), then a white point
           and a gentle curve so faint print gets darker
  upscale, sharpen   available but OFF by default (they made OCR worse; see
           DEFAULT_STEPS)

Every step is conservative and skipped when it does not apply, so a clean
scan comes out practically unchanged.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import layout

MIN_SIDE = 1600   # upscale photos whose long side is smaller than this
MAX_SIDE = 3200   # and never work on more pixels than this


def _cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        return None


def _edge_quad(img: np.ndarray):
    """Four page corners from edges (finds white paper on a light desk, where
    a brightness threshold sees no outline). None when nothing page-like."""
    cv2 = _cv2()
    h, w = img.shape[:2]
    scale = min(1.0, 900 / max(h, w))
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    v = float(np.median(gray))
    sh, sw = small.shape[:2]
    # Median-based thresholds first; a low fixed pair finds faint paper edges
    # on a light desk (paper and desk almost the same brightness).
    for lo, hi in ((int(max(0, 0.66 * v)), int(min(255, 1.33 * v))), (20, 60)):
        edges = cv2.dilate(cv2.Canny(gray, lo, hi), np.ones((3, 3), np.uint8), iterations=2)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        q = _best_quad(cv2, contours, sh, sw)
        if q is not None:
            return q / scale
    return None


def _best_quad(cv2, contours, sh, sw):
    for c in sorted(contours, key=lambda c: cv2.contourArea(cv2.convexHull(c)), reverse=True)[:8]:
        hull = cv2.convexHull(c)  # a page edge broken by text/shadow still has a 4-corner hull
        area = cv2.contourArea(hull)
        if area < 0.25 * sh * sw:
            break
        if area > 0.97 * sh * sw:
            continue  # the photo frame itself
        for eps in (0.02, 0.03, 0.045, 0.06):
            approx = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
            if len(approx) == 4:
                q = approx.reshape(4, 2).astype("float32")
                if layout.plausible_page(_order(q)) and cv2.contourArea(approx) > 0.9 * area:
                    return q
                break
    return None


def _order(q: np.ndarray) -> np.ndarray:
    s, d = q.sum(1), np.diff(q, axis=1).ravel()
    return np.array([q[np.argmin(s)], q[np.argmin(d)], q[np.argmax(s)], q[np.argmax(d)]])


def _crop(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """Warp the page flat. Two detectors: brightness (paper vs dark desk) and
    edges (light desk). A shadow can hide part of the page from the brightness
    one, which would cut text off, so the LARGEST plausible outline wins: the
    sheet is always the outermost quadrilateral (table borders are smaller)."""
    cv2 = _cv2()
    best, best_area = None, 0.0
    for q in (layout.bright_page_quad(img), _edge_quad(img)):
        if q is None or not layout.plausible_page(_order(q)):
            continue
        area = cv2.contourArea(_order(q).astype(np.float32))
        if area > best_area:
            best, best_area = q, area
    if best is not None:
        out = layout.warp_quad(img, best)
        if out is not img:
            return out, True
    return img, False


def _flatten_light(img: np.ndarray) -> np.ndarray:
    """Divide each channel by its estimated background (paper colour + shadows).
    Closing (not plain dilation) removes the ink without inflating the
    background next to it, which is what leaves grey halos around words."""
    cv2 = _cv2()
    h, w = img.shape[:2]
    scale = min(1.0, 800 / max(h, w))
    small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    k = max(9, int(round(max(small.shape[:2]) / 40)) | 1)        # wider than a text stroke
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.medianBlur(bg, (k * 2) | 1)
    bg = cv2.GaussianBlur(bg, (0, 0), k / 2)
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    out = img.astype(np.float32) * 255.0 / np.maximum(bg, 24.0)
    return np.clip(out, 0, 255)


WHITE_POINT = 235   # >= this becomes paper white (removes texture and JPEG noise)
GAMMA = 1.1         # > 1 darkens the mid-tones a little more
# Black point: this share of the ink pixels ends up pure black.
# Measured (real photos / synthetic photos / median ink grey, 0 = black):
#   0.0: 102 / 96.6% / 94    0.5: 101 / 93.6% / 18    0.7: 102 / 92.9% / 12
# Blacker ink did not help OCR and cost 3-4 points on blurry photos (strokes
# merge), so OCR/AI get 0.0 and only the picture shown to people is blackened.
OCR_BLACK = 0.0
DISPLAY_BLACK = 0.7


def _ink_curve(f: np.ndarray, black: float = OCR_BLACK) -> np.ndarray:
    """Paper -> pure white, ink -> darker. With a black point, it is measured
    on the ink itself (the darkest strokes stay black, grey print is pulled
    down to black), so weak, grey text becomes solid dark text.
    Colours keep their hue."""
    gray = f.mean(axis=2)
    lo = 0.0
    ink = gray[gray < 0.75 * WHITE_POINT]
    if black and ink.size > 0.001 * gray.size:
        lo = float(np.percentile(ink, black * 100))
        lo = min(lo, 0.6 * WHITE_POINT)  # never treat light-grey paper detail as ink
    f = np.clip((f - lo) * (255.0 / max(1.0, WHITE_POINT - lo)), 0, 255)
    f = 255.0 * np.power(f / 255.0, GAMMA)
    return np.clip(f, 0, 255).astype(np.uint8)


def _sharpen(img: np.ndarray) -> np.ndarray:
    cv2 = _cv2()
    blur = cv2.GaussianBlur(img, (0, 0), 1.0)
    return cv2.addWeighted(img, 1.35, blur, -0.35, 0)


ALL_STEPS = ("crop", "upscale", "light", "sharpen")
# Measured: on synthetic photos (tests/enhance_test.py) crop + light was as
# good as anything and upscaling hurt; on real phone photos
# (tests/real_docs_eval.py) sharpening cost quantities on thin thermal print
# (score 102 -> 97), so the default is crop + light only.
DEFAULT_STEPS = ("crop", "light")


def enhance_array(img: np.ndarray, use=DEFAULT_STEPS, black: float = OCR_BLACK) -> tuple[np.ndarray, list[str]]:
    """RGB uint8 array -> (enhanced RGB uint8 array, steps applied).
    `black`: OCR_BLACK for what OCR/AI read, DISPLAY_BLACK for what people see
    (same crop and lighting, only the ink is darker)."""
    cv2 = _cv2()
    if cv2 is None or img.ndim != 3 or min(img.shape[:2]) < 50:
        return img, []
    steps = []
    h, w = img.shape[:2]
    if max(h, w) > MAX_SIDE:
        r = MAX_SIDE / max(h, w)
        img = cv2.resize(img, (int(w * r), int(h * r)), interpolation=cv2.INTER_AREA)
    if "crop" in use:
        img, cropped = _crop(img)
        if cropped:
            steps.append("crop")
    h, w = img.shape[:2]
    if "upscale" in use and max(h, w) < MIN_SIDE:
        r = MIN_SIDE / max(h, w)
        img = cv2.resize(img, (int(w * r), int(h * r)), interpolation=cv2.INTER_CUBIC)
        steps.append("upscale")
    if "light" in use:
        img = _ink_curve(_flatten_light(img), black)
        steps.append("light")
    if "sharpen" in use:
        img = _sharpen(img)
        steps.append("sharpen")
    return img, steps


def enhance(img: Image.Image, use=DEFAULT_STEPS, black: float = OCR_BLACK) -> tuple[Image.Image, list[str]]:
    arr, steps = enhance_array(np.array(img.convert("RGB")), use, black)
    return (Image.fromarray(arr) if steps else img), steps
