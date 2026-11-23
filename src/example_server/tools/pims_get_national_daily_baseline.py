"""pims_get_national_daily_baseline — national daily totals (synthetic)."""

from __future__ import annotations

from fastmcp import FastMCP

from example_server.pims import repository
from example_server.pims.tooling import READ_ONLY, FromDate, Limit, ToDate, safe_call


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="pims_get_national_daily_baseline",
        annotations={"title": "Get national daily baseline", **READ_ONLY},
    )
    def pims_get_national_daily_baseline(
        from_date: FromDate, to_date: ToDate, limit: Limit = 100
    ) -> dict:
        """National daily passenger counts and the prior-year same-weekday count.

        In the bundled seed these are synthetic (source_kind 'synthetic_demo'),
        not TSA figures. The six sample airports do not add up to this total.
        """
        return safe_call(repository.national_baseline, from_date, to_date, limit)
