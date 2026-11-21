"""Paths and settings. Settings live in a `.env` file next to the exe (or the
project root when running from source). Nothing is hard-coded: API keys are
read from that file, and the in-app settings screen writes back to it."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

APP_NAME = "Doc2Excel"

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    RES_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    RES_DIR = BASE_DIR

WEB_DIR = RES_DIR / "app" / "web"
ENV_PATH = BASE_DIR / ".env"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
INBOX_DIR = DATA_DIR / "inbox"
STATE_PATH = DATA_DIR / "state.json"
DOCS_HOME = Path.home() / "Documents" / APP_NAME

DEFAULTS: dict[str, str] = {
    # Text LLM that turns OCR text into a table (OpenAI-compatible endpoint).
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_API_KEY": "",
    "LLM_MODEL": "deepseek-chat",
    # Optional vision LLM used for OCR when online (OpenAI-compatible).
    "VISION_BASE_URL": "",
    "VISION_API_KEY": "",
    "VISION_MODEL": "",
    # auto = vision if configured and online, else local RapidOCR | local | vision
    "OCR_MODE": "auto",
    # auto = AI when online and a key is set, else offline rules | online | offline
    "AI_MODE": "auto",
    # AI ERP connection ("发送到 ERP"): the ERP address and a scan token the
    # user makes in the ERP (Scan documents > Connect Doc2Excel).
    "ERP_URL": "",
    "ERP_TOKEN": "",
    "WATCH_DIR": str(DOCS_HOME / "Inbox"),
    "SERVER_PORT": "8765",
    "UI_LANG": "zh",
}

SECRET_KEYS = {"LLM_API_KEY", "VISION_API_KEY", "ERP_TOKEN"}

_lock = threading.Lock()
_values: dict[str, str] = {}


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def load() -> dict[str, str]:
    with _lock:
        vals = dict(DEFAULTS)
        if ENV_PATH.exists():
            vals.update(_parse_env(ENV_PATH.read_text(encoding="utf-8-sig")))
        for k in DEFAULTS:  # D2X_<KEY> environment variables override .env (testing)
            if os.environ.get("D2X_" + k):
                vals[k] = os.environ["D2X_" + k]
        for k in ("WATCH_DIR", "SERVER_PORT", "UI_LANG", "OCR_MODE", "AI_MODE", "LLM_BASE_URL", "LLM_MODEL"):
            if not vals.get(k):
                vals[k] = DEFAULTS[k]
        _values.clear()
        _values.update(vals)
        return dict(_values)


def get(key: str) -> str:
    if not _values:
        load()
    return _values.get(key, DEFAULTS.get(key, ""))


def save(updates: dict[str, str]) -> None:
    """Update keys in .env, keeping comments and unknown lines intact."""
    with _lock:
        lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.exists() else []
        pending = {k: str(v) for k, v in updates.items() if k in DEFAULTS}
        for i, raw in enumerate(lines):
            s = raw.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k in pending:
                    lines[i] = f"{k}={pending.pop(k)}"
        for k, v in pending.items():
            lines.append(f"{k}={v}")
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load()


def public_settings() -> dict:
    """Settings for the UI. Secrets are never sent back, only whether set."""
    vals = load()
    out = {k: v for k, v in vals.items() if k in DEFAULTS and k not in SECRET_KEYS}
    for k in SECRET_KEYS:
        out[k + "_SET"] = bool(vals.get(k))
    out["ENV_PATH"] = str(ENV_PATH)
    return out


def ensure_dirs() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    watch = get("WATCH_DIR")
    if watch:
        try:
            Path(watch).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
