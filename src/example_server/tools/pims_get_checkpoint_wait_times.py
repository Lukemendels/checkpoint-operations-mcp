"""pims_get_checkpoint_wait_times — checkpoint-hour wait metrics."""

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
    Screening,
    ToDate,
    safe_call,
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="pims_get_checkpoint_wait_times",
        annotations={"title": "Get checkpoint wait times", **READ_ONLY},
    )
    def pims_get_checkpoint_wait_times(
        from_date: FromDate,
        to_date: ToDate,
        airport_code: Airport = None,
        checkpoint_id: Checkpoint = None,
        screening_type: Screening = None,
        hour: Hour = None,
        limit: Limit = 100,
    ) -> dict:
        """Synthetic avg / p95 / max wait minutes per checkpoint, hour, screening type.

        Flags: exceeded_15m_flag / exceeded_30m_flag mean max_wait_minutes is
        strictly above 15 / 30. Wait times have no modset breakdown.
        """
        return safe_call(
            repository.wait_times,
            from_date,
            to_date,
            airport_code,
            checkpoint_id,
            screening_type,
            hour,
            limit,
        )
