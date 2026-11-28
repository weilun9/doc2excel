"""Excel: a formatted .xlsx (one sheet per document, a combined sheet with a
summary, or both). Every edition ships it."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from flask import jsonify, request, send_file

from .. import exporter
from .base import Destination, Result, SendError

MODES = ("per_doc", "combined", "both")


class Excel(Destination):
    id = "excel"
    needs_path = True

    def send(self, docs, options):
        path = options.get("path")
        if not path:
            raise SendError("path_required")
        mode = options.get("mode", "per_doc")
        if mode not in MODES:
            raise SendError("bad_mode")
        out = exporter.export(docs, path, mode)
        return [Result(d["id"], True, "exported") for d in docs], {"path": str(out)}

    def register_routes(self, app, ctx):
        store = ctx.store

        @app.post("/api/export")
        @ctx.local_only
        def do_export():
            body = request.json or {}
            docs = [store.get(i) for i in body.get("ids") or []]
            docs = [d for d in docs if d and d.get("table")]
            if not docs:
                return jsonify({"ok": False, "error": "no_ready_docs"}), 400
            mode = body.get("mode", "per_doc")
            path = body.get("path")
            if path:
                out = exporter.export(docs, path, mode)
                store.mark_exported([d["id"] for d in docs])
                return jsonify({"ok": True, "path": str(out)})
            # Browser fallback (dev mode): stream the file.
            tmp = Path(tempfile.gettempdir()) / f"doc2excel_{int(time.time())}.xlsx"
            exporter.export(docs, tmp, mode)
            store.mark_exported([d["id"] for d in docs])
            return send_file(tmp, as_attachment=True, download_name="Doc2Excel.xlsx")


DESTINATION = Excel()
