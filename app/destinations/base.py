"""The contract every destination follows.

A destination says whether it is ready (configured), describes its state for
the settings screen, sends a batch of documents, and can test its connection.
It may also add its own HTTP routes for flows that need more than "send"
(e.g. DynaMod's work-sheet round trip). Routes must use ctx.local_only: the
per-launch key, this PC only, correct Host. Secrets stay in this process; a
destination never returns a key or token to the page."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Result:
    """Outcome for one document."""
    doc_id: str
    ok: bool
    state: str              # e.g. exported | sent | already | failed
    message: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"doc_id": self.doc_id, "ok": self.ok, "state": self.state, "message": self.message, **self.extra}


class SendError(Exception):
    """The whole batch could not be sent (not configured, bad options, ...)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class Destination:
    id = ""
    needs_path = False       # send() needs options["path"] (a save dialog in the desktop window)

    def ready(self) -> bool:
        return True

    def status(self) -> dict:
        """{"ready": bool, "detail": str} for the settings screen; never secrets."""
        return {"ready": self.ready(), "detail": ""}

    def send(self, docs: list[dict], options: dict) -> tuple[list[Result], dict]:
        """Deliver documents. Returns per-document results and batch info
        (e.g. the written path). Raises SendError for a batch-level problem."""
        raise NotImplementedError

    def test(self) -> dict:
        return {"ok": self.ready()}

    def register_routes(self, app, ctx) -> None:
        """Optional extra routes. ctx has .store and .local_only."""
