"""Local HTTP server. Serves the desktop UI (localhost only) and the phone
upload page (LAN, guarded by a random token that changes every launch)."""
from __future__ import annotations

import io
import mimetypes
import secrets
import tempfile
import threading
import time
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename

from . import config, edition, llm, ocr, sources, structure, updater
from .destinations import registry as destinations
from .destinations.base import SendError
from .version import VERSION

mimetypes.add_type("font/woff2", ".woff2")   # Windows' registry often lacks it

LOCAL_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _port_taken(port: int) -> bool:
    """Werkzeug binds with SO_REUSEADDR, which on Windows lets a second
    process share a port that is already listening. Check first: something
    answering on the port, or an exclusive bind failing, means it is taken."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


class AppServer:
    def __init__(self, store, port: int, upd: updater.Updater | None = None):
        self.store = store
        self.port = port
        self.updater = upd or updater.Updater()
        self.token = secrets.token_urlsafe(9)        # phone page token (shown in the QR code)
        self.api_key = secrets.token_urlsafe(24)     # desktop UI token, never leaves this PC
        self.phone_uploads = 0
        self.app = self._build()
        self._srv = None

    # ---------------- lifecycle ----------------
    def start(self) -> int:
        last_err = None
        for p in range(self.port, self.port + 20):
            if _port_taken(p):
                continue
            try:
                self._srv = make_server("0.0.0.0", p, self.app, threaded=True)
                self.port = p
                break
            except OSError as e:
                last_err = e
        if self._srv is None:
            raise RuntimeError(f"No free port: {last_err}")
        threading.Thread(target=self._srv.serve_forever, name="http", daemon=True).start()
        return self.port

    def stop(self):
        if self._srv:
            self._srv.shutdown()

    def phone_urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}/m/{self.token}" for ip in sources.lan_ips()]

    # ---------------- routes ----------------
    def _build(self) -> Flask:
        app = Flask(__name__, static_folder=None)
        app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024
        store = self.store
        web = config.WEB_DIR

        def _local_host_ok() -> bool:
            # DNS rebinding defence: a web page on evil.example that re-points its
            # name to 127.0.0.1 still sends "Host: evil.example".
            host = (request.host or "").lower()
            return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

        def local_page(fn):
            """The desktop page itself: this PC only, correct Host."""
            @wraps(fn)
            def wrapper(*a, **kw):
                if request.remote_addr not in LOCAL_ADDRS or not _local_host_ok():
                    abort(403)
                return fn(*a, **kw)
            return wrapper

        def local_only(fn):
            """Desktop API: this PC only, correct Host, and the per-launch API key.
            The key travels in a custom header (cross-site pages cannot add one
            without a CORS preflight, which this server never approves) or, for
            <img> GETs only, as ?k=."""
            @wraps(fn)
            def wrapper(*a, **kw):
                if request.remote_addr not in LOCAL_ADDRS or not _local_host_ok():
                    abort(403)
                given = request.headers.get("X-D2X-Key", "")
                if not given and request.method == "GET":
                    given = request.args.get("k", "")
                if not secrets.compare_digest(given, self.api_key):
                    abort(403)
                return fn(*a, **kw)
            return wrapper

        def check_token(token):
            if not secrets.compare_digest(token, self.token):
                abort(404)

        @app.after_request
        def harden(resp):
            h = resp.headers
            h["Cache-Control"] = "no-store"
            h["X-Content-Type-Options"] = "nosniff"
            h["Referrer-Policy"] = "no-referrer"       # phone token must not leak
            h["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
                "form-action 'none'; base-uri 'none'")
            h["X-Frame-Options"] = "DENY"
            return resp

        # ---- desktop UI ----
        @app.get("/")
        @local_page
        def index():
            html = (web / "index.html").read_text(encoding="utf-8")
            html = html.replace("__D2X_KEY__", self.api_key)
            return html, 200, {"Content-Type": "text/html; charset=utf-8"}

        @app.get("/static/<path:name>")
        @local_page
        def static_files(name):
            return send_from_directory(web, name)

        @app.get("/api/state")
        @local_only
        def state():
            s = config.public_settings()
            ai_mode = structure.choose_mode()
            dests = destinations.summary()
            return jsonify({
                "docs": store.summary(),
                "phone_urls": self.phone_urls(),
                "phone_uploads": self.phone_uploads,
                "ui_lang": s.get("UI_LANG", "zh"),
                "watch_dir": s.get("WATCH_DIR", ""),
                "ai_mode": ai_mode,
                "ocr_mode": s.get("OCR_MODE", "auto"),
                "ai_mode_setting": s.get("AI_MODE", "auto"),
                "ocr_engine": ocr.choose_engine(),
                "llm_ready": llm.text_llm_ready(),
                "llm_model": s.get("LLM_MODEL", ""),
                "vision_ready": llm.vision_ready(),
                "erp_ready": any(d["id"] == "erp" and d["ready"] for d in dests),
                "destinations": dests,
                "edition": edition.ID,
                "app_name": edition.NAME,
                "version": VERSION,
            })

        # ---- destinations: where checked documents go (edition decides which) ----
        ctx = SimpleNamespace(store=store, local_only=local_only)
        for dest in destinations.enabled():
            dest.register_routes(app, ctx)

        @app.get("/api/destinations")
        @local_only
        def list_destinations():
            return jsonify(destinations.summary())

        @app.post("/api/destinations/<dest_id>/send")
        @local_only
        def destination_send(dest_id):
            dest = destinations.get(dest_id)
            if not dest:
                abort(404)
            body = request.json or {}
            docs = [store.get(i) for i in body.get("ids") or []]
            docs = [d for d in docs if d and d.get("table")]
            if not docs:
                return jsonify({"ok": False, "error": "no_ready_docs"}), 400
            options = body.get("options") or {}
            if not isinstance(options, dict):
                return jsonify({"ok": False, "error": "bad_options"}), 400
            try:
                results, info = dest.send(docs, options)
            except SendError as e:
                return jsonify({"ok": False, "error": e.code}), 400
            for r in results:
                store.record_delivery(r.doc_id, dest.id, r.state, r.message)
            return jsonify({"ok": True, "results": [r.as_dict() for r in results], **info})

        @app.post("/api/destinations/<dest_id>/test")
        @local_only
        def destination_test(dest_id):
            dest = destinations.get(dest_id)
            if not dest:
                abort(404)
            return jsonify(dest.test())

        # ---- self-update (this PC only; install uses only what check found) ----
        @app.get("/api/update/check")
        @local_only
        def update_check():
            try:
                info = self.updater.check()
            except updater.UpdateError as e:
                return jsonify({"ok": False, "current": self.updater.current, "error": str(e)}), 200
            keep = ("current", "latest", "update", "dev", "page")
            return jsonify({"ok": True, **{k: info[k] for k in keep if k in info}})

        @app.post("/api/update/install")
        @local_only
        def update_install():
            if not self.updater.install():
                return jsonify({"ok": False, "error": "no_update"}), 400
            return jsonify({"ok": True})

        @app.get("/api/update/status")
        @local_only
        def update_status():
            return jsonify(self.updater.status())

        @app.get("/api/qr.png")
        @local_only
        def qr():
            import qrcode
            url = request.args.get("u") or self.phone_urls()[0]
            if self.token not in url:
                abort(400)
            img = qrcode.make(url, box_size=8, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return send_file(buf, mimetype="image/png")

        @app.post("/api/upload")
        @local_only
        def upload_local():
            return jsonify(_save_uploads("local"))

        @app.post("/api/add-paths")
        @local_only
        def add_paths():
            added = []
            for p in (request.json or {}).get("paths", []):
                path = Path(p)
                if path.is_file():
                    d = store.add_file(path, source="local")
                    if d:
                        added.append(d["id"])
            return jsonify({"added": added})

        @app.get("/api/doc/<doc_id>")
        @local_only
        def get_doc(doc_id):
            d = store.get(doc_id)
            if not d:
                abort(404)
            out = {k: v for k, v in d.items() if k not in ("ocr_pages",)}
            return jsonify(out)

        @app.get("/api/doc/<doc_id>/page/<int:n>.jpg")
        @local_only
        def page_img(doc_id, n):
            d = store.get(doc_id)
            if not d:
                abort(404)
            cache = config.INBOX_DIR / "preview" / f"{doc_id}_{n}.jpg"
            if not cache.exists():
                pages = ocr.load_pages(Path(d["file"]), enhanced=bool(d.get("enhanced")),
                                       rotate=int(d.get("rotate") or 0), display=True)
                if n >= len(pages):
                    abort(404)
                cache.parent.mkdir(parents=True, exist_ok=True)
                img = pages[n]
                img.thumbnail((1800, 1800))
                img.save(cache, "JPEG", quality=85)
            return send_file(cache, mimetype="image/jpeg")

        @app.put("/api/doc/<doc_id>/table")
        @local_only
        def put_table(doc_id):
            body = request.json or {}
            d = store.update_table(doc_id, body.get("table") or {}, body.get("confirmed", False))
            if not d:
                abort(404)
            return jsonify({"ok": True, "table": d["table"]})

        @app.post("/api/doc/<doc_id>/redo")
        @local_only
        def redo(doc_id):
            kind = (request.json or {}).get("kind", "ai")
            if not store.requeue(doc_id, "full" if kind == "full" else "ai"):
                abort(404)
            return jsonify({"ok": True})

        @app.post("/api/doc/<doc_id>/rotate")
        @local_only
        def rotate_doc(doc_id):
            deg = (request.json or {}).get("deg")
            if deg not in (90, -90, 180):
                return jsonify({"ok": False, "error": "bad_angle"}), 400
            d = store.rotate(doc_id, deg)
            if not d:
                abort(404)
            return jsonify({"ok": True, "rotate": d["rotate"]})

        @app.delete("/api/doc/<doc_id>")
        @local_only
        def delete_doc(doc_id):
            return jsonify({"ok": store.delete(doc_id)})

        @app.get("/api/settings")
        @local_only
        def get_settings():
            return jsonify(config.public_settings())

        @app.post("/api/settings")
        @local_only
        def post_settings():
            body = request.json or {}
            # A saved key is only ever sent to the host it was entered for: moving
            # an endpoint to another host requires typing the key again.
            from urllib.parse import urlparse
            for url_k, key_k in (("LLM_BASE_URL", "LLM_API_KEY"), ("VISION_BASE_URL", "VISION_API_KEY"),
                                 ("ERP_URL", "ERP_TOKEN")):
                if url_k not in config.DEFAULTS:
                    continue  # a destination this edition does not ship
                new_url = str(body.get(url_k, "") or "").strip()
                old_host = urlparse(config.get(url_k)).hostname
                if (new_url and config.get(key_k) and not body.get(key_k)
                        and old_host and urlparse(new_url).hostname != old_host):
                    return jsonify({"error": "key_required", "field": key_k}), 400
            # Model IDs: no spaces or display names ("Gemini 2.5 Flash"); Gemini
            # also rejects upper case ("unexpected model name format").
            import re
            for model_k, url_k in (("LLM_MODEL", "LLM_BASE_URL"), ("VISION_MODEL", "VISION_BASE_URL")):
                m = str(body.get(model_k, "") or "").strip()
                host = (urlparse(str(body.get(url_k) or config.get(url_k) or "")).hostname or "").lower()
                if m and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*", m)
                          or (host.endswith("googleapis.com") and m != m.lower())):
                    return jsonify({"error": "bad_model", "field": model_k}), 400
            updates = {}
            for k, v in body.items():
                if k not in config.DEFAULTS:
                    continue
                if k in config.SECRET_KEYS and not v:
                    continue  # blank secret field = keep the current key
                updates[k] = str(v).strip()
            config.save(updates)
            config.ensure_dirs()
            return jsonify(config.public_settings())

        @app.post("/api/scan")
        @local_only
        def scan():
            try:
                path = sources.scan_with_wia()
            except RuntimeError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            if not path:
                return jsonify({"ok": False, "error": "cancelled"})
            d = store.add_file(path, name=path.name, source="scanner", move=True)
            return jsonify({"ok": True, "id": d["id"] if d else None})

        # ---- phone ----
        @app.get("/m/<token>")
        def mobile(token):
            check_token(token)
            return send_from_directory(web, "mobile.html")

        @app.get("/m/<token>/font/<name>")
        def mobile_font(token, name):
            # The phone cannot reach /static (this PC only); give it just the UI font.
            check_token(token)
            if not name.endswith(".woff2"):
                abort(404)
            return send_from_directory(web / "fonts", name)

        @app.post("/m/<token>/upload")
        def mobile_upload(token):
            check_token(token)
            res = _save_uploads("phone")
            self.phone_uploads += len(res["added"])
            return jsonify(res)

        def _save_uploads(source):
            added, rejected = [], []
            for f in request.files.getlist("files"):
                name = f.filename or "upload"
                ext = Path(name).suffix.lower()
                if not ext and f.mimetype:
                    ext = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf",
                           "image/webp": ".webp", "image/heic": ".heic"}.get(f.mimetype, "")
                if ext not in ocr.SUPPORTED_EXT:
                    rejected.append(name)
                    continue
                tmp = Path(tempfile.mkdtemp(prefix="d2x_")) / ((secure_filename(Path(name).stem) or "file") + ext)
                f.save(tmp)
                if source == "phone" and name.lower().startswith("image"):
                    name = time.strftime("phone_%H%M%S") + ext
                d = store.add_file(tmp, name=name, source=source, move=True)
                if d:
                    added.append(d["id"])
            return {"added": added, "rejected": rejected}

        return app
