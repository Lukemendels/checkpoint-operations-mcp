"""pims_get_customer_throughput — modset-hour passenger counts."""

from __future__ import annotations

from fastmcp import FastMCP

from example_server.pims import repository
from example_server.pims.tooling import (
    READ_ONLY,
    Airport,
    Checkpoint,
    FromDate,
    Hour,
    Limit,
    Machine,
    Screening,
    ToDate,
    safe_call,
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="pims_get_customer_throughput",
        annotations={"title": "Get modset throughput rows", **READ_ONLY},
    )
    def pims_get_customer_throughput(
        from_date: FromDate,
        to_date: ToDate,
        airport_code: Airport = None,
        checkpoint_id: Checkpoint = None,
        screening_type: Screening = None,
        machine_type: Machine = None,
        hour: Hour = None,
        limit: Limit = 100,
    ) -> dict:
        """Raw synthetic rows: passengers per screening machine (modset) per hour.

        One row per active modset-hour. passenger_throughput is additive;
        active_modsets is NOT (it repeats per row). For checkpoint totals use
        pims_get_checkpoint_hourly_summary instead of summing these rows.
        Returns has_more when the limit truncated the result.
        """
        return safe_call(
            repository.customer_throughput,
            from_date,
            to_date,
            airport_code,
            checkpoint_id,
            screening_type,
            machine_type,
            hour,
            limit,
        )
