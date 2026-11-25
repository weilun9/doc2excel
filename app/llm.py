"""Minimal OpenAI-compatible chat client (stdlib only) and a network check."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import config, net

_net_cache: dict[str, tuple[float, bool]] = {}
_net_lock = threading.Lock()


def is_reachable(base_url: str, ttl: float = 20.0) -> bool:
    """True if the API host accepts a TCP connection. Cached for `ttl` seconds."""
    if not base_url:
        return False
    u = urlparse(base_url)
    host = u.hostname
    if not host:
        return False
    port = u.port or (443 if u.scheme == "https" else 80)
    key = f"{host}:{port}"
    now = time.time()
    with _net_lock:
        hit = _net_cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    ok = False
    try:
        with socket.create_connection((host, port), timeout=2.5):
            ok = True
    except OSError:
        ok = False
    with _net_lock:
        _net_cache[key] = (now, ok)
    return ok


def text_llm_ready() -> bool:
    return bool(config.get("LLM_API_KEY") and config.get("LLM_BASE_URL") and config.get("LLM_MODEL"))


def vision_ready() -> bool:
    return bool(config.get("VISION_API_KEY") and config.get("VISION_BASE_URL") and config.get("VISION_MODEL"))


class LLMError(RuntimeError):
    pass


def chat(base_url: str, api_key: str, model: str, messages: list, *,
         json_mode: bool = False, timeout: float = 120.0, max_tokens: int = 4096) -> str:
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body: dict = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=net.tls_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"Network error: {e}") from e
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Unexpected response: {str(data)[:300]}") from e
