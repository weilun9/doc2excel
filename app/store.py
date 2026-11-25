"""Document store and the background worker that runs OCR + table building.
The worker is a daemon thread: it only exists while the app window is open."""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config, ocr, structure


class Store:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.order: list[str] = []
        self.lock = threading.RLock()
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self._load()

    # ---------- persistence ----------
    def _load(self):
        try:
            data = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in data.get("docs", []):
            if not Path(d.get("file", "")).exists():
                continue
            if d.get("status") not in ("ready", "error"):
                d["status"] = "queued"
                self.q.put((d["id"], "full"))
            self.docs[d["id"]] = d
            self.order.append(d["id"])

    def save(self):
        with self.lock:
            data = {"docs": [self.docs[i] for i in self.order]}
        tmp = config.STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(config.STATE_PATH)

    # ---------- documents ----------
    def add_file(self, src: Path, name: str | None = None, source: str = "local", move: bool = False) -> dict | None:
        ext = src.suffix.lower()
        if ext not in ocr.SUPPORTED_EXT:
            return None
        doc_id = uuid.uuid4().hex[:12]
        dest = config.INBOX_DIR / f"{doc_id}{ext}"
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        (shutil.move if move else shutil.copy2)(str(src), str(dest))
        doc = {
            "id": doc_id, "name": name or src.name, "file": str(dest), "source": source,
            "added": time.time(), "status": "queued", "progress": "", "pages": 0,
            "engine": "", "mode": "", "warning": None, "error": None,
            "text": "", "table": None, "ai_table": None, "confirmed": False,
        }
        with self.lock:
            self.docs[doc_id] = doc
            self.order.insert(0, doc_id)
        self.save()
        self.q.put((doc_id, "full"))
        return doc

    def get(self, doc_id: str) -> dict | None:
        with self.lock:
            return self.docs.get(doc_id)

    def summary(self) -> list[dict]:
        keep = ("id", "name", "source", "added", "status", "progress", "pages", "engine",
                "mode", "warning", "error", "confirmed", "erp_state", "erp_message", "enhanced")
        with self.lock:
            out = []
            for i in self.order:
                d = self.docs[i]
                s = {k: d.get(k) for k in keep}
                t = d.get("table") or {}
                s["title"] = t.get("title", "")
                s["doc_type"] = t.get("doc_type", "")
                s["n_rows"] = len(t.get("rows", []))
                out.append(s)
            return out

    def update_table(self, doc_id: str, table: dict, confirmed: bool) -> dict | None:
        with self.lock:
            d = self.docs.get(doc_id)
            if not d:
                return None
            d["table"] = structure.normalize(table)
            d["confirmed"] = bool(confirmed)
        self.save()
        return d

    def set_erp(self, doc_id: str, state: str, message: str = "", erp_id=None) -> None:
        """Remember the last "send to ERP" result: sent | already | failed."""
        with self.lock:
            d = self.docs.get(doc_id)
            if not d:
                return
            d["erp_state"], d["erp_message"], d["erp_id"] = state, message, erp_id
        self.save()

    def delete(self, doc_id: str) -> bool:
        with self.lock:
            d = self.docs.pop(doc_id, None)
            if not d:
                return False
            self.order.remove(doc_id)
        try:
            Path(d["file"]).unlink(missing_ok=True)
        except OSError:
            pass
        self.save()
        return True

    def requeue(self, doc_id: str, kind: str) -> bool:
        d = self.get(doc_id)
        if not d:
            return False
        if kind == "ai" and (not d.get("text") or d.get("vision_tables")):
            kind = "full"  # the vision model builds the table while reading the image
        d["status"], d["error"], d["warning"] = "queued", None, None
        self.q.put((doc_id, kind))
        return True

    # ---------- worker ----------
    def start_worker(self):
        threading.Thread(target=self._work, name="worker", daemon=True).start()
        threading.Thread(target=ocr.warm_up, name="ocr-warmup", daemon=True).start()

    def _set(self, d, **kw):
        with self.lock:
            d.update(kw)

    def _work(self):
        while True:
            doc_id, kind = self.q.get()
            d = self.get(doc_id)
            if not d:
                continue
            try:
                path = Path(d["file"])
                if kind == "full" or not d.get("ocr_pages"):
                    self._set(d, status="ocr", progress="")
                    pages = ocr.load_pages(path)
                    steps = []
                    if ocr.should_enhance(path):
                        pages, steps = ocr.enhance_pages(pages)
                    self._set(d, pages=len(pages), enhanced=steps)
                    for old in (config.INBOX_DIR / "preview").glob(f"{doc_id}_*.jpg"):
                        old.unlink(missing_ok=True)  # preview must show the image that was read
                    res = ocr.run(path, pages, progress=lambda i, n, e: self._set(d, progress=f"{i}/{n}", engine=e))
                    self._set(d, engine=res["engine"], text=res["text"], ocr_pages=res["pages"],
                              ocr_boxes=res.get("boxes"), vision_tables=res.get("vision_tables"))
                self._set(d, status="ai", progress="")
                table, mode, warn = structure.run({"text": d["text"], "pages": d["ocr_pages"],
                                                   "boxes": d.get("ocr_boxes"),
                                                   "vision_tables": d.get("vision_tables")})
                self._set(d, table=table, ai_table=table, mode=mode, warning=warn,
                          status="ready", confirmed=False, progress="")
            except Exception as e:  # keep the worker alive whatever happens
                traceback.print_exc()
                self._set(d, status="error", error=f"{type(e).__name__}: {e}")
            self.save()

    def wait_idle(self, timeout: float = 600) -> None:
        """Block until no document is queued or processing (used by CLI mode)."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                busy = any(d["status"] in ("queued", "ocr", "ai") for d in self.docs.values())
            if not busy:
                return
            time.sleep(0.3)
