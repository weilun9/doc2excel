"""Photo clean-up (app/enhance.py) and the model-name check in Settings.

Builds synthetic phone photos from the sample documents (perspective, desk
background, shadow, yellow paper, blur, JPEG) and checks that local OCR reads
them at least as well after enhancement, and clearly better under shadow.

    .venv\\Scripts\\python tests\\enhance_test.py
"""
from __future__ import annotations

import difflib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="d2x_enh_"))
os.environ["LOCALAPPDATA"] = str(TMP / "appdata")
os.environ["D2X_KEYSTORE"] = "memory"
os.environ["D2X_AI_MODE"] = "offline"
os.environ["D2X_OCR_MODE"] = "local"
os.environ["D2X_WATCH_DIR"] = str(TMP / "watch")
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app import config, enhance, layout, ocr  # noqa: E402

PASSED = 0
rng = np.random.default_rng(7)


def ok(cond, msg):
    global PASSED
    if not cond:
        raise AssertionError(msg)
    PASSED += 1
    print("  ok  ", msg)


def text_of(img):
    rows, _ = ocr.rapid_page(img)
    return " ".join(" ".join(r) for r in rows)


def sim(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def photo(clean, desk="dark", angle=9, shadow=0.45, blur=1.3, q=55, side=1300):
    doc = np.array(clean.convert("RGB")).astype(np.float32) * np.array([1.0, 0.96, 0.86])
    h, w = doc.shape[:2]
    H, W = int(h * 1.45), int(w * 1.45)
    base = (95, [1.0, 0.8, 0.6]) if desk == "dark" else (225, [0.97, 0.97, 1.0])
    bg = cv2.GaussianBlur((rng.normal(base[0], 12, (H, W, 3)) * np.array(base[1])).astype(np.float32), (0, 0), 3)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    ox, oy, t = W * 0.18, H * 0.14, np.tan(np.radians(angle))
    dst = np.float32([[ox + h * t * 0.4, oy], [ox + w * 0.95, oy + w * t * 0.3],
                      [ox + w * 1.02, oy + h * 0.97], [ox - h * t * 0.2, oy + h * 1.01]])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(doc, m, (W, H))
    mask = cv2.warpPerspective(np.ones((h, w), np.float32), m, (W, H))[..., None]
    img = bg * (1 - mask) + warped * mask
    yy, xx = np.mgrid[0:H, 0:W]
    img = img * (shadow + (1 - shadow) * np.clip((xx / W + yy / H) / 1.3, 0, 1))[..., None]
    img = np.clip(cv2.GaussianBlur(img, (0, 0), blur) + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)
    r = side / max(H, W)
    img = cv2.resize(img, (int(W * r), int(H * r)), interpolation=cv2.INTER_AREA)
    _, enc = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    return Image.fromarray(cv2.cvtColor(cv2.imdecode(enc, 1), cv2.COLOR_BGR2RGB))


def t_quads():
    print("page outline checks")
    sq = np.array([[0, 0], [800, 0], [800, 1100], [0, 1100]], np.float32)
    ok(layout.plausible_page(sq), "upright page accepted")
    persp = np.array([[60, 0], [760, 30], [800, 1100], [0, 1080]], np.float32)
    ok(layout.plausible_page(persp), "page seen at an angle accepted")
    twisted = np.array([[0, 0], [800, 300], [500, 1100], [300, 200]], np.float32)
    ok(not layout.plausible_page(twisted), "twisted outline rejected")
    sliver = np.array([[0, 0], [800, 0], [800, 1100], [700, 300]], np.float32)
    ok(not layout.plausible_page(sliver), "outline with one bad corner rejected")
    img = np.full((600, 600, 3), 200, np.uint8)
    ok(layout.warp_quad(img, twisted) is img, "implausible outline: image left as is")


def t_ocr():
    print("OCR on synthetic phone photos (similarity to the clean original)")
    raw_tot = enh_tot = 0.0
    n = 0
    for name in ("sample_invoice.png", "sample_receipt.jpg"):
        clean = Image.open(ROOT / "samples" / name).convert("RGB")
        ref = text_of(clean)
        e, steps = enhance.enhance(clean)
        s = sim(ref, text_of(e))
        ok(s >= 0.98, f"{name} clean scan stays readable after clean-up ({s:.1%}, {steps})")
        for case, kw in (("dark desk", {}), ("light desk", {"desk": "light", "angle": 6}),
                         ("shadowed edge", {"shadow": 0.3}),
                         ("heavy shadow", {"shadow": 0.25, "blur": 1.6, "q": 45, "side": 1000})):
            img = photo(clean, **kw)
            a = sim(ref, text_of(img))
            e, steps = enhance.enhance(img)
            b = sim(ref, text_of(e))
            raw_tot += a; enh_tot += b; n += 1
            ok("crop" in steps, f"{name} {case}: page found and flattened")
            ok(b >= a - 0.01, f"{name} {case}: raw {a:.1%} -> enhanced {b:.1%}")
            # a crop that cuts into the page loses whole words at one edge:
            # the enhanced image must keep at least the words the raw photo gives
            want = [wd for wd in ref.split() if len(wd) >= 3]
            raw_txt, enh_txt = text_of(img), text_of(e)
            r_raw = sum(wd in raw_txt for wd in want) / len(want)
            r_enh = sum(wd in enh_txt for wd in want) / len(want)
            ok(r_enh >= r_raw - 0.03, f"{name} {case}: nothing cut off (words kept: raw {r_raw:.0%}, enhanced {r_enh:.0%})")
            if case == "heavy shadow":
                ok(b >= a + 0.02, f"{name} {case}: shadow removal clearly helps")
    ok(enh_tot / n > raw_tot / n, f"average raw {raw_tot / n:.1%} -> enhanced {enh_tot / n:.1%}")


def t_pipeline():
    print("pipeline + setting")
    p = TMP / "photo.jpg"
    photo(Image.open(ROOT / "samples" / "sample_invoice.png"), shadow=0.3).save(p)
    pdf = ROOT / "samples" / "sample_invoice.pdf"
    config.ENV_PATH = TMP / ".env"
    config.ENV_PATH.write_text("PHOTO_ENHANCE=auto\n", encoding="utf-8")
    config.load()
    ok(ocr.should_enhance(p) and not ocr.should_enhance(pdf), "auto: images enhanced, PDFs not")
    pages, steps = ocr.enhance_pages(ocr.load_pages(p))
    again = ocr.load_pages(p, enhanced=True)
    ok(np.array_equal(np.array(pages[0]), np.array(again[0])), "preview shows exactly the image that was read")
    shown = ocr.load_pages(p, enhanced=True, display=True)[0]
    ok(shown.size == pages[0].size, "preview has the same crop as what was read")
    ink = lambda im: float(np.median([v for v in np.asarray(im.convert("L")).ravel() if v < 128]))
    ok(ink(shown) < ink(pages[0]) - 20, f"preview ink is darker ({ink(pages[0]):.0f} -> {ink(shown):.0f}, 0 = black)")
    w, h = ocr.load_pages(p)[0].size
    ok(ocr.load_pages(p, rotate=90)[0].size == (h, w) and ocr.load_pages(p, rotate=180)[0].size == (w, h),
       "rotate 90 swaps width/height, 180 keeps them")
    a = np.asarray(ocr.load_pages(p, rotate=90)[0]); b = np.asarray(ocr.load_pages(p)[0])
    ok(np.array_equal(a, np.rot90(b, k=-1)), "rotate 90 = clockwise")
    config.save({"PHOTO_ENHANCE": "off"})
    ok(not ocr.should_enhance(p), "off: photos used as they are")


def t_rotate_api():
    print("rotate endpoint")
    from app.server import AppServer
    from app.store import Store
    st = Store()
    srv = AppServer(st, 8765)
    c = srv.app.test_client()
    h = {"Host": "127.0.0.1:8765", "X-D2X-Key": srv.api_key}
    env = {"REMOTE_ADDR": "127.0.0.1"}
    src = TMP / "rot.jpg"
    Image.open(ROOT / "samples" / "sample_receipt.jpg").save(src)
    d = st.add_file(src)
    r = c.post(f"/api/doc/{d['id']}/rotate", json={"deg": 90}, headers=h, environ_base=env)
    ok(r.status_code == 200 and r.get_json()["rotate"] == 90 and st.get(d["id"])["status"] == "queued",
       "rotate right: saved as 90 and queued to be read again")
    c.post(f"/api/doc/{d['id']}/rotate", json={"deg": -90}, headers=h, environ_base=env)
    c.post(f"/api/doc/{d['id']}/rotate", json={"deg": -90}, headers=h, environ_base=env)
    ok(st.get(d["id"])["rotate"] == 270, "two turns left from 90 -> 270")
    r = c.post(f"/api/doc/{d['id']}/rotate", json={"deg": 45}, headers=h, environ_base=env)
    ok(r.status_code == 400, "only quarter turns accepted")
    r = c.post(f"/api/doc/{d['id']}/rotate", json={"deg": 90}, headers={"Host": "127.0.0.1:8765"}, environ_base=env)
    ok(r.status_code == 403, "rotate needs the desktop key")
    r = c.get(f"/api/doc/{d['id']}/page/0.jpg?k={srv.api_key}", headers={"Host": "127.0.0.1:8765"}, environ_base=env)
    im = Image.open(__import__("io").BytesIO(r.data))
    w, hh = Image.open(src).size
    ok(r.status_code == 200 and (im.width > im.height) == (hh > w), "preview shows the turned page")


def t_model_names():
    print("model name check in Settings")
    from app.server import AppServer
    from app.store import Store
    srv = AppServer(Store(), 8765)
    c = srv.app.test_client()
    h = {"Host": "127.0.0.1:8765", "X-D2X-Key": srv.api_key}
    env = {"REMOTE_ADDR": "127.0.0.1"}
    gem = "https://generativelanguage.googleapis.com/v1beta/openai"
    for body, want in ((({"VISION_BASE_URL": gem, "VISION_MODEL": "Gemini 2.5 Flash"}), 400),
                       (({"VISION_BASE_URL": gem, "VISION_MODEL": "Gemini-2.5-Flash"}), 400),
                       (({"LLM_MODEL": "deepseek chat"}), 400),
                       (({"VISION_BASE_URL": gem, "VISION_MODEL": "gemini-2.5-flash"}), 200),
                       (({"VISION_BASE_URL": gem, "VISION_MODEL": "models/gemini-2.5-flash"}), 200),
                       (({"LLM_MODEL": "deepseek-chat"}), 200),
                       (({"VISION_BASE_URL": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                          "VISION_MODEL": "qwen-vl-max"}), 200)):
        r = c.post("/api/settings", json=body, headers=h, environ_base=env)
        ok(r.status_code == want and (want == 200 or r.get_json()["error"] == "bad_model"),
           f"{body} -> {r.status_code}")


if __name__ == "__main__":
    for fn in (t_quads, t_ocr, t_pipeline, t_rotate_api, t_model_names):
        fn()
    print(f"\nALL {PASSED} CHECKS PASSED")
