"""The destinations this build ships, in display order."""
from __future__ import annotations

import importlib

from .. import edition
from .base import Destination

_cache: dict[str, Destination] | None = None


def _load() -> dict[str, Destination]:
    global _cache
    if _cache is None:
        out: dict[str, Destination] = {}
        for i in edition.DESTINATIONS:
            mod = importlib.import_module(f"{__package__}.{i}")
            out[i] = mod.DESTINATION
        _cache = out
    return _cache


def enabled() -> list[Destination]:
    return list(_load().values())


def get(dest_id: str) -> Destination | None:
    return _load().get(dest_id)


def summary() -> list[dict]:
    """For the page: which destinations exist and whether each is ready."""
    out = []
    for d in enabled():
        try:
            st = d.status()
        except Exception as e:  # a broken destination must not take the page down
            st = {"ready": False, "detail": f"{type(e).__name__}: {e}"}
        out.append({"id": d.id, "needs_path": d.needs_path, **st})
    return out
