"""Send to the AI ERP's scan queue (internal edition). The HTTP client stays in
app/erp.py; the ERP token never leaves this process."""
from __future__ import annotations

from flask import abort, jsonify

from .. import erp
from .base import Destination, Result


class ERP(Destination):
    id = "erp"

    def ready(self):
        return erp.configured()

    def status(self):
        return {"ready": self.ready(), "detail": ""}

    def send_one(self, doc: dict) -> Result:
        try:
            res = erp.send(doc)
        except erp.ERPError as e:
            return Result(doc["id"], False, "failed", str(e), {"code": e.code})
        except (OSError, KeyError) as e:  # source file missing / unreadable / not recorded
            return Result(doc["id"], False, "failed", f"File error: {e}")
        return Result(doc["id"], True, res["state"], "", {k: v for k, v in res.items() if k != "state"})

    def send(self, docs, options):
        out = []
        for d in docs:
            r = self.send_one(d)
            out.append(r)
            if r.extra.get("code") in ("unauthorized", "unreachable", "not_configured"):
                break  # the same error for every remaining file
        return out, {}

    def test(self):
        try:
            info = erp.ping()
        except erp.ERPError as e:
            return {"ok": False, "code": e.code, "error": str(e)}
        return {"ok": True, "user": info.get("user"), "queue": info.get("queue"),
                "company_id": info.get("company_id")}

    def register_routes(self, app, ctx):
        store = ctx.store

        @app.post("/api/erp/ping")
        @ctx.local_only
        def erp_ping():
            return jsonify(self.test())

        @app.post("/api/doc/<doc_id>/erp")
        @ctx.local_only
        def erp_send(doc_id):
            d = store.get(doc_id)
            if not d:
                abort(404)
            r = self.send_one(d)
            store.set_erp(doc_id, r.state, r.message, r.extra.get("erp_id"))
            store.record_delivery(doc_id, self.id, r.state, r.message)
            if not r.ok:
                return jsonify({"ok": False, "state": "failed", "error": r.message,
                                **({"code": r.extra["code"]} if "code" in r.extra else {})}), 200
            return jsonify({"ok": True, "state": r.state, **r.extra})


DESTINATION = ERP()
