"""Tool-level tests: discovery, envelopes, errors, through the FastMCP API."""

from __future__ import annotations

from typing import Any

import pytest

from example_server.app import mcp

pytestmark = pytest.mark.usefixtures("csv_fixture_source")

STAFFING_TOOLS = {
    "staffing_explain_model",
    "staffing_estimate_modset_capacity",
    "staffing_fit_wait_response",
    "staffing_required_for_forecast",
    "staffing_simulate_scenario",
    "staffing_find_historical_gaps",
}


async def call(name: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(name, arguments)
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured
    return result.content[0].text


@pytest.mark.asyncio
async def test_staffing_tools_are_discoverable():
    names = {t.name for t in await mcp.list_tools()}
    assert STAFFING_TOOLS <= names


@pytest.mark.asyncio
async def test_explain_model_carries_labels_and_constants():
    out = await call("staffing_explain_model")
    assert out["data_label"] == "synthetic"
    assert len(out["model_steps"]) == 6
    assert "backtest" in out
    rules = {
        (r["machine_type"], r["screening_type"])
        for r in out["constants"]["staffing_rules"]
    }
    assert ("AIT", "Standard") in rules
    assert any("causal" in limit for limit in out["limits"])


@pytest.mark.asyncio
async def test_explain_model_markdown():
    text = await call("staffing_explain_model", response_format="markdown")
    assert isinstance(text, str)
    assert text.startswith("# Staffing scenario model")


@pytest.mark.asyncio
async def test_capacity_tool_groups_per_checkpoint():
    out = await call(
        "staffing_estimate_modset_capacity",
        airport_code="IAD",
        date_range={"start": "2026-10-05", "end": "2026-10-07"},
    )
    assert out["data_label"] == "synthetic"
    assert out["filters"]["date_range"] == {"start": "2026-10-05", "end": "2026-10-07"}
    checkpoints = {g["checkpoint_id"] for g in out["groups"]}
    assert checkpoints == {"East", "West"}
    ait = next(
        g
        for g in out["groups"]
        if g["checkpoint_id"] == "East" and g["machine_type"] == "AIT"
    )
    assert 100 <= ait["capacity_proxy_p90"] <= 175
    assert any("lower bound" in w for w in out["warnings"])


@pytest.mark.asyncio
async def test_capacity_tool_rejects_unknown_checkpoint():
    out = await call(
        "staffing_estimate_modset_capacity", airport_code="BWI", checkpoint_id="Z"
    )
    assert "error" in out
    assert "Checkpoint A" in out["hint"]


@pytest.mark.asyncio
async def test_capacity_tool_rejects_bad_date_range():
    with pytest.raises(Exception):
        await call(
            "staffing_estimate_modset_capacity",
            airport_code="BWI",
            date_range={"start": "2026-10-07", "end": "2026-10-05"},
        )


@pytest.mark.asyncio
async def test_capacity_tool_empty_filter_is_actionable():
    out = await call(
        "staffing_estimate_modset_capacity",
        airport_code="BWI",
        date_range={"start": "2025-01-01", "end": "2025-01-02"},
    )
    assert "error" in out and "hint" in out


@pytest.mark.asyncio
async def test_fit_tool_returns_fit_and_bins():
    out = await call(
        "staffing_fit_wait_response", airport_code="DCA", capacity_source="rules"
    )
    assert out["capacity_source"] == "rules"
    assert out["fit"]["knee_utilization"] is not None
    assert out["bins"]
    assert out["join"]["unmatched_wait_hours"] == 0
    assert "AIT/Standard" in out["capacity_lookup"]


@pytest.mark.asyncio
async def test_fit_tool_estimated_capacity_single_checkpoint():
    out = await call(
        "staffing_fit_wait_response",
        airport_code="BWI",
        checkpoint_id="Checkpoint A",
        screening_type="Standard",
        capacity_source="estimated",
    )
    assert out["capacity_source"] == "estimated"
    assert out["n_points"] == 7 * 19
    assert out["fit"]["r2"] is not None


@pytest.mark.asyncio
async def test_fit_tool_markdown():
    text = await call(
        "staffing_fit_wait_response", airport_code="IAD", response_format="markdown"
    )
    assert isinstance(text, str)
    assert "Knee at u" in text
