"""pims_get_airport_day_summary — daily airport totals beside national days."""

from __future__ import annotations

from fastmcp import FastMCP

from example_server.pims import repository
from example_server.pims.tooling import (
    READ_ONLY,
    Airport,
    FromDate,
    Limit,
    ToDate,
    safe_call,
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="pims_get_airport_day_summary",
        annotations={"title": "Get airport daily summary", **READ_ONLY},
    )
    def pims_get_airport_day_summary(
        from_date: FromDate,
        to_date: ToDate,
        airport_code: Airport = None,
        limit: Limit = 100,
    ) -> dict:
        """Daily synthetic passenger totals per airport, with the national day beside.

        The airports are a six-airport synthetic sample; do not treat their sum
        as a share of the national series.
        """
        return safe_call(
            repository.airport_day_summary, from_date, to_date, airport_code, limit
        )
