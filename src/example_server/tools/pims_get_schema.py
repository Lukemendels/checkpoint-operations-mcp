"""pims_get_schema — canonical table grains, fields and joining rules."""

from __future__ import annotations

from fastmcp import FastMCP

from example_server.pims import repository
from example_server.pims.tooling import READ_ONLY, safe_call


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="pims_get_schema", annotations={"title": "Get schema", **READ_ONLY})
    def pims_get_schema() -> dict:
        """Explain the three synthetic PIMS/PMIS tables: grains, keys, fields.

        Read this first. It states the rules an analyst must follow: modset
        throughput is per screening machine (not X-ray lane); wait times exist
        only at checkpoint-hour grain; active_modsets repeats on every modset
        row and must never be summed.
        """
        return safe_call(repository.schema)
