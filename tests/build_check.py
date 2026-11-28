"""Check a built dist\\Doc2Excel.exe carries exactly its edition's code.

    .venv\\Scripts\\python tests\\build_check.py <internal|public> [dist\\Doc2Excel.exe]

Reads the PyInstaller archive (no need to run the exe): the public build must
not contain the DynaMod or ERP modules or the DynaMod templates; every build
must contain its own destinations and the edition marker."""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.edition import EDITIONS  # noqa: E402

edition = sys.argv[1] if len(sys.argv) > 1 else "internal"
exe = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "dist" / "Doc2Excel.exe"
want = set(EDITIONS[edition]["destinations"])
every = set(EDITIONS["internal"]["destinations"])

arc = CArchiveReader(str(exe))
names = list(arc.toc)
pyz_name = next(n for n in names if n.endswith(".pyz"))
modules = set(arc.open_embedded_archive(pyz_name).toc)

fail = []
for d in sorted(every):
    for mod in (f"app.destinations.{d}", f"app.{d}") if d != "excel" else (f"app.destinations.{d}",):
        if (mod in modules) != (d in want):
            fail.append(f"{mod}: {'missing' if d in want else 'must not be in this edition'}")
for mod in ("app._build_edition", "app.edition", "app.destinations.registry"):
    if mod not in modules:
        fail.append(f"{mod}: missing")
has_dm_data = any(n.replace("\\", "/").startswith("assets/dynamod/") for n in names)
if has_dm_data != ("dynamod" in want):
    fail.append("assets/dynamod: " + ("missing" if "dynamod" in want else "must not be in this edition"))

print(f"{exe.name} [{edition}]: {len(modules)} modules")
if fail:
    print("FAILED:\n  " + "\n  ".join(fail))
    sys.exit(1)
print("build OK: carries exactly", ", ".join(sorted(want)))
