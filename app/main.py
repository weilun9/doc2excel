"""Entry point. GUI by default; `--process FILE... --out OUT.xlsx` runs headless."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config, edition


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

    def open_xlsx(self):
        """Pick one Excel workbook (DynaMod 整理模板 / an edited 整理表)."""
        import webview
        res = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=str(config.DOCS_HOME),
            file_types=("Excel workbook (*.xlsx)",))
        return (res[0] if res else None)

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
        return set_clipboard_text(content)

    def open_path(self, path: str):
        if path and Path(path).exists():
            os.startfile(path)  # noqa: S606 - opens the user's own exported file
        return True


def set_clipboard_text(text: str) -> bool:
    """Put Unicode text on the Windows clipboard. False if it stays busy.

    Plain Win32 through ctypes with explicit 64-bit handle types:
    pywin32's win32clipboard.SetClipboardData(CF_UNICODETEXT, str) corrupted
    the heap (0xC0000374) when called from pywebview's js_api thread, which
    killed the whole app the moment Copy was pressed."""
    import ctypes
    import time
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002

    data = (text or "").encode("utf-16-le") + b"\x00\x00"  # NUL-terminated
    for _ in range(10):  # another app may hold the clipboard briefly
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)  # still ours only if the call failed
            return False
        return True  # the clipboard owns the memory now
    finally:
        user32.CloseClipboard()


_mutex = None


def _claim_single_instance() -> bool:
    """Only one Doc2Excel window may run. A second launch focuses the first
    window and exits, so there is only ever one server and one QR token."""
    global _mutex
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    # Per edition, so the internal and public apps can run side by side.
    _mutex = kernel32.CreateMutexW(None, False, f"Local\\{edition.APP_ID}.SingleInstance")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return True
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, edition.NAME)
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
        edition.NAME, f"http://127.0.0.1:{port}/", js_api=api,
        width=1440, height=900, min_size=(1000, 640),
        text_select=True)  # OCR text and table cells can be selected and copied
    api._window = window
    try:
        webview.start(private_mode=True)
    finally:
        # Nothing keeps running after the window closes.
        watcher.stop()
        server.stop()
        os._exit(0)


def run_cli(files: list[str], out: str, mode: str, dynamod_wb: str | None = None) -> int:
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
    if dynamod_wb:
        return _cli_dynamod(ready, dynamod_wb, out)
    path = exporter.export(ready, out, mode)
    print("exported:", path)
    return 0


def _cli_dynamod(docs: list[dict], workbook: str, out: str) -> int:
    """--dynamod: 进单整理表 at --out + import files for bills that pass."""
    from . import dynamod
    if not Path(workbook).is_file():
        print("DynaMod template not found:", workbook)
        return 1
    lk = dynamod.Lookups.from_workbook(workbook)
    loc = config.get("DYNAMOD_LOCATION") or dynamod.DEFAULT_LOCATION
    bills = [dynamod.bill_from_doc(d, lk, loc) for d in docs]
    print("work sheet:", dynamod.write_workbook(bills, workbook, out))
    for r in dynamod.write_import_files(bills, config.get("DYNAMOD_OUT_DIR")):
        print(f"   {r['doc_id']}: " + (f"import file {r['path']}" if r["ok"] else "CHECK - " + "; ".join(r["problems"])))
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
    ap = argparse.ArgumentParser(prog=edition.NAME)
    ap.add_argument("--process", nargs="+", metavar="FILE", help="process files without the GUI")
    ap.add_argument("--out", default="Doc2Excel.xlsx")
    ap.add_argument("--mode", default="both", choices=["per_doc", "combined", "both"])
    if edition.has("dynamod"):
        ap.add_argument("--dynamod", metavar="TEMPLATE.xlsx",
                        help="SmartAcc DynaMod: write a 进单整理表 (--out) from this template "
                             "+ import files into DYNAMOD_OUT_DIR")
        ap.add_argument("--from-worksheet", metavar="整理表.xlsx",
                        help="SmartAcc DynaMod: make import files from an edited, Excel-saved 整理表")
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
    if getattr(args, "from_worksheet", None):
        from . import dynamod
        config.load()
        bills, notes = dynamod.bills_from_workbook(args.from_worksheet)
        for n in notes:
            print(n)
        for r in dynamod.write_import_files(bills, config.get("DYNAMOD_OUT_DIR")):
            print(f"{r['doc_id']}: " + (f"import file {r['path']}" if r["ok"] else "CHECK - " + "; ".join(r["problems"])))
        sys.exit(0)
    if args.process:
        sys.exit(run_cli(args.process, args.out, args.mode, getattr(args, "dynamod", None)))
    run_gui()


if __name__ == "__main__":
    main()
