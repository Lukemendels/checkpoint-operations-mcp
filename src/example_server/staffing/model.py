"""Pure calculations for the staffing scenario model (steps 1-3 of the sketch).

Step 1  capacity per modset      estimate_modset_capacity()
Step 2  utilization              aggregate_to_checkpoint_hour()
Step 3  wait response p95 = f(u) fit_wait_response()

All functions take plain lists of dicts (as returned by provider.py) and return
plain dicts, so they are easy to test without a server. Rules carried from
PLAN.md: aggregate Table 1 to checkpoint-hour before joining Table 2; refuse
joins whose cardinality is not 1:1; never divide by a zero or missing
denominator; percentiles over hours are not passenger percentiles.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

CheckpointHourKey = tuple[str, int, str, str, str]
"""(date, hour, airport_code, checkpoint_id, screening_type)"""

CapacityLookup = dict[tuple[str, str], float]
"""(machine_type, screening_type) -> assumed pax per modset-hour"""

MIN_POINTS_PER_SEGMENT = 5
KNEE_CANDIDATES = [round(0.60 + 0.05 * i, 2) for i in range(8)]  # 0.60 .. 0.95
BIN_WIDTH = 0.1


class CardinalityError(ValueError):
    """The checkpoint-hour join is not 1:1; refusing to aggregate."""


# ── helpers ──────────────────────────────────────────────────────────────────


def quantile(values: list[float], q: float) -> float:
    """Linear-interpolation quantile over the given observations (0 <= q <= 1)."""
    if not values:
        raise ValueError("quantile of empty list")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return float(xs[lo] + (xs[hi] - xs[lo]) * frac)


def _median(values: list[float]) -> float:
    return quantile(values, 0.5)


def _linear_fit(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares line y = a + b*x. Returns (intercept, slope, sse)."""
    n = len(points)
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mean_x) ** 2 for p in points)
    if sxx == 0:
        slope = 0.0
    else:
        sxy = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
        slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    sse = sum((p[1] - (intercept + slope * p[0])) ** 2 for p in points)
    return intercept, slope, sse


def checkpoint_hour_key(row: dict[str, Any]) -> CheckpointHourKey:
    return (
        row["date"],
        row["hour"],
        row["airport_code"],
        row["checkpoint_id"],
        row["screening_type"],
    )


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    airport_code: str | None = None,
    checkpoint_id: str | None = None,
    screening_type: str | None = None,
    machine_type: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Filter by exact field matches and an inclusive ISO date range."""
    start_s = start.isoformat() if start else None
    end_s = end.isoformat() if end else None
    out = []
    for r in rows:
        if airport_code and r["airport_code"] != airport_code:
            continue
        if checkpoint_id and r["checkpoint_id"] != checkpoint_id:
            continue
        if screening_type and r["screening_type"] != screening_type:
            continue
        if machine_type and r.get("machine_type") != machine_type:
            continue
        if start_s and r["date"] < start_s:
            continue
        if end_s and r["date"] > end_s:
            continue
        out.append(r)
    return out


# ── step 1: capacity per modset ──────────────────────────────────────────────


def estimate_modset_capacity(
    throughput_rows: list[dict[str, Any]],
    group_by: tuple[str, ...] = (
        "airport_code",
        "checkpoint_id",
        "machine_type",
        "screening_type",
    ),
) -> dict[str, Any]:
    """Observed passengers per active modset-hour, summarized per group.

    Interpretation of Table 1: one row per active modset-hour, so each row's
    `passenger_throughput` is that modset's hourly count. `active_modsets` is
    the count for the checkpoint-hour-screening_type and is used only for
    consistency checks. The p90 of per-modset-hour counts is reported as the
    capacity proxy (throughput in busy hours approaches capacity); the median
    is typical throughput, which is lower whenever the modset is underused.

    Observed throughput only reveals capacity in saturated hours. When staffing
    keeps utilization below 1 the p90 proxy is a LOWER BOUND on capacity; a low
    p90_to_max_ratio signals that. Prefer the assumed capacities in
    staffing_rules for utilization unless the proxy is near the max.
    """
    by_group: dict[tuple, list[float]] = defaultdict(list)
    hours_seen: dict[CheckpointHourKey, set[str]] = defaultdict(set)
    declared: dict[CheckpointHourKey, int] = {}
    zero_denominator: set[CheckpointHourKey] = set()

    for r in throughput_rows:
        key = checkpoint_hour_key(r)
        if r["active_modsets"] <= 0:
            zero_denominator.add(key)
            continue
        declared[key] = r["active_modsets"]
        hours_seen[key].add(r["modset_id"])
        if r["passenger_throughput"] <= 0:
            continue  # idle modset-hour: carries no capacity information
        by_group[tuple(r[g] for g in group_by)].append(float(r["passenger_throughput"]))

    mismatch = sum(1 for key, ids in hours_seen.items() if len(ids) != declared[key])

    groups = []
    for gkey in sorted(by_group):
        vals = by_group[gkey]
        groups.append(
            {
                **dict(zip(group_by, gkey)),
                "n_modset_hours": len(vals),
                "median_pax_per_modset_hour": round(_median(vals), 1),
                "q25": round(quantile(vals, 0.25), 1),
                "q75": round(quantile(vals, 0.75), 1),
                "capacity_proxy_p90": round(quantile(vals, 0.90), 1),
                "max": round(max(vals), 1),
                "p90_to_max_ratio": round(quantile(vals, 0.90) / max(vals), 2),
            }
        )

    return {
        "group_by": list(group_by),
        "groups": groups,
        "excluded_zero_denominator_hours": len(zero_denominator),
        "active_modsets_mismatch_hours": mismatch,
    }


def capacity_lookup_from_rules(rules: list[dict[str, Any]]) -> CapacityLookup:
    """Assumed capacities from staffing_rules.csv."""
    return {
        (r["machine_type"], r["screening_type"]): float(
            r["assumed_capacity_pax_per_hour"]
        )
        for r in rules
    }


def capacity_lookup_from_estimate(estimate: dict[str, Any]) -> CapacityLookup:
    """Pool a capacity estimate (grouped by machine/screening) into a lookup.

    When the estimate was grouped more finely (e.g. per checkpoint), the p90
    proxies are averaged across groups sharing the same machine/screening pair.
    """
    sums: dict[tuple[str, str], list[float]] = defaultdict(list)
    for g in estimate["groups"]:
        sums[(g["machine_type"], g["screening_type"])].append(g["capacity_proxy_p90"])
    return {k: sum(v) / len(v) for k, v in sums.items()}


# ── step 2: utilization at checkpoint-hour ───────────────────────────────────


def aggregate_to_checkpoint_hour(
    throughput_rows: list[dict[str, Any]],
    capacity: CapacityLookup,
) -> dict[CheckpointHourKey, dict[str, Any]]:
    """Sum Table 1 to checkpoint-hour-screening grain and attach capacity.

    Capacity of the hour is the sum over active modset rows of the assumed
    capacity for that row's (machine_type, screening_type). Utilization is
    pax / capacity, or None when capacity is zero or unknown.
    """
    agg: dict[CheckpointHourKey, dict[str, Any]] = {}
    for r in throughput_rows:
        key = checkpoint_hour_key(r)
        slot = agg.setdefault(
            key,
            {"pax": 0, "capacity": 0.0, "rows": 0, "unknown_capacity_rows": 0},
        )
        slot["pax"] += r["passenger_throughput"]
        slot["rows"] += 1
        cap = capacity.get((r["machine_type"], r["screening_type"]))
        if cap is None:
            slot["unknown_capacity_rows"] += 1
        else:
            slot["capacity"] += cap
    for slot in agg.values():
        slot["utilization"] = (
            slot["pax"] / slot["capacity"] if slot["capacity"] > 0 else None
        )
    return agg


# ── step 3: wait response ────────────────────────────────────────────────────


def fit_wait_response(
    throughput_rows: list[dict[str, Any]],
    wait_rows: list[dict[str, Any]],
    capacity: CapacityLookup,
) -> dict[str, Any]:
    """Fit p95 wait against utilization from the 1:1 checkpoint-hour join.

    Returns binned medians (descriptive), a two-segment linear fit with the
    knee chosen by least squares (parametric), residual spread, and the join
    diagnostics. Raises CardinalityError if Table 2 has duplicate
    checkpoint-hour-screening keys.
    """
    wait_index: dict[CheckpointHourKey, dict[str, Any]] = {}
    for w in wait_rows:
        key = checkpoint_hour_key(w)
        if key in wait_index:
            raise CardinalityError(
                "wait_times has more than one row for a checkpoint-hour-screening "
                "key; the join would not be 1:1"
            )
        wait_index[key] = w

    agg = aggregate_to_checkpoint_hour(throughput_rows, capacity)
    points: list[tuple[float, float]] = []
    skipped_zero_capacity = 0
    for key, slot in agg.items():
        w = wait_index.get(key)
        if w is None:
            continue
        if slot["utilization"] is None:
            skipped_zero_capacity += 1
            continue
        points.append((slot["utilization"], float(w["p95_wait_minutes"])))

    unmatched_throughput = sum(1 for k in agg if k not in wait_index)
    unmatched_wait = sum(1 for k in wait_index if k not in agg)

    result: dict[str, Any] = {
        "n_points": len(points),
        "join": {
            "throughput_hours": len(agg),
            "wait_hours": len(wait_index),
            "matched_hours": len(points) + skipped_zero_capacity,
            "unmatched_throughput_hours": unmatched_throughput,
            "unmatched_wait_hours": unmatched_wait,
            "skipped_zero_capacity_hours": skipped_zero_capacity,
        },
        "bins": [],
        "fit": None,
    }
    if not points:
        return result

    # Descriptive: binned medians of p95 by utilization.
    binned: dict[int, list[float]] = defaultdict(list)
    for u, p95 in points:
        binned[int(u // BIN_WIDTH)].append(p95)
    for b in sorted(binned):
        vals = binned[b]
        result["bins"].append(
            {
                "u_low": round(b * BIN_WIDTH, 2),
                "u_high": round((b + 1) * BIN_WIDTH, 2),
                "n": len(vals),
                "median_p95_wait": round(_median(vals), 1),
                "q25": round(quantile(vals, 0.25), 1),
                "q75": round(quantile(vals, 0.75), 1),
            }
        )

    # Parametric: two-segment line with least-squares knee.
    best: dict[str, Any] | None = None
    for knee in KNEE_CANDIDATES:
        left = [p for p in points if p[0] <= knee]
        right = [p for p in points if p[0] > knee]
        if len(left) < MIN_POINTS_PER_SEGMENT or len(right) < MIN_POINTS_PER_SEGMENT:
            continue
        a_l, b_l, sse_l = _linear_fit(left)
        a_r, b_r, sse_r = _linear_fit(right)
        sse = sse_l + sse_r
        if best is None or sse < best["sse"]:
            best = {
                "knee_utilization": knee,
                "below_knee": {"intercept": a_l, "slope": b_l, "n": len(left)},
                "above_knee": {"intercept": a_r, "slope": b_r, "n": len(right)},
                "sse": sse,
            }
    if best is None:
        a, b, sse = _linear_fit(points)
        best = {
            "knee_utilization": None,
            "below_knee": {"intercept": a, "slope": b, "n": len(points)},
            "above_knee": None,
            "sse": sse,
        }

    mean_y = sum(p[1] for p in points) / len(points)
    sst = sum((p[1] - mean_y) ** 2 for p in points)
    r2 = 1 - best["sse"] / sst if sst > 0 else None
    residual_sd = (best["sse"] / len(points)) ** 0.5
    for seg in ("below_knee", "above_knee"):
        if best[seg]:
            best[seg] = {
                "intercept": round(best[seg]["intercept"], 2),
                "slope": round(best[seg]["slope"], 2),
                "n": best[seg]["n"],
            }
    result["fit"] = {
        "form": "two-segment linear in utilization, knee by least squares",
        "knee_utilization": best["knee_utilization"],
        "below_knee": best["below_knee"],
        "above_knee": best["above_knee"],
        "r2": round(r2, 3) if r2 is not None else None,
        "residual_sd_minutes": round(residual_sd, 2),
        "u_range": [
            round(min(p[0] for p in points), 3),
            round(max(p[0] for p in points), 3),
        ],
    }
    return result


def predict_p95(fit: dict[str, Any], u: float) -> float:
    """Evaluate a fit returned by fit_wait_response at utilization u."""
    knee = fit["knee_utilization"]
    seg = fit["below_knee"]
    if knee is not None and u > knee and fit["above_knee"]:
        seg = fit["above_knee"]
    return seg["intercept"] + seg["slope"] * u
