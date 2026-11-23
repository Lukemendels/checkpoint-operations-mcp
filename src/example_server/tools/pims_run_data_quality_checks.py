"""pims_run_data_quality_checks — structural checks on the seed."""

from __future__ import annotations

from fastmcp import FastMCP

from example_server.pims import repository
from example_server.pims.tooling import READ_ONLY, safe_call


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="pims_run_data_quality_checks",
        annotations={"title": "Run data quality checks", **READ_ONLY},
    )
    def pims_run_data_quality_checks() -> dict:
        """Check modset counts, join coverage, national dates, flags, SQLite integrity.

        Structural checks only: passing does not validate the synthetic values.
        """
        return safe_call(repository.quality_checks)
