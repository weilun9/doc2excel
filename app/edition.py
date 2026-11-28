"""Which edition this build is. One code base, two products:

* internal — "Doc2Excel Pro": Excel + SmartAcc DynaMod + send to AI ERP.
  Keeps the technical identity every earlier release used (install folder,
  data folder, single-instance lock, update channel), so 1.x upgrades in place.
* public — "Doc2Excel": recognise, review, export to Excel. Released from its
  own repository; installs and stores data beside, never over, the internal one.

build.ps1 -Edition <id> writes app/_build_edition.py (git-ignored) before
packaging. A source checkout uses D2X_EDITION, else "internal"."""
from __future__ import annotations

import os

EDITIONS: dict[str, dict] = {
    "internal": {
        "name": "Doc2Excel Pro",
        "app_id": "Doc2Excel",                 # folders, lock, uninstall key: unchanged since 1.0
        "repo": "weilun9/doc2excel",
        "setup": "Doc2Excel-Setup",            # installer asset: <setup>-<version>.exe
        "destinations": ("excel", "dynamod", "erp"),
    },
    "public": {
        "name": "Doc2Excel",
        "app_id": "Doc2ExcelApp",
        "repo": "weilun9/doc2excel-app",
        "setup": "Doc2Excel-Setup",
        "destinations": ("excel",),
    },
}

try:
    from ._build_edition import EDITION as _BUILT  # written by build.ps1
except ImportError:  # running from source
    _BUILT = None

ID = _BUILT or os.environ.get("D2X_EDITION", "").strip().lower() or "internal"
if ID not in EDITIONS:
    raise SystemExit(f"Unknown edition {ID!r} (expected one of {', '.join(EDITIONS)})")

_E = EDITIONS[ID]
NAME: str = _E["name"]
APP_ID: str = _E["app_id"]
REPO: str = _E["repo"]
SETUP_PREFIX: str = _E["setup"]
DESTINATIONS: tuple[str, ...] = _E["destinations"]


def has(destination: str) -> bool:
    return destination in DESTINATIONS
