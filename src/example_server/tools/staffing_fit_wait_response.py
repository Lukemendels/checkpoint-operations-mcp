"""staffing_fit_wait_response — step 3 of the staffing model.

Fits p95 wait (Table 2) against utilization computed from Table 1 summed to
checkpoint-hour grain. Refuses if the join is not 1:1.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from fastmcp import FastMCP

from example_server.models import (
    AirportCode,
    DateRange,
    ResponseFormat,
    ScreeningType,
)
from example_server.staffing.common import (
    apply_filters,
    checkpoint_error,
    describe_filters,
    envelope,
    error,
    known_checkpoints,
    unavailable,
)
from example_server.staffing.model import (
    CardinalityError,
    capacity_lookup_from_estimate,
    capacity_lookup_from_rules,
    estimate_modset_capacity,
    fit_wait_response,
)
from example_server.staffing.provider import DataUnavailableError, get_provider


class CapacitySource(str, Enum):
    """Where the per-modset capacity used for utilization comes from."""

    ESTIMATED = "estimated"  # p90 proxies from staffing_estimate_modset_capacity
    RULES = "rules"  # assumed_capacity_pax_per_hour from staffing_rules.csv


ASSUMPTIONS = [
    "Table 1 is summed to checkpoint-hour-screening_type before joining Table 2; "
    "the join must be 1:1 or the tool refuses.",
    "Utilization u = hour passengers / sum of per-modset capacity over the "
    "active modset rows in that hour.",
    "Fit is a two-segment line in u with the knee chosen by least squares over "
    "candidates 0.60-0.95; each segment needs at least 5 points.",
    "Residual standard deviation is the uncertainty band for later predictions.",
]


def register(mcp: FastMCP) -> None:
    """Register the wait-response fit tool with the MCP server."""

    @mcp.tool(
        name="staffing_fit_wait_response",
        annotations={
            "title": "Fit p95 wait against utilization",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def staffing_fit_wait_response(
        airport_code: Annotated[AirportCode, "IATA code of the airport."],
        checkpoint_id: Annotated[
            str | None, "Checkpoint id as configured for the airport; omit to pool."
        ] = None,
        screening_type: Annotated[
            ScreeningType | None, "Standard or PreCheck; omit to pool both."
        ] = None,
        date_range: Annotated[
            DateRange | None, "Inclusive date range (max 90 days); omit for all dates."
        ] = None,
        capacity_source: Annotated[
            CapacitySource,
            "'rules' (assumed capacities from staffing_rules, default) or "
            "'estimated' (p90 proxies from the same rows; a lower bound when "
            "staffing holds utilization below 1).",
        ] = CapacitySource.RULES,
        response_format: Annotated[
            ResponseFormat,
            "Return machine-readable JSON (default) or human-readable Markdown.",
        ] = ResponseFormat.JSON,
    ) -> dict[str, Any] | str:
        """Fit the wait-response curve p95_wait = f(utilization) (synthetic).

        Returns join diagnostics, binned medians of p95 by utilization, and a
        two-segment linear fit with knee, slopes, r2 and residual sd, plus the
        capacity lookup used. Hours with zero or unknown capacity are skipped
        and counted. Data: the synthetic fixture only. This is step 3 of the
        model explained by `staffing_explain_model`.
        """
        try:
            provider = get_provider()
            config = provider.checkpoint_config()
            throughput = provider.throughput()
            waits = provider.wait_times()
            rules = provider.staffing_rules()
        except DataUnavailableError as exc:
            return unavailable(exc)

        airport = airport_code.value
        if checkpoint_id and checkpoint_id not in known_checkpoints(config, airport):
            return checkpoint_error(config, airport, checkpoint_id)

        screening = screening_type.value if screening_type else None
        common = {
            "airport_code": airport,
            "checkpoint_id": checkpoint_id,
            "screening_type": screening,
            "date_range": date_range,
        }
        t_rows = apply_filters(throughput, **common)
        w_rows = apply_filters(waits, **common)
        filters = describe_filters(**common)
        if not t_rows or not w_rows:
            return error(
                "no throughput or wait-time rows match the filters.",
                "Widen the date range or drop a filter; call "
                "pims_get_dataset_coverage for the dates the data covers.",
            )

        if capacity_source is CapacitySource.RULES:
            capacity = capacity_lookup_from_rules(rules)
        else:
            estimate = estimate_modset_capacity(
                t_rows, group_by=("machine_type", "screening_type")
            )
            capacity = capacity_lookup_from_estimate(estimate)

        try:
            fit = fit_wait_response(t_rows, w_rows, capacity)
        except CardinalityError as exc:
            return error(
                str(exc),
                "Deduplicate wait_times at checkpoint-hour-screening grain before "
                "fitting; the tool will not aggregate ambiguous waits.",
            )

        warnings = []
        if fit["n_points"] == 0:
            warnings.append("no joined hours with positive capacity; nothing to fit.")
        elif fit["fit"]["knee_utilization"] is None:
            warnings.append(
                "too few points on one side of every candidate knee; a single line "
                "was fitted instead of two segments."
            )
        if (
            fit["join"]["unmatched_throughput_hours"]
            or fit["join"]["unmatched_wait_hours"]
        ):
            warnings.append(
                f"{fit['join']['unmatched_throughput_hours']} throughput hours and "
                f"{fit['join']['unmatched_wait_hours']} wait hours had no partner row "
                "and were left out (missing is not zero)."
            )
        result = envelope(
            {
                "capacity_source": capacity_source.value,
                "capacity_lookup": {
                    f"{m}/{s}": round(v, 1) for (m, s), v in sorted(capacity.items())
                },
                **fit,
            },
            assumptions=ASSUMPTIONS,
            warnings=warnings,
            filters=filters,
        )

        if response_format is ResponseFormat.MARKDOWN:
            lines = [f"# Wait response — {airport} (synthetic)", ""]
            lines.append(
                f"Joined hours: {fit['join']['matched_hours']} "
                f"(fit on {fit['n_points']}); capacity source: {capacity_source.value}."
            )
            if fit["fit"]:
                f = fit["fit"]
                lines.append("")
                lines.append(f"Knee at u = {f['knee_utilization']}; r2 = {f['r2']}; ")
                lines.append(f"residual sd = {f['residual_sd_minutes']} min.")
                lines.append(
                    f"Below knee: p95 = {f['below_knee']['intercept']} + "
                    f"{f['below_knee']['slope']}·u"
                )
                if f["above_knee"]:
                    lines.append(
                        f"Above knee: p95 = {f['above_knee']['intercept']} + "
                        f"{f['above_knee']['slope']}·u"
                    )
            lines += [
                "",
                "| u bin | n | median p95 | q25–q75 |",
                "| --- | --- | --- | --- |",
            ]
            for b in fit["bins"]:
                lines.append(
                    f"| {b['u_low']}–{b['u_high']} | {b['n']} | "
                    f"{b['median_p95_wait']} | {b['q25']}–{b['q75']} |"
                )
            lines.append("")
            lines += [f"- {w}" for w in warnings]
            lines.append("_Synthetic data; a fitted curve is not a causal claim._")
            return "\n".join(lines)
        return result
