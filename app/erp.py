"""Client for the AI ERP scan inbox ("发送到 ERP").

Contract (ERP side):
  GET  {ERP_URL}/api/v1/inbox/remote/ping   Authorization: Bearer <token>
       200 {"ok": true, "user", "company_id", "queue"}; 401 = token missing/revoked.
  POST {ERP_URL}/api/v1/inbox/remote        multipart: file (+ optional hint)
       201 queued inbox file; 200 {"already_uploaded": true, ...} = sent before.
       Errors: {"error": {"code", "message", "details"}}. Max 20 MB.
       Allowed: JPEG / PNG / WebP / PDF (magic-byte checked).
Only the original file is sent; the ERP runs its own OCR + AI on it.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import config, net

MAX_BYTES = 20 * 1024 * 1024
DIRECT_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".pdf": "application/pdf"}
# Only send a hint when Doc2Excel's type maps cleanly; otherwise let the ERP decide.
HINTS = {"invoice": "INVOICE", "statement": "BANK", "credit_note": "CN", "return_note": "RETURN_NOTE"}


class ERPError(RuntimeError):
    def __init__(self, message: str, code: str = "error", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def configured() -> bool:
    return bool(config.get("ERP_URL") and config.get("ERP_TOKEN"))


def _base() -> str:
    url = config.get("ERP_URL").strip().rstrip("/")
    if not url.lower().startswith(("http://", "https://")):
        raise ERPError("ERP address must start with http:// or https://", "bad_url")
    return url


def _request(method: str, path: str, body: bytes | None = None, content_type: str | None = None,
             timeout: float = 30) -> tuple[int, dict]:
    if not configured():
        raise ERPError("ERP address or token not set", "not_configured")
    headers = {"Authorization": f"Bearer {config.get('ERP_TOKEN')}", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(_base() + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=net.tls_context()) as r:
            return r.status, _json(r.read())
    except urllib.error.HTTPError as e:
        data = _json(e.read())
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        if e.code == 401:
            raise ERPError("Token missing, revoked or not allowed. Make a new token in the ERP "
                           "(Scan documents > Connect Doc2Excel).", "unauthorized", 401) from e
        msg = err.get("message") or data.get("detail") or f"HTTP {e.code}"
        raise ERPError(str(msg), str(err.get("code") or f"http_{e.code}"), e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ERPError(f"Cannot reach the ERP at {config.get('ERP_URL')}: {reason}", "unreachable") from e


def _json(raw: bytes) -> dict:
    try:
        v = json.loads(raw.decode("utf-8") or "{}")
        return v if isinstance(v, dict) else {"data": v}
    except ValueError:
        return {}


def ping() -> dict:
    _, data = _request("GET", "/api/v1/inbox/remote/ping", timeout=10)
    return data


def _payload(path: Path, name: str) -> tuple[bytes, str, str]:
    """File bytes ready for the ERP: allowed types as-is; anything else (HEIC,
    TIFF, BMP, GIF) or an over-size image is re-encoded as JPEG."""
    ext = path.suffix.lower()
    raw = path.read_bytes()
    if ext in DIRECT_EXT and len(raw) <= MAX_BYTES:
        return raw, name, DIRECT_EXT[ext]
    if ext == ".pdf":
        raise ERPError("PDF is larger than the ERP's 20 MB limit", "too_large")
    from PIL import Image, ImageOps
    from . import ocr  # registers the HEIC opener
    _ = ocr
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    for side, quality in ((None, 90), (4000, 85), (3000, 82), (2200, 80)):
        im = img
        if side and max(im.size) > side:
            im = im.copy()
            im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        if buf.tell() <= MAX_BYTES:
            return buf.getvalue(), Path(name).stem + ".jpg", "image/jpeg"
    raise ERPError("Image is too large to send even after compression", "too_large")


def send(doc: dict) -> dict:
    """Send one document's original file. Returns {"state": "sent"|"already", ...}."""
    data, filename, ctype = _payload(Path(doc["file"]), doc.get("name") or Path(doc["file"]).name)
    hint = HINTS.get(((doc.get("table") or {}).get("doc_type") or "").lower())
    boundary = "----d2x" + uuid.uuid4().hex
    safe_name = filename.replace('"', "_").replace("\r", "").replace("\n", "")
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
             f"Content-Type: {ctype}\r\n\r\n".encode("utf-8"), data, b"\r\n"]
    if hint:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="hint"\r\n\r\n{hint}\r\n'.encode())
    parts.append(f"--{boundary}--\r\n".encode())
    try:
        status, res = _request("POST", "/api/v1/inbox/remote", b"".join(parts),
                               f"multipart/form-data; boundary={boundary}", timeout=120)
    except ERPError as e:
        # A server may refuse a bad token with 401 before it has read the
        # upload and close the socket; we then see "connection aborted"
        # instead of the 401. Ask the cheap ping (no body) what is wrong.
        if e.code != "unreachable":
            raise
        try:
            ping()
        except ERPError as why:
            if why.code == "unauthorized":
                raise why from e
        raise
    if res.get("already_uploaded"):
        return {"state": "already", "erp_id": res.get("id"), "hint": hint}
    return {"state": "sent", "erp_id": res.get("id"), "hint": hint, "status": status}
