"""staffing_estimate_modset_capacity — step 1 of the staffing model.

Observed passengers per active modset-hour from Table 1, summarized per
airport / checkpoint / machine type / screening type. The p90 is the capacity
proxy used downstream; the median is typical throughput.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from example_server.models import (
    AirportCode,
    DateRange,
    MachineType,
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
from example_server.staffing.model import estimate_modset_capacity
from example_server.staffing.provider import DataUnavailableError, get_provider

ASSUMPTIONS = [
    "Each Table 1 row is one active modset-hour; its passenger_throughput is the "
    "observation.",
    "Idle modset-hours (zero passengers) carry no capacity information and are "
    "excluded from the statistics but counted.",
    "p90 of per-modset-hour passengers is used as the capacity proxy because "
    "throughput in busy hours approaches capacity; it is still an observed "
    "throughput, not a rated machine capacity.",
]


def register(mcp: FastMCP) -> None:
    """Register the capacity estimate tool with the MCP server."""

    @mcp.tool(
        name="staffing_estimate_modset_capacity",
        annotations={
            "title": "Estimate passengers per modset-hour",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def staffing_estimate_modset_capacity(
        airport_code: Annotated[AirportCode, "IATA code of the airport."],
        checkpoint_id: Annotated[
            str | None, "Checkpoint id as configured for the airport; omit for all."
        ] = None,
        machine_type: Annotated[
            MachineType | None, "WTMD or AIT; omit for both."
        ] = None,
        screening_type: Annotated[
            ScreeningType | None, "Standard or PreCheck; omit for both."
        ] = None,
        date_range: Annotated[
            DateRange | None, "Inclusive date range (max 90 days); omit for all dates."
        ] = None,
        group_by_checkpoint: Annotated[
            bool, "True: one group per checkpoint; False: pool checkpoints."
        ] = True,
        response_format: Annotated[
            ResponseFormat,
            "Return machine-readable JSON (default) or human-readable Markdown.",
        ] = ResponseFormat.JSON,
    ) -> dict[str, Any] | str:
        """Summarize observed passengers per active modset-hour (synthetic Table 1).

        Returns, per group: n modset-hours, median, interquartile range, the p90
        capacity proxy and max, plus counts of excluded zero-denominator hours
        and hours where the number of modset rows disagrees with
        `active_modsets`. Data: the synthetic fixture only; coverage is stated
        in the response filters. This is step 1 of the model explained by
        `staffing_explain_model`.
        """
        try:
            provider = get_provider()
            config = provider.checkpoint_config()
            rows = provider.throughput()
        except DataUnavailableError as exc:
            return unavailable(exc)

        airport = airport_code.value
        if checkpoint_id and checkpoint_id not in known_checkpoints(config, airport):
            return checkpoint_error(config, airport, checkpoint_id)

        machine = machine_type.value if machine_type else None
        screening = screening_type.value if screening_type else None
        selected = apply_filters(
            rows,
            airport_code=airport,
            checkpoint_id=checkpoint_id,
            screening_type=screening,
            machine_type=machine,
            date_range=date_range,
        )
        filters = describe_filters(
            airport_code=airport,
            checkpoint_id=checkpoint_id,
            screening_type=screening,
            machine_type=machine,
            date_range=date_range,
        )
        if not selected:
            return error(
                "no throughput rows match the filters.",
                "Widen the date range or drop a filter; call "
                "pims_get_dataset_coverage for the dates the data covers.",
            )

        group_by = ("airport_code", "machine_type", "screening_type")
        if group_by_checkpoint:
            group_by = (
                "airport_code",
                "checkpoint_id",
                "machine_type",
                "screening_type",
            )
        estimate = estimate_modset_capacity(selected, group_by=group_by)

        warnings = []
        low = [g for g in estimate["groups"] if g["p90_to_max_ratio"] < 0.85]
        if low:
            warnings.append(
                f"{len(low)} group(s) have p90 well below their max (ratio < 0.85): "
                "staffing likely held utilization below capacity, so the p90 proxy "
                "is a lower bound. Use capacity_source='rules' downstream unless "
                "the proxy is near the max."
            )
        if estimate["active_modsets_mismatch_hours"]:
            warnings.append(
                f"{estimate['active_modsets_mismatch_hours']} checkpoint-hours have a "
                "modset row count that differs from active_modsets; the row-level "
                "interpretation may not match the source."
            )
        if estimate["excluded_zero_denominator_hours"]:
            warnings.append(
                f"{estimate['excluded_zero_denominator_hours']} checkpoint-hours with "
                "active_modsets = 0 were excluded (missing is not zero)."
            )
        result = envelope(
            {"rows_considered": len(selected), **estimate},
            assumptions=ASSUMPTIONS,
            warnings=warnings,
            filters=filters,
        )

        if response_format is ResponseFormat.MARKDOWN:
            lines = [f"# Passengers per modset-hour — {airport} (synthetic)", ""]
            lines.append(
                "| "
                + " | ".join(group_by)
                + " | n | median | q25–q75 | p90 proxy | max |"
            )
            lines.append("|" + " --- |" * (len(group_by) + 5))
            for g in estimate["groups"]:
                keys = " | ".join(str(g[k]) for k in group_by)
                lines.append(
                    f"| {keys} | {g['n_modset_hours']} | "
                    f"{g['median_pax_per_modset_hour']} | {g['q25']}–{g['q75']} | "
                    f"{g['capacity_proxy_p90']} | {g['max']} |"
                )
            lines.append("")
            lines += [f"- {w}" for w in warnings]
            lines.append(
                f"_Rows considered: {len(selected)}. Synthetic data; p90 is an "
                "observed-throughput proxy, not rated capacity._"
            )
            return "\n".join(lines)
        return result
