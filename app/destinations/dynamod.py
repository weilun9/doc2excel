"""SmartAcc DynaMod supplier bills (internal edition). The bill logic stays in
app/dynamod.py; this adapter adds the destination contract and its routes."""
from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from .. import config, dynamod
from .base import Destination, Result, SendError


def _workbook() -> Path | None:
    wbk = config.get("DYNAMOD_WORKBOOK")
    return Path(wbk) if wbk and Path(wbk).is_file() else None


def _results(docs: list[dict], bill_results: list[dict]) -> list[Result]:
    """write_import_files returns one result per bill, in the order of the docs.
    Its doc_id is the bill number (supplier + invoice no.), so pair by position."""
    return [Result(d["id"], bool(r.get("ok")), "made" if r.get("ok") else "check",
                   "; ".join(r.get("problems") or []), {"bill": r.get("doc_id", "")})
            for d, r in zip(docs, bill_results)]


class DynaMod(Destination):
    id = "dynamod"
    needs_path = True

    def ready(self):
        return _workbook() is not None

    def status(self):
        return {"ready": self.ready(), "detail": ""}

    def send(self, docs, options):
        wbk = _workbook()
        if not wbk:
            raise SendError("dm_workbook_missing")
        path = options.get("path")
        if not path:
            raise SendError("path_required")
        lk = dynamod.Lookups.from_workbook(wbk)
        loc = config.get("DYNAMOD_LOCATION") or dynamod.DEFAULT_LOCATION
        bills = [dynamod.bill_from_doc(d, lk, loc) for d in docs]
        out = dynamod.write_workbook(bills, wbk, path)
        results = dynamod.write_import_files(bills, config.get("DYNAMOD_OUT_DIR"))
        return _results(docs, results), {"workbook": str(out), "out_dir": config.get("DYNAMOD_OUT_DIR"),
                                   "results": results}

    def register_routes(self, app, ctx):
        store = ctx.store

        @app.post("/api/dynamod/export")
        @ctx.local_only
        def dm_export():
            """Selected docs -> 进单整理表 (copy of the user's template) + one
            import file per bill that passes every check."""
            body = request.json or {}
            docs = [store.get(i) for i in body.get("ids") or []]
            docs = [d for d in docs if d and d.get("table")]
            if not _workbook():
                return jsonify({"ok": False, "error": "dm_workbook_missing"}), 400
            if not docs:
                return jsonify({"ok": False, "error": "no_ready_docs"}), 400
            if not body.get("path"):
                return jsonify({"ok": False, "error": "path_required"}), 400
            results, info = self.send(docs, {"path": body["path"]})
            for r in results:
                store.record_delivery(r.doc_id, self.id, r.state, r.message)
            return jsonify({"ok": True, **info})

        @app.post("/api/dynamod/from_workbook")
        @ctx.local_only
        def dm_from_workbook():
            """Edited 整理表 (saved by Excel) -> import files from 明细 A:E."""
            path = (request.json or {}).get("path")
            if not path or not Path(path).is_file() or Path(path).suffix.lower() != ".xlsx":
                return jsonify({"ok": False, "error": "bad_path"}), 400
            try:
                bills, notes = dynamod.bills_from_workbook(path)
            except KeyError:
                return jsonify({"ok": False, "error": "dm_not_workbook"}), 400
            results = dynamod.write_import_files(bills, config.get("DYNAMOD_OUT_DIR"))
            return jsonify({"ok": True, "out_dir": config.get("DYNAMOD_OUT_DIR"), "results": results,
                            "notes": notes})


DESTINATION = DynaMod()
