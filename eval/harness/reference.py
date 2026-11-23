"""Compute reference answers for eval/harness/cases.yaml with raw SQL.

The SQL here is written from the schema documentation (table grains and field
notes in the data dictionary), not by calling example_server.pims.repository,
so a bug in the server's query layer cannot also hide in the references.

    uv run python eval/harness/reference.py            # writes references.json
    uv run python eval/harness/reference.py --db PATH  # another seed

The seed path is PIMS_DB_PATH if set, else the default build location. All
values are computed from the SYNTHETIC demonstration seed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_common import seed_content_sha256  # noqa: E402

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "synthetic" / "pims" / "pims_pmis_synthetic.sqlite"
STAFFING_DIR = REPO_ROOT / "data" / "synthetic" / "pims_staffing"
REFERENCES_PATH = HARNESS_DIR / "references.json"

# Checkpoint-hour grain: aggregate modset rows first, then join waits.
_CHECKPOINT_HOUR = """
SELECT date, hour, airport_code, checkpoint_id, screening_type,
       SUM(passenger_throughput) AS pax,
       COUNT(DISTINCT modset_id) AS modsets
FROM customer_throughput
GROUP BY date, hour, airport_code, checkpoint_id, screening_type
"""

# case id -> (sql, params). Each query returns one row with one column.
SQL_REFERENCES: dict[str, tuple[str, list[Any]]] = {
    "lookup-01": (
        """SELECT p95_wait_minutes FROM checkpoint_wait_times
           WHERE date = ? AND hour = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-11-02", 17, "ATL", "Checkpoint A", "Standard"],
    ),
    "lookup-02": (
        """SELECT SUM(passenger_throughput) FROM customer_throughput
           WHERE date = ? AND hour = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-10-25", 8, "ORD", "North", "PreCheck"],
    ),
    "lookup-03": (
        """SELECT max_wait_minutes FROM checkpoint_wait_times
           WHERE date = ? AND hour = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-11-14", 16, "BWI", "Checkpoint A", "Standard"],
    ),
    "lookup-04": (
        """SELECT COUNT(DISTINCT modset_id) FROM customer_throughput
           WHERE date = ? AND hour = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ? AND machine_type = 'AIT'""",
        ["2026-10-30", 18, "IAD", "Checkpoint B", "Standard"],
    ),
    "lookup-05": ("SELECT COUNT(DISTINCT date) FROM customer_throughput", []),
    "aggregation-01": (
        """SELECT COUNT(DISTINCT modset_id) FROM customer_throughput
           WHERE date = ? AND hour = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-11-05", 12, "LAX", "South", "Standard"],
    ),
    "aggregation-02": (
        f"""SELECT SUM(modsets) FROM ({_CHECKPOINT_HOUR})
           WHERE date = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-11-01", "DCA", "North", "Standard"],
    ),
    "aggregation-03": (
        """SELECT SUM(passenger_throughput) FROM customer_throughput
           WHERE date = ? AND airport_code = ?""",
        ["2026-11-08", "IAD"],
    ),
    "aggregation-04": (
        """SELECT AVG(p95_wait_minutes) FROM checkpoint_wait_times
           WHERE date BETWEEN ? AND ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-11-09", "2026-11-15", "BWI", "Checkpoint B", "PreCheck"],
    ),
    "aggregation-05": (
        """SELECT COUNT(*) FROM checkpoint_wait_times
           WHERE date = ? AND airport_code = ? AND max_wait_minutes > 15""",
        ["2026-11-05", "ATL"],
    ),
    "aggregation-06": (
        "SELECT national_throughput FROM national_daily_baseline WHERE date = ?",
        ["2026-11-12"],
    ),
    "join-grain-01": (
        f"""SELECT CAST(pax AS REAL) / modsets FROM ({_CHECKPOINT_HOUR})
           WHERE date = ? AND hour = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-10-28", 7, "ATL", "Checkpoint B", "Standard"],
    ),
    "join-grain-02": (
        f"""SELECT AVG(t.pax) FROM ({_CHECKPOINT_HOUR}) AS t
           JOIN checkpoint_wait_times AS w
             ON t.date = w.date AND t.hour = w.hour
            AND t.airport_code = w.airport_code
            AND t.checkpoint_id = w.checkpoint_id
            AND t.screening_type = w.screening_type
           WHERE t.date BETWEEN ? AND ? AND t.airport_code = ?
             AND t.checkpoint_id = ? AND t.screening_type = ?
             AND w.p95_wait_minutes > 20""",
        ["2026-11-09", "2026-11-15", "LAX", "North", "Standard"],
    ),
    "join-grain-03": (
        """SELECT COUNT(*) FROM checkpoint_wait_times
           WHERE date = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ? AND max_wait_minutes > 30""",
        ["2026-11-03", "ORD", "South", "Standard"],
    ),
    "comparison-01": (
        """SELECT 100.0 * (national_throughput - prior_year_same_weekday_throughput)
                  / prior_year_same_weekday_throughput
           FROM national_daily_baseline WHERE date = ?""",
        ["2026-11-10"],
    ),
    "comparison-02": (
        """SELECT 100.0 * (b.pax - a.pax) / a.pax FROM
             (SELECT SUM(passenger_throughput) AS pax FROM customer_throughput
               WHERE date = ? AND airport_code = ?) AS a,
             (SELECT SUM(passenger_throughput) AS pax FROM customer_throughput
               WHERE date = ? AND airport_code = ?) AS b""",
        ["2026-11-02", "ATL", "2026-11-09", "ATL"],
    ),
    "comparison-03": (
        """SELECT airport_code FROM customer_throughput GROUP BY airport_code
           ORDER BY SUM(passenger_throughput) DESC LIMIT 1""",
        [],
    ),
    "comparison-04": (
        """SELECT
             (SELECT AVG(p95_wait_minutes) FROM checkpoint_wait_times
               WHERE date BETWEEN ?1 AND ?2 AND airport_code = ?3
                 AND checkpoint_id = ?4 AND screening_type = 'Standard')
           - (SELECT AVG(p95_wait_minutes) FROM checkpoint_wait_times
               WHERE date BETWEEN ?1 AND ?2 AND airport_code = ?3
                 AND checkpoint_id = ?4 AND screening_type = 'PreCheck')""",
        ["2026-11-09", "2026-11-15", "DCA", "South"],
    ),
    "staffing-03": (
        """SELECT COUNT(*) FROM checkpoint_wait_times
           WHERE date BETWEEN ? AND ? AND airport_code = ?
             AND p95_wait_minutes > 30""",
        ["2026-11-09", "2026-11-15", "LAX"],
    ),
    "staffing-04": (
        """SELECT COUNT(*) FROM checkpoint_wait_times
           WHERE date BETWEEN ? AND ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ? AND p95_wait_minutes > 15""",
        ["2026-11-01", "2026-11-07", "DCA", "North", "PreCheck"],
    ),
    "adversarial-01": (
        "SELECT national_throughput FROM national_daily_baseline WHERE date = ?",
        ["2026-11-10"],
    ),
    "adversarial-02": (
        """SELECT p95_wait_minutes FROM checkpoint_wait_times
           WHERE date = ? AND hour = ? AND airport_code = ? AND checkpoint_id = ?
             AND screening_type = ?""",
        ["2026-11-11", 9, "LAX", "North", "PreCheck"],
    ),
}


def database_path(override: str | None = None) -> Path:
    raw = override or os.environ.get("PIMS_DB_PATH") or str(DEFAULT_DB)
    return Path(raw).resolve()


def connect_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(
            f"seed not found at {path}; build it with "
            "`uv run python scripts/build_pims_seed.py`"
        )
    uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar(conn: sqlite3.Connection, sql: str, params: list[Any]) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"reference query returned no value: {sql!r} {params!r}")
    value = row[0]
    return round(value, 6) if isinstance(value, float) else value


def _read_csv(name: str, staffing_dir: Path) -> list[dict[str, str]]:
    with (staffing_dir / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Installed modsets for one checkpoint and screening type: the distinct
# modset_ids the seed ever uses for that lane. The seed never shares a modset
# between Standard and PreCheck in the same hour, so the checkpoint-wide count
# in checkpoint_config.csv (WTMD + AIT) overstates what one lane can reach.
# This matches staffing_required_for_forecast's definition of installed.
_INSTALLED_MODSETS_SQL = """
SELECT COUNT(DISTINCT modset_id) FROM customer_throughput
WHERE airport_code = ? AND checkpoint_id = ? AND screening_type = ?
"""


def staffing_references(
    conn: sqlite3.Connection, staffing_dir: Path = STAFFING_DIR
) -> dict[str, dict]:
    """Property references for staffing cases, from the synthetic seed and config."""
    rules = _read_csv("staffing_rules.csv", staffing_dir)
    installed_params = ["ATL", "Checkpoint A", "Standard"]

    standard = [r for r in rules if r["screening_type"] == "Standard"]
    min_staff_per_modset = min(
        int(r["staff_per_modset"]) * float(r["relief_factor"]) for r in standard
    )
    added, available = 3, 6
    needed = round(added * min_staff_per_modset, 4)
    return {
        "staffing-01": {
            "installed_modsets": _scalar(
                conn, _INSTALLED_MODSETS_SQL, installed_params
            ),
            "sql": " ".join(_INSTALLED_MODSETS_SQL.split()),
            "params": installed_params,
            "source": "distinct modset_ids the seed uses for ATL / Checkpoint A "
            "/ Standard (Standard and PreCheck never share a modset)",
        },
        "staffing-02": {"source": "property-only case; no numeric reference"},
        "staffing-05": {
            "minimum_staff_needed": needed,
            "available_staff": available,
            "constraint_binding": needed > available,
            "source": "staffing_rules.csv Standard rows: 3 added modsets x "
            "min(staff_per_modset x relief_factor)",
        },
    }


def compute_references(db_path: Path, staffing_dir: Path = STAFFING_DIR) -> dict:
    conn = connect_ro(db_path)
    try:
        cases: dict[str, dict] = {}
        for case_id, (sql, params) in SQL_REFERENCES.items():
            cases[case_id] = {
                "value": _scalar(conn, sql, params),
                "sql": " ".join(sql.split()),
                "params": params,
            }
        cases.update(staffing_references(conn, staffing_dir))
    finally:
        conn.close()
    return {
        "note": "Computed from the SYNTHETIC demonstration seed with raw SQL; "
        "not real TSA data.",
        "seed_content_sha256": seed_content_sha256(db_path),
        "cases": dict(sorted(cases.items())),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="seed path (default: PIMS_DB_PATH or seed)")
    parser.add_argument("--out", default=str(REFERENCES_PATH))
    args = parser.parse_args(argv)
    refs = compute_references(database_path(args.db))
    Path(args.out).write_text(
        json.dumps(refs, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    for case_id, ref in refs["cases"].items():
        shown = ref.get("value", {k: v for k, v in ref.items() if k != "source"})
        print(f"{case_id:16} {shown}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
