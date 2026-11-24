"""API keys in Windows Credential Manager (generic credentials).

Windows encrypts them with DPAPI under the current user's login, so they can
only be read by programs running as that same Windows user. A copied .env, a
backup or another account on the PC sees nothing.

Backends:
  * "credman" (default on Windows): win32cred.
  * "memory" (D2X_KEYSTORE=memory): in-process dict, for tests, so a test run
    never touches the real stored keys.
  * none: not on Windows / pywin32 missing, or D2X_KEYSTORE=off (tests that
    must leave the real .env untouched); config.py then keeps keys in .env.
"""
from __future__ import annotations

import os
import threading

# CredRead also imports win32timezone at run time (for LastWritten); the exe
# build must bundle it (build.ps1: --hidden-import win32timezone).
try:
    import pywintypes
    import win32cred
    import win32timezone  # noqa: F401
except ImportError:  # not Windows / no pywin32
    pywintypes = win32cred = None

_lock = threading.Lock()
_cache: dict[str, str] = {}
_memory: dict[str, str] = {}
MAX_CHARS = 1200  # CRED_MAX_CREDENTIAL_BLOB_SIZE is 2560 bytes (UTF-16)


class KeystoreError(Exception):
    pass


def backend() -> str:
    mode = os.environ.get("D2X_KEYSTORE", "").lower()
    if mode == "memory":
        return "memory"
    if mode == "off" or win32cred is None:
        return ""
    return "credman"


def available() -> bool:
    return bool(backend())


def get(target: str) -> str:
    """The stored value, or "" when there is none."""
    b = backend()
    if b == "memory":
        return _memory.get(target, "")
    if not b:
        return ""
    with _lock:
        if target in _cache:
            return _cache[target]
    try:
        c = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
        blob = c.get("CredentialBlob") or b""
        val = blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
    except pywintypes.error as e:
        if e.winerror != 1168:  # ERROR_NOT_FOUND
            raise KeystoreError(f"Credential Manager read failed: {e.strerror}") from e
        val = ""
    except Exception as e:  # never let a key lookup crash the app
        raise KeystoreError(f"Credential Manager read failed: {e!r}") from e
    with _lock:
        _cache[target] = val
    return val


def put(target: str, value: str, comment: str = "") -> None:
    """Store a value; an empty value deletes the entry."""
    value = value or ""
    if len(value) > MAX_CHARS:
        raise KeystoreError("key is too long for Credential Manager")
    b = backend()
    if b == "memory":
        if value:
            _memory[target] = value
        else:
            _memory.pop(target, None)
        return
    if not b:
        raise KeystoreError("Credential Manager is not available")
    try:
        if value:
            win32cred.CredWrite({
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": "Doc2Excel",
                "CredentialBlob": value,
                "Comment": comment[:250],
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,  # this PC only, never roams
            }, 0)
        else:
            try:
                win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC)
            except pywintypes.error as e:
                if e.winerror != 1168:
                    raise
    except pywintypes.error as e:
        raise KeystoreError(f"Credential Manager write failed: {e.strerror}") from e
    except Exception as e:
        raise KeystoreError(f"Credential Manager write failed: {e!r}") from e
    finally:
        with _lock:
            _cache.pop(target, None)
