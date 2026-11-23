"""Resolve committed source fixtures or the self-contained runtime bundle."""

from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_BUNDLED = _PACKAGE / "runtime_data" / "synthetic"
_SOURCE = _PACKAGE.parent.parent / "data" / "synthetic"

# Once a staged runtime exists, never silently mix in data from another checkout.
SYNTHETIC_ROOT = _BUNDLED if _BUNDLED.is_dir() else _SOURCE
