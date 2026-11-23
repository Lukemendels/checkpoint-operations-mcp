"""Hour-by-hour fluid queue model for the staffing tools (pure functions, no I/O).

Why a queue model: on the synthetic PIMS seed the same-hour fit
p95 = f(utilization) explains little, because the generator carries a backlog
from one hour to the next and waits depend on that backlog, not on the hour's
utilization alone. This module models the backlog explicitly.

Per (airport, checkpoint, screening_type, date), with hours in order:

    capacity_h   = capacity_factor * sum of assumed capacity of active modsets
    backlog_h    = max(0, avg_wait_h - base_wait) / minutes_per_backlog * capacity_h
                   (Little's law: wait ~ queue length / service rate; with the
                   coefficient at 60 this is exactly "minutes = 60 * L / mu")
    demand_h     = max(0, served_h + backlog_h - backlog_{h-1})   (reconstructed)

Simulation of a capacity plan (any demand series):

    served_h     = min(backlog_{h-1} + demand_h, capacity_h)
    backlog_h    = backlog_{h-1} + demand_h - served_h
    avg_wait_h   = base_wait + minutes_per_backlog * backlog_h / capacity_h
    p95_h        = max(avg_wait_h, p95_slope * avg_wait_h + p95_intercept)
    band         = p95_h * (1 + q) for q the 10th/90th percentile of relative
                   replay residuals (observed - modeled) / modeled

Everything here is a simulation estimate over labeled synthetic data. A modeled
wait change is not evidence that a staffing change causes a wait change.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date as Date
from typing import Any

from example_server.staffing.model import (
    CapacityLookup,
    CardinalityError,
    fit_wait_response,
    predict_p95,
    quantile,
)

LITTLE_LAW_MINUTES = 60.0
"""Minutes of wait per unit of backlog/hourly-capacity under Little's law."""

LOW_UTILIZATION = 0.70
"""Hours with served / nominal capacity at or below this estimate the base wait."""

SATURATED_EXCESS_MINUTES = 3.0
"""Hours whose avg wait exceeds base by this much are treated as saturated."""

MIN_HOURS_FOR_ESTIMATE = 5
BAND_QUANTILES = (0.10, 0.90)
"""The uncertainty band is the 10th-90th percentile of relative replay residuals."""

CEILING_MIN_TIES = 3
"""A group maximum shared by at least this many hours is a reporting ceiling."""

DEFAULT_CAPACITY_FACTOR = 1.0
K_GRID = (20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 60.0)


# ── data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HourObs:
    """One observed checkpoint-hour for one screening type."""

    hour: int
    served: float
    nominal_capacity: float
    modsets: tuple[tuple[str, str], ...]  # (modset_id, machine_type)
    avg_wait: float
    p95_wait: float


@dataclass(frozen=True)
class QueueParams:
    """Calibrated constants of the queue model for one checkpoint/screening type."""

    base_wait_minutes: float
    minutes_per_backlog: float
    capacity_factor: float
    p95_slope: float
    p95_intercept: float
    band_low: float = 0.0  # relative: p95_low = p95 * (1 + band_low)
    band_high: float = 0.0  # relative: p95_high = p95 * (1 + band_high)
    p95_ceiling: float | None = None  # observed reporting ceiling, if any
    avg_ceiling: float | None = None
    censored_hours: int = 0
    n_hours: int = 0
    n_days: int = 0
    base_wait_hours: int = 0
    saturated_hours: int = 0

    def rounded(self) -> dict[str, Any]:
        return {
            k: (round(v, 3) if isinstance(v, float) else v)
            for k, v in asdict(self).items()
        }


# ── building blocks ──────────────────────────────────────────────────────────


def infer_backlog(
    avg_wait: float, capacity: float, base_wait: float, minutes_per_backlog: float
) -> float:
    """Backlog (passengers) at the end of an hour from its observed avg wait."""
    if capacity <= 0 or minutes_per_backlog <= 0:
        raise ValueError("capacity and minutes_per_backlog must be positive")
    return max(0.0, avg_wait - base_wait) / minutes_per_backlog * capacity


def modeled_avg_wait(
    backlog: float, capacity: float, base_wait: float, minutes_per_backlog: float
) -> float:
    """Inverse of infer_backlog: modeled avg wait (minutes) from backlog."""
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    return base_wait + minutes_per_backlog * max(0.0, backlog) / capacity


def p95_from_avg(avg_wait: float, params: QueueParams) -> float:
    return max(avg_wait, params.p95_slope * avg_wait + params.p95_intercept)


def reconstruct_demand(served: list[float], backlog: list[float]) -> list[float]:
    """demand_h = served_h + backlog_h - backlog_{h-1}, clipped at 0.

    The day starts with an empty queue (backlog_{-1} = 0), as in the seed.
    """
    if len(served) != len(backlog):
        raise ValueError("served and backlog must have the same length")
    out = []
    prev = 0.0
    for s, b in zip(served, backlog):
        out.append(max(0.0, s + b - prev))
        prev = b
    return out


def simulate(
    demand: list[float],
    capacity: list[float],
    params: QueueParams,
    *,
    initial_backlog: float = 0.0,
) -> list[dict[str, float]]:
    """Run the fluid queue hour by hour under a capacity plan.

    `capacity` is effective passengers per hour (already multiplied by the
    capacity factor). Returns one dict per hour with demand, capacity, served,
    end-of-hour backlog, modeled avg wait, modeled p95 and its band.
    """
    if len(demand) != len(capacity):
        raise ValueError("demand and capacity must have the same length")
    out = []
    backlog = max(0.0, initial_backlog)
    for d, c in zip(demand, capacity):
        if c <= 0:
            raise ValueError("every simulated hour needs positive capacity")
        d = max(0.0, d)
        served = min(backlog + d, c)
        backlog = backlog + d - served
        avg = modeled_avg_wait(
            backlog, c, params.base_wait_minutes, params.minutes_per_backlog
        )
        p95 = p95_from_avg(avg, params)
        out.append(
            {
                "demand": d,
                "capacity": c,
                "served": served,
                "backlog": backlog,
                "avg_wait": avg,
                "p95": p95,
                "p95_low": max(0.0, p95 * (1 + params.band_low)),
                "p95_high": p95 * (1 + params.band_high),
            }
        )
    return out


def reconstruct_day(hours: list[HourObs], params: QueueParams) -> dict[str, list]:
    """Effective capacity, inferred backlog and reconstructed demand for a day."""
    cap = [h.nominal_capacity * params.capacity_factor for h in hours]
    backlog = [
        infer_backlog(
            h.avg_wait, c, params.base_wait_minutes, params.minutes_per_backlog
        )
        for h, c in zip(hours, cap)
    ]
    demand = reconstruct_demand([h.served for h in hours], backlog)
    return {"capacity": cap, "backlog": backlog, "demand": demand}


# ── grouping the seed rows ───────────────────────────────────────────────────


def build_days(
    throughput_rows: list[dict[str, Any]],
    wait_rows: list[dict[str, Any]],
    capacity: CapacityLookup,
) -> tuple[dict[str, list[HourObs]], dict[str, int]]:
    """Group one checkpoint/screening type's rows into ordered days of HourObs.

    Rows must already be filtered to one airport, checkpoint and screening
    type. Hours without a partner row in the other table, or with unknown
    capacity, are skipped and counted (missing is not zero).
    """
    waits: dict[tuple[str, int], dict[str, Any]] = {}
    for w in wait_rows:
        key = (w["date"], w["hour"])
        if key in waits:
            raise CardinalityError(
                "wait_times has more than one row for a checkpoint-hour-screening key"
            )
        waits[key] = w
    slots: dict[tuple[str, int], dict[str, Any]] = {}
    for r in throughput_rows:
        key = (r["date"], r["hour"])
        slot = slots.setdefault(key, {"served": 0.0, "cap": 0.0, "mods": {}, "bad": 0})
        slot["served"] += r["passenger_throughput"]
        cap = capacity.get((r["machine_type"], r["screening_type"]))
        if cap is None:
            slot["bad"] += 1
        else:
            slot["cap"] += cap
        slot["mods"][r["modset_id"]] = r["machine_type"]

    diag = {"unmatched_throughput_hours": 0, "unmatched_wait_hours": 0}
    diag["unknown_capacity_hours"] = 0
    days: dict[str, list[HourObs]] = defaultdict(list)
    for key in sorted(slots):
        slot = slots[key]
        w = waits.get(key)
        if w is None:
            diag["unmatched_throughput_hours"] += 1
            continue
        if slot["bad"] or slot["cap"] <= 0:
            diag["unknown_capacity_hours"] += 1
            continue
        days[key[0]].append(
            HourObs(
                hour=key[1],
                served=float(slot["served"]),
                nominal_capacity=slot["cap"],
                modsets=tuple(sorted(slot["mods"].items())),
                avg_wait=float(w["avg_wait_minutes"]),
                p95_wait=float(w["p95_wait_minutes"]),
            )
        )
    diag["unmatched_wait_hours"] = sum(1 for k in waits if k not in slots)
    return dict(days), diag


# ── calibration ──────────────────────────────────────────────────────────────


def _least_squares(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares line y = slope * x + intercept. Returns (slope, intercept)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def _ceiling(values: list[float]) -> float | None:
    """The group maximum when it is shared by >= CEILING_MIN_TIES hours."""
    if not values:
        return None
    top = max(values)
    ties = sum(1 for v in values if v >= top - 1e-9)
    return top if ties >= CEILING_MIN_TIES else None


def _is_censored(h: HourObs, params: QueueParams) -> bool:
    return (params.p95_ceiling is not None and h.p95_wait >= params.p95_ceiling) or (
        params.avg_ceiling is not None and h.avg_wait >= params.avg_ceiling
    )


def calibrate(
    days: dict[str, list[HourObs]],
    *,
    minutes_per_backlog: float = LITTLE_LAW_MINUTES,
) -> QueueParams:
    """Estimate base wait, capacity factor and the p95-from-avg relation.

    - base wait: median avg wait over low-utilization hours (served / nominal
      capacity <= 0.70), where the seed has no backlog;
    - capacity factor: median served / nominal capacity over saturated hours
      (avg wait more than 3 minutes above base), so that simulated capacity
      matches what saturated hours actually served;
    - reporting ceilings: a group maximum of avg or p95 shared by >= 3 hours
      is treated as a ceiling (the seed caps avg at 150 and p95 at 180 min);
    - p95 = slope * avg + intercept by least squares over uncensored hours.
    The band is filled in afterwards from replay residuals (see with_band()).
    """
    hours = [h for hs in days.values() for h in hs]
    if not hours:
        raise ValueError("no calibration hours")
    low = [
        h.avg_wait for h in hours if h.served / h.nominal_capacity <= LOW_UTILIZATION
    ]
    if len(low) >= MIN_HOURS_FOR_ESTIMATE:
        base = quantile(low, 0.5)
    else:
        base = quantile([h.avg_wait for h in hours], 0.10)
    saturated = [
        h.served / h.nominal_capacity
        for h in hours
        if h.avg_wait - base > SATURATED_EXCESS_MINUTES
    ]
    factor = (
        quantile(saturated, 0.5)
        if len(saturated) >= MIN_HOURS_FOR_ESTIMATE
        else DEFAULT_CAPACITY_FACTOR
    )
    params = QueueParams(
        base_wait_minutes=base,
        minutes_per_backlog=minutes_per_backlog,
        capacity_factor=factor,
        p95_slope=1.0,
        p95_intercept=0.0,
        p95_ceiling=_ceiling([h.p95_wait for h in hours]),
        avg_ceiling=_ceiling([h.avg_wait for h in hours]),
    )
    clean = [h for h in hours if not _is_censored(h, params)]
    fit_on = clean if len(clean) >= MIN_HOURS_FOR_ESTIMATE else hours
    slope, intercept = _least_squares(
        [h.avg_wait for h in fit_on], [h.p95_wait for h in fit_on]
    )
    return replace(
        params,
        p95_slope=slope,
        p95_intercept=intercept,
        n_hours=len(hours),
        n_days=len(days),
        base_wait_hours=len(low),
        saturated_hours=len(saturated),
        censored_hours=len(hours) - len(clean),
    )


def replay_day(hours: list[HourObs], params: QueueParams) -> list[dict[str, float]]:
    """Replay one observed day: observed modsets, reconstructed demand."""
    rec = reconstruct_day(hours, params)
    return simulate(rec["demand"], rec["capacity"], params)


def _residual(h: HourObs, modeled: float, params: QueueParams) -> float:
    """observed - modeled p95. An observation at the p95 ceiling only says
    p95 >= ceiling, so a modeled value at or above it counts as a match."""
    if params.p95_ceiling is not None and h.p95_wait >= params.p95_ceiling:
        modeled = min(modeled, h.p95_wait)
    return h.p95_wait - modeled


def _summary(residuals: list[float]) -> dict[str, Any]:
    if not residuals:
        return {"n": 0, "mae": None, "bias": None}
    n = len(residuals)
    return {
        "n": n,
        "mae": round(sum(abs(r) for r in residuals) / n, 2),
        "bias": round(sum(residuals) / n, 2),
    }


def _profile_demand(
    recon: dict[str, dict[str, list]],
    days: dict[str, list[HourObs]],
    target: str,
) -> dict[int, float]:
    """Mean reconstructed demand per hour over other dates (same weekday if >= 2)."""
    wd = Date.fromisoformat(target).weekday()
    others = [d for d in days if d != target]
    same = [d for d in others if Date.fromisoformat(d).weekday() == wd]
    pool = same if len(same) >= 2 else others
    sums: dict[int, list[float]] = defaultdict(list)
    for d in pool:
        for h, dem in zip(days[d], recon[d]["demand"]):
            sums[h.hour].append(dem)
    return {hr: sum(v) / len(v) for hr, v in sums.items()}


def backtest(
    days: dict[str, list[HourObs]],
    params: QueueParams,
    *,
    throughput_rows: list[dict[str, Any]] | None = None,
    wait_rows: list[dict[str, Any]] | None = None,
    capacity: CapacityLookup | None = None,
) -> dict[str, Any]:
    """Modeled vs observed p95 on the calibration days (residual = obs - model).

    - replay: observed modsets + demand reconstructed from the SAME day's
      observed waits. This is close to circular (it mostly checks that the
      inversion and the p95-from-avg relation are consistent) and is an upper
      bound on accuracy;
    - profile: observed modsets + demand = mean reconstructed demand at that
      hour on OTHER dates (same weekday when possible). Out of sample for
      demand, so this is the fairer test of the queue dynamics;
    - same_hour_fit: the two-segment p95 = f(utilization) fit from
      staffing_fit_wait_response, fitted and evaluated in sample on the same
      hours (which favours it).
    Hours at the p95 reporting ceiling are scored as censored for all three.
    """
    replay_res: list[float] = []
    replay_rel: list[float] = []
    profile_res: list[float] = []
    recon = {d: reconstruct_day(hs, params) for d, hs in days.items()}
    for d, hs in days.items():
        sim = simulate(recon[d]["demand"], recon[d]["capacity"], params)
        for h, s in zip(hs, sim):
            r = _residual(h, s["p95"], params)
            replay_res.append(r)
            if s["p95"] > 0:
                replay_rel.append(r / s["p95"])
        if len(days) >= 2:
            prof = _profile_demand(recon, days, d)
            demand = [prof.get(h.hour, 0.0) for h in hs]
            sim_p = simulate(demand, recon[d]["capacity"], params)
            profile_res += [_residual(h, s["p95"], params) for h, s in zip(hs, sim_p)]

    result: dict[str, Any] = {
        "replay": _summary(replay_res),
        "profile": _summary(profile_res),
        "same_hour_fit": {"n": 0, "mae": None, "bias": None},
        "censored_hours": sum(
            1 for hs in days.values() for h in hs if _is_censored(h, params)
        ),
        "residual_band": None,
    }
    if replay_rel:
        result["residual_band"] = {
            "kind": "relative",
            "quantiles": list(BAND_QUANTILES),
            "low": round(quantile(replay_rel, BAND_QUANTILES[0]), 3),
            "high": round(quantile(replay_rel, BAND_QUANTILES[1]), 3),
        }
    if throughput_rows is not None and wait_rows is not None and capacity:
        fit = fit_wait_response(throughput_rows, wait_rows, capacity)
        if fit["fit"]:
            same = [
                _residual(
                    h, predict_p95(fit["fit"], h.served / h.nominal_capacity), params
                )
                for hs in days.values()
                for h in hs
            ]
            result["same_hour_fit"] = {**_summary(same), "r2": fit["fit"]["r2"]}
    result["queue_beats_same_hour"] = {
        "replay": _beats(result["replay"], result["same_hour_fit"]),
        "profile": _beats(result["profile"], result["same_hour_fit"]),
    }
    result["verdict"] = verdict(result)
    return result


def _beats(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    if a["mae"] is None or b["mae"] is None:
        return None
    return a["mae"] < b["mae"]


def verdict(bt: dict[str, Any]) -> str:
    """Plain statement of whether the queue model beats the same-hour fit."""
    same = bt["same_hour_fit"]["mae"]
    rep = bt["replay"]["mae"]
    prof = bt["profile"]["mae"]
    if same is None or rep is None:
        return "not enough data to compare the queue model with the same-hour fit."
    parts = [
        f"replay MAE {rep} vs same-hour fit MAE {same} min: queue model "
        + ("lower" if rep < same else "NOT lower")
    ]
    if prof is not None:
        parts.append(
            f"profile (out-of-sample demand) MAE {prof} min: "
            + ("lower" if prof < same else "NOT lower")
            + " than the same-hour fit"
        )
    parts.append(
        "replay reuses the same day's waits to reconstruct demand, so it is an "
        "upper bound on accuracy, not a forecast test"
    )
    return "; ".join(parts) + "."


def with_band(params: QueueParams, bt: dict[str, Any]) -> QueueParams:
    band = bt.get("residual_band")
    if not band:
        return params
    return replace(params, band_low=band["low"], band_high=band["high"])


def calibrate_k(days: dict[str, list[HourObs]]) -> dict[str, Any]:
    """Profile-backtest MAE over a grid of minutes_per_backlog values.

    Diagnostic only: the tools use Little's law (60) unless told otherwise.
    """
    grid = []
    for k in K_GRID:
        p = calibrate(days, minutes_per_backlog=k)
        grid.append({"minutes_per_backlog": k, **backtest(days, p)["profile"]})
    return {"grid": grid}


# ── plans: installed modsets, required modsets, staff ───────────────────────


def installed_modsets(
    days: dict[str, list[HourObs]], capacity: CapacityLookup, screening_type: str
) -> list[tuple[str, str, float]]:
    """Distinct modsets observed in the calibration days, highest capacity first.

    Returns (modset_id, machine_type, nominal capacity). With the seed's
    rules this puts WTMD (94 pax/h) ahead of AIT (76 pax/h); ties by id.
    """
    seen: dict[str, str] = {}
    for hs in days.values():
        for h in hs:
            for mid, mt in h.modsets:
                seen[mid] = mt
    out = [
        (mid, mt, capacity.get((mt, screening_type), 0.0)) for mid, mt in seen.items()
    ]
    return sorted(out, key=lambda m: (-m[2], m[0]))


def prefix_capacities(
    installed: list[tuple[str, str, float]], params: QueueParams
) -> list[float]:
    """Effective capacity of the first n installed modsets, for n = 1..len."""
    out = []
    total = 0.0
    for _mid, _mt, cap in installed:
        total += cap
        out.append(total * params.capacity_factor)
    return out


PLAN_TOLERANCE_MINUTES = 0.5


def required_plan(
    demand: list[float],
    installed: list[tuple[str, str, float]],
    params: QueueParams,
    target_p95: float,
    *,
    use_band_upper: bool = False,
) -> list[dict[str, Any]]:
    """Hour-by-hour minimum modsets that keep the plan on target.

    Reference run: every installed modset active in every hour. An hour is
    `reachable` when the reference run meets the target there; otherwise the
    target is out of reach at installed capacity and the plan uses every
    installed modset in that hour.

    For a reachable hour, the plan picks the smallest n (highest-capacity
    modsets first) such that (a) this hour's modeled p95 (or band upper bound)
    is <= target given the backlog carried from earlier hours of the plan, and
    (b) with every installed modset active afterwards, no later reachable hour
    goes above the target and no later unreachable hour goes more than 0.5 min
    above its reference value. (b) stops a low early choice from leaving a
    backlog that later hours cannot clear. All-installed always satisfies both,
    so a choice always exists.
    """
    if not installed:
        raise ValueError("no installed modsets")
    caps = prefix_capacities(installed, params)
    n_max = len(installed)
    key = "p95_high" if use_band_upper else "p95"
    full = simulate(demand, [caps[-1]] * len(demand), params)
    reachable = [s[key] <= target_p95 for s in full]

    out = []
    backlog = 0.0
    for h, d in enumerate(demand):
        chosen: tuple[int, dict[str, float]] | None = None
        start = 1 if reachable[h] else n_max
        for n in range(start, n_max + 1):
            step = simulate([d], [caps[n - 1]], params, initial_backlog=backlog)[0]
            if reachable[h] and step[key] > target_p95:
                continue
            rest = simulate(
                demand[h + 1 :],
                [caps[-1]] * (len(demand) - h - 1),
                params,
                initial_backlog=step["backlog"],
            )
            ok = all(
                r[key] <= target_p95
                if reachable[h + 1 + i]
                else r[key] <= full[h + 1 + i][key] + PLAN_TOLERANCE_MINUTES
                for i, r in enumerate(rest)
            )
            if ok or n == n_max:
                chosen = (n, step)
                break
        if chosen is None:  # unreachable only through float noise; use all
            step = simulate([d], [caps[-1]], params, initial_backlog=backlog)[0]
            chosen = (n_max, step)
        n, step = chosen
        backlog = step["backlog"]
        out.append(
            {
                "n": n,
                "reachable": reachable[h],
                "meets_target": step[key] <= target_p95,
                **step,
            }
        )
    return out


def gap_beyond_installed(
    demand: list[float],
    installed: list[tuple[str, str, float]],
    params: QueueParams,
    target_p95: float,
    *,
    max_extra: int = 10,
) -> list[dict[str, Any]]:
    """Hypothetical modsets beyond installed needed per hour.

    For e = 0..max_extra, run every installed modset plus e hypothetical
    copies of the lowest-capacity installed modset in every hour; the gap at
    an hour is the smallest e that meets the target there. `capped` marks
    hours still above target at max_extra.
    """
    base = sum(m[2] for m in installed)
    weakest = installed[-1][2]
    gaps: list[int | None] = [None] * len(demand)
    for e in range(max_extra + 1):
        cap = (base + e * weakest) * params.capacity_factor
        sim = simulate(demand, [cap] * len(demand), params)
        for h, s in enumerate(sim):
            if gaps[h] is None and s["p95"] <= target_p95:
                gaps[h] = e
        if all(g is not None for g in gaps):
            break
    return [{"gap": max_extra if g is None else g, "capped": g is None} for g in gaps]


def plan_capacity(modsets: list[tuple[str, str, float]], params: QueueParams) -> float:
    return sum(m[2] for m in modsets) * params.capacity_factor


def staff_for(
    modsets: list[tuple[str, str]],
    rules: dict[tuple[str, str], dict[str, Any]],
    screening_type: str,
) -> float:
    """Screening staff for a set of modsets: sum staff_per_modset x relief."""
    total = 0.0
    for _mid, mt in modsets:
        r = rules[(mt, screening_type)]
        total += r["staff_per_modset"] * r["relief_factor"]
    return total


def adjust_modsets(
    active: list[tuple[str, str, float]],
    installed: list[tuple[str, str, float]],
    delta: int,
) -> list[tuple[str, str, float]]:
    """Apply a modset delta: add highest-capacity inactive first, remove
    lowest-capacity active first. Result is clipped to 1..installed."""
    current = sorted(active, key=lambda m: (-m[2], m[0]))
    if delta > 0:
        active_ids = {m[0] for m in current}
        spare = [m for m in installed if m[0] not in active_ids]
        current = current + spare[:delta]
    elif delta < 0:
        keep = max(1, len(current) + delta)
        current = current[:keep]
    return sorted(current, key=lambda m: (-m[2], m[0]))
