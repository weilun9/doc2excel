"""Paths and settings. Settings live in a `.env` file next to the exe (or the
project root when running from source); the in-app settings screen writes back
to it. API keys (SECRET_KEYS) are kept in Windows Credential Manager instead
(see keystore.py): a key found in plain text in .env is moved there on the next
load and its line is blanked. Without Credential Manager they stay in .env."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from . import keystore

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
    # SmartAcc DynaMod supplier-bill import ("DynaMod 进单"):
    #   the user's own 进单整理模板 (供应商 + 条码对照 sheets, stays on this PC),
    #   DynaMod's blank import templates, the import-file folder, and Location ID.
    "DYNAMOD_WORKBOOK": "",
    "DYNAMOD_TEMPLATE_DIR": r"C:\SmartAcc\DynaMod\Import Template",
    "DYNAMOD_OUT_DIR": r"C:\Opp",
    "DYNAMOD_LOCATION": "HQ",
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


def _target(key: str) -> str:
    """Credential name, one set of keys per settings file (install folder)."""
    return f"Doc2Excel:{key}:{ENV_PATH.resolve().parent}"


def _write_env_lines(updates: dict[str, str], append: bool) -> None:
    """Set KEY=value in .env, keeping comments and unknown lines. Caller holds _lock."""
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.exists() else []
    pending = dict(updates)
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in pending:
                lines[i] = f"{k}={pending.pop(k)}"
    if append:
        for k, v in pending.items():
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _store_secrets(secrets: dict[str, str]) -> bool:
    """Put keys into Credential Manager. False (and nothing half-done that
    matters) when it is unavailable or fails; the caller then keeps .env."""
    if not keystore.available():
        return False
    try:
        for k, v in secrets.items():
            keystore.put(_target(k), v, f"Doc2Excel {k} ({ENV_PATH.parent})")
    except keystore.KeystoreError as e:
        print("keystore:", e)
        return False
    return True


def key_storage() -> str:
    return "credman" if keystore.available() else "env"


def load() -> dict[str, str]:
    with _lock:
        vals = dict(DEFAULTS)
        file_vals = _parse_env(ENV_PATH.read_text(encoding="utf-8-sig")) if ENV_PATH.exists() else {}
        vals.update(file_vals)
        # Plain-text keys in .env (hand-edited, or from an older version): move
        # them into Credential Manager and blank them in the file.
        plain = {k: file_vals[k] for k in SECRET_KEYS if file_vals.get(k)}
        if plain and _store_secrets(plain):
            _write_env_lines({k: "" for k in plain}, append=False)
            plain = {}
        if keystore.available():
            for k in SECRET_KEYS:
                if k not in plain:
                    try:
                        vals[k] = keystore.get(_target(k))
                    except keystore.KeystoreError as e:
                        print("keystore:", e)
                        vals[k] = ""
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
    """Update settings in .env, keeping comments and unknown lines intact.
    API keys go to Credential Manager (an empty value deletes the key)."""
    with _lock:
        pending = {k: str(v) for k, v in updates.items() if k in DEFAULTS}
        secrets = {k: v for k, v in pending.items() if k in SECRET_KEYS}
        if secrets and _store_secrets(secrets):
            for k in secrets:
                pending.pop(k)
            if ENV_PATH.exists():  # blank any old plain-text line
                _write_env_lines({k: "" for k in secrets}, append=False)
        if pending:
            _write_env_lines(pending, append=True)
    load()


def public_settings() -> dict:
    """Settings for the UI. Secrets are never sent back, only whether set."""
    vals = load()
    out = {k: v for k, v in vals.items() if k in DEFAULTS and k not in SECRET_KEYS}
    for k in SECRET_KEYS:
        out[k + "_SET"] = bool(vals.get(k))
    out["ENV_PATH"] = str(ENV_PATH)
    out["KEY_STORAGE"] = key_storage()
    return out


def ensure_dirs() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    watch = get("WATCH_DIR")
    if watch:
        try:
            Path(watch).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
