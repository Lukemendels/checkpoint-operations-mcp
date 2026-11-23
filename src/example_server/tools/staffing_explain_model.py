"""staffing_explain_model — what the staffing scenario model is and is not.

Read this tool's output before interpreting any other staffing_* result. It
states the model steps, how the synthetic tables are read, the assumed
constants, and the limits, so an SI agent never guesses field meanings.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from example_server.models import ResponseFormat
from example_server.staffing.common import STANDARD_LIMITS, envelope, unavailable
from example_server.staffing.planning import backtest_table
from example_server.staffing.provider import DataUnavailableError, get_provider

MODEL_STEPS = [
    {
        "step": 1,
        "name": "capacity per modset",
        "tool": "staffing_estimate_modset_capacity",
        "how": "Nominal capacity per modset-hour comes from staffing_rules (WTMD 94, "
        "AIT 76 pax/h on the seed); the tool reports the observed p90 per active "
        "modset-hour beside it as a lower-bound check.",
    },
    {
        "step": 2,
        "name": "same-hour wait response (descriptive comparison)",
        "tool": "staffing_fit_wait_response",
        "how": "p95 wait = f(u), u = hour passengers / hour capacity, two-segment "
        "line with a least-squares knee. On the seed it explains little (r2 "
        "0.21-0.26 at ATL, ORD and LAX) because waits depend on backlog carried from "
        "earlier hours; it is kept as the baseline the queue model is scored "
        "against.",
    },
    {
        "step": 3,
        "name": "backlog and demand reconstruction",
        "tool": "(internal) staffing/queue.py",
        "how": "Effective capacity = capacity_factor x nominal (factor = median "
        "served/nominal over saturated hours). Backlog at the end of each hour by "
        "Little's law: (avg wait - base wait) / 60 x capacity, with base wait the "
        "median avg wait in low-utilization hours. Demand = served + backlog - "
        "previous backlog, clipped at 0.",
    },
    {
        "step": 4,
        "name": "simulate a capacity plan",
        "tool": "staffing_required_for_forecast, staffing_simulate_scenario",
        "how": "served = min(backlog + demand, capacity); backlog carries to the next "
        "hour; avg wait = base + 60 x backlog / capacity; p95 = slope x avg + "
        "intercept (fitted on hours below the seed's reporting ceiling); band = "
        "10th-90th percentile of relative replay residuals.",
    },
    {
        "step": 5,
        "name": "required modsets and staff",
        "tool": "staffing_required_for_forecast",
        "how": "hour by hour, the smallest n (highest-capacity installed modsets "
        "first) with modeled p95 <= target; conservative n uses the band upper "
        "bound and interval_high. Staff = sum staff_per_modset x relief_factor "
        "(rounded up) + fixed posts once per checkpoint-hour.",
    },
    {
        "step": 6,
        "name": "historical gaps",
        "tool": "staffing_find_historical_gaps",
        "how": "descriptive: observed hours with p95 above 15 or 30 min, split into "
        "under_deployed (active < installed) and at_capacity.",
    },
]

BACKTEST_NOTES = [
    "replay: observed modsets and demand reconstructed from the same day's waits. "
    "Close to circular, so it is an upper bound on accuracy.",
    "profile: observed modsets and demand = mean reconstructed demand at that hour "
    "on other dates (same weekday when possible). Out of sample for demand.",
    "same_hour: the step-2 fit, fitted and scored in sample on the same hours.",
    "Hours at the seed's reporting ceiling (p95 180 min) are scored as censored.",
    "The seed generator scales arrivals with the hour's active capacity, so the "
    "seed cannot show demand exceeding a reduced deployment the way a real "
    "outage would; the profile test penalizes the queue model for that.",
    "The wait coefficient is fixed at Little's law (60 min per hour of backlog). "
    "On the seed a profile-backtest grid is lowest at about 10-20, below the "
    "generator's own coefficient (28), consistent with the arrivals-scale-with-"
    "capacity artifact above; at 60 the modeled wait response to modset changes "
    "is likely overstated relative to the seed generator.",
]


SCHEMA_INTERPRETATION = {
    "throughput (Table 1)": "one row per ACTIVE modset-hour at airport/checkpoint/"
    "modset/hour grain; passenger_throughput is that modset's hourly count; "
    "active_modsets is the count for the checkpoint-hour-screening_type.",
    "wait_times (Table 2)": "one row per checkpoint-hour-screening_type; avg/p95/max "
    "wait minutes; exceeded flags are p95 > 15 and > 30 in the fixture.",
    "national_daily_baseline (Table 3)": "national daily totals used only to anchor "
    "the synthetic cube; not used by the staffing tools.",
    "checkpoint_config": "staffing-owned: installed modsets by machine type, fixed "
    "posts, open/close hours. Tools 4-6 take fixed posts and hours from here but "
    "count installed modsets as the distinct modset_ids observed in Table 1 for "
    "the checkpoint and screening type, because the configured counts do not "
    "match the seed.",
    "staffing_rules": "staffing-owned: assumed capacity per modset-hour, staff per "
    "modset, relief factor.",
}


def register(mcp: FastMCP) -> None:
    """Register the explain tool with the MCP server."""

    @mcp.tool(
        name="staffing_explain_model",
        annotations={
            "title": "Explain the staffing scenario model",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def staffing_explain_model(
        response_format: Annotated[
            ResponseFormat,
            "Return machine-readable JSON (default) or human-readable Markdown.",
        ] = ResponseFormat.JSON,
    ) -> dict[str, Any] | str:
        """Explain the staffing scenario model, its data, constants and limits.

        Data: the synthetic PIMS seed (or the CSV fixture when
        STAFFING_SOURCE=csv_fixture); no live source. Returns the model steps
        (same-hour fit and the hour-by-hour queue model), how each table is
        interpreted, the configured constants, the limits that apply to every
        staffing_* result, and a backtest of the queue model against the
        same-hour fit for every checkpoint and screening type. Call this before
        narrating any staffing number.
        """
        try:
            provider = get_provider()
            rules = provider.staffing_rules()
            config = provider.checkpoint_config()
            table = backtest_table(provider)
        except DataUnavailableError as exc:
            return unavailable(exc)

        payload = {
            "purpose": "Scenario planner for a checkpoint supervisor: compare "
            "hypothetical staffing choices against predicted demand and waits. "
            "It is a client of the wait-time prediction, not a second forecaster, "
            "and it never writes a roster.",
            "model_steps": MODEL_STEPS,
            "schema_interpretation": SCHEMA_INTERPRETATION,
            "backtest": {
                "calibration": "in sample: each checkpoint/screening type "
                "calibrated and scored on all its dates (tools 4-5 exclude the "
                "target date).",
                "notes": BACKTEST_NOTES,
                "summary": table["summary"],
                "rows": table["rows"],
            },
            "constants": {
                "staffing_rules": rules,
                "checkpoints": [
                    {
                        "airport_code": c["airport_code"],
                        "checkpoint_id": c["checkpoint_id"],
                        "installed_modsets_wtmd": c["installed_modsets_wtmd"],
                        "installed_modsets_ait": c["installed_modsets_ait"],
                        "fixed_posts": c["fixed_posts"],
                        "hours": f"{c['open_hour']:02d}-{c['close_hour']:02d}",
                    }
                    for c in config
                ],
            },
        }
        result = envelope(payload)

        if response_format is ResponseFormat.MARKDOWN:
            lines = [
                "# Staffing scenario model (synthetic)",
                "",
                payload["purpose"],
                "",
            ]
            lines.append("## Steps")
            for s in MODEL_STEPS:
                lines.append(
                    f"{s['step']}. **{s['name']}** — {s['how']} (`{s['tool']}`)"
                )
            lines += ["", "## How the tables are read"]
            lines += [f"- **{k}**: {v}" for k, v in SCHEMA_INTERPRETATION.items()]
            summ = table["summary"]
            lines += [
                "",
                "## Backtest: queue model vs same-hour fit (p95 MAE, minutes)",
                "",
                f"Pooled over {summ['groups']} checkpoint/screening groups: replay "
                f"{summ['pooled_replay_mae']}, profile {summ['pooled_profile_mae']}, "
                f"same-hour fit {summ['pooled_same_hour_mae']}. Profile beats the "
                f"same-hour fit in {summ['groups_where_profile_beats_same_hour']} of "
                f"{summ['groups']} groups.",
                "",
                f"**Verdict:** {summ['verdict']}",
                "",
                "| airport | checkpoint | screening | n | replay | profile | "
                "same-hour |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for r in table["rows"]:
                lines.append(
                    f"| {r['airport_code']} | {r['checkpoint_id']} | "
                    f"{r['screening_type']} | {r['n_hours']} | {r['replay_mae']} | "
                    f"{r['profile_mae']} | {r['same_hour_mae']} |"
                )
            lines.append("")
            lines += [f"- {n}" for n in BACKTEST_NOTES]
            lines += ["", "## Assumed constants (synthetic)"]
            for r in rules:
                lines.append(
                    f"- {r['machine_type']} / {r['screening_type']}: "
                    f"{r['assumed_capacity_pax_per_hour']:.0f} pax per modset-hour, "
                    f"{r['staff_per_modset']} staff per modset, "
                    f"relief x{r['relief_factor']}"
                )
            lines += ["", "## Limits"]
            lines += [f"- {limit}" for limit in STANDARD_LIMITS]
            return "\n".join(lines)
        return result
