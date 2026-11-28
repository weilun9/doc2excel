"""Destinations and editions.

Runs a real server in-process (throw-away folders, in-memory key store) and
checks the destination contract for the edition picked by D2X_EDITION
(internal by default):

    .venv\\Scripts\\python tests\\destinations_test.py
    set D2X_EDITION=public&& .venv\\Scripts\\python tests\\destinations_test.py
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="d2x_dest_"))
os.environ["LOCALAPPDATA"] = str(TMP / "appdata")
os.environ["D2X_AI_MODE"] = "offline"
os.environ["D2X_KEYSTORE"] = "memory"
os.environ["D2X_WATCH_DIR"] = str(TMP / "watch")
sys.path.insert(0, str(ROOT))

from app import config, edition  # noqa: E402

config.ENV_PATH = TMP / ".env"
config.ENV_PATH.write_text("LLM_MODEL=deepseek-chat\n", encoding="utf-8")
config.load()
config.ensure_dirs()

from app.destinations import registry  # noqa: E402
from app.destinations.base import Destination, Result, SendError  # noqa: E402
from app.server import AppServer  # noqa: E402
from app.store import Store  # noqa: E402

failures = []


def ok(cond, name, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  ({detail})"))
    if not cond:
        failures.append(name)


# ---- a fake destination, registered only in this test process ----
class Fake(Destination):
    id = "fake"

    def __init__(self):
        self.sent = []

    def send(self, docs, options):
        if options.get("explode"):
            raise SendError("fake_down", "the fake service is down")
        out = []
        for d in docs:
            self.sent.append(d["id"])
            bad = d["name"].startswith("bad")
            out.append(Result(d["id"], not bad, "failed" if bad else "sent", "rejected" if bad else ""))
        return out, {"batch": len(docs)}


FAKE = Fake()
registry._load()["fake"] = FAKE

store = Store()


def add_doc(name):
    doc_id = uuid.uuid4().hex[:12]
    with store.lock:
        store.docs[doc_id] = {
            "id": doc_id, "name": name, "source": "local", "added": time.time(), "status": "ready",
            "progress": "", "pages": 1, "text": "TOTAL 10.00", "confirmed": True,
            "table": {"doc_type": "invoice", "title": name, "fields": [{"key": "Total", "value": "10.00"}],
                      "columns": ["Item", "Amount"], "rows": [["Thing", "10.00"]]},
        }
        store.order.insert(0, doc_id)
    store.save()
    return doc_id


good, bad = add_doc("good.jpg"), add_doc("bad.jpg")
srv = AppServer(store, 18795)
PORT = srv.start()
KEY = {"X-D2X-Key": srv.api_key, "Content-Type": "application/json"}


def req(method, path, headers=None, body=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Host": host or f"127.0.0.1:{PORT}"}
    h.update(headers or {})
    conn.request(method, path, body=json.dumps(body).encode() if isinstance(body, dict) else body, headers=h)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    try:
        return r.status, json.loads(data or b"null")
    except ValueError:
        return r.status, data


print(f"edition: {edition.ID} ({edition.NAME}); destinations {edition.DESTINATIONS}")

# ---- the API is local-only and keyed ----
s1, _ = req("GET", "/api/destinations")
s2, _ = req("GET", "/api/destinations", headers=KEY, host=f"evil.example:{PORT}")
s3, _ = req("POST", "/api/destinations/fake/send", headers={"Content-Type": "application/json"}, body={"ids": [good]})
ok(s1 == 403 and s2 == 403 and s3 == 403, "destination routes refuse no key / foreign Host", f"{s1}/{s2}/{s3}")

# ---- this edition's list ----
s, lst = req("GET", "/api/destinations", headers=KEY)
ids = [d["id"] for d in lst] if s == 200 else []
ok(ids[:-1] == list(edition.DESTINATIONS) and ids[-1] == "fake", "lists exactly this edition's destinations", ids)
s, st = req("GET", "/api/state", headers=KEY)
ok(st.get("edition") == edition.ID and st.get("app_name") == edition.NAME, "state names the edition", st.get("app_name"))

# ---- generic send: results and delivery records ----
s, r = req("POST", "/api/destinations/fake/send", headers=KEY, body={"ids": [good, bad, "nope"]})
ok(s == 200 and r.get("batch") == 2 and len(r.get("results", [])) == 2, "send skips unknown ids, returns per-doc results", r)
dl = {d["id"]: d.get("deliveries") or {} for d in store.summary()}
ok(dl[good].get("fake", {}).get("state") == "sent", "a delivery is recorded per document", dl[good])
ok(dl[bad].get("fake", {}).get("state") == "failed" and dl[bad]["fake"].get("message") == "rejected",
   "a failure is recorded with its reason", dl[bad])
s, r = req("POST", "/api/destinations/fake/send", headers=KEY, body={"ids": [good], "options": {"explode": True}})
ok(s == 400 and r.get("error") == "fake_down", "a batch-level failure is a clean 400", (s, r))
s, r = req("POST", "/api/destinations/fake/send", headers=KEY, body={"ids": [good], "options": "x"})
ok(s == 400 and r.get("error") == "bad_options", "options must be an object", (s, r))
s, _ = req("POST", "/api/destinations/nope/send", headers=KEY, body={"ids": [good]})
ok(s == 404, "unknown destination -> 404", s)
s, r = req("POST", "/api/destinations/fake/send", headers=KEY, body={"ids": []})
ok(s == 400 and r.get("error") == "no_ready_docs", "nothing to send -> 400", (s, r))

# ---- Excel through the generic route ----
out = TMP / "out.xlsx"
s, r = req("POST", "/api/destinations/excel/send", headers=KEY, body={"ids": [good], "options": {"path": str(out)}})
ok(s == 200 and out.is_file(), "Excel writes the workbook", (s, r))
exp = next(d for d in store.summary() if d["id"] == good)
ok(bool(exp.get("exported_at")) and exp["deliveries"]["excel"]["state"] == "exported", "Excel marks the document exported")
s, r = req("POST", "/api/destinations/excel/send", headers=KEY, body={"ids": [good]})
ok(s == 400 and r.get("error") == "path_required", "Excel needs a path", (s, r))

# ---- edition-specific ----
dm_routes = ("/api/dynamod/export", "/api/dynamod/from_workbook", "/api/erp/ping", f"/api/doc/{good}/erp")
codes = [req("POST", p, headers=KEY, body={})[0] for p in dm_routes]
if edition.has("erp"):
    token = "erp-" + uuid.uuid4().hex
    s, _ = req("POST", "/api/settings", headers=KEY, body={"ERP_URL": "http://127.0.0.1:9", "ERP_TOKEN": token})
    env = config.ENV_PATH.read_text(encoding="utf-8")
    _, pub = req("GET", "/api/settings", headers=KEY)
    _, lst = req("GET", "/api/destinations", headers=KEY)
    _, stt = req("GET", "/api/state", headers=KEY)
    ok(s == 200 and token not in env, "ERP token is not written to .env")
    ok(token not in json.dumps(pub) + json.dumps(lst) + json.dumps(stt), "ERP token never reaches the page")
    ok(all(c != 404 for c in codes), "internal edition keeps the DynaMod and ERP routes", codes)
    s, r = req("POST", "/api/destinations/erp/test", headers=KEY)
    ok(s == 200 and r.get("ok") is False and token not in json.dumps(r), "ERP test reports failure without the token", r)
else:
    ok(all(c == 404 for c in codes), "public edition has no DynaMod or ERP routes", codes)
    s, _ = req("POST", "/api/settings", headers=KEY, body={"ERP_URL": "http://x", "ERP_TOKEN": "t", "DYNAMOD_OUT_DIR": "C:\\x"})
    env = config.ENV_PATH.read_text(encoding="utf-8")
    ok(s == 200 and "ERP_" not in env and "DYNAMOD_" not in env, "public edition ignores DynaMod / ERP settings", env)
    ok("ERP_TOKEN" not in config.SECRET_KEYS, "public edition keeps no ERP secret")
    ok(not any(m in sys.modules for m in ("app.dynamod", "app.erp", "app.destinations.dynamod",
                                          "app.destinations.erp")), "public edition never imports DynaMod / ERP code")
    ok(config.APP_NAME == "Doc2ExcelApp" and "Doc2ExcelApp" in str(config.DATA_DIR), "public edition has its own data folder")

from app import updater  # noqa: E402

ok(updater.REPO == edition.REPO and updater.API_URL.endswith(f"/repos/{edition.REPO}/releases/latest"),
   "updates come only from this edition's repository", updater.API_URL)

srv.stop()
print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL CHECKS PASSED")
