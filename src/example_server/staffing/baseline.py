"""Pure, leakage-safe comparable-hour synthetic throughput baseline.

This is a reference baseline, not an operational prediction model. Processed
throughput is not latent arrival demand. Historical wait ranges are descriptive,
not forecast confidence intervals. No provider mutation or external access.
"""

from collections import defaultdict
from datetime import date, timedelta
from math import isfinite
from statistics import mean, median
from typing import Any

MODEL_VERSION = "synthetic-comparable-weekday-hour-v1"
WARNINGS = [
    "Processed-throughput proxy, not latent arrival demand or staffing advice.",
    "Same-weekday/hour historical mean; no trend, holidays or queue dynamics modeled.",
    "Wait median/min/max describe prior hourly p95, not confidence intervals.",
    "Synthetic baseline only; no operational predictive validity established.",
]


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite nonnegative number")
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


def baseline_forecast(
    throughput_rows: list[dict[str, Any]],
    wait_rows: list[dict[str, Any]],
    *,
    target_date: date,
    hours: list[int],
    airport_code: str,
    checkpoint_id: str,
    screening_type: str,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """Return draft forecast rows, plus explicit per-hour qualified refusals.

    Aggregate complete distinct-modset groups before averaging. Require at least
    two prior same-weekday dates and a matching p95 wait for every training date.
    Missing hours are absent, never zeros. Inputs are treated as synthetic only.
    Reject ambiguous groups rather than silently dropping malformed observations.
    """
    if not isinstance(target_date, date):
        raise ValueError("target_date must be a date")
    if (
        not hours
        or len(hours) > 24
        or len(set(hours)) != len(hours)
        or any(type(h) is not int or not 0 <= h <= 23 for h in hours)
    ):
        raise ValueError("hours must be 1–24 unique integers from 0 through 23")
    if type(lookback_days) is not int or not 1 <= lookback_days <= 90:
        raise ValueError("lookback_days must be 1–90")
    if (
        len(airport_code) != 3
        or not airport_code.isalpha()
        or not airport_code.isupper()
        or not checkpoint_id
        or len(checkpoint_id) > 80
        or screening_type not in {"Standard", "PreCheck"}
    ):
        raise ValueError("invalid airport/checkpoint/screening scope")
    start = target_date - timedelta(days=lookback_days)

    def selected(row: dict[str, Any]) -> bool:
        if (
            row["airport_code"] != airport_code
            or row["checkpoint_id"] != checkpoint_id
            or row["screening_type"] != screening_type
            or row["hour"] not in hours
        ):
            return False
        d = date.fromisoformat(row["date"])
        return start <= d < target_date and d.weekday() == target_date.weekday()

    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in throughput_rows:
        if selected(row):
            groups[(row["date"], row["hour"])].append(row)
    totals = {}
    for key, group in groups.items():
        ids = [r["modset_id"] for r in group]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate or ambiguous modset-hour in training data")
        if any(
            type(r["active_modsets"]) is not int or r["active_modsets"] != len(ids)
            for r in group
        ):
            raise ValueError("inconsistent active-modset group in training data")
        totals[key] = sum(
            _finite_nonnegative(r["passenger_throughput"], "throughput") for r in group
        )

    waits = {}
    for row in wait_rows:
        if selected(row):
            key = (row["date"], row["hour"])
            if key in waits:
                raise ValueError("duplicate checkpoint-hour wait in training data")
            waits[key] = _finite_nonnegative(row["p95_wait_minutes"], "p95 wait")

    forecasts, refused = [], []
    for hour in sorted(hours):
        keys = sorted(k for k in totals if k[1] == hour)
        dates = [k[0] for k in keys]
        if len(keys) < 2:
            refused.append(
                {
                    "hour": hour,
                    "reason": "fewer_than_two_prior_same_weekdays",
                    "training_dates": dates,
                    "sample_count": len(keys),
                }
            )
            continue
        if any(k not in waits for k in keys):
            refused.append(
                {
                    "hour": hour,
                    "reason": "missing_matching_historical_wait",
                    "training_dates": dates,
                    "sample_count": len(keys),
                }
            )
            continue
        wait_values = [waits[k] for k in keys]
        forecasts.append(
            {
                "date": target_date.isoformat(),
                "hour": hour,
                "airport_code": airport_code,
                "checkpoint_id": checkpoint_id,
                "screening_type": screening_type,
                "predicted_pax": mean(totals[k] for k in keys),
                "predicted_p95_wait": median(wait_values),
                "interval_low": min(wait_values),
                "interval_high": max(wait_values),
                "interval_kind": "historical_p95_range_not_confidence_interval",
                "model_version": MODEL_VERSION,
                "training_dates": dates,
                "sample_count": len(keys),
            }
        )
    return {
        "data_label": "synthetic",
        "model_version": MODEL_VERSION,
        "lookback_start": start.isoformat(),
        "training_cutoff_exclusive": target_date.isoformat(),
        "rows": forecasts,
        "refused_hours": refused,
        "warnings": WARNINGS,
        "row_limit": 24,
    }
