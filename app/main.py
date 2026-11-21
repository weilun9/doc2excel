"""Entry point. GUI by default; `--process FILE... --out OUT.xlsx` runs headless."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config


class Api:
    """Native helpers exposed to the page as window.pywebview.api.*"""

    def __init__(self):
        self._window = None
        self._server = None

    def save_dialog(self, default_name: str = "Doc2Excel.xlsx"):
        import webview
        res = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(config.DOCS_HOME),
            save_filename=default_name, file_types=("Excel workbook (*.xlsx)",))
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    def open_files(self):
        import webview
        res = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True,
            file_types=("Documents (*.jpg;*.jpeg;*.png;*.bmp;*.tif;*.tiff;*.webp;*.heic;*.pdf)", "All files (*.*)"))
        return list(res or [])

    def pick_folder(self):
        import webview
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return (res[0] if res else None)

    def save_text(self, content: str, default_name: str = "document.txt"):
        """Save OCR text (.txt) or a table (.csv) where the user chooses."""
        import webview
        is_csv = default_name.lower().endswith(".csv")
        res = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(config.DOCS_HOME), save_filename=default_name,
            file_types=("CSV (*.csv)",) if is_csv else ("Text file (*.txt)",))
        if not res:
            return None
        path = Path(res if isinstance(res, str) else res[0])
        if not path.suffix:
            path = path.with_suffix(".csv" if is_csv else ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        # BOM so Excel and older Notepad read Chinese correctly.
        path.write_text(content, encoding="utf-8-sig", newline="")
        return str(path)

    def copy_text(self, content: str):
        """Clipboard via Win32, which works regardless of WebView permissions."""
        import win32clipboard
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                import time
                time.sleep(0.1)
        else:
            return False
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, content)
        finally:
            win32clipboard.CloseClipboard()
        return True

    def open_path(self, path: str):
        if path and Path(path).exists():
            os.startfile(path)  # noqa: S606 - opens the user's own exported file
        return True


_mutex = None


def _claim_single_instance() -> bool:
    """Only one Doc2Excel window may run. A second launch focuses the first
    window and exits, so there is only ever one server and one QR token."""
    global _mutex
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex = kernel32.CreateMutexW(None, False, "Local\\Doc2Excel.SingleInstance")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return True
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "Doc2Excel")
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return False


def run_gui():
    if not _claim_single_instance():
        return
    import webview
    from .server import AppServer
    from .sources import FolderWatcher
    from .store import Store

    config.load()
    config.ensure_dirs()
    store = Store()
    store.start_worker()
    watcher = FolderWatcher(store)
    watcher.start()
    server = AppServer(store, int(config.get("SERVER_PORT") or 8765))
    port = server.start()

    api = Api()
    api._server = server
    window = webview.create_window(
        "Doc2Excel", f"http://127.0.0.1:{port}/", js_api=api,
        width=1440, height=900, min_size=(1000, 640))
    api._window = window
    try:
        webview.start(private_mode=True)
    finally:
        # Nothing keeps running after the window closes.
        watcher.stop()
        server.stop()
        os._exit(0)


def run_cli(files: list[str], out: str, mode: str) -> int:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="doc2excel_cli_"))
    config.INBOX_DIR = tmp / "inbox"
    config.STATE_PATH = tmp / "state.json"
    config.load()
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    from . import exporter
    from .store import Store
    store = Store()
    store.start_worker()
    for f in files:
        if not store.add_file(Path(f)):
            print(f"skip (unsupported): {f}")
    store.wait_idle()
    docs = [store.get(i) for i in reversed(store.order)]
    for d in docs:
        print(f"== {d['name']}: status={d['status']} ocr={d['engine']} table={d['mode']}")
        if d.get("warning"):
            print("   warning:", d["warning"])
        if d.get("error"):
            print("   error:", d["error"])
        if d.get("table"):
            t = d["table"]
            print(f"   title={t['title']!r} fields={len(t['fields'])} columns={t['columns']} rows={len(t['rows'])}")
    ready = [d for d in docs if d.get("table")]
    if not ready:
        return 1
    path = exporter.export(ready, out, mode)
    print("exported:", path)
    return 0


def _setup_streams(cli: bool):
    """A windowed exe has no stdout/stderr. In CLI mode attach to the calling
    console; otherwise send output to a log file in the data folder."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    if cli:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = sys.stdout
            return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = open(config.DATA_DIR / "doc2excel.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


def main(argv=None):
    _setup_streams(cli=bool(argv or sys.argv[1:]))
    ap = argparse.ArgumentParser(prog="Doc2Excel")
    ap.add_argument("--process", nargs="+", metavar="FILE", help="process files without the GUI")
    ap.add_argument("--out", default="Doc2Excel.xlsx")
    ap.add_argument("--mode", default="both", choices=["per_doc", "combined", "both"])
    ap.add_argument("--server-only", action="store_true", help="dev: run the server without a window")
    args = ap.parse_args(argv)
    if args.server_only:
        import time
        from .server import AppServer
        from .sources import FolderWatcher
        from .store import Store
        config.load(); config.ensure_dirs()
        store = Store(); store.start_worker(); FolderWatcher(store).start()
        srv = AppServer(store, int(config.get("SERVER_PORT")))
        print("serving on", srv.start(), srv.phone_urls(), flush=True)
        while True:
            time.sleep(3600)
    if args.process:
        sys.exit(run_cli(args.process, args.out, args.mode))
    run_gui()


if __name__ == "__main__":
    main()
