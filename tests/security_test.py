"""Security tests for Doc2Excel's local server, exporter and file handling.

Runs a real server in-process on a free port with throw-away data folders and a
fake API key (the real .env is never read or written), then attacks it the way a
malicious website, another device on the LAN, or a booby-trapped document would.

    .venv\\Scripts\\python tests\\security_test.py
"""
from __future__ import annotations

import http.client
import io
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="d2x_sec_"))
os.environ["LOCALAPPDATA"] = str(TMP / "appdata")       # isolate state + inbox
os.environ["D2X_AI_MODE"] = "offline"                   # never call a real API
os.environ["D2X_KEYSTORE"] = "memory"                   # never touch the real Credential Manager
os.environ["D2X_WATCH_DIR"] = str(TMP / "watch")
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

FAKE_KEY = "sk-test-" + uuid.uuid4().hex
config.ENV_PATH = TMP / ".env"
config.ENV_PATH.write_text(
    f"LLM_BASE_URL=https://api.deepseek.com\nLLM_API_KEY={FAKE_KEY}\nLLM_MODEL=deepseek-chat\n",
    encoding="utf-8")
config.load()
config.ensure_dirs()

from openpyxl import load_workbook  # noqa: E402
from PIL import Image  # noqa: E402

from app import exporter, sources  # noqa: E402
from app.server import AppServer  # noqa: E402
from app.store import Store  # noqa: E402

store = Store()
store.start_worker()
srv = AppServer(store, 18765)
PORT = srv.start()
LAN = next((ip for ip in sources.lan_ips() if not ip.startswith("127.")), None)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, bool(ok), detail))


def req(method, path, *, host="127.0.0.1", host_header=None, headers=None, body=None, ip="127.0.0.1"):
    conn = http.client.HTTPConnection(ip, PORT, timeout=20)
    h = {"Host": host_header or f"{host}:{PORT}"}
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, dict(r.getheaders()), data


def multipart(files: list[tuple[str, bytes, str]]):
    b = "----d2x" + uuid.uuid4().hex
    out = io.BytesIO()
    for name, content, ctype in files:
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
                  f"Content-Type: {ctype}\r\n\r\n".encode())
        out.write(content + b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), {"Content-Type": f"multipart/form-data; boundary={b}"}


def png_bytes(w=200, h=80):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


KEY = {"X-D2X-Key": srv.api_key}

# ---------- 1. desktop API access control ----------
s, _, _ = req("GET", "/api/state")
check("API without key is refused (CSRF / other local apps)", s == 403, f"status {s}")
s, _, _ = req("GET", "/api/state", headers=KEY)
check("API with key works", s == 200, f"status {s}")
s, _, _ = req("GET", "/api/state", headers=KEY, host_header=f"evil.example:{PORT}")
check("DNS rebinding (foreign Host header) is refused", s == 403, f"status {s}")
s, _, _ = req("GET", "/", host_header=f"evil.example:{PORT}")
check("Desktop page refused under foreign Host", s == 403, f"status {s}")
s, _, _ = req("GET", "/api/state", headers={"X-D2X-Key": "wrong"})
check("Wrong API key is refused", s == 403, f"status {s}")
s, _, page = req("GET", "/")
check("Desktop page carries the per-launch key", s == 200 and srv.api_key.encode() in page, f"status {s}")
if LAN:
    s, _, _ = req("GET", "/api/state", headers=KEY, ip=LAN, host=LAN)
    check(f"API from LAN address ({LAN}) is refused even with key", s == 403, f"status {s}")
    s, _, _ = req("GET", "/", ip=LAN, host=LAN)
    check("Desktop page from LAN is refused", s == 403, f"status {s}")
    s, _, _ = req("GET", "/static/app.js", ip=LAN, host=LAN)
    check("Desktop assets from LAN are refused", s == 403, f"status {s}")

# CSRF-style simple form post (what a malicious web page can send without preflight)
body, h = multipart([("x.png", png_bytes(), "image/png")])
s, _, _ = req("POST", "/api/upload", headers=h, body=body)
check("Cross-site form upload without key is refused", s == 403, f"status {s}")
s, _, _ = req("POST", "/api/scan", headers={"Content-Type": "text/plain"}, body=b"")
check("Cross-site trigger of scanner dialog is refused", s == 403, f"status {s}")
s, _, _ = req("POST", "/api/add-paths", headers={"Content-Type": "text/plain"},
              body=json.dumps({"paths": [str(config.ENV_PATH)]}).encode())
check("Cross-site local-file import is refused", s == 403, f"status {s}")

# ---------- 2. secrets ----------
s, _, data = req("GET", "/api/settings", headers=KEY)
check("Settings API never returns the API key", s == 200 and FAKE_KEY.encode() not in data, f"status {s}")
s, _, data = req("GET", "/api/state", headers=KEY)
check("State API never returns the API key", FAKE_KEY.encode() not in data, "")
check("Plain-text key in .env is moved to the key store and blanked in .env",
      FAKE_KEY not in config.ENV_PATH.read_text(encoding="utf-8")
      and "LLM_API_KEY=\n" in config.ENV_PATH.read_text(encoding="utf-8")
      and config.get("LLM_API_KEY") == FAKE_KEY, "")
s, _, data = req("POST", "/api/settings", headers={**KEY, "Content-Type": "application/json"},
                 body=json.dumps({"LLM_BASE_URL": "https://attacker.example/v1"}).encode())
env_after = config.ENV_PATH.read_text(encoding="utf-8")
check("Moving API URL to a new host without re-entering key is refused",
      s == 400 and b"key_required" in data and "attacker.example" not in env_after, f"status {s}")
s, _, _ = req("POST", "/api/settings", headers={**KEY, "Content-Type": "application/json"},
              body=json.dumps({"EVIL_KEY": "x", "LLM_MODEL": "deepseek-chat"}).encode())
check("Unknown settings keys are ignored", "EVIL_KEY" not in config.ENV_PATH.read_text(encoding="utf-8"), "")
s, _, _ = req("GET", "/static/../.env", headers=KEY)
s2, _, d2 = req("GET", "/static/..%2f..%2f.env", headers=KEY)
check("Path traversal via /static is blocked", s in (403, 404) and s2 in (403, 404) and FAKE_KEY.encode() not in d2,
      f"status {s}/{s2}")

# ---------- 3. phone upload page ----------
tok = srv.token
s, _, _ = req("GET", "/m/wrongtoken", ip=LAN or "127.0.0.1", host=LAN or "127.0.0.1")
check("Phone page with wrong token is 404", s == 404, f"status {s}")
s, hdr, _ = req("GET", f"/m/{tok}", ip=LAN or "127.0.0.1", host=LAN or "127.0.0.1")
check("Phone page with token works", s == 200, f"status {s}")
check("Security headers set (CSP, nosniff, no-referrer, no framing)",
      "Content-Security-Policy" in hdr and hdr.get("X-Content-Type-Options") == "nosniff"
      and hdr.get("Referrer-Policy") == "no-referrer" and hdr.get("X-Frame-Options") == "DENY", "")
check("Phone token has >= 64 bits of randomness", len(tok) >= 12, f"len {len(tok)}")

body, h = multipart([("evil.exe", b"MZ\x90\x00", "application/octet-stream"),
                     ("script.py", b"print(1)", "text/plain"),
                     ("x.html", b"<script>alert(1)</script>", "text/html")])
s, _, data = req("POST", f"/m/{tok}/upload", headers=h, body=body)
res = json.loads(data)
check("Phone upload rejects non-document types (.exe .py .html)",
      s == 200 and not res["added"] and len(res["rejected"]) == 3, str(res))

xss = '<img src=x onerror=alert(1)>.png'
trav = '..\\..\\..\\Windows\\evil.png'
body, h = multipart([(xss, png_bytes(), "image/png"), (trav, png_bytes(), "image/png")])
s, _, data = req("POST", f"/m/{tok}/upload", headers=h, body=body)
res = json.loads(data)
stored = [Path(store.get(i)["file"]) for i in res["added"]]
check("Uploaded files are stored only inside the inbox under random names",
      len(stored) == 2 and all(p.parent == config.INBOX_DIR and p.stem in res["added"] for p in stored),
      str([str(p) for p in stored]))
check("Traversal filename did not write outside the inbox",
      not (Path("C:/Windows/evil.png").exists()), "")
wrong_tok = req("POST", "/m/wrongtoken/upload", headers=h, body=body)[0]
check("Phone upload with wrong token is refused", wrong_tok == 404, f"status {wrong_tok}")

big = b"0" * (81 * 1024 * 1024)
body, h = multipart([("big.png", big, "image/png")])
try:
    s, _, _ = req("POST", f"/m/{tok}/upload", headers=h, body=body)
except (ConnectionError, OSError):
    s = 413  # server closed the connection after rejecting the size
check("Uploads over 80 MB are rejected", s == 413, f"status {s}")

# Decompression bomb: tiny file that claims a 40000x40000 image
bomb = io.BytesIO()
Image.new("1", (40000, 40000)).save(bomb, "PNG")
body, h = multipart([("bomb.png", bomb.getvalue(), "image/png")])
s, _, data = req("POST", f"/m/{tok}/upload", headers=h, body=body)
bomb_id = json.loads(data)["added"][0]
for _ in range(120):
    st = store.get(bomb_id)["status"]
    if st in ("ready", "error"):
        break
    time.sleep(0.5)
d = store.get(bomb_id)
check("Decompression-bomb image fails safely (no crash / memory blow-up)",
      d["status"] == "error" and "Decompression" in (d["error"] or ""), f"{d['status']}: {d['error']}")
# worker still alive afterwards?
body, h = multipart([("after.png", png_bytes(), "image/png")])
_, _, data = req("POST", f"/m/{tok}/upload", headers=h, body=body)
after_id = json.loads(data)["added"][0]
for _ in range(120):
    if store.get(after_id)["status"] in ("ready", "error"):
        break
    time.sleep(0.5)
check("Worker keeps running after a malicious file", store.get(after_id)["status"] == "ready",
      store.get(after_id)["status"])

# ---------- 4. exported files ----------
evil_doc = {"name": "evil.png", "table": {
    "title": "=cmd|' /C calc'!A0", "doc_type": "invoice",
    "fields": [{"key": "@SUM(1+1)", "value": "=HYPERLINK(\"http://attacker.example\",\"click\")"}],
    "columns": ["Item", "Amount"], "rows": [["+1+1", "-12.50"], ["=1+1", "1,234.00"]]}}
out = exporter.export([evil_doc], TMP / "evil.xlsx", "both")
wb = load_workbook(out)
formulas = [(ws.title, c.coordinate, c.value) for ws in wb for row in ws.iter_rows() for c in row
            if c.data_type == "f"]
check("Excel export never creates formulas from document text", not formulas, str(formulas[:3]))
vals = [c.value for ws in wb for row in ws.iter_rows() for c in row if c.value is not None]
check("Real numbers still export as numbers (-12.50, 1,234.00)", -12.5 in vals and 1234.0 in vals, "")

# ---------- 5. Send to ERP ----------
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

ERP_TOKEN = "erp-test-" + uuid.uuid4().hex
received: list[dict] = []
seen_hashes: set[int] = set()


class FakeERP(BaseHTTPRequestHandler):
    """Implements the ERP contract: Bearer auth, ping, multipart upload, duplicate detection."""

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if self.headers.get("Authorization") != f"Bearer {ERP_TOKEN}":
            self._reply(401, {"error": {"code": "unauthorized", "message": "bad token", "details": None}})
            return False
        return True

    def do_GET(self):
        if self.path == "/api/v1/inbox/remote/ping" and self._authed():
            self._reply(200, {"ok": True, "user": "tester", "company_id": "c1", "queue": len(received)})

    def do_POST(self):
        if self.path != "/api/v1/inbox/remote" or not self._authed():
            return
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        ctype = self.headers["Content-Type"]
        from email.parser import BytesParser
        from email.policy import default
        msg = BytesParser(policy=default).parsebytes(f"Content-Type: {ctype}\r\n\r\n".encode() + raw)
        fields = {}
        for part in msg.iter_parts():
            fields[part.get_param("name", header="content-disposition")] = (
                part.get_filename(), part.get_content_type(), part.get_payload(decode=True))
        fname, ftype, data = fields["file"]
        h = hash(data)
        if h in seen_hashes:
            return self._reply(200, {"already_uploaded": True, "id": "dup"})
        seen_hashes.add(h)
        received.append({"name": fname, "type": ftype, "magic": data[:4], "size": len(data),
                         "hint": fields.get("hint", (None, None, b""))[2].decode() or None})
        self._reply(201, {"id": f"f{len(received)}", "status": "NEW", "source": "DOC2EXCEL"})


erp_srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeERP)
threading.Thread(target=erp_srv.serve_forever, daemon=True).start()
ERP_URL = f"http://127.0.0.1:{erp_srv.server_address[1]}"
JSON_H = {**KEY, "Content-Type": "application/json"}
s, _, _ = req("POST", "/api/settings", headers=JSON_H,
              body=json.dumps({"ERP_URL": ERP_URL, "ERP_TOKEN": ERP_TOKEN}).encode())
check("ERP settings can be saved", s == 200, f"status {s}")
check("ERP token saved from Settings is not written to .env",
      ERP_TOKEN not in config.ENV_PATH.read_text(encoding="utf-8") and config.get("ERP_TOKEN") == ERP_TOKEN, "")

# access control on the new routes
s1, _, _ = req("POST", "/api/erp/ping", headers={"Content-Type": "text/plain"}, body=b"")
s2, _, _ = req("POST", f"/api/doc/{after_id}/erp", headers={"Content-Type": "text/plain"}, body=b"")
s3, _, _ = req("POST", "/api/erp/ping", headers=JSON_H, body=b"{}", host_header=f"evil.example:{PORT}")
check("ERP routes refuse requests without the desktop key", s1 == 403 and s2 == 403, f"{s1}/{s2}")
check("ERP routes refuse a foreign Host (DNS rebinding)", s3 == 403, f"{s3}")
if LAN:
    s4, _, _ = req("POST", f"/api/doc/{after_id}/erp", headers=JSON_H, body=b"{}", ip=LAN, host=LAN)
    check("ERP send refused from the LAN even with key", s4 == 403, f"{s4}")

# the ERP token never leaves the PC process
leaks = []
for path, hdrs, ip_ in (("/api/settings", KEY, "127.0.0.1"), ("/api/state", KEY, "127.0.0.1"),
                        (f"/api/doc/{after_id}", KEY, "127.0.0.1"), ("/", {}, "127.0.0.1"),
                        (f"/m/{tok}", {}, LAN or "127.0.0.1")):
    _, _, data = req("GET", path, headers=hdrs, ip=ip_, host=ip_)
    if ERP_TOKEN.encode() in data:
        leaks.append(path)
check("ERP token is never returned to the UI or the phone page", not leaks, str(leaks))
s, _, data = req("POST", "/api/settings", headers=JSON_H,
                 body=json.dumps({"ERP_URL": "https://attacker.example"}).encode())
check("Moving ERP address to a new host requires re-entering the token",
      s == 400 and b"key_required" in data, f"status {s}")

# behaviour against the contract
_, _, data = req("POST", "/api/erp/ping", headers=JSON_H, body=b"{}")
r = json.loads(data)
check("Test connection reports user and queue", r.get("ok") and r.get("user") == "tester", str(r))
_, _, data = req("POST", f"/api/doc/{after_id}/erp", headers=JSON_H, body=b"{}")
r1 = json.loads(data)
_, _, data = req("POST", f"/api/doc/{after_id}/erp", headers=JSON_H, body=b"{}")
r2 = json.loads(data)
check("Send queues the original file (201 -> sent)", r1.get("state") == "sent" and received
      and received[0]["magic"].startswith(b"\x89PNG"), str(r1))
check("Sending the same file again shows 'already in ERP', not an error", r2.get("state") == "already", str(r2))
_, _, data = req("GET", "/api/state", headers=KEY)
st_doc = next(x for x in json.loads(data)["docs"] if x["id"] == after_id)
check("Document shows its ERP status", st_doc.get("erp_state") == "already", str(st_doc.get("erp_state")))

# a format the ERP does not accept (BMP) is converted to JPEG before sending
bmp = io.BytesIO()
Image.new("RGB", (300, 120), "white").save(bmp, "BMP")
body, h = multipart([("scan.bmp", bmp.getvalue(), "image/bmp")])
_, _, data = req("POST", "/api/upload", headers={**KEY, **h}, body=body)
bmp_id = json.loads(data)["added"][0]
_, _, data = req("POST", f"/api/doc/{bmp_id}/erp", headers=JSON_H, body=b"{}")
check("Unsupported formats (BMP/HEIC/TIFF) are sent as JPEG",
      json.loads(data).get("state") == "sent" and received[-1]["magic"].startswith(b"\xff\xd8")
      and received[-1]["name"].endswith(".jpg"), str(received[-1]))

# a revoked token gives a clear message, stored on the document
config.save({"ERP_TOKEN": "revoked-token"})
_, _, data = req("POST", f"/api/doc/{bmp_id}/erp", headers=JSON_H, body=b"{}")
r = json.loads(data)
check("Revoked token -> clear 'make a new token' message", r.get("code") == "unauthorized"
      and "new token" in r.get("error", ""), str(r))
erp_srv.shutdown()


# a server that refuses a bad token BEFORE reading the upload and drops the
# connection: the client sees "connection aborted", not the 401
class EarlyReject(FakeERP):
    def do_POST(self):
        self._reply(401, {"error": {"code": "unauthorized", "message": "bad token", "details": None}})
        self.close_connection = True
        import socket as _s
        try:
            self.connection.shutdown(_s.SHUT_RDWR)
        except OSError:
            pass

    def do_GET(self):
        self._authed()  # ping: 401 for the revoked token


early = ThreadingHTTPServer(("127.0.0.1", 0), EarlyReject)
threading.Thread(target=early.serve_forever, daemon=True).start()
config.save({"ERP_URL": f"http://127.0.0.1:{early.server_address[1]}"})
config.save({"ERP_TOKEN": "revoked-token"})
big = TMP / "big.png"
Image.effect_noise((2600, 2600), 80).convert("RGB").save(big)  # a few MB: still uploading when refused
from app import erp as _erp  # noqa: E402
try:
    _erp.send({"file": str(big), "name": "big.png"})
    code = "sent?"
except _erp.ERPError as e:
    code = e.code
check("Token refused mid-upload (connection cut) still says 'make a new token'", code == "unauthorized", code)
early.shutdown()

# ---------- report ----------
srv.stop()
width = max(len(n) for n, _, _ in results)
fails = 0
for name, ok, detail in results:
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {'' if ok else detail}")
print(f"\n{len(results) - fails}/{len(results)} passed")
sys.exit(1 if fails else 0)
