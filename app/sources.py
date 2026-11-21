"""Other ways documents arrive: a watched folder and a WIA desktop scanner.
Also LAN address helpers for the phone QR code."""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
import uuid
from pathlib import Path

from . import config, ocr


# ---------------- watched folder ----------------
class FolderWatcher:
    """Polls WATCH_DIR every 2 s while the app is open. A file is imported once
    its size stops changing, then moved to WATCH_DIR/imported/."""

    def __init__(self, store):
        self.store = store
        self._sizes: dict[str, int] = {}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="watcher", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(2.0):
            watch = config.get("WATCH_DIR")
            if not watch:
                continue
            folder = Path(watch)
            if not folder.is_dir():
                continue
            try:
                entries = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ocr.SUPPORTED_EXT]
            except OSError:
                continue
            seen = set()
            for p in entries:
                key = str(p)
                seen.add(key)
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if size > 0 and self._sizes.get(key) == size:
                    self._import(p)
                    self._sizes.pop(key, None)
                else:
                    self._sizes[key] = size
            for k in list(self._sizes):
                if k not in seen:
                    self._sizes.pop(k, None)

    def _import(self, p: Path):
        done = p.parent / "imported"
        try:
            done.mkdir(exist_ok=True)
            target = done / p.name
            if target.exists():
                target = done / f"{p.stem}_{uuid.uuid4().hex[:6]}{p.suffix}"
            self.store.add_file(p, name=p.name, source="folder")
            p.replace(target)
        except OSError:
            pass


# ---------------- WIA scanner ----------------
def scan_with_wia() -> Path | None:
    """Show the Windows scanner dialog and return the saved image path.
    Returns None if the user cancels. Raises RuntimeError if no scanner/WIA."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        try:
            dlg = win32com.client.Dispatch("WIA.CommonDialog")
            # DeviceType 1 = scanner; Intent 1 = colour; JPEG format GUID
            img = dlg.ShowAcquireImage(1, 1, 0, "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}", False, True, False)
        except Exception as e:  # pywintypes.com_error
            msg = str(e)
            if "0x80210015" in msg or "-2145320939" in msg:
                raise RuntimeError("No scanner found") from e
            raise RuntimeError(f"Scanner error: {msg}") from e
        if img is None:
            return None
        out = config.INBOX_DIR / f"scan_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        img.SaveFile(str(out))
        return out
    finally:
        pythoncom.CoUninitialize()


# ---------------- LAN address ----------------
def lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the default route
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    good = [ip for ip in ips if ipaddress.ip_address(ip).is_private and not ip.startswith("127.")
            and not ip.startswith("169.254.")]
    # Order for the QR code (stable sort keeps the default route first within a rank):
    # 0  phone hotspot / USB tethering (iPhone 172.20.10.x, Windows hotspot 192.168.137.x).
    #    If the PC is linked straight to the phone, that link always works, while home
    #    Wi-Fi may isolate devices.
    # 1  normal LAN addresses
    # 2  other 172.16-31.x, usually virtual adapters (WSL / Hyper-V)
    def rank(ip: str) -> int:
        if ip.startswith(("172.20.10.", "192.168.137.")):
            return 0
        return 2 if ip.startswith("172.") else 1
    good.sort(key=rank)
    return good or ["127.0.0.1"]
