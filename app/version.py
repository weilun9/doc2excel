"""App version. build.ps1 writes app/_build_version.py (git-ignored) before
packaging; a source checkout has no such file and reports "dev", which never
checks for updates."""
try:
    from ._build_version import VERSION
except ImportError:  # running from source
    VERSION = "dev"
