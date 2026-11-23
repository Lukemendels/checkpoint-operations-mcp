"""Generate the SYNTHETIC staffing fixture under data/synthetic/staffing/.

Clean-room synthetic data: shaped like the team's proposed PMIS-style schema
(Table 1 throughput at modset-hour grain, Table 2 wait times at checkpoint-hour
grain, Table 3 national daily baseline) plus the staffing-owned configuration
tables from ideas/staffing-tools.md. Every number comes from the constants
below and a seeded generator. Nothing here is observed operational data.

Run:  uv run python scripts/make_staffing_fixture.py [--out DIR]

The generator is deterministic (fixed seed) so tests can regenerate and compare.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20261113
START = date(2026, 10, 5)  # a Monday; synthetic week
DAYS = 7
OPEN_HOUR, CLOSE_HOUR = 4, 22

# True synthetic capacities (pax per modset-hour). Tool 2 should recover these.
CAPACITY = {
    ("AIT", "Standard"): 170.0,
    ("WTMD", "Standard"): 140.0,
    ("WTMD", "PreCheck"): 230.0,
}
STAFF_PER_MODSET = {"AIT": 4, "WTMD": 3}
RELIEF_FACTOR = 1.15
FIXED_POSTS = 3

# Hour-of-day demand profile (share of peak).
PROFILE = {
    4: 0.20,
    5: 0.80,
    6: 1.00,
    7: 0.90,
    8: 0.70,
    9: 0.50,
    10: 0.45,
    11: 0.50,
    12: 0.55,
    13: 0.50,
    14: 0.60,
    15: 0.80,
    16: 0.90,
    17: 0.85,
    18: 0.70,
    19: 0.50,
    20: 0.35,
    21: 0.25,
    22: 0.15,
}
DAY_FACTOR = [1.00, 0.92, 0.90, 0.95, 1.08, 0.85, 1.02]  # Mon..Sun
PRECHECK_SHARE = 0.35

# airport -> (category, {checkpoint: peak Standard+PreCheck demand pax/hr})
AIRPORTS = {
    "BWI": ("Cat X", {"Checkpoint A": 560.0, "Checkpoint B": 420.0}),
    "DCA": ("Cat X", {"North": 500.0, "South": 460.0}),
    "IAD": ("Cat X", {"East": 640.0, "West": 520.0}),
}
# Modsets per checkpoint: (machine_type, screening_type) in id order.
MODSET_LAYOUT = [
    ("AIT", "Standard"),
    ("AIT", "Standard"),
    ("AIT", "Standard"),
    ("WTMD", "Standard"),
    ("WTMD", "PreCheck"),
    ("WTMD", "PreCheck"),
]
UNDER_DEPLOY_PROB = 0.10  # synthetic "installed but idle" hours for gap tools
KNEE = 0.85


def wait_response(u: float) -> float:
    """True synthetic p95 wait (minutes) as a function of utilization u."""
    if u <= KNEE:
        return 3.0 + 8.0 * u
    return 3.0 + 8.0 * KNEE + 90.0 * (u - KNEE)


def build(out: Path) -> dict[str, int]:
    """Write the five CSV files to `out` and return row counts."""
    rng = random.Random(SEED)
    out.mkdir(parents=True, exist_ok=True)
    throughput: list[dict] = []
    waits: list[dict] = []
    national: list[dict] = []
    config: list[dict] = []
    rules: list[dict] = []

    n_wtmd = sum(1 for m, _ in MODSET_LAYOUT if m == "WTMD")
    n_ait = sum(1 for m, _ in MODSET_LAYOUT if m == "AIT")
    for airport, (_category, checkpoints) in AIRPORTS.items():
        for ckpt in checkpoints:
            config.append(
                {
                    "airport_code": airport,
                    "checkpoint_id": ckpt,
                    "installed_modsets_wtmd": n_wtmd,
                    "installed_modsets_ait": n_ait,
                    "fixed_posts": FIXED_POSTS,
                    "open_hour": OPEN_HOUR,
                    "close_hour": CLOSE_HOUR,
                }
            )

    for (machine, screening), cap in CAPACITY.items():
        rules.append(
            {
                "machine_type": machine,
                "screening_type": screening,
                "assumed_capacity_pax_per_hour": cap,
                "staff_per_modset": STAFF_PER_MODSET[machine],
                "relief_factor": RELIEF_FACTOR,
            }
        )

    modsets = [(f"Modset-{i + 1:02d}", m, s) for i, (m, s) in enumerate(MODSET_LAYOUT)]

    for d in range(DAYS):
        day = START + timedelta(days=d)
        dfac = DAY_FACTOR[day.weekday()]
        nat = 2_350_000 * dfac * rng.uniform(0.98, 1.02)
        national.append(
            {
                "date": day.isoformat(),
                "national_throughput": int(nat),
                "prior_year_same_weekday_throughput": int(nat * 0.96),
            }
        )
        for airport, (category, checkpoints) in AIRPORTS.items():
            for ckpt, peak in checkpoints.items():
                for hour in range(OPEN_HOUR, CLOSE_HOUR + 1):
                    total = peak * PROFILE[hour] * dfac * rng.uniform(0.9, 1.1)
                    for screening in ("Standard", "PreCheck"):
                        share = PRECHECK_SHARE
                        if screening == "Standard":
                            share = 1 - PRECHECK_SHARE
                        demand = total * share
                        pool = [x for x in modsets if x[2] == screening]
                        avg_cap = sum(CAPACITY[(m, s)] for _, m, s in pool)
                        avg_cap /= len(pool)
                        n = math.ceil(demand / (0.8 * avg_cap))
                        n = max(1, min(n, len(pool)))
                        if n > 1 and rng.random() < UNDER_DEPLOY_PROB:
                            n -= 1  # synthetic under-deployment
                        active = pool[:n]
                        cap_sum = sum(CAPACITY[(m, s)] for _, m, s in active)
                        u = demand / cap_sum
                        per_modset_demand = demand / n
                        for mid, m, s in active:
                            eff = rng.uniform(0.92, 1.0)
                            pax = min(per_modset_demand, CAPACITY[(m, s)] * eff)
                            throughput.append(
                                {
                                    "date": day.isoformat(),
                                    "hour": hour,
                                    "airport_code": airport,
                                    "airport_category": category,
                                    "checkpoint_id": ckpt,
                                    "modset_id": mid,
                                    "machine_type": m,
                                    "screening_type": s,
                                    "passenger_throughput": int(round(pax)),
                                    "active_modsets": n,
                                }
                            )
                        p95 = max(1.0, wait_response(u) + rng.gauss(0, 1.5))
                        waits.append(
                            {
                                "date": day.isoformat(),
                                "hour": hour,
                                "airport_code": airport,
                                "checkpoint_id": ckpt,
                                "screening_type": screening,
                                "avg_wait_minutes": round(p95 * 0.55, 1),
                                "p95_wait_minutes": round(p95, 1),
                                "max_wait_minutes": round(p95 * 1.35, 1),
                                "exceeded_15m_flag": p95 > 15,
                                "exceeded_30m_flag": p95 > 30,
                            }
                        )

    files = {
        "throughput.csv": throughput,
        "wait_times.csv": waits,
        "national_daily_baseline.csv": national,
        "checkpoint_config.csv": config,
        "staffing_rules.csv": rules,
    }
    for name, rows in files.items():
        with (out / name).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=list(rows[0].keys()), lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
    return {name: len(rows) for name, rows in files.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = Path(__file__).resolve().parents[1] / "data/synthetic/staffing"
    parser.add_argument("--out", default=str(default_out))
    args = parser.parse_args()
    counts = build(Path(args.out))
    for name, n in counts.items():
        print(f"{name}: {n} rows")


if __name__ == "__main__":
    main()
