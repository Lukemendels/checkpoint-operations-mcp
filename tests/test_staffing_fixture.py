"""The synthetic fixture is deterministic and matches the committed files."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO / "data" / "synthetic" / "staffing"
FILES = [
    "throughput.csv",
    "wait_times.csv",
    "national_daily_baseline.csv",
    "checkpoint_config.csv",
    "staffing_rules.csv",
]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "make_staffing_fixture", REPO / "scripts" / "make_staffing_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _text(path: Path) -> str:
    """Read as text with newlines normalized (git may check out CRLF)."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_committed_fixture_matches_generator(tmp_path: Path):
    gen = _load_generator()
    counts = gen.build(tmp_path)
    assert counts["wait_times.csv"] == 3 * 2 * 7 * 19 * 2
    for name in FILES:
        assert _text(tmp_path / name) == _text(FIXTURE_DIR / name), (
            f"{name} differs from the generator output; regenerate the fixture"
        )


def test_generator_is_deterministic(tmp_path: Path):
    gen = _load_generator()
    gen.build(tmp_path / "a")
    gen.build(tmp_path / "b")
    for name in FILES:
        assert _text(tmp_path / "a" / name) == _text(tmp_path / "b" / name)
