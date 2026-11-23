"""pims_get_checkpoint_hourly_summary — correct-grain throughput joined to waits."""

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
        name="pims_get_checkpoint_hourly_summary",
        annotations={"title": "Get checkpoint hourly summary", **READ_ONLY},
    )
    def pims_get_checkpoint_hourly_summary(
        from_date: FromDate,
        to_date: ToDate,
        airport_code: Airport = None,
        checkpoint_id: Checkpoint = None,
        screening_type: Screening = None,
        hour: Hour = None,
        limit: Limit = 100,
    ) -> dict:
        """One row per checkpoint / hour / screening type: passengers, modsets, waits.

        Throughput is summed over modsets first, active modsets are counted (not
        summed), then joined to wait times on the exact five keys. Prefer this
        tool for any checkpoint-level question.
        """
        return safe_call(
            repository.checkpoint_summary,
            from_date,
            to_date,
            airport_code,
            checkpoint_id,
            screening_type,
            hour,
            limit,
        )
