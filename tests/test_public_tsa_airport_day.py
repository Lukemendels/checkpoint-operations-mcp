from __future__ import annotations

import pytest
from fastmcp import Client

from example_server.app import mcp


@pytest.mark.asyncio
async def test_public_lookup_and_missing_are_separate_from_synthetic():
    async with Client(mcp) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert {
            "public_tsa_airport_day_throughput",
            "pims_get_dataset_coverage",
        } <= tools
        observed = await client.call_tool(
            "public_tsa_airport_day_throughput", {"airport": "IAD", "day": "2018-12-30"}
        )
        missing = await client.call_tool(
            "public_tsa_airport_day_throughput", {"airport": "IAD", "day": "2026-11-19"}
        )
    assert observed.data["data_classification"] == "public_origin_historical_tsa_report"
    assert observed.data["reported_screened"] == 27_089
    assert observed.data["checkpoint_hour_rows"] == 83
    assert missing.data["status"] == "no_observation"
    assert missing.data["reported_screened"] is None
