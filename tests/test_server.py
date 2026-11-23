"""Smoke tests for the MCP server.

These verify the server imports and every submodule registers without error.
Add functional tests for your own tools as you build them.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from example_server.app import SERVER_NAME, mcp
from example_server.config import settings


def test_server_is_importable():
    assert mcp is not None


def test_server_has_name():
    assert mcp.name == SERVER_NAME


def test_default_transport_is_stdio():
    """Guard against accidentally shipping with a non-stdio default."""
    assert settings.mcp_transport == "stdio"


def test_tools_register_without_error():
    from example_server.tools import register_tools

    register_tools(FastMCP("test"))  # must not raise


def test_prompts_register_without_error():
    from example_server.prompts import register_prompts

    register_prompts(FastMCP("test"))  # must not raise


def test_resources_register_without_error():
    from example_server.resources import register_resources

    register_resources(FastMCP("test"))  # must not raise


def test_routes_register_without_error():
    from example_server.routes import register_routes

    register_routes(FastMCP("test"))  # must not raise


@pytest.mark.asyncio
async def test_pims_tools_are_discoverable():
    """All eight pims_* data tools are registered and discoverable."""
    tool_names = {t.name for t in await mcp.list_tools()}
    expected = {
        "pims_get_schema",
        "pims_get_dataset_coverage",
        "pims_get_customer_throughput",
        "pims_get_checkpoint_wait_times",
        "pims_get_national_daily_baseline",
        "pims_get_checkpoint_hourly_summary",
        "pims_get_airport_day_summary",
        "pims_run_data_quality_checks",
    }
    assert expected <= tool_names
    assert "example_search_datasets" not in tool_names
