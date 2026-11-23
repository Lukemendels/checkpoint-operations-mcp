"""Shared set-up for the queue-model tools (staffing_required_for_forecast and
staffing_simulate_scenario).

Builds, for one airport / checkpoint / screening type, the calibration days,
the calibrated queue parameters with their backtest, the installed modsets,
the staffing rules and the demand series for the target date. Reads only
through the provider passed in; every number is a simulation estimate over
labeled synthetic data.
"""

from __future__ import annotations

import math
import weakref
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from example_server.models import DateRange, ForecastHour, ForecastSource
from example_server.staffing.baseline import WARNINGS, baseline_forecast
from example_server.staffing.common import error
from example_server.staffing.model import (
    CardinalityError,
    capacity_lookup_from_rules,
    filter_rows,
)
from example_server.staffing.queue import (
    HourObs,
    QueueParams,
    backtest,
    build_days,
    calibrate,
    installed_modsets,
    reconstruct_day,
    with_band,
)

MIN_CALIBRATION_DAYS = 3


class PlanningError(ValueError):
    """Carries a sanitized error dict for the tool to return."""

    def __init__(self, payload: dict[str, str]) -> None:
        super().__init__(payload["error"])
        self.payload = payload


@dataclass
class GroupContext:
    airport: str
    checkpoint: str
    screening: str
    target_date: str
    params: QueueParams
    backtest: dict[str, Any]
    installed: list[tuple[str, str, float]]
    rules: dict[tuple[str, str], dict[str, Any]]
    fixed_posts: int
    open_hour: int
    close_hour: int
    calibration_dates: list[str]
    target_hours: list[HourObs] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    forecast_metadata: dict[str, Any] | None = None

    # ── demand ────────────────────────────────────────────────────────────

    def seed_demand(self) -> list[dict[str, Any]]:
        """Reconstructed demand for the target date (calibration params)."""
        rec = reconstruct_day(self.target_hours, self.params)
        return [
            {
                "hour": h.hour,
                "predicted_pax": round(d, 1),
                "interval_low": None,
                "interval_high": None,
                "observed_served": h.served,
                "observed_active_modsets": len(h.modsets),
                "observed_p95_wait": h.p95_wait,
            }
            for h, d in zip(self.target_hours, rec["demand"])
        ]

    # ── staff ─────────────────────────────────────────────────────────────

    def staff(self, modsets: list[tuple[str, str, float]]) -> dict[str, Any]:
        screening_staff = 0.0
        positions = 0
        for _mid, mt, _cap in modsets:
            r = self.rules[(mt, self.screening)]
            positions += r["staff_per_modset"]
            screening_staff += r["staff_per_modset"] * r["relief_factor"]
        return {
            "modset_positions": positions,
            "screening_staff_with_relief": round(screening_staff, 2),
            "fixed_posts": self.fixed_posts,
            "total_staff": math.ceil(screening_staff - 1e-9) + self.fixed_posts,
        }

    def model_block(self) -> dict[str, Any]:
        bt = self.backtest
        return {
            **({"forecast": self.forecast_metadata} if self.forecast_metadata else {}),
            "kind": "hour-by-hour fluid queue (backlog carried between hours)",
            "parameters": self.params.rounded(),
            "calibration": {
                "dates": len(self.calibration_dates),
                "first": self.calibration_dates[0],
                "last": self.calibration_dates[-1],
                "target_date_excluded": True,
            },
            "backtest": {
                "replay": bt["replay"],
                "profile": bt["profile"],
                "same_hour_fit": bt["same_hour_fit"],
                "queue_beats_same_hour": bt["queue_beats_same_hour"],
                "verdict": bt["verdict"],
            },
            "installed_modsets": [
                {"modset_id": m, "machine_type": t, "nominal_pax_per_hour": c}
                for m, t, c in self.installed
            ],
        }


def build_context(
    provider: Any,
    *,
    airport: str,
    checkpoint: str,
    screening: str,
    target: date,
    calibration_range: DateRange | None,
    need_target_day: bool,
    prior_only: bool = False,
) -> GroupContext:
    """Calibrate the queue model for one group; raise PlanningError on misuse."""
    config = provider.checkpoint_config()
    cfg = next(
        (
            c
            for c in config
            if c["airport_code"] == airport and c["checkpoint_id"] == checkpoint
        ),
        None,
    )
    if cfg is None:
        known = sorted(
            c["checkpoint_id"] for c in config if c["airport_code"] == airport
        )
        raise PlanningError(
            error(
                f"checkpoint '{checkpoint}' is not configured for {airport}.",
                "Known checkpoints: " + ", ".join(known),
            )
        )
    rules_rows = provider.staffing_rules()
    capacity = capacity_lookup_from_rules(rules_rows)
    rules = {(r["machine_type"], r["screening_type"]): r for r in rules_rows}

    sel = {
        "airport_code": airport,
        "checkpoint_id": checkpoint,
        "screening_type": screening,
    }
    t_rows = filter_rows(provider.throughput(), **sel)
    w_rows = filter_rows(provider.wait_times(), **sel)
    target_s = target.isoformat()
    if prior_only:
        t_rows = [r for r in t_rows if r["date"] < target_s]
        w_rows = [r for r in w_rows if r["date"] < target_s]
    try:
        all_days, diag = build_days(t_rows, w_rows, capacity)
    except CardinalityError as exc:
        raise PlanningError(
            error(str(exc), "Deduplicate wait_times before modeling.")
        ) from exc
    if not all_days:
        raise PlanningError(
            error(
                f"no joined throughput and wait rows for {airport} {checkpoint} "
                f"{screening}.",
                "Call pims_get_dataset_coverage for the airports and dates the "
                "synthetic seed covers.",
            )
        )
    seed_dates = sorted(all_days)
    if need_target_day and target_s not in all_days:
        raise PlanningError(
            error(
                f"date {target_s} is not in the synthetic seed for this checkpoint.",
                f"Use a date from {seed_dates[0]} to {seed_dates[-1]}, or pass "
                "forecast_source='inline' with a forecast.",
            )
        )

    warnings: list[str] = []
    if calibration_range is not None:
        lo, hi = calibration_range.start.isoformat(), calibration_range.end.isoformat()
        calib_dates = [d for d in seed_dates if lo <= d <= hi and d != target_s]
        if lo <= target_s <= hi:
            warnings.append(
                "the target date was inside calibration_range and was excluded "
                "from calibration to avoid scoring the model on its own input."
            )
    else:
        calib_dates = [d for d in seed_dates if d != target_s]
    if len(calib_dates) < MIN_CALIBRATION_DAYS:
        raise PlanningError(
            error(
                f"only {len(calib_dates)} calibration day(s) available; "
                f"need at least {MIN_CALIBRATION_DAYS}.",
                f"Widen calibration_range within {seed_dates[0]}..{seed_dates[-1]} "
                "or omit it to use every seed date except the target.",
            )
        )
    calib_days = {d: all_days[d] for d in calib_dates}
    calib_set = set(calib_dates)
    params = calibrate(calib_days)
    bt = backtest(
        calib_days,
        params,
        throughput_rows=[r for r in t_rows if r["date"] in calib_set],
        wait_rows=[r for r in w_rows if r["date"] in calib_set],
        capacity=capacity,
    )
    params = with_band(params, bt)
    if diag["unmatched_throughput_hours"] or diag["unmatched_wait_hours"]:
        warnings.append(
            f"{diag['unmatched_throughput_hours']} throughput hours and "
            f"{diag['unmatched_wait_hours']} wait hours had no partner row and were "
            "left out (missing is not zero)."
        )
    if params.censored_hours:
        warnings.append(
            f"{params.censored_hours} calibration hours sit at the seed's reporting "
            f"ceiling (p95 {params.p95_ceiling} / avg {params.avg_ceiling} min); "
            "their backlog is a lower bound and they were left out of the "
            "p95-from-avg fit."
        )
    if bt["queue_beats_same_hour"]["profile"] is False:
        warnings.append(
            "on this checkpoint the queue model's out-of-sample (profile) backtest "
            "MAE is NOT lower than the same-hour fit's in-sample MAE; treat modeled "
            "p95 values with extra caution. " + bt["verdict"]
        )
    ctx = GroupContext(
        airport=airport,
        checkpoint=checkpoint,
        screening=screening,
        target_date=target_s,
        params=params,
        backtest=bt,
        installed=installed_modsets(calib_days, capacity, screening),
        rules=rules,
        fixed_posts=int(cfg["fixed_posts"]),
        open_hour=int(cfg["open_hour"]),
        close_hour=int(cfg["close_hour"]),
        calibration_dates=calib_dates,
        target_hours=all_days.get(target_s, []),
        warnings=warnings,
    )
    if prior_only:
        try:
            baseline = baseline_forecast(
                [r for r in t_rows if r["date"] in calib_set],
                [r for r in w_rows if r["date"] in calib_set],
                target_date=target,
                hours=list(range(ctx.open_hour, ctx.close_hour + 1)),
                airport_code=airport,
                checkpoint_id=checkpoint,
                screening_type=screening,
            )
        except (ValueError, OverflowError) as exc:
            raise PlanningError(
                error("Invalid synthetic forecast history.", "Check seed data quality.")
            ) from exc
        ctx.forecast_metadata = baseline
    return ctx


def inline_demand(
    forecast: list[ForecastHour] | None, ctx_open: int, ctx_close: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate an inline forecast; return ordered rows and warnings."""
    if not forecast:
        raise PlanningError(
            error(
                "forecast_source='inline' needs a non-empty forecast list.",
                "Pass forecast=[{hour, predicted_pax, interval_low?, "
                "interval_high?}, ...] for contiguous hours, or use "
                "forecast_source='seed_replay'.",
            )
        )
    hours = [f.hour for f in forecast]
    if len(set(hours)) != len(hours):
        raise PlanningError(
            error(
                "forecast has more than one entry for the same hour.",
                "Give exactly one entry per hour.",
            )
        )
    ordered = sorted(forecast, key=lambda f: f.hour)
    if ordered[-1].hour - ordered[0].hour + 1 != len(ordered):
        raise PlanningError(
            error(
                "forecast hours must be contiguous (backlog carries hour to hour).",
                "Fill the missing hours, e.g. with predicted_pax=0 if closed.",
            )
        )
    rows = []
    for f in ordered:
        low = f.interval_low
        high = f.interval_high
        if (low is not None and low > f.predicted_pax) or (
            high is not None and high < f.predicted_pax
        ):
            raise PlanningError(
                error(
                    f"hour {f.hour}: interval must satisfy "
                    "interval_low <= predicted_pax <= interval_high.",
                    "Fix or omit the interval bounds.",
                )
            )
        rows.append(
            {
                "hour": f.hour,
                "predicted_pax": f.predicted_pax,
                "interval_low": low,
                "interval_high": high,
            }
        )
    warnings = []
    outside = [r["hour"] for r in rows if not ctx_open <= r["hour"] <= ctx_close]
    if outside:
        warnings.append(
            f"forecast hours {outside} fall outside the checkpoint's configured "
            f"hours {ctx_open:02d}-{ctx_close:02d}; they were modeled anyway."
        )
    if rows[0]["hour"] != ctx_open:
        warnings.append(
            f"the forecast starts at hour {rows[0]['hour']}, not at opening "
            f"({ctx_open:02d}); the backlog is assumed empty at its first hour."
        )
    return rows, warnings


def resolve_demand(
    ctx: GroupContext,
    source: ForecastSource,
    forecast: list[ForecastHour] | None,
) -> list[dict[str, Any]]:
    if source is ForecastSource.SEED_REPLAY:
        if forecast:
            ctx.warnings.append(
                "forecast was ignored because forecast_source='seed_replay'."
            )
        return ctx.seed_demand()
    if source is ForecastSource.SYNTHETIC_WEEKDAY:
        if forecast is not None:
            raise PlanningError(
                error(
                    "synthetic_weekday does not accept inline forecast input.",
                    "Remove forecast or use forecast_source='inline'.",
                )
            )
        baseline = ctx.forecast_metadata
        if not baseline or baseline["refused_hours"]:
            refused = [r["hour"] for r in (baseline or {}).get("refused_hours", [])]
            raise PlanningError(
                error(
                    f"Insufficient comparable synthetic history for hours {refused}.",
                    "Choose a later target with two complete prior same weekdays "
                    "or use seed_replay. Missing hours are not treated as zero.",
                )
            )
        ctx.warnings.extend(WARNINGS)
        ctx.warnings.append(
            "No passenger-volume interval provided; output bands are queue-model "
            "bands, not end-to-end demand uncertainty."
        )
        return [
            {
                "hour": r["hour"],
                "predicted_pax": r["predicted_pax"],
                "interval_low": None,
                "interval_high": None,
            }
            for r in baseline["rows"]
        ]
    rows, warns = inline_demand(forecast, ctx.open_hour, ctx.close_hour)
    ctx.warnings.extend(warns)
    return rows


def band(step: dict[str, float]) -> list[float]:
    return [round(step["p95_low"], 1), round(step["p95_high"], 1)]


_BACKTEST_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def backtest_table(provider: Any) -> dict[str, Any]:
    """Backtest of the queue model for every configured checkpoint and screening
    type, calibrated on all available dates (in sample), cached per provider."""
    cached = _BACKTEST_CACHE.get(provider)
    if cached is not None:
        return cached
    rules_rows = provider.staffing_rules()
    capacity = capacity_lookup_from_rules(rules_rows)
    screenings = sorted({r["screening_type"] for r in rules_rows})
    throughput = provider.throughput()
    waits = provider.wait_times()
    rows = []
    for cfg in provider.checkpoint_config():
        for st in screenings:
            sel = {
                "airport_code": cfg["airport_code"],
                "checkpoint_id": cfg["checkpoint_id"],
                "screening_type": st,
            }
            t_rows = filter_rows(throughput, **sel)
            w_rows = filter_rows(waits, **sel)
            try:
                days, _diag = build_days(t_rows, w_rows, capacity)
            except CardinalityError:
                continue
            if len(days) < MIN_CALIBRATION_DAYS:
                continue
            params = calibrate(days)
            bt = backtest(
                days,
                params,
                throughput_rows=t_rows,
                wait_rows=w_rows,
                capacity=capacity,
            )
            rows.append(
                {
                    **sel,
                    "n_hours": bt["replay"]["n"],
                    "replay_mae": bt["replay"]["mae"],
                    "replay_bias": bt["replay"]["bias"],
                    "profile_mae": bt["profile"]["mae"],
                    "profile_bias": bt["profile"]["bias"],
                    "same_hour_mae": bt["same_hour_fit"]["mae"],
                    "same_hour_r2": bt["same_hour_fit"].get("r2"),
                    "censored_hours": bt["censored_hours"],
                    "profile_beats_same_hour": bt["queue_beats_same_hour"]["profile"],
                }
            )

    def pooled(key: str) -> float | None:
        vals = [(r[key], r["n_hours"]) for r in rows if r[key] is not None]
        n = sum(w for _v, w in vals)
        return round(sum(v * w for v, w in vals) / n, 2) if n else None

    summary = {
        "groups": len(rows),
        "pooled_replay_mae": pooled("replay_mae"),
        "pooled_profile_mae": pooled("profile_mae"),
        "pooled_same_hour_mae": pooled("same_hour_mae"),
        "groups_where_replay_beats_same_hour": sum(
            1
            for r in rows
            if r["replay_mae"] is not None
            and r["same_hour_mae"] is not None
            and r["replay_mae"] < r["same_hour_mae"]
        ),
        "groups_where_profile_beats_same_hour": sum(
            1 for r in rows if r["profile_beats_same_hour"]
        ),
    }
    rep, prof, same = (
        summary["pooled_replay_mae"],
        summary["pooled_profile_mae"],
        summary["pooled_same_hour_mae"],
    )
    if rep is None or same is None:
        summary["verdict"] = "not enough data for a backtest."
    else:
        summary["verdict"] = (
            f"Pooled p95 MAE: replay {rep} min ("
            + ("lower" if rep < same else "NOT lower")
            + f" than the same-hour fit's {same}); profile {prof} min ("
            + ("lower" if prof is not None and prof < same else "NOT lower")
            + f"; better in {summary['groups_where_profile_beats_same_hour']} of "
            f"{summary['groups']} groups). Replay reuses the same day's waits, so "
            "only the profile test is out of sample for demand; on that test the "
            "queue model does not beat the same-hour fit overall on this seed."
            if prof is not None and prof >= same
            else f"Pooled p95 MAE: replay {rep}, profile {prof}, same-hour fit "
            f"{same} min. Replay reuses the same day's waits, so only the profile "
            "test is out of sample for demand."
        )
    result = {"summary": summary, "rows": rows}
    _BACKTEST_CACHE[provider] = result
    return result
