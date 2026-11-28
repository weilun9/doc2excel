"""Settings each destination needs. Pure data (no imports), so config.py can
read it while it is still loading. Only the destinations of this build's
edition (edition.DESTINATIONS) add their settings and secrets."""
from __future__ import annotations

SPECS: dict[str, dict] = {
    "excel": {
        "settings": {},
        "secrets": (),
    },
    # SmartAcc DynaMod supplier-bill import ("DynaMod 进单"):
    #   the user's own 进单整理模板 (供应商 + 条码对照 sheets, stays on this PC),
    #   DynaMod's blank import templates, the import-file folder, and Location ID.
    "dynamod": {
        "settings": {
            "DYNAMOD_WORKBOOK": "",
            "DYNAMOD_TEMPLATE_DIR": r"C:\SmartAcc\DynaMod\Import Template",
            "DYNAMOD_OUT_DIR": r"C:\Opp",
            "DYNAMOD_LOCATION": "HQ",
        },
        "secrets": (),
    },
    # AI ERP connection ("发送到 ERP"): the ERP address and a scan token the
    # user makes in the ERP (Scan documents > Connect Doc2Excel).
    "erp": {
        "settings": {"ERP_URL": "", "ERP_TOKEN": ""},
        "secrets": ("ERP_TOKEN",),
    },
}


def settings_for(ids) -> tuple[dict[str, str], set[str]]:
    defaults: dict[str, str] = {}
    secrets: set[str] = set()
    for i in ids:
        spec = SPECS[i]
        defaults.update(spec["settings"])
        secrets.update(spec["secrets"])
    return defaults, secrets
