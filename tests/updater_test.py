"""Tests for app/updater.py and the /api/update/* endpoints. No network: every
HTTP call goes through a fake urlopen.

    .venv\\Scripts\\python tests\\updater_test.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="d2x_upd_"))
os.environ["LOCALAPPDATA"] = str(TMP / "appdata")
os.environ["D2X_AI_MODE"] = "offline"
os.environ["D2X_KEYSTORE"] = "memory"  # never touch the real Credential Manager
os.environ["D2X_WATCH_DIR"] = str(TMP / "watch")
sys.path.insert(0, str(ROOT))

from app import updater  # noqa: E402
from app.updater import UpdateError  # noqa: E402

PASSED = 0


def ok(cond, msg):
    global PASSED
    if not cond:
        raise AssertionError(msg)
    PASSED += 1
    print("  ok  ", msg)


def raises(fn, msg, contains=""):
    try:
        fn()
    except UpdateError as e:
        ok(contains.lower() in str(e).lower(), f"{msg} ({e})")
        return
    raise AssertionError(f"{msg}: no UpdateError")


PAYLOAD = b"MZ fake installer " * 5000
GOOD = {
    "asset": "Doc2Excel-Setup-1.2.0.exe",
    "url": "https://github.com/weilun9/doc2excel/releases/download/v1.2.0/Doc2Excel-Setup-1.2.0.exe",
    "size": len(PAYLOAD),
    "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeNet:
    """urlopen stand-in: maps URL -> bytes and records every request."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.calls.append(url)
        if url not in self.routes:
            raise OSError(f"unexpected URL {url}")
        return FakeResp(self.routes[url])


def release(tag="v1.2.0", digest=GOOD["digest"], size=len(PAYLOAD), name="Doc2Excel-Setup-1.2.0.exe"):
    return json.dumps({"tag_name": tag, "html_url": "https://github.com/weilun9/doc2excel/releases/tag/" + tag,
                       "body": "notes", "assets": [
                           {"name": "Doc2Excel.exe", "browser_download_url": "https://github.com/x", "size": 1},
                           {"name": name, "size": size, "digest": digest,
                            "browser_download_url": GOOD["url"]}]}).encode()


def t_versions():
    print("version comparison")
    ok(updater.is_newer("1.1.10", "1.1.9"), "1.1.10 > 1.1.9 (numeric, not text)")
    ok(updater.is_newer("v1.2.0", "1.1.2"), "v-prefixed tag")
    ok(updater.is_newer("2.0", "1.9.9"), "major bump")
    ok(not updater.is_newer("1.1.2", "1.1.2"), "same version is not newer")
    ok(not updater.is_newer("1.2", "1.2.0"), "1.2 == 1.2.0")
    ok(not updater.is_newer("1.1.1", "1.1.2"), "older is not newer")
    ok(not updater.is_newer("1.3.0-beta", "1.1.2"), "non-numeric tag ignored")
    ok(not updater.is_newer("1.3.0", "dev"), "dev never compares")


def t_check():
    print("check")
    net = FakeNet({})
    ok(updater.check("dev", net) == {"current": "dev", "dev": True, "update": False} and not net.calls,
       "dev build: no update, no network call")
    net = FakeNet({updater.API_URL: release()})
    info = updater.check("1.1.2", net)
    ok(info["update"] and info["latest"] == "1.2.0" and info["url"] == GOOD["url"]
       and info["digest"] == GOOD["digest"] and info["size"] == len(PAYLOAD), "newer release found")
    ok(not updater.check("1.2.0", FakeNet({updater.API_URL: release()}))["update"], "already latest")
    ok(not updater.check("1.1.2", FakeNet({updater.API_URL: release(name="Other.exe")}))["update"],
       "release without the installer asset: no update")
    raises(lambda: updater.check("1.1.2", FakeNet({})), "network error -> UpdateError", "github")
    raises(lambda: updater.check("1.1.2", FakeNet({updater.API_URL: b"<html>"})), "bad JSON -> UpdateError")


def t_download():
    print("download + verify")
    d = TMP / "dl"
    seen = []
    p = updater.download(GOOD, d, lambda a, b: seen.append((a, b)), FakeNet({GOOD["url"]: PAYLOAD}))
    ok(p.read_bytes() == PAYLOAD and p.name == GOOD["asset"], "good download kept")
    ok(seen and seen[-1] == (len(PAYLOAD), len(PAYLOAD)), "progress reported to 100%")

    bad = dict(GOOD, digest="sha256:" + "0" * 64)
    raises(lambda: updater.download(bad, d, None, FakeNet({GOOD["url"]: PAYLOAD})), "digest mismatch refused", "sha256")
    ok(not (d / GOOD["asset"]).exists(), "file deleted after digest mismatch")

    raises(lambda: updater.download(GOOD, d, None, FakeNet({GOOD["url"]: PAYLOAD[:-10]})), "short download refused", "size")
    ok(not (d / GOOD["asset"]).exists(), "file deleted after size mismatch")
    raises(lambda: updater.download(GOOD, d, None, FakeNet({GOOD["url"]: PAYLOAD + b"x"})), "long download refused", "larger")
    raises(lambda: updater.download(dict(GOOD, digest=""), d, None, FakeNet({GOOD["url"]: PAYLOAD})),
           "missing digest refused", "digest")
    raises(lambda: updater.download(dict(GOOD, digest="md5:abc"), d, None, FakeNet({GOOD["url"]: PAYLOAD})),
           "non-sha256 digest refused", "digest")


def t_urls():
    print("download URL allow-list")
    for url in [
        "http://github.com/weilun9/doc2excel/releases/download/v1.2.0/x.exe",
        "https://evil.example/weilun9/doc2excel/releases/download/v1.2.0/x.exe",
        "https://github.com.evil.example/weilun9/doc2excel/releases/download/v1.2.0/x.exe",
        "https://github.com/someone/doc2excel/releases/download/v1.2.0/x.exe",
        "https://github.com/weilun9/doc2excel-fork/releases/download/v1.2.0/x.exe",
        "https://github.com/weilun9/doc2excel/archive/v1.2.0.zip",
        "https://github.com/weilun9/doc2excel/releases/download/../../../evil/x.exe",
        "https://user:pw@github.com/weilun9/doc2excel/releases/download/v1.2.0/x.exe",
        "https://github.com:8443/weilun9/doc2excel/releases/download/v1.2.0/x.exe",
        "https://objects.githubusercontent.com/github-production-release-asset/x.exe",
        "file:///C:/Windows/System32/calc.exe",
        "",
    ]:
        net = FakeNet({url: PAYLOAD})
        raises(lambda: updater.download(dict(GOOD, url=url), TMP / "dl2", None, net), f"refused {url!r}", "refused")
        ok(not net.calls, "  ...and nothing was fetched")
    ok(updater.allowed_url(GOOD["url"]), "the repo's own release URL is allowed")


def t_updater_flow():
    print("Updater install flow")
    launched, exited = [], []
    up = updater.Updater("1.1.2", FakeNet({updater.API_URL: release(), GOOD["url"]: PAYLOAD}),
                         launcher=launched.append, on_launched=lambda: exited.append(1))
    ok(not up.install(), "install refused before a check")
    ok(up.check()["update"], "check finds 1.2.0")
    ok(up.install(), "install started")
    for _ in range(100):
        if exited or up.status()["state"] == "error":
            break
        time.sleep(0.05)
    st = up.status()
    ok(st["state"] == "installing" and launched and launched[0].name == GOOD["asset"] and exited,
       f"installer launched, app told to exit ({st})")

    launched.clear(); exited.clear()
    up = updater.Updater("1.1.2", FakeNet({updater.API_URL: release(digest="sha256:" + "1" * 64),
                                           GOOD["url"]: PAYLOAD}),
                         launcher=launched.append, on_launched=lambda: exited.append(1))
    up.check(); up.install()
    for _ in range(100):
        if up.status()["state"] == "error":
            break
        time.sleep(0.05)
    st = up.status()
    ok(st["state"] == "error" and "sha256" in st["error"] and not launched and not exited,
       "tampered download: error, installer never started")


def t_tls():
    print("HTTPS certificate checks")
    import ssl
    from app import net
    ctx = net.tls_context()
    ok(type(ctx).__module__.startswith("truststore"), f"Windows trust store used ({type(ctx).__module__})")
    ok(ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname, "certificates and host names are verified")
    ok(updater.check.__defaults__[-1] is net.urlopen and updater.download.__defaults__[-1] is net.urlopen,
       "updater uses the verified context by default")


def t_endpoints():
    print("/api/update/* endpoints are local-only")
    from app import config
    config.ENV_PATH = TMP / ".env"
    config.ENV_PATH.write_text("", encoding="utf-8")
    config.load(); config.ensure_dirs()
    from app.server import AppServer
    from app.store import Store
    up = updater.Updater("1.1.2", FakeNet({updater.API_URL: release(), GOOD["url"]: PAYLOAD}),
                         launcher=lambda p: None, on_launched=lambda: None)
    srv = AppServer(Store(), 8765, up)
    c = srv.app.test_client()
    good = {"Host": "127.0.0.1:8765", "X-D2X-Key": srv.api_key}
    local = {"REMOTE_ADDR": "127.0.0.1"}
    for method, path in (("get", "/api/update/check"), ("post", "/api/update/install"), ("get", "/api/update/status")):
        r = getattr(c, method)(path, headers={"Host": "127.0.0.1:8765"}, environ_base=local)
        ok(r.status_code == 403, f"{method.upper()} {path} without key -> 403")
        r = getattr(c, method)(path, headers=good, environ_base={"REMOTE_ADDR": "192.168.1.50"})
        ok(r.status_code == 403, f"{method.upper()} {path} from the LAN -> 403")
        r = getattr(c, method)(path, headers={**good, "Host": "evil.example:8765"}, environ_base=local)
        ok(r.status_code == 403, f"{method.upper()} {path} with foreign Host -> 403")
    r = c.post("/api/update/install", headers=good, environ_base=local)
    ok(r.status_code == 400, "install before check -> 400")
    r = c.get("/api/update/check", headers=good, environ_base=local)
    j = r.get_json()
    ok(r.status_code == 200 and j["update"] and j["latest"] == "1.2.0" and "url" not in j,
       "check works locally and does not expose the download URL")
    ok(c.get("/api/update/status", headers=good, environ_base=local).get_json()["current"] == "1.1.2", "status")


if __name__ == "__main__":
    for fn in (t_versions, t_check, t_download, t_urls, t_updater_flow, t_tls, t_endpoints):
        fn()
    print(f"\nALL {PASSED} CHECKS PASSED")
