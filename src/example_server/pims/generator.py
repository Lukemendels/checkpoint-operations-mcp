"""Create an offline, deterministic and explicitly synthetic PIMS/PMIS SQLite seed.

No real PIMS or PMIS records are required, accessed, or reproduced.
Use --tsa-csv to substitute user-supplied national daily counts (not authenticated).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from example_server.pims.schema import (
    CheckpointWaitTime,
    CustomerThroughput,
    NationalDailyBaseline,
)

DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE customer_throughput (
  date TEXT NOT NULL CHECK(date GLOB '????-??-??'),
  hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
  airport_code TEXT NOT NULL CHECK(length(airport_code)=3 AND airport_code=upper(airport_code)),
  airport_category TEXT NOT NULL CHECK(airport_category IN ('Cat X','Cat I','Cat II')),
  checkpoint_id TEXT NOT NULL,
  modset_id TEXT NOT NULL,
  machine_type TEXT NOT NULL CHECK(machine_type IN ('WTMD','AIT')),
  screening_type TEXT NOT NULL CHECK(screening_type IN ('Standard','PreCheck')),
  passenger_throughput INTEGER NOT NULL CHECK(passenger_throughput >= 0),
  active_modsets INTEGER NOT NULL CHECK(active_modsets >= 1),
  PRIMARY KEY (date,hour,airport_code,checkpoint_id,modset_id,machine_type,screening_type)
) WITHOUT ROWID;
CREATE INDEX idx_ct_airport_dates ON customer_throughput(airport_code,date,hour);
CREATE INDEX idx_ct_checkpoint ON customer_throughput(airport_code,checkpoint_id,date,hour,screening_type);
CREATE TABLE checkpoint_wait_times (
  date TEXT NOT NULL CHECK(date GLOB '????-??-??'),
  hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
  airport_code TEXT NOT NULL CHECK(length(airport_code)=3 AND airport_code=upper(airport_code)),
  checkpoint_id TEXT NOT NULL,
  screening_type TEXT NOT NULL CHECK(screening_type IN ('Standard','PreCheck')),
  avg_wait_minutes REAL NOT NULL CHECK(avg_wait_minutes >= 0),
  p95_wait_minutes REAL NOT NULL CHECK(p95_wait_minutes >= avg_wait_minutes),
  max_wait_minutes REAL NOT NULL CHECK(max_wait_minutes >= p95_wait_minutes),
  exceeded_15m_flag INTEGER NOT NULL CHECK(exceeded_15m_flag IN (0,1)),
  exceeded_30m_flag INTEGER NOT NULL CHECK(exceeded_30m_flag IN (0,1)),
  CHECK(exceeded_15m_flag = (max_wait_minutes > 15.0)),
  CHECK(exceeded_30m_flag = (max_wait_minutes > 30.0)),
  PRIMARY KEY (date,hour,airport_code,checkpoint_id,screening_type)
) WITHOUT ROWID;
CREATE INDEX idx_wait_airport_dates ON checkpoint_wait_times(airport_code,date,hour);
CREATE TABLE national_daily_baseline (
  date TEXT PRIMARY KEY NOT NULL CHECK(date GLOB '????-??-??'),
  national_throughput INTEGER NOT NULL CHECK(national_throughput >= 0),
  prior_year_same_weekday_throughput INTEGER NOT NULL CHECK(prior_year_same_weekday_throughput >= 0)
) WITHOUT ROWID;
CREATE TABLE dataset_metadata (
  key TEXT PRIMARY KEY NOT NULL,
  value TEXT NOT NULL
) WITHOUT ROWID;
CREATE VIEW checkpoint_hourly_summary AS
SELECT t.date, t.hour, t.airport_code, t.airport_category,
       t.checkpoint_id, t.screening_type,
       SUM(t.passenger_throughput) AS passenger_throughput,
       COUNT(DISTINCT t.modset_id) AS observed_active_modsets,
       MAX(t.active_modsets) AS active_modsets,
       w.avg_wait_minutes, w.p95_wait_minutes, w.max_wait_minutes,
       w.exceeded_15m_flag, w.exceeded_30m_flag
FROM customer_throughput AS t
LEFT JOIN checkpoint_wait_times AS w
  ON t.date=w.date AND t.hour=w.hour AND t.airport_code=w.airport_code
 AND t.checkpoint_id=w.checkpoint_id AND t.screening_type=w.screening_type
GROUP BY t.date,t.hour,t.airport_code,t.airport_category,t.checkpoint_id,t.screening_type;
"""


@dataclass(frozen=True)
class Airport:
    code: str
    # THESE CATEGORIES ARE SYNTHETIC DEMONSTRATION LABELS, NOT VERIFIED TSA CLASSIFICATIONS.
    category: str
    load_factor: float
    checkpoints: tuple[str, ...]


AIRPORTS = (
    Airport("ATL", "Cat X", 1.30, ("Checkpoint A", "Checkpoint B")),
    Airport("ORD", "Cat X", 1.15, ("North", "South")),
    Airport("LAX", "Cat X", 1.20, ("North", "South")),
    Airport("IAD", "Cat I", 0.88, ("Checkpoint A", "Checkpoint B")),
    Airport("DCA", "Cat II", 0.76, ("North", "South")),
    Airport("BWI", "Cat I", 0.92, ("Checkpoint A", "Checkpoint B")),
)

# A synthetic demand profile, not TSA measured demand or equipment performance.
HOUR_WEIGHTS = (
    0,
    0,
    0,
    0,
    0.17,
    0.55,
    0.98,
    1.18,
    1.06,
    0.86,
    0.75,
    0.77,
    0.83,
    0.81,
    0.75,
    0.72,
    0.80,
    1.10,
    1.14,
    0.94,
    0.66,
    0.46,
    0.27,
    0.12,
)


def distribute(total: int, capacities: list[float]) -> list[int]:
    """Deterministic largest-remainder allocation that preserves the exact total."""
    if not capacities or total <= 0:
        return [0] * len(capacities)
    s = sum(capacities)
    raw = [total * c / s for c in capacities]
    result = [math.floor(x) for x in raw]
    for idx in sorted(range(len(raw)), key=lambda i: (-(raw[i] - result[i]), i))[
        : total - sum(result)
    ]:
        result[idx] += 1
    return result


def load_csv_national(
    path: Path, first: date, last: date
) -> dict[date, NationalDailyBaseline]:
    """CSV must have EXACT schema headers and every date in generated range."""
    required = {"date", "national_throughput", "prior_year_same_weekday_throughput"}
    result: dict[date, NationalDailyBaseline] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or set(reader.fieldnames) != required:
            raise ValueError(
                f"TSA CSV must have exactly these columns: {', '.join(sorted(required))}"
            )
        for row in reader:
            model = NationalDailyBaseline.model_validate(row)
            if model.date in result:
                raise ValueError(f"Duplicate TSA CSV date: {model.date}")
            result[model.date] = model
    missing = [
        first + timedelta(days=i)
        for i in range((last - first).days + 1)
        if first + timedelta(days=i) not in result
    ]
    if missing:
        raise ValueError(
            f"National CSV missing {len(missing)} generated days; first missing {missing[0]}"
        )
    return result


def simulated_national(d: date, rng: random.Random) -> NationalDailyBaseline:
    # Clearly illustrative synthetic figures; not derived from tsa.gov.
    day_mult = (0.92, 0.82, 0.84, 0.89, 1.03, 1.14, 1.04)[d.weekday()]
    seasonal = 1 + 0.05 * math.sin(2 * math.pi * d.timetuple().tm_yday / 365)
    count = int(2_280_000 * day_mult * seasonal * rng.uniform(0.971, 1.029))
    previous = int(count / rng.uniform(0.975, 1.045))
    return NationalDailyBaseline(
        date=d, national_throughput=count, prior_year_same_weekday_throughput=previous
    )


def build_seed(
    path: Path,
    *,
    start: date,
    days: int,
    seed: int = 20261117,
    national_csv: Path | None = None,
    replace: bool = False,
) -> dict:
    if not 1 <= days <= 366:
        raise ValueError("days must be 1..366")
    if path.exists() and not replace:
        raise FileExistsError(f"Refusing to overwrite {path}; use --replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    end = start + timedelta(days=days - 1)
    supplied = load_csv_national(national_csv, start, end) if national_csv else None
    if path.exists():
        path.unlink()
    rng = random.Random(seed)
    db = sqlite3.connect(path)
    try:
        db.executescript(DDL)
        n_throughput = n_wait = 0
        for di in range(days):
            d = start + timedelta(days=di)
            ds = d.isoformat()
            daily = supplied[d] if supplied is not None else simulated_national(d, rng)
            db.execute(
                "INSERT INTO national_daily_baseline VALUES (?,?,?)",
                (
                    ds,
                    daily.national_throughput,
                    daily.prior_year_same_weekday_throughput,
                ),
            )
            # The queues below are synthetic and separate by airport/checkpoint/screening type.
            for airport in AIRPORTS:
                for checkpoint in airport.checkpoints:
                    for screening_type in ("Standard", "PreCheck"):
                        # Unique modset identifiers across screening type and machine type.
                        prefix = "STD" if screening_type == "Standard" else "PCK"
                        modsets = [
                            (f"{prefix}-Modset-{n:02d}", "AIT" if n % 3 else "WTMD")
                            for n in range(
                                1, (5 if screening_type == "Standard" else 3) + 1
                            )
                        ]
                        queue = 0
                        for hour, weight in enumerate(HOUR_WEIGHTS):
                            if not weight:
                                continue  # Closed checkpoint hours: no rows for that hour.
                            # Peak capacity constriction occasionally creates plausible queues.
                            active = [
                                m
                                for m in modsets
                                if rng.random()
                                > (0.11 if hour in (6, 7, 17, 18) else 0.05)
                            ]
                            if not active:
                                active = [modsets[0]]
                            capacities = [
                                (94.0 if mt == "WTMD" else 76.0)
                                * rng.uniform(0.93, 1.07)
                                for _mid, mt in active
                            ]
                            capacity = sum(capacities)
                            # Standard demand typically larger than PreCheck; both scale with capacity.
                            weekday = (0.98, 0.90, 0.96, 1.02, 1.13, 1.05, 0.93)[
                                d.weekday()
                            ]
                            surge = (
                                rng.uniform(1.01, 1.23) if rng.random() < 0.095 else 1.0
                            )
                            arrivals = round(
                                capacity
                                * weight
                                * airport.load_factor
                                * weekday
                                * surge
                                * rng.uniform(0.83, 1.11)
                            )
                            served = min(
                                queue + arrivals,
                                math.floor(capacity * rng.uniform(0.89, 1.0)),
                            )
                            queue = max(0, queue + arrivals - served)
                            allocation = distribute(served, capacities)
                            for (mid, mt), num in zip(active, allocation):
                                row = CustomerThroughput(
                                    date=d,
                                    hour=hour,
                                    airport_code=airport.code,
                                    airport_category=airport.category,
                                    checkpoint_id=checkpoint,
                                    modset_id=mid,
                                    machine_type=mt,
                                    screening_type=screening_type,
                                    passenger_throughput=num,
                                    active_modsets=len(active),
                                )
                                db.execute(
                                    "INSERT INTO customer_throughput VALUES (?,?,?,?,?,?,?,?,?,?)",
                                    (
                                        ds,
                                        row.hour,
                                        row.airport_code,
                                        row.airport_category,
                                        row.checkpoint_id,
                                        row.modset_id,
                                        row.machine_type,
                                        row.screening_type,
                                        row.passenger_throughput,
                                        row.active_modsets,
                                    ),
                                )
                                n_throughput += 1
                            # Simplified queueing model: illustrative, NOT a validated wait-time estimator.
                            utilization = (queue + arrivals) / max(capacity, 1)
                            avg = min(
                                150.0,
                                max(
                                    0.7,
                                    (2.4 if screening_type == "PreCheck" else 3.8)
                                    + queue / max(capacity, 1) * 28.0
                                    + max(0, utilization - 0.90) * 7.2
                                    + rng.uniform(0.0, 2.2),
                                ),
                            )
                            p95 = min(
                                180.0,
                                max(
                                    avg,
                                    avg * rng.uniform(1.23, 1.68) + rng.uniform(0, 3),
                                ),
                            )
                            maximum = min(
                                240.0,
                                max(
                                    p95,
                                    p95 * rng.uniform(1.07, 1.31) + rng.uniform(0, 4),
                                ),
                            )
                            avg, p95, maximum = (
                                round(n, 2) for n in (avg, p95, maximum)
                            )
                            wait = CheckpointWaitTime(
                                date=d,
                                hour=hour,
                                airport_code=airport.code,
                                checkpoint_id=checkpoint,
                                screening_type=screening_type,
                                avg_wait_minutes=avg,
                                p95_wait_minutes=p95,
                                max_wait_minutes=maximum,
                                exceeded_15m_flag=maximum > 15,
                                exceeded_30m_flag=maximum > 30,
                            )
                            db.execute(
                                "INSERT INTO checkpoint_wait_times VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (
                                    ds,
                                    wait.hour,
                                    wait.airport_code,
                                    wait.checkpoint_id,
                                    wait.screening_type,
                                    wait.avg_wait_minutes,
                                    wait.p95_wait_minutes,
                                    wait.max_wait_minutes,
                                    int(wait.exceeded_15m_flag),
                                    int(wait.exceeded_30m_flag),
                                ),
                            )
                            n_wait += 1
        provenance = (
            "user_supplied_csv_unverified" if national_csv else "synthetic_demo"
        )
        build_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        created = (
            datetime.fromtimestamp(int(build_epoch), timezone.utc)
            if build_epoch is not None
            else datetime.now(timezone.utc)
        )
        metadata = {
            "schema_version": "1.0.0",
            "dataset": "SYNTHETIC PIMS / PMIS DEMONSTRATION -- NOT REAL AIRPORT OPERATIONS",
            "created_utc": created.isoformat(),
            "created_utc_policy": "fixed_build_epoch" if build_epoch else "wall_clock",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "random_seed": str(seed),
            "local_clock_note": "Local hour; fall DST duplicate hour cannot be uniquely represented by supplied schema",
            "category_warning": "Airport categories in this demo are arbitrary synthetic labels; not official TSA categorizations",
            "national_baseline_kind": provenance,
            "national_source": str(national_csv.resolve())
            if national_csv
            else "NO OFFICIAL SOURCE: fully synthetic",
            "official_reference_url": "https://www.tsa.gov/travel/passenger-volumes",
            "csv_warning": "A CSV file is user-supplied and is not independently authenticated by this tool",
            "wait_flag_rule": "max_wait_minutes > threshold (strict); synthetic illustration",
            "active_modsets_rule": "Repeated group count on each modset row; never SUM active_modsets",
            "covered_airports": ",".join(a.code for a in AIRPORTS),
            "identity_notice": "No real PIMS/PMIS data were used; table names and field definitions follow the user-supplied specification",
        }
        db.executemany("INSERT INTO dataset_metadata VALUES (?,?)", metadata.items())
        db.commit()
        result = {
            "path": str(path),
            "start_date": str(start),
            "end_date": str(end),
            "customer_throughput_rows": n_throughput,
            "checkpoint_wait_time_rows": n_wait,
            "national_daily_rows": days,
            "national_baseline_kind": provenance,
            "airport_count": len(AIRPORTS),
            "random_seed": seed,
        }
        return result
    except Exception:
        db.rollback()
        db.close()
        path.unlink(missing_ok=True)
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--start-date", type=date.fromisoformat, default=date(2026, 10, 18)
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261117)
    parser.add_argument(
        "--tsa-csv",
        type=Path,
        help="Canonical user-provided national CSV; authenticity not verified",
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.db is None:
        from example_server.pims.repository import DEFAULT_DB

        args.db = DEFAULT_DB
    print(
        json.dumps(
            build_seed(
                args.db,
                start=args.start_date,
                days=args.days,
                seed=args.seed,
                national_csv=args.tsa_csv,
                replace=args.replace,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
