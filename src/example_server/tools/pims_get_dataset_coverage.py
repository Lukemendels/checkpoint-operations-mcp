"""pims_get_dataset_coverage — date ranges, row counts, airports, provenance."""

from __future__ import annotations

from fastmcp import FastMCP

from example_server.pims import repository
from example_server.pims.tooling import READ_ONLY, safe_call


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="pims_get_dataset_coverage",
        annotations={"title": "Get dataset coverage", **READ_ONLY},
    )
    def pims_get_dataset_coverage() -> dict:
        """Return the seed's date range, row counts, airports and checkpoints.

        Also returns provenance metadata (random seed, synthetic labels). Use it
        to find valid dates and checkpoint ids before calling other tools.
        """
        return safe_call(repository.coverage)
