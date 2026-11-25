"""HTTPS certificate checking for every outgoing request (AI, ERP, updates).

Python's own ssl only trusts root certificates already present in the Windows
store. Windows itself downloads missing roots on demand, so a PC that never
needed a new root (e.g. Let's Encrypt's "YR" chain used by GitHub's release
downloads) fails in Python with CERTIFICATE_VERIFY_FAILED while browsers work.
truststore hands verification to Windows (CryptoAPI), which fetches the root.
Certificates are still fully verified; this never turns checking off.
"""
from __future__ import annotations

import ssl
import threading

_lock = threading.Lock()
_ctx: ssl.SSLContext | None = None


def tls_context() -> ssl.SSLContext:
    global _ctx
    with _lock:
        if _ctx is None:
            try:
                import truststore
                _ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except Exception:  # truststore missing/unsupported: Python's default checks
                _ctx = ssl.create_default_context()
        return _ctx


def urlopen(req, timeout=None):
    import urllib.request
    return urllib.request.urlopen(req, timeout=timeout, context=tls_context())
