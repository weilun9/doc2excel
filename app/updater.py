"""Self-update from GitHub Releases (weilun9/doc2excel).

check()   -> asks the GitHub API for the latest release and compares versions.
install() -> downloads Doc2Excel-Setup-<ver>.exe, verifies its size and the
             sha256 digest GitHub reports for the asset, starts it detached with
             "/S /RESTART" (silent install, then relaunch) and exits the app.

Only HTTPS download URLs for this repository on github.com are accepted; the
installer keeps the user's .env.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import net
from .version import VERSION

REPO = "weilun9/doc2excel"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"/{REPO}/releases/download/"
HEADERS = {"User-Agent": "Doc2Excel-updater", "Accept": "application/vnd.github+json"}
CHUNK = 256 * 1024


class UpdateError(Exception):
    pass


def parse_version(s: str | None) -> tuple[int, ...] | None:
    """'v1.10.2' -> (1, 10, 2). None for anything that is not dotted numbers."""
    s = (s or "").strip().lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+)*", s):
        return None
    parts = [int(p) for p in s.split(".")]
    while len(parts) > 1 and parts[-1] == 0:  # 1.2 == 1.2.0
        parts.pop()
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    lv, cv = parse_version(latest), parse_version(current)
    return bool(lv and cv and lv > cv)


def allowed_url(url: str) -> bool:
    u = urlparse(url or "")
    return (u.scheme == "https" and (u.hostname or "").lower() == "github.com"
            and u.port in (None, 443) and not u.username and not u.password
            and u.path.startswith(DOWNLOAD_PREFIX) and ".." not in u.path)


def check(current: str = VERSION, urlopen=net.urlopen) -> dict:
    """Latest release info. Raises UpdateError when GitHub can't be read."""
    if current == "dev":
        return {"current": current, "dev": True, "update": False}
    try:
        with urlopen(urllib.request.Request(API_URL, headers=HEADERS), timeout=15) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except UpdateError:
        raise
    except Exception as e:  # network, HTTP error, bad JSON
        raise UpdateError(f"GitHub: {e}") from e
    latest = str(rel.get("tag_name", "")).lstrip("vV")
    info = {"current": current, "latest": latest, "update": False,
            "notes": rel.get("body") or "", "page": rel.get("html_url") or ""}
    if not parse_version(latest) or not is_newer(latest, current):
        return info
    name = f"Doc2Excel-Setup-{latest}.exe"
    asset = next((a for a in rel.get("assets") or [] if a.get("name") == name), None)
    if not asset:
        return info  # release without an installer: nothing to install
    info.update(update=True, asset=name, url=asset.get("browser_download_url", ""),
                size=int(asset.get("size") or 0), digest=asset.get("digest") or "")
    return info


def download(info: dict, dest_dir: Path, progress=None, urlopen=net.urlopen) -> Path:
    """Download the installer and verify size + sha256. Deletes it on mismatch."""
    url, size, digest = info.get("url", ""), int(info.get("size") or 0), info.get("digest", "")
    if not allowed_url(url):
        raise UpdateError(f"refused download URL: {url}")
    m = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest or "")
    if not m:
        raise UpdateError("release asset has no sha256 digest")
    if size <= 0:
        raise UpdateError("release asset has no size")
    want = m.group(1).lower()
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / info["asset"]
    if path.name != Path(info["asset"]).name:
        raise UpdateError("bad asset name")
    h, done = hashlib.sha256(), 0
    try:
        with urlopen(urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]}),
                     timeout=60) as r, open(path, "wb") as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > size:
                    raise UpdateError("download is larger than expected")
                h.update(chunk)
                f.write(chunk)
                if progress:
                    progress(done, size)
        if done != size:
            raise UpdateError(f"size mismatch: got {done} bytes, expected {size}")
        if h.hexdigest() != want:
            raise UpdateError("sha256 mismatch: the download is damaged or was tampered with")
    except BaseException as e:
        path.unlink(missing_ok=True)
        if isinstance(e, UpdateError):
            raise
        raise UpdateError(f"download failed: {e}") from e
    return path


def launch_installer(path: Path):
    """Run the installer as an independent process that outlives this app."""
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    args = [str(path), "/S", "/RESTART"]
    try:  # also leave any job object, if the job allows it
        subprocess.Popen(args, creationflags=flags | 0x01000000, close_fds=True)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(args, creationflags=flags, close_fds=True)


def _exit_app():
    os._exit(0)  # releases the single-instance mutex the installer waits for


class Updater:
    """Holds the last check result and the state of a running install, for the
    /api/update/* endpoints. install() only ever uses what check() found."""

    def __init__(self, current: str = VERSION, urlopen=net.urlopen,
                 launcher=launch_installer, on_launched=None, exit_delay: float = 1.5):
        self.current = current
        self._urlopen = urlopen
        self._launcher = launcher
        self._on_launched = on_launched or (lambda: threading.Timer(exit_delay, _exit_app).start())
        self._lock = threading.Lock()
        self.info: dict | None = None
        self.state = {"state": "idle", "done": 0, "total": 0, "error": ""}

    def check(self) -> dict:
        info = check(self.current, self._urlopen)
        with self._lock:
            self.info = info
        return info

    def status(self) -> dict:
        with self._lock:
            return {**self.state, "current": self.current}

    def install(self) -> bool:
        """Start the download + install in the background. False if there is
        no checked update or one is already running."""
        with self._lock:
            if not (self.info and self.info.get("update")):
                return False
            if self.state["state"] in ("downloading", "installing"):
                return False
            self.state = {"state": "downloading", "done": 0, "total": self.info.get("size", 0), "error": ""}
            info = dict(self.info)
        threading.Thread(target=self._run, args=(info,), name="updater", daemon=True).start()
        return True

    def _progress(self, done, total):
        with self._lock:
            self.state.update(done=done, total=total)

    def _run(self, info: dict):
        try:
            folder = Path(tempfile.gettempdir()) / "doc2excel_update"
            if folder.exists():  # leftovers from an earlier update
                for old in folder.glob("Doc2Excel-Setup-*.exe"):
                    old.unlink(missing_ok=True)
            path = download(info, folder, self._progress, self._urlopen)
            with self._lock:
                self.state["state"] = "installing"
            self._launcher(path)
            time.sleep(0.2)
            self._on_launched()
        except Exception as e:
            with self._lock:
                self.state.update(state="error", error=str(e))
