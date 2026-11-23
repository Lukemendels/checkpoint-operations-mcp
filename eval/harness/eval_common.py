"""Shared loaders and text patterns for the evaluation harness."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parents[1]
CASES_PATH = HARNESS_DIR / "cases.yaml"
REFERENCES_PATH = HARNESS_DIR / "references.json"
RUNS_DIR = REPO_ROOT / "eval" / "runs"


@lru_cache(maxsize=4)
def load_suite(path: str = str(CASES_PATH)) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        suite = yaml.safe_load(handle)
    ids = [c["id"] for c in suite["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in cases.yaml")
    return suite


def load_cases(path: str = str(CASES_PATH)) -> list[dict[str, Any]]:
    return list(load_suite(path)["cases"])


def rubric_library(path: str = str(CASES_PATH)) -> dict[str, list[str]]:
    return dict(load_suite(path)["rubric_library"])


def load_references(path: Path = REFERENCES_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} missing; run `uv run python eval/harness/reference.py`"
        )
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def find_matches(patterns: list[str], text: str) -> list[re.Match[str]]:
    found: list[re.Match[str]] = []
    for pattern in patterns:
        found.extend(_compile(pattern).finditer(text or ""))
    return found


def matches_any(patterns: list[str], text: str) -> bool:
    return any(_compile(p).search(text or "") for p in patterns)


SYNTHETIC_PATTERNS = rubric_library()["synthetic_label"]
CAUSAL_PATTERNS = rubric_library()["causal_claim"]


def seed_content_sha256(path) -> str | None:
    """Hash of the three canonical tables' rows, in key order.

    The SQLite file itself differs between rebuilds (dataset_metadata stores
    a creation timestamp), so the file hash cannot pin the data. This hash is
    identical for any rebuild with the same start date, days and random seed.
    """
    import hashlib
    import sqlite3
    from pathlib import Path

    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        for table in (
            "customer_throughput",
            "checkpoint_wait_times",
            "national_daily_baseline",
        ):
            digest.update(table.encode())
            for row in conn.execute(
                f"SELECT * FROM {table} ORDER BY 1,2,3,4,5,6,7"
                if table != "national_daily_baseline"
                else f"SELECT * FROM {table} ORDER BY 1"
            ):
                digest.update(repr(row).encode())
    finally:
        conn.close()
    return digest.hexdigest()
