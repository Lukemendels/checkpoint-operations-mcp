"""Bounded, parameterized, read-only SQLite queries for the PIMS/PMIS MCP server."""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

from example_server.pims.schema import DATA_DICTIONARY, TABLE_GRAINS
from example_server.synthetic_paths import SYNTHETIC_ROOT

DEFAULT_DB = SYNTHETIC_ROOT / "pims" / "pims_pmis_synthetic.sqlite"
MAX_RETURN_ROWS = 500
MAX_WINDOW_DAYS = 366


def database_path() -> Path:
    return Path(os.environ.get("PIMS_DB_PATH", str(DEFAULT_DB))).resolve()


@contextmanager
def db_connection() -> Iterator[sqlite3.Connection]:
    path = database_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Database missing: {path}; build it with `uv run python scripts/build_pims_seed.py`"
        )
    # mode=ro avoids ever silently creating a blank database. No write tools are exposed.
    uri_path = quote(path.as_posix(), safe="/:")
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 3000")
    try:
        yield conn
    finally:
        conn.close()


def rows(
    conn: sqlite3.Connection, sql: str, values: list[Any] | tuple[Any, ...] = ()
) -> list[dict]:
    return [dict(rec) for rec in conn.execute(sql, values).fetchall()]


def _iso(text: str, name: str) -> str:
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{name} must be YYYY-MM-DD")
    return text


def validate_range(from_date: str, to_date: str) -> tuple[str, str]:
    a, b = _iso(from_date, "from_date"), _iso(to_date, "to_date")
    distance = (date.fromisoformat(b) - date.fromisoformat(a)).days
    if distance < 0 or distance >= MAX_WINDOW_DAYS:
        raise ValueError(
            f"Require from_date <= to_date, window <= {MAX_WINDOW_DAYS} days"
        )
    return a, b


def validate_limit(limit: int) -> int:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_RETURN_ROWS
    ):
        raise ValueError(f"limit must be integer 1..{MAX_RETURN_ROWS}")
    return limit


def _filters(
    airport_code: str | None = None,
    checkpoint_id: str | None = None,
    screening_type: str | None = None,
    machine_type: str | None = None,
    hour: int | None = None,
    *,
    allowed: set[str],
) -> tuple[str, list[Any]]:
    exprs: list[str] = []
    values: list[Any] = []
    candidates = {
        "airport_code": airport_code,
        "checkpoint_id": checkpoint_id,
        "screening_type": screening_type,
        "machine_type": machine_type,
        "hour": hour,
    }
    for name, value in candidates.items():
        if value is None:
            continue
        if name not in allowed:
            raise ValueError(f"Filter {name} is unsupported for this table")
        if name == "airport_code" and not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("airport_code must be three uppercase letters")
        if name == "checkpoint_id" and (
            not isinstance(value, str) or not 1 <= len(value) <= 80
        ):
            raise ValueError("checkpoint_id must be 1..80 characters")
        if name == "screening_type" and value not in ("Standard", "PreCheck"):
            raise ValueError("screening_type must be Standard or PreCheck")
        if name == "machine_type" and value not in ("WTMD", "AIT"):
            raise ValueError("machine_type must be WTMD or AIT")
        if name == "hour" and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 23
        ):
            raise ValueError("hour must be 0..23")
        exprs.append(
            f"{name} = ?"
        )  # Only fixed, developer-authored column names can reach this point.
        values.append(value)
    return (" AND " + " AND ".join(exprs) if exprs else ""), values


def schema() -> dict:
    return {
        "schema_version": "1.0.0",
        "tables": DATA_DICTIONARY,
        "grain_keys": TABLE_GRAINS,
        "important": [
            "Modset throughput tracks screening machine (WTMD/AIT), never X-ray lane.",
            "Wait time has no modset grain; join only after grouping throughput to checkpoint/hour/screening type.",
            "active_modsets is repeated on every modset row within a checkpoint/hour/screening-type group; never sum it.",
            "National baseline covers all U.S. airports, while six sample airports are a small synthetic subset.",
            "All values are synthetic unless a separately verified authoritative source is documented.",
        ],
    }


def coverage() -> dict:
    with db_connection() as db:
        meta = {
            r["key"]: r["value"]
            for r in rows(db, "SELECT key,value FROM dataset_metadata")
        }
        table_counts = {}
        for name in (
            "customer_throughput",
            "checkpoint_wait_times",
            "national_daily_baseline",
        ):
            table_counts[name] = rows(
                db,
                f"SELECT MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS rows FROM {name}",
            )[0]
        airports = rows(
            db,
            """
            SELECT airport_code, MIN(airport_category) AS synthetic_airport_category,
                   COUNT(DISTINCT checkpoint_id) AS checkpoints, COUNT(*) AS throughput_rows
            FROM customer_throughput GROUP BY airport_code ORDER BY airport_code
            """,
        )
        for airport in airports:
            airport["checkpoint_ids"] = [
                r["checkpoint_id"]
                for r in rows(
                    db,
                    """
                SELECT DISTINCT checkpoint_id FROM customer_throughput
                WHERE airport_code = ? ORDER BY checkpoint_id""",
                    [airport["airport_code"]],
                )
            ]
        return {
            "metadata": meta,
            "tables": table_counts,
            "sample_airports": airports,
            "provenance_notice": "Sample airport counts are synthetic. National rows are synthetic unless metadata states user_supplied_csv_unverified.",
        }


def customer_throughput(
    from_date: str,
    to_date: str,
    airport_code: str | None = None,
    checkpoint_id: str | None = None,
    screening_type: str | None = None,
    machine_type: str | None = None,
    hour: int | None = None,
    limit: int = 100,
) -> dict:
    a, b = validate_range(from_date, to_date)
    n = validate_limit(limit)
    extra, values = _filters(
        airport_code,
        checkpoint_id,
        screening_type,
        machine_type,
        hour,
        allowed={
            "airport_code",
            "checkpoint_id",
            "screening_type",
            "machine_type",
            "hour",
        },
    )
    with db_connection() as db:
        result = rows(
            db,
            """
          SELECT date,hour,airport_code,airport_category,checkpoint_id,modset_id,
                 machine_type,screening_type,passenger_throughput,active_modsets
          FROM customer_throughput WHERE date BETWEEN ? AND ?"""
            + extra
            + """
          ORDER BY date,hour,airport_code,checkpoint_id,screening_type,modset_id
          LIMIT ?""",
            [a, b, *values, n + 1],
        )
    return {
        "table": "customer_throughput",
        "grain": TABLE_GRAINS["customer_throughput"],
        "rows": result[:n],
        "returned": min(n, len(result)),
        "has_more": len(result) > n,
        "synthetic": True,
        "active_modsets_not_additive": True,
    }


def wait_times(
    from_date: str,
    to_date: str,
    airport_code: str | None = None,
    checkpoint_id: str | None = None,
    screening_type: str | None = None,
    hour: int | None = None,
    limit: int = 100,
) -> dict:
    a, b = validate_range(from_date, to_date)
    n = validate_limit(limit)
    extra, values = _filters(
        airport_code,
        checkpoint_id,
        screening_type,
        hour=hour,
        allowed={"airport_code", "checkpoint_id", "screening_type", "hour"},
    )
    with db_connection() as db:
        result = rows(
            db,
            """
          SELECT date,hour,airport_code,checkpoint_id,screening_type,
                 avg_wait_minutes,p95_wait_minutes,max_wait_minutes,
                 exceeded_15m_flag,exceeded_30m_flag
          FROM checkpoint_wait_times WHERE date BETWEEN ? AND ?"""
            + extra
            + """
          ORDER BY date,hour,airport_code,checkpoint_id,screening_type LIMIT ?""",
            [a, b, *values, n + 1],
        )
    for rec in result:
        rec["exceeded_15m_flag"] = bool(rec["exceeded_15m_flag"])
        rec["exceeded_30m_flag"] = bool(rec["exceeded_30m_flag"])
    return {
        "table": "checkpoint_wait_times",
        "grain": TABLE_GRAINS["checkpoint_wait_times"],
        "rows": result[:n],
        "returned": min(n, len(result)),
        "has_more": len(result) > n,
        "synthetic": True,
    }


def national_baseline(from_date: str, to_date: str, limit: int = 100) -> dict:
    a, b = validate_range(from_date, to_date)
    n = validate_limit(limit)
    with db_connection() as db:
        result = rows(
            db,
            """
          SELECT date,national_throughput,prior_year_same_weekday_throughput
          FROM national_daily_baseline WHERE date BETWEEN ? AND ? ORDER BY date LIMIT ?
          """,
            [a, b, n + 1],
        )
        metadata = rows(
            db, "SELECT value FROM dataset_metadata WHERE key='national_baseline_kind'"
        )
    kind = metadata[0]["value"] if metadata else "unknown"
    return {
        "table": "national_daily_baseline",
        "grain": ["date"],
        "rows": result[:n],
        "returned": min(n, len(result)),
        "has_more": len(result) > n,
        "source_kind": kind,
        "is_verified_tsa": False,
        "synthetic": kind == "synthetic_demo",
        "data_label": (
            "SYNTHETIC demonstration values, not TSA figures"
            if kind == "synthetic_demo"
            else "user-supplied CSV, not authenticated as TSA data"
        ),
        "warning": "User CSV input, if present, has not been independently authenticated as TSA data.",
    }


def checkpoint_summary(
    from_date: str,
    to_date: str,
    airport_code: str | None = None,
    checkpoint_id: str | None = None,
    screening_type: str | None = None,
    hour: int | None = None,
    limit: int = 100,
) -> dict:
    a, b = validate_range(from_date, to_date)
    n = validate_limit(limit)
    extra, values = _filters(
        airport_code,
        checkpoint_id,
        screening_type,
        hour=hour,
        allowed={"airport_code", "checkpoint_id", "screening_type", "hour"},
    )
    with db_connection() as db:
        result = rows(
            db,
            """
          SELECT * FROM checkpoint_hourly_summary WHERE date BETWEEN ? AND ?"""
            + extra
            + """
          ORDER BY date,hour,airport_code,checkpoint_id,screening_type LIMIT ?""",
            [a, b, *values, n + 1],
        )
    for rec in result:
        for key in ("exceeded_15m_flag", "exceeded_30m_flag"):
            rec[key] = bool(rec[key]) if rec[key] is not None else None
    return {
        "view": "checkpoint_hourly_summary",
        "grain": ["date", "hour", "airport_code", "checkpoint_id", "screening_type"],
        "rows": result[:n],
        "returned": min(n, len(result)),
        "has_more": len(result) > n,
        "methodology": "SUM(passenger_throughput) over modsets; COUNT(DISTINCT modset_id) for active; exact-key join to checkpoint wait time",
        "synthetic": True,
    }


def airport_day_summary(
    from_date: str, to_date: str, airport_code: str | None = None, limit: int = 100
) -> dict:
    """Airport sample counts alongside national days; no claim of national completeness."""
    a, b = validate_range(from_date, to_date)
    n = validate_limit(limit)
    extra, values = _filters(airport_code=airport_code, allowed={"airport_code"})
    with db_connection() as db:
        result = rows(
            db,
            """
          SELECT t.date,t.airport_code,SUM(t.passenger_throughput) AS synthetic_airport_throughput,
                 n.national_throughput,n.prior_year_same_weekday_throughput
          FROM customer_throughput AS t
          LEFT JOIN national_daily_baseline AS n ON t.date=n.date
          WHERE t.date BETWEEN ? AND ?"""
            + extra
            + """
          GROUP BY t.date,t.airport_code ORDER BY t.date,t.airport_code LIMIT ?
          """,
            [a, b, *values, n + 1],
        )
        kind = rows(
            db, "SELECT value FROM dataset_metadata WHERE key='national_baseline_kind'"
        )[0]["value"]
    return {
        "rows": result[:n],
        "returned": min(n, len(result)),
        "has_more": len(result) > n,
        "national_baseline_kind": kind,
        "warning": "Airport volumes are synthetic and cover only the six example airports; do not treat them as the real subset of the national TSA series.",
    }


def quality_checks() -> dict:
    checks = {}
    with db_connection() as db:

        def scalar(sql: str) -> int:
            return db.execute(sql).fetchone()[0]

        checks["active_modset_count_mismatches"] = scalar("""
          SELECT COUNT(*) FROM (
            SELECT date,hour,airport_code,checkpoint_id,screening_type
            FROM customer_throughput GROUP BY date,hour,airport_code,checkpoint_id,screening_type
            HAVING MIN(active_modsets)<>MAX(active_modsets)
               OR MAX(active_modsets)<>COUNT(DISTINCT modset_id)
          )
        """)
        checks["wait_rows_without_matching_throughput"] = scalar("""
          SELECT COUNT(*) FROM checkpoint_wait_times w
          WHERE NOT EXISTS (SELECT 1 FROM customer_throughput t
             WHERE t.date=w.date AND t.hour=w.hour AND t.airport_code=w.airport_code
               AND t.checkpoint_id=w.checkpoint_id AND t.screening_type=w.screening_type)
        """)
        checks["throughput_groups_without_wait_rows"] = scalar("""
          SELECT COUNT(*) FROM (
            SELECT date,hour,airport_code,checkpoint_id,screening_type FROM customer_throughput
            GROUP BY date,hour,airport_code,checkpoint_id,screening_type
          ) t WHERE NOT EXISTS (
            SELECT 1 FROM checkpoint_wait_times w
            WHERE t.date=w.date AND t.hour=w.hour AND t.airport_code=w.airport_code
              AND t.checkpoint_id=w.checkpoint_id AND t.screening_type=w.screening_type)
        """)
        checks["days_without_national_baseline"] = scalar("""
          SELECT COUNT(*) FROM (SELECT DISTINCT date FROM customer_throughput) t
          WHERE NOT EXISTS (SELECT 1 FROM national_daily_baseline n WHERE n.date=t.date)
        """)
        checks["invalid_wait_threshold_flags"] = scalar("""
          SELECT COUNT(*) FROM checkpoint_wait_times
          WHERE exceeded_15m_flag<>(max_wait_minutes>15)
             OR exceeded_30m_flag<>(max_wait_minutes>30)
        """)
        checks["duplicate_modset_across_machine_types"] = scalar("""
          SELECT COUNT(*) FROM (
           SELECT date,hour,airport_code,checkpoint_id,screening_type,modset_id
           FROM customer_throughput GROUP BY date,hour,airport_code,checkpoint_id,screening_type,modset_id
           HAVING COUNT(DISTINCT machine_type)>1)
        """)
        db_check = db.execute("PRAGMA quick_check").fetchone()[0]
    return {
        "checks": checks,
        "sqlite_integrity": db_check,
        "passed": db_check == "ok" and all(n == 0 for n in checks.values()),
        "note": "Structural checks only. Synthetic figures are not independently validated operational observations.",
    }
