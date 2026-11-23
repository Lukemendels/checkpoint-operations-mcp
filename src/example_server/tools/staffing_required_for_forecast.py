"""staffing_required_for_forecast — minimum modsets and staff to meet a p95 target.

Hour by hour, with backlog carried between hours (fluid queue, see
staffing/queue.py), find the smallest number of modsets whose modeled p95 wait
is at or below the target, plus a conservative number that uses the upper
uncertainty band and the forecast's upper interval. Demand comes from the
synthetic seed day (seed_replay) or from an inline forecast.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from example_server.models import (
    AirportCode,
    DateRange,
    ForecastHour,
    ForecastSource,
    ResponseFormat,
    ScreeningType,
)
from example_server.staffing.common import envelope, unavailable
from example_server.staffing.planning import (
    PlanningError,
    band,
    build_context,
    resolve_demand,
)
from example_server.staffing.provider import DataUnavailableError, get_provider
from example_server.staffing.queue import gap_beyond_installed, required_plan

MAX_HYPOTHETICAL_EXTRA = 10

ASSUMPTIONS = [
    "Fluid queue: served = min(backlog + demand, capacity); backlog carries to the "
    "next hour; the day starts with an empty queue.",
    "Capacity of n modsets = capacity_factor x sum of assumed capacities "
    "(staffing_rules) of the n highest-capacity installed modsets (WTMD before AIT).",
    "Modeled avg wait = base wait + 60 x backlog / capacity minutes (Little's law); "
    "p95 = fitted slope x avg + intercept; band = 10th-90th percentile of relative "
    "replay residuals.",
    "Installed modsets = distinct modset_ids observed for this checkpoint and "
    "screening type on the calibration dates.",
    "Minimum modsets per hour: the smallest n that meets the target given the "
    "backlog left by earlier hours of the plan, and that does not leave a backlog "
    "pushing a later hour over target (assuming all installed modsets afterwards).",
    "An hour is unreachable when the target is not met even with every installed "
    "modset active in every hour of the day; those hours use all installed "
    "modsets.",
    "Staff = sum of staff_per_modset x relief_factor over the plan's modsets "
    "(rounded up) + the checkpoint's fixed posts. Fixed posts are per "
    "checkpoint-hour: do not add them again when combining screening types.",
    "The band comes from replay residuals, which reuse each day's own waits; it "
    "does not include demand-forecast error (use interval_high for that).",
    "seed_replay demand is reconstructed from the target day's synthetic waits "
    "and throughput (served + backlog change); it stands in for a forecast.",
]


def register(mcp: FastMCP) -> None:
    """Register the required-staffing tool with the MCP server."""

    @mcp.tool(
        name="staffing_required_for_forecast",
        annotations={
            "title": "Modeled modsets and staff needed for a p95 target",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def staffing_required_for_forecast(
        airport_code: Annotated[AirportCode, "IATA code of the airport."],
        checkpoint_id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=80,
                description="Checkpoint id as configured for the airport.",
            ),
        ],
        date: Annotated[
            date,
            "Target date (YYYY-MM-DD). For seed_replay it must be in the seed; it "
            "is always excluded from calibration.",
        ],
        screening_type: Annotated[ScreeningType, "Standard or PreCheck."],
        target_p95_minutes: Annotated[
            Literal[15, 30], "Target modeled p95 wait in minutes: 15 or 30."
        ],
        forecast_source: Annotated[
            ForecastSource,
            "'seed_replay' (default): demand reconstructed from the synthetic seed "
            "day. 'inline': demand from the `forecast` argument. "
            "'synthetic_weekday': prior-only processed-volume proxy.",
        ] = ForecastSource.SEED_REPLAY,
        forecast: Annotated[
            list[ForecastHour] | None,
            Field(
                max_length=24,
                description="Required when forecast_source='inline': contiguous "
                "hours of {hour, predicted_pax, interval_low?, interval_high?} "
                "(max 24).",
            ),
        ] = None,
        calibration_range: Annotated[
            DateRange | None,
            "Dates used to calibrate the queue model; default every seed date "
            "except the target date.",
        ] = None,
        response_format: Annotated[
            ResponseFormat,
            "Return machine-readable JSON (default) or human-readable Markdown.",
        ] = ResponseFormat.JSON,
    ) -> dict[str, Any] | str:
        """Modeled minimum modsets and staff per hour to keep p95 wait <= target.

        Synthetic data only; outputs are simulation estimates, not staffing
        recommendations. Returns per hour: forecast demand, minimum modsets and
        the modeled p95 (with band) at that number, a conservative number (band
        upper bound and interval_high), installed modsets, the modeled gap
        beyond installed, staff, and a flag where the target is not reached
        even with every installed modset. Also returns the calibrated queue
        parameters and a backtest against the same-hour fit
        (staffing_fit_wait_response). Use staffing_simulate_scenario to test
        changes to a plan.
        """
        if forecast is not None and len(forecast) > 24:
            return {
                "error": "forecast has more than 24 hours.",
                "hint": "Pass at most one entry per hour of one day.",
                "data_label": "synthetic",
            }
        try:
            provider = get_provider()
            ctx = build_context(
                provider,
                airport=airport_code.value,
                checkpoint=checkpoint_id,
                screening=screening_type.value,
                target=date,
                calibration_range=calibration_range,
                need_target_day=forecast_source is ForecastSource.SEED_REPLAY,
                prior_only=forecast_source is ForecastSource.SYNTHETIC_WEEKDAY,
            )
            demand_rows = resolve_demand(ctx, forecast_source, forecast)
        except DataUnavailableError as exc:
            return unavailable(exc)
        except PlanningError as exc:
            return exc.payload

        target = float(target_p95_minutes)
        installed = ctx.installed
        pred = [r["predicted_pax"] for r in demand_rows]
        high = [
            r["interval_high"] if r["interval_high"] is not None else r["predicted_pax"]
            for r in demand_rows
        ]
        plan = required_plan(pred, installed, ctx.params, target)
        cons = required_plan(high, installed, ctx.params, target, use_band_upper=True)
        weakest = installed[-1]
        gaps = gap_beyond_installed(
            pred, installed, ctx.params, target, max_extra=MAX_HYPOTHETICAL_EXTRA
        )

        hours = []
        for row, p, c, g in zip(demand_rows, plan, cons, gaps):
            staff = ctx.staff(installed[: p["n"]])
            cons_staff = ctx.staff(installed[: c["n"]])
            entry = {
                "hour": row["hour"],
                "forecast_demand_pax": round(row["predicted_pax"], 1),
                "interval_low": row["interval_low"],
                "interval_high": row["interval_high"],
                "min_modsets": p["n"],
                "min_plan_modset_ids": [m[0] for m in installed[: p["n"]]],
                "modeled_p95_minutes": round(p["p95"], 1),
                "p95_band_minutes": band(p),
                "modeled_backlog_pax": round(p["backlog"], 1),
                "meets_target": p["meets_target"],
                "conservative_modsets": c["n"],
                "conservative_p95_high_minutes": round(c["p95_high"], 1),
                "conservative_meets_target": c["meets_target"],
                "installed_modsets": len(installed),
                "unreachable_at_installed": not p["reachable"],
                "gap_modsets_beyond_installed": g["gap"],
                "gap_capped": g["capped"],
                "staff": staff,
                "conservative_total_staff": cons_staff["total_staff"],
            }
            if "observed_active_modsets" in row:
                entry["observed_active_modsets"] = row["observed_active_modsets"]
                entry["observed_p95_wait_minutes"] = row["observed_p95_wait"]
            hours.append(entry)

        unreachable = [h["hour"] for h in hours if h["unreachable_at_installed"]]
        totals = {
            "hours": len(hours),
            "hours_meeting_target_min_plan": sum(h["meets_target"] for h in hours),
            "unreachable_hours": unreachable,
            "min_plan_modset_hours": sum(h["min_modsets"] for h in hours),
            "conservative_modset_hours": sum(h["conservative_modsets"] for h in hours),
            "min_plan_staff_hours": sum(h["staff"]["total_staff"] for h in hours),
            "conservative_staff_hours": sum(
                h["conservative_total_staff"] for h in hours
            ),
            "max_modeled_p95_minutes": max(h["modeled_p95_minutes"] for h in hours),
        }
        warnings = list(ctx.warnings)
        if unreachable:
            warnings.append(
                f"the {target_p95_minutes}-minute target is not reached in "
                f"{len(unreachable)} hour(s) even with all {len(installed)} installed "
                "modsets active all day; gap_modsets_beyond_installed is a "
                f"hypothetical count of extra {weakest[1]} modsets active all day "
                f"(capped at {MAX_HYPOTHETICAL_EXTRA}), not equipment that exists."
            )
        filters = {
            "airport_code": ctx.airport,
            "checkpoint_id": ctx.checkpoint,
            "screening_type": ctx.screening,
            "date": ctx.target_date,
            "target_p95_minutes": target_p95_minutes,
            "forecast_source": forecast_source.value,
        }
        result = envelope(
            {
                "hours": hours,
                "totals": totals,
                "model": ctx.model_block(),
                "next_step": "Call staffing_simulate_scenario to compare modeled "
                "p95 under changes to this plan.",
            },
            assumptions=ASSUMPTIONS,
            warnings=warnings,
            filters=filters,
        )
        if response_format is ResponseFormat.MARKDOWN:
            return _markdown(result)
        return result


def _markdown(result: dict[str, Any]) -> str:
    f = result["filters"]
    bt = result["model"]["backtest"]
    lines = [
        f"# Modeled modsets for p95 <= {f['target_p95_minutes']} min — "
        f"{f['airport_code']} {f['checkpoint_id']} {f['screening_type']}, "
        f"{f['date']} (synthetic)",
        "",
        f"Demand source: {f['forecast_source']}. Simulation estimates only.",
        "",
        "| hour | demand | min n | p95 (band) | conservative n | installed | gap | "
        "staff | target met |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for h in result["hours"]:
        lo, hi = h["p95_band_minutes"]
        lines.append(
            f"| {h['hour']:02d} | {h['forecast_demand_pax']} | {h['min_modsets']} | "
            f"{h['modeled_p95_minutes']} ({lo}–{hi}) | {h['conservative_modsets']} | "
            f"{h['installed_modsets']} | {h['gap_modsets_beyond_installed']} | "
            f"{h['staff']['total_staff']} | {'yes' if h['meets_target'] else 'NO'} |"
        )
    t = result["totals"]
    lines += [
        "",
        f"Modset-hours: {t['min_plan_modset_hours']} (min) / "
        f"{t['conservative_modset_hours']} (conservative). Staff-hours: "
        f"{t['min_plan_staff_hours']} / {t['conservative_staff_hours']}.",
        f"Unreachable hours: {t['unreachable_hours'] or 'none'}.",
        "",
        f"Backtest: {bt['verdict']}",
        "",
    ]
    lines += [f"- {w}" for w in result["warnings"]]
    lines.append(
        "_Synthetic data; a modeled wait change is not evidence of a causal "
        "staffing benefit._"
    )
    return "\n".join(lines)
