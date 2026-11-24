"""API keys in Windows Credential Manager (app/keystore.py + config.py).

Uses the REAL Credential Manager, but only under credential names tied to a
throw-away settings folder, and deletes them at the end. The user's own keys
(named after the real install folder) are never read or written.

    .venv\\Scripts\\python tests\\keystore_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="d2x_ks_"))
os.environ["LOCALAPPDATA"] = str(TMP / "appdata")
os.environ.pop("D2X_KEYSTORE", None)
for k in ("LLM_API_KEY", "VISION_API_KEY", "ERP_TOKEN"):
    os.environ.pop("D2X_" + k, None)
sys.path.insert(0, str(ROOT))

import pywintypes  # noqa: E402
import win32cred  # noqa: E402

from app import config, keystore  # noqa: E402

PASSED = 0


def ok(cond, msg):
    global PASSED
    if not cond:
        raise AssertionError(msg)
    PASSED += 1
    print("  ok  ", msg)


def cred(key):
    """Read straight from Windows, bypassing the app's cache."""
    try:
        c = win32cred.CredRead(config._target(key), win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as e:
        if e.winerror == 1168:
            return None
        raise
    return c


def env_text():
    return config.ENV_PATH.read_text(encoding="utf-8")


KEY1 = "sk-测试=#'\" key " + os.urandom(6).hex()
KEY2 = "sk-second-" + os.urandom(6).hex()
TOKEN = "erp-" + os.urandom(8).hex()


def main():
    config.ENV_PATH = TMP / "install" / ".env"
    config.ENV_PATH.parent.mkdir(parents=True)
    ok(str(TMP) in config._target("LLM_API_KEY"), "credential name is tied to the (temp) settings folder")
    ok(keystore.backend() == "credman", "real Credential Manager backend in use")

    print("migration of a plain-text key")
    config.ENV_PATH.write_text(
        "# my settings\nLLM_BASE_URL=https://api.deepseek.com\n"
        f"LLM_API_KEY={KEY1}\nLLM_MODEL=deepseek-chat\nERP_TOKEN=\nCUSTOM=keep\n", encoding="utf-8")
    vals = config.load()
    ok(vals["LLM_API_KEY"] == KEY1, "key still usable after the move")
    text = env_text()
    ok(KEY1 not in text and "LLM_API_KEY=\n" in text, ".env line blanked")
    ok("# my settings" in text and "CUSTOM=keep" in text and "LLM_MODEL=deepseek-chat" in text,
       "comments, other settings and unknown lines kept")
    c = cred("LLM_API_KEY")
    ok(c and c["CredentialBlob"].decode("utf-16-le") == KEY1, "key is in Credential Manager (special chars + Chinese intact)")
    ok(c["Persist"] == win32cred.CRED_PERSIST_LOCAL_MACHINE, "stored for this PC only (does not roam)")
    ok(cred("ERP_TOKEN") is None, "empty key: nothing stored")

    print("reload / save / delete")
    keystore._cache.clear()
    ok(config.load()["LLM_API_KEY"] == KEY1, "fresh load reads it back from Credential Manager")
    config.save({"LLM_API_KEY": KEY2, "ERP_TOKEN": TOKEN, "LLM_MODEL": "deepseek-v4"})
    text = env_text()
    ok(config.get("LLM_API_KEY") == KEY2 and config.get("ERP_TOKEN") == TOKEN, "saved keys are used")
    ok(KEY2 not in text and TOKEN not in text, "saved keys are not written to .env")
    ok("LLM_MODEL=deepseek-v4" in text, "normal settings still go to .env")
    ok(cred("LLM_API_KEY")["CredentialBlob"].decode("utf-16-le") == KEY2, "Credential Manager updated")
    ok(config.public_settings()["KEY_STORAGE"] == "credman" and "LLM_API_KEY" not in config.public_settings(),
       "settings API reports the storage, never the key")
    config.save({"ERP_TOKEN": ""})
    ok(cred("ERP_TOKEN") is None and config.get("ERP_TOKEN") == "", "empty value deletes the key")

    print("copied .env is useless elsewhere")
    other = TMP / "copy" / ".env"
    other.parent.mkdir()
    other.write_text(env_text(), encoding="utf-8")
    real = config.ENV_PATH
    config.ENV_PATH = other
    ok(config.load()["LLM_API_KEY"] == "", "a copy of .env in another folder has no key")
    config.ENV_PATH = real

    print("fallback when Credential Manager fails")
    orig = keystore.put

    def broken(*a, **kw):
        raise keystore.KeystoreError("simulated failure")
    keystore.put = broken
    try:
        config.ENV_PATH.write_text(f"VISION_API_KEY=vk-plain\n", encoding="utf-8")
        ok(config.load()["VISION_API_KEY"] == "vk-plain" and "VISION_API_KEY=vk-plain" in env_text(),
           "write fails: key kept in .env, nothing lost")
    finally:
        keystore.put = orig
    ok(config.load()["VISION_API_KEY"] == "vk-plain" and "vk-plain" not in env_text(),
       "next load moves it once Credential Manager works again")

    print("D2X_KEYSTORE=off leaves .env alone")
    os.environ["D2X_KEYSTORE"] = "off"
    try:
        config.ENV_PATH.write_text("LLM_API_KEY=plain-off\n", encoding="utf-8")
        ok(config.load()["LLM_API_KEY"] == "plain-off" and "plain-off" in env_text(), "off: plain text untouched")
        ok(config.public_settings()["KEY_STORAGE"] == "env", "off: settings say keys are in .env")
    finally:
        os.environ.pop("D2X_KEYSTORE")


if __name__ == "__main__":
    try:
        main()
    finally:
        for p in (TMP / "install", TMP / "copy"):
            config.ENV_PATH = p / ".env"
            for k in config.SECRET_KEYS:
                keystore.put(config._target(k), "")
        config.ENV_PATH = TMP / "install" / ".env"
        left = [k for k in config.SECRET_KEYS if cred(k)]
        print("cleanup:", "all test credentials removed" if not left else f"LEFT {left}")
    print(f"\nALL {PASSED} CHECKS PASSED")
