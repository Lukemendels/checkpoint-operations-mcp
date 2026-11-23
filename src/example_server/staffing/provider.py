"""Data provider for the staffing tools.

`SyntheticCsvProvider` reads the five CSV tables described in
data/synthetic/staffing/README.md from a directory. The directory comes from
`STAFFING_DATA_DIR` (see config.py) and defaults to the committed synthetic
fixture. A future provider with the same four methods could wrap the team's
SQLite seed or an internal adapter; the tools only depend on this interface.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

from example_server.config import settings
from example_server.synthetic_paths import SYNTHETIC_ROOT

DATA_LABEL = "synthetic"

_TRUE = {"true", "1", "yes"}

_THROUGHPUT_COLS = {
    "date": str,
    "hour": int,
    "airport_code": str,
    "airport_category": str,
    "checkpoint_id": str,
    "modset_id": str,
    "machine_type": str,
    "screening_type": str,
    "passenger_throughput": int,
    "active_modsets": int,
}
_WAIT_COLS = {
    "date": str,
    "hour": int,
    "airport_code": str,
    "checkpoint_id": str,
    "screening_type": str,
    "avg_wait_minutes": float,
    "p95_wait_minutes": float,
    "max_wait_minutes": float,
    "exceeded_15m_flag": bool,
    "exceeded_30m_flag": bool,
}
_CONFIG_COLS = {
    "airport_code": str,
    "checkpoint_id": str,
    "installed_modsets_wtmd": int,
    "installed_modsets_ait": int,
    "fixed_posts": int,
    "open_hour": int,
    "close_hour": int,
}
_RULE_COLS = {
    "machine_type": str,
    "screening_type": str,
    "assumed_capacity_pax_per_hour": float,
    "staff_per_modset": int,
    "relief_factor": float,
}


class DataUnavailableError(RuntimeError):
    """A required table is missing or malformed; message is safe to return."""


def _convert(value: str, typ: type) -> Any:
    if typ is bool:
        return value.strip().lower() in _TRUE
    if typ is int:
        return int(float(value))
    return typ(value)


def _read_table(path: Path, columns: dict[str, type]) -> list[dict[str, Any]]:
    if not path.is_file():
        raise DataUnavailableError(
            f"table '{path.name}' not found in the configured staffing data directory"
        )
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = set(columns) - set(reader.fieldnames or [])
        if missing:
            raise DataUnavailableError(
                f"table '{path.name}' is missing columns: {sorted(missing)}"
            )
        rows: list[dict[str, Any]] = []
        for raw in reader:
            try:
                rows.append({c: _convert(raw[c], t) for c, t in columns.items()})
            except (TypeError, ValueError) as exc:
                raise DataUnavailableError(
                    f"table '{path.name}' row {len(rows) + 2} could not be parsed"
                ) from exc
    return rows


def default_data_dir() -> Path:
    """Repository fixture directory, resolved relative to this package."""
    return SYNTHETIC_ROOT / "staffing"


class SyntheticCsvProvider:
    """Read-only access to the synthetic tables in one directory."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        configured = data_dir or settings.staffing_data_dir or default_data_dir()
        self.data_dir = Path(configured)

    def throughput(self) -> list[dict[str, Any]]:
        return _read_table(self.data_dir / "throughput.csv", _THROUGHPUT_COLS)

    def wait_times(self) -> list[dict[str, Any]]:
        return _read_table(self.data_dir / "wait_times.csv", _WAIT_COLS)

    def checkpoint_config(self) -> list[dict[str, Any]]:
        return _read_table(self.data_dir / "checkpoint_config.csv", _CONFIG_COLS)

    def staffing_rules(self) -> list[dict[str, Any]]:
        return _read_table(self.data_dir / "staffing_rules.csv", _RULE_COLS)


def seed_config_dir() -> Path:
    """Staffing-owned config tables that accompany the PIMS seed."""
    return SYNTHETIC_ROOT / "pims_staffing"


class PimsSeedProvider:
    """Tables 1-2 from the team's synthetic PIMS SQLite seed; config from CSV.

    The seed is read once per process (it is read-only and ~54k rows). The
    staffing-owned checkpoint_config and staffing_rules live in
    data/synthetic/pims_staffing/ because they are not part of the seed schema.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        self.config_dir = Path(config_dir or seed_config_dir())
        self._throughput: list[dict[str, Any]] | None = None
        self._waits: list[dict[str, Any]] | None = None

    def _query(self, sql: str) -> list[dict[str, Any]]:
        import sqlite3

        from example_server.pims import repository

        try:
            with repository.db_connection() as db:
                return [dict(r) for r in db.execute(sql).fetchall()]
        except FileNotFoundError as exc:
            raise DataUnavailableError(
                "the synthetic PIMS seed was not found; build it with "
                "`uv run python scripts/build_pims_seed.py`"
            ) from exc
        except sqlite3.Error as exc:
            raise DataUnavailableError("the PIMS seed could not be read") from exc

    def throughput(self) -> list[dict[str, Any]]:
        if self._throughput is None:
            self._throughput = self._query(
                "SELECT date, hour, airport_code, airport_category, checkpoint_id, "
                "modset_id, machine_type, screening_type, passenger_throughput, "
                "active_modsets FROM customer_throughput"
            )
        return self._throughput

    def wait_times(self) -> list[dict[str, Any]]:
        if self._waits is None:
            rows = self._query(
                "SELECT date, hour, airport_code, checkpoint_id, screening_type, "
                "avg_wait_minutes, p95_wait_minutes, max_wait_minutes, "
                "exceeded_15m_flag, exceeded_30m_flag FROM checkpoint_wait_times"
            )
            for r in rows:
                r["exceeded_15m_flag"] = bool(r["exceeded_15m_flag"])
                r["exceeded_30m_flag"] = bool(r["exceeded_30m_flag"])
            self._waits = rows
        return self._waits

    def checkpoint_config(self) -> list[dict[str, Any]]:
        return _read_table(self.config_dir / "checkpoint_config.csv", _CONFIG_COLS)

    def staffing_rules(self) -> list[dict[str, Any]]:
        return _read_table(self.config_dir / "staffing_rules.csv", _RULE_COLS)


Provider = SyntheticCsvProvider | PimsSeedProvider


@lru_cache(maxsize=1)
def get_provider() -> Provider:
    """Process-wide provider chosen by STAFFING_SOURCE.

    "pims_seed" (default) reads the team's synthetic PIMS seed, so the staffing
    and pims_* tools answer from one dataset. "csv_fixture" reads the small CSV
    fixture in STAFFING_DATA_DIR (used by the original unit tests).
    """
    if settings.staffing_source == "csv_fixture":
        return SyntheticCsvProvider()
    return PimsSeedProvider()
