"""staffing_find_historical_gaps — hours above a p95 threshold, by deployment.

Descriptive only: for each synthetic checkpoint-hour whose observed p95 wait
exceeded the threshold, report whether some installed modsets were idle
("under_deployed": active < installed) or every installed modset was active
("at_capacity"). It does not show that opening idle modsets would have changed
the wait.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from example_server.models import AirportCode, DateRange, ResponseFormat, ScreeningType
from example_server.staffing.common import (
    checkpoint_error,
    describe_filters,
    envelope,
    error,
    known_checkpoints,
    unavailable,
)
from example_server.staffing.model import filter_rows
from example_server.staffing.provider import DataUnavailableError, get_provider

ASSUMPTIONS = [
    "A gap hour is a checkpoint-hour-screening_type whose observed p95_wait_minutes "
    "is strictly greater than the threshold (the seed's exceeded_* flags use "
    "max_wait_minutes instead, so counts differ from those flags).",
    "Active modsets = distinct modset_ids in Table 1 for that checkpoint-hour and "
    "screening type.",
    "Installed modsets = distinct modset_ids observed for the checkpoint and "
    "screening type on any date in the synthetic seed.",
    "under_deployed: active < installed. at_capacity: active == installed. "
    "no_throughput_rows: the wait row has no Table 1 partner (missing, not zero).",
    "Percentages are over hours, not passengers.",
]


def register(mcp: FastMCP) -> None:
    """Register the historical gaps tool with the MCP server."""

    @mcp.tool(
        name="staffing_find_historical_gaps",
        annotations={
            "title": "Find hours above a p95 threshold by deployment",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def staffing_find_historical_gaps(
        airport_code: Annotated[AirportCode, "IATA code of the airport."],
        date_range: Annotated[DateRange, "Inclusive date range (max 90 days)."],
        threshold_minutes: Annotated[
            Literal[15, 30], "p95 wait threshold in minutes: 15 or 30."
        ],
        checkpoint_id: Annotated[
            str | None, "Checkpoint id as configured for the airport; omit for all."
        ] = None,
        screening_type: Annotated[
            ScreeningType | None, "Standard or PreCheck; omit for both."
        ] = None,
        limit: Annotated[
            int,
            Field(ge=1, le=200, description="Maximum top hours to list (1-200)."),
        ] = 50,
        response_format: Annotated[
            ResponseFormat,
            "Return machine-readable JSON (default) or human-readable Markdown.",
        ] = ResponseFormat.JSON,
    ) -> dict[str, Any] | str:
        """List synthetic hours whose observed p95 wait exceeded a threshold.

        Each gap hour is classified as under_deployed (some installed modsets
        idle) or at_capacity (all installed modsets active). Returns counts per
        checkpoint and screening type and the top hours by p95. Descriptive
        only: an under_deployed hour does not show that opening more modsets
        would have lowered the wait (use staffing_simulate_scenario for a
        modeled comparison).
        """
        try:
            provider = get_provider()
            config = provider.checkpoint_config()
            throughput = provider.throughput()
            waits = provider.wait_times()
        except DataUnavailableError as exc:
            return unavailable(exc)

        airport = airport_code.value
        if checkpoint_id and checkpoint_id not in known_checkpoints(config, airport):
            return checkpoint_error(config, airport, checkpoint_id)
        screening = screening_type.value if screening_type else None
        sel = {
            "airport_code": airport,
            "checkpoint_id": checkpoint_id,
            "screening_type": screening,
        }
        w_rows = filter_rows(waits, **sel, start=date_range.start, end=date_range.end)
        if not w_rows:
            return error(
                "no wait-time rows match the filters.",
                "Call pims_get_dataset_coverage for the dates the synthetic seed "
                "covers, or drop a filter.",
            )

        installed: dict[tuple[str, str], set[str]] = defaultdict(set)
        active: dict[tuple[str, int, str, str], set[str]] = defaultdict(set)
        lo, hi = date_range.start.isoformat(), date_range.end.isoformat()
        for r in filter_rows(throughput, **sel):
            installed[(r["checkpoint_id"], r["screening_type"])].add(r["modset_id"])
            if lo <= r["date"] <= hi:
                key = (r["date"], r["hour"], r["checkpoint_id"], r["screening_type"])
                active[key].add(r["modset_id"])

        counts: dict[tuple[str, str], dict[str, int]] = {}
        gaps = []
        for w in w_rows:
            group = (w["checkpoint_id"], w["screening_type"])
            c = counts.setdefault(
                group,
                {
                    "hours_considered": 0,
                    "hours_above_threshold": 0,
                    "under_deployed": 0,
                    "at_capacity": 0,
                    "no_throughput_rows": 0,
                },
            )
            c["hours_considered"] += 1
            if not w["p95_wait_minutes"] > threshold_minutes:
                continue
            c["hours_above_threshold"] += 1
            key = (w["date"], w["hour"], w["checkpoint_id"], w["screening_type"])
            n_inst = len(installed.get(group, ()))
            n_act = len(active.get(key, ()))
            if n_act == 0:
                cls = "no_throughput_rows"
            elif n_act < n_inst:
                cls = "under_deployed"
            else:
                cls = "at_capacity"
            c[cls] += 1
            gaps.append(
                {
                    "date": w["date"],
                    "hour": w["hour"],
                    "checkpoint_id": w["checkpoint_id"],
                    "screening_type": w["screening_type"],
                    "p95_wait_minutes": w["p95_wait_minutes"],
                    "avg_wait_minutes": w["avg_wait_minutes"],
                    "active_modsets": n_act,
                    "installed_modsets": n_inst,
                    "idle_installed_modsets": max(0, n_inst - n_act),
                    "classification": cls,
                }
            )

        by_group = []
        for (cp, st), c in sorted(counts.items()):
            above = c["hours_above_threshold"]
            by_group.append(
                {
                    "checkpoint_id": cp,
                    "screening_type": st,
                    "installed_modsets": len(installed.get((cp, st), ())),
                    **c,
                    "share_above_threshold": round(above / c["hours_considered"], 3),
                    "share_under_deployed_of_above": (
                        round(c["under_deployed"] / above, 3) if above else None
                    ),
                }
            )
        gaps.sort(
            key=lambda g: (
                -g["p95_wait_minutes"],
                g["date"],
                g["hour"],
                g["checkpoint_id"],
                g["screening_type"],
            )
        )
        top = gaps[:limit]
        totals = {
            key: sum(g[key] for g in by_group)
            for key in (
                "hours_considered",
                "hours_above_threshold",
                "under_deployed",
                "at_capacity",
                "no_throughput_rows",
            )
        }
        warnings = []
        if totals["no_throughput_rows"]:
            warnings.append(
                f"{totals['no_throughput_rows']} gap hours had no Table 1 rows and "
                "could not be classified."
            )
        if len(gaps) > limit:
            warnings.append(
                f"top_hours truncated to {limit} of {len(gaps)}; raise limit (max 200) "
                "or narrow the filters."
            )
        filters = describe_filters(
            airport_code=airport,
            checkpoint_id=checkpoint_id,
            screening_type=screening,
            date_range=date_range,
        )
        filters["threshold_minutes"] = threshold_minutes
        result = envelope(
            {
                "totals": totals,
                "by_checkpoint_screening": by_group,
                "top_hours": top,
                "returned": len(top),
                "has_more": len(gaps) > limit,
                "interpretation": "under_deployed hours had idle installed modsets "
                "while p95 exceeded the threshold; this is a descriptive pattern, "
                "not evidence that more modsets would have changed the wait.",
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
    dr = f["date_range"]
    lines = [
        f"# Hours with p95 > {f['threshold_minutes']} min — {f['airport_code']}, "
        f"{dr['start']}..{dr['end']} (synthetic)",
        "",
        "| checkpoint | screening | installed | hours | above | under-deployed | "
        "at capacity |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for g in result["by_checkpoint_screening"]:
        lines.append(
            f"| {g['checkpoint_id']} | {g['screening_type']} | "
            f"{g['installed_modsets']} | {g['hours_considered']} | "
            f"{g['hours_above_threshold']} | "
            f"{g['under_deployed']} | {g['at_capacity']} |"
        )
    lines += [
        "",
        "| date | hour | checkpoint | screening | p95 | active/installed | class |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for h in result["top_hours"]:
        lines.append(
            f"| {h['date']} | {h['hour']:02d} | {h['checkpoint_id']} | "
            f"{h['screening_type']} | {h['p95_wait_minutes']} | "
            f"{h['active_modsets']}/{h['installed_modsets']} | {h['classification']} |"
        )
    lines.append("")
    lines += [f"- {w}" for w in result["warnings"]]
    lines.append(f"_{result['interpretation']}_")
    return "\n".join(lines)
