"""staffing_simulate_scenario — modeled p95 before and after modset changes.

Baseline plan: the observed active modsets on the seed day (seed_replay), or
the minimum plan from staffing_required_for_forecast (inline). Changes add or
remove modsets at given hours; the fluid queue is re-run, so a change at one
hour also changes the modeled backlog and wait in later hours.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from example_server.models import (
    AirportCode,
    ForecastHour,
    ForecastSource,
    ResponseFormat,
    ScenarioChange,
    ScreeningType,
)
from example_server.staffing.common import envelope, error, unavailable
from example_server.staffing.planning import (
    PlanningError,
    band,
    build_context,
    resolve_demand,
)
from example_server.staffing.provider import DataUnavailableError, get_provider
from example_server.staffing.queue import (
    adjust_modsets,
    plan_capacity,
    required_plan,
    simulate,
)

ASSUMPTIONS = [
    "Same fluid queue as staffing_required_for_forecast; demand is held fixed "
    "between baseline and scenario (the model has no demand response).",
    "Baseline (seed_replay) = observed active modsets each hour; baseline "
    "(inline) = the minimum plan for the target from staffing_required_for_forecast.",
    "A positive delta adds the highest-capacity installed modsets that are not "
    "active; a negative delta removes the lowest-capacity active modsets first.",
    "Requested changes are clipped to 1..installed modsets; the clip is reported "
    "as the binding constraint.",
    "available_staff is compared with this screening type's staff plus the "
    "checkpoint's fixed posts; it is reported, not enforced.",
    "Backlog carries between hours, so a change at hour h also changes modeled "
    "waits at later hours.",
]


def register(mcp: FastMCP) -> None:
    """Register the scenario tool with the MCP server."""

    @mcp.tool(
        name="staffing_simulate_scenario",
        annotations={
            "title": "Simulate modeled waits under modset changes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def staffing_simulate_scenario(
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
            "Target date (YYYY-MM-DD); must be in the seed for seed_replay.",
        ],
        screening_type: Annotated[ScreeningType, "Standard or PreCheck."],
        changes: Annotated[
            list[ScenarioChange],
            Field(
                max_length=24,
                description="Modset changes: [{hour, delta_modsets}], one entry "
                "per hour (max 24). An empty list returns the baseline.",
            ),
        ],
        target_p95_minutes: Annotated[
            Literal[15, 30], "Target modeled p95 wait in minutes: 15 or 30."
        ],
        available_staff: Annotated[
            int | None,
            Field(
                ge=0,
                le=500,
                description="Optional TOTAL staff on hand per hour (0-500) for "
                "this screening type plus the checkpoint's fixed posts. It is "
                "compared with total modeled staff each hour, not with the "
                "staff added by the change.",
            ),
        ] = None,
        forecast_source: Annotated[
            ForecastSource,
            "'seed_replay' (default), 'inline' (requires `forecast`), or "
            "'synthetic_weekday' (prior-only processed-volume proxy).",
        ] = ForecastSource.SEED_REPLAY,
        forecast: Annotated[
            list[ForecastHour] | None,
            Field(
                max_length=24,
                description="Inline forecast: contiguous hours of {hour, "
                "predicted_pax, interval_low?, interval_high?}.",
            ),
        ] = None,
        response_format: Annotated[
            ResponseFormat,
            "Return machine-readable JSON (default) or human-readable Markdown.",
        ] = ResponseFormat.JSON,
    ) -> dict[str, Any] | str:
        """Compare modeled p95 waits and staff before and after modset changes.

        Synthetic data only; simulation estimates, not recommendations or
        causal claims. Returns per hour: modsets, modeled p95 with band and
        staff before and after, the modeled p95 change, feasibility
        (1 <= modsets <= installed; staff <= available_staff when given) and
        the binding constraint; plus totals. Because backlog carries between
        hours, hours after a change can show a modeled change too.
        """
        hours_changed = [c.hour for c in changes]
        if len(set(hours_changed)) != len(hours_changed):
            return error(
                "changes has more than one entry for the same hour.",
                "Combine them into one delta_modsets per hour.",
            )
        try:
            provider = get_provider()
            ctx = build_context(
                provider,
                airport=airport_code.value,
                checkpoint=checkpoint_id,
                screening=screening_type.value,
                target=date,
                calibration_range=None,
                need_target_day=forecast_source is ForecastSource.SEED_REPLAY,
                prior_only=forecast_source is ForecastSource.SYNTHETIC_WEEKDAY,
            )
            demand_rows = resolve_demand(ctx, forecast_source, forecast)
        except DataUnavailableError as exc:
            return unavailable(exc)
        except PlanningError as exc:
            return exc.payload

        plan_hours = [r["hour"] for r in demand_rows]
        outside = sorted(set(hours_changed) - set(plan_hours))
        if outside:
            return error(
                f"changes refer to hours {outside} that are not in the plan.",
                f"Use hours {plan_hours[0]:02d}-{plan_hours[-1]:02d}.",
            )
        target = float(target_p95_minutes)
        installed = ctx.installed
        by_id = {m[0]: m for m in installed}
        demand = [r["predicted_pax"] for r in demand_rows]

        if forecast_source is ForecastSource.SEED_REPLAY:
            baseline = []
            for h in ctx.target_hours:
                active = [
                    by_id.get(
                        mid,
                        (
                            mid,
                            mt,
                            ctx.rules[(mt, ctx.screening)][
                                "assumed_capacity_pax_per_hour"
                            ],
                        ),
                    )
                    for mid, mt in h.modsets
                ]
                baseline.append(sorted(active, key=lambda m: (-m[2], m[0])))
            baseline_label = "observed active modsets on the seed day"
        else:
            req = required_plan(demand, installed, ctx.params, target)
            baseline = [installed[: p["n"]] for p in req]
            baseline_label = "minimum plan from staffing_required_for_forecast"

        delta_by_hour = {c.hour: c.delta_modsets for c in changes}
        after_plan = []
        meta = []
        for hr, base in zip(plan_hours, baseline):
            delta = delta_by_hour.get(hr, 0)
            requested = len(base) + delta
            binding = []
            if requested < 1:
                binding.append("minimum_one_modset")
            if requested > len(installed):
                binding.append("installed_limit")
            new = adjust_modsets(base, installed, delta) if delta else list(base)
            after_plan.append(new)
            meta.append({"delta": delta, "requested": requested, "binding": binding})

        cap_before = [plan_capacity(p, ctx.params) for p in baseline]
        cap_after = [plan_capacity(p, ctx.params) for p in after_plan]
        sim_before = simulate(demand, cap_before, ctx.params)
        sim_after = simulate(demand, cap_after, ctx.params)

        hours = []
        for hr, b, a, sb, sa, m in zip(
            plan_hours, baseline, after_plan, sim_before, sim_after, meta
        ):
            staff_b = ctx.staff(b)
            staff_a = ctx.staff(a)
            binding = list(m["binding"])
            if available_staff is not None and staff_a["total_staff"] > available_staff:
                binding.append("available_staff")
            change = sa["p95"] - sb["p95"]
            hours.append(
                {
                    "hour": hr,
                    "demand_pax": round(sb["demand"], 1),
                    "modsets_before": len(b),
                    "requested_delta": m["delta"],
                    "applied_delta": len(a) - len(b),
                    "modsets_after": len(a),
                    "modset_ids_after": [x[0] for x in a],
                    "modeled_p95_before_minutes": round(sb["p95"], 1),
                    "p95_band_before_minutes": band(sb),
                    "modeled_p95_after_minutes": round(sa["p95"], 1),
                    "p95_band_after_minutes": band(sa),
                    "modeled_p95_change_minutes": round(change, 1),
                    "modeled_backlog_before_pax": round(sb["backlog"], 1),
                    "modeled_backlog_after_pax": round(sa["backlog"], 1),
                    "meets_target_before": sb["p95"] <= target,
                    "meets_target_after": sa["p95"] <= target,
                    "staff_before": staff_b["total_staff"],
                    "staff_after": staff_a["total_staff"],
                    "feasible": not binding,
                    "binding_constraints": binding,
                    "changed_by_carryover_only": m["delta"] == 0
                    and abs(change) >= 0.05,
                }
            )

        def _sum(key: str) -> float:
            return sum(h[key] for h in hours)

        totals = {
            "hours": len(hours),
            "modset_hours_before": int(_sum("modsets_before")),
            "modset_hours_after": int(_sum("modsets_after")),
            "staff_hours_before": int(_sum("staff_before")),
            "staff_hours_after": int(_sum("staff_after")),
            "hours_above_target_before": sum(
                not h["meets_target_before"] for h in hours
            ),
            "hours_above_target_after": sum(not h["meets_target_after"] for h in hours),
            "max_modeled_p95_before_minutes": max(
                h["modeled_p95_before_minutes"] for h in hours
            ),
            "max_modeled_p95_after_minutes": max(
                h["modeled_p95_after_minutes"] for h in hours
            ),
            "sum_modeled_p95_change_minutes": round(
                _sum("modeled_p95_change_minutes"), 1
            ),
            "infeasible_hours": [h["hour"] for h in hours if not h["feasible"]],
            "hours_changed_by_carryover_only": [
                h["hour"] for h in hours if h["changed_by_carryover_only"]
            ],
        }
        warnings = list(ctx.warnings)
        if totals["infeasible_hours"]:
            warnings.append(
                f"{len(totals['infeasible_hours'])} hour(s) hit a constraint; modset "
                "changes were clipped to 1..installed and staff ceilings are "
                "reported, not enforced."
            )
        filters = {
            "airport_code": ctx.airport,
            "checkpoint_id": ctx.checkpoint,
            "screening_type": ctx.screening,
            "date": ctx.target_date,
            "target_p95_minutes": target_p95_minutes,
            "forecast_source": forecast_source.value,
            "available_staff": available_staff,
        }
        result = envelope(
            {
                "baseline": baseline_label,
                "hours": hours,
                "totals": totals,
                "carryover_note": "Backlog carries between hours: a change at hour "
                "h changes the modeled backlog and wait at later hours too "
                "(see hours_changed_by_carryover_only).",
                "model": ctx.model_block(),
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
    lines = [
        f"# Modeled scenario — {f['airport_code']} {f['checkpoint_id']} "
        f"{f['screening_type']}, {f['date']} (synthetic)",
        "",
        f"Baseline: {result['baseline']}. Target p95 <= {f['target_p95_minutes']} "
        "min. Simulation estimates only.",
        "",
        "| hour | modsets before→after | p95 before | p95 after | modeled change | "
        "staff before→after | feasible |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for h in result["hours"]:
        feas = "yes" if h["feasible"] else ", ".join(h["binding_constraints"])
        lines.append(
            f"| {h['hour']:02d} | {h['modsets_before']}→{h['modsets_after']} | "
            f"{h['modeled_p95_before_minutes']} | {h['modeled_p95_after_minutes']} | "
            f"{h['modeled_p95_change_minutes']:+} | "
            f"{h['staff_before']}→{h['staff_after']} | {feas} |"
        )
    t = result["totals"]
    lines += [
        "",
        f"Hours above target: {t['hours_above_target_before']} → "
        f"{t['hours_above_target_after']}. Staff-hours: {t['staff_hours_before']} → "
        f"{t['staff_hours_after']}.",
        result["carryover_note"],
        "",
    ]
    lines += [f"- {w}" for w in result["warnings"]]
    lines.append(
        "_Synthetic data; a modeled wait change is not evidence of a causal "
        "staffing benefit._"
    )
    return "\n".join(lines)
