"""Exercise a staged toolkit over real MCP stdio from outside its tree."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def smoke(stage: Path) -> None:
    with tempfile.TemporaryDirectory() as cwd:
        spec = StdioServerParameters(
            command=sys.executable, args=[str(stage / "server.py")], cwd=cwd
        )
        async with (
            stdio_client(spec) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def tool(name: str, args: dict | None = None) -> dict:
                result = await session.call_tool(name, args or {})
                assert not result.is_error, result.content
                out = result.structured_content or json.loads(result.content[0].text)
                if set(out) == {"result"}:
                    out = out["result"]
                assert "error" not in out, out
                return out

            coverage = await tool("pims_get_dataset_coverage")
            assert coverage["tables"]["customer_throughput"]["rows"] == 53982
            assert coverage["tables"]["checkpoint_wait_times"]["rows"] == 14400
            assert coverage["tables"]["national_daily_baseline"]["rows"] == 30
            listed = {item.name for item in (await session.list_tools()).tools}
            assert len(listed) == 15
            observed = await tool(
                "public_tsa_airport_day_throughput",
                {"airport": "IAD", "day": "2018-12-30"},
            )
            assert observed["data_classification"] == (
                "public_origin_historical_tsa_report"
            )
            assert observed["reported_screened"] == 27089
            assert observed["checkpoint_hour_rows"] == 83
            args = {
                "airport_code": "ATL",
                "checkpoint_id": "Checkpoint A",
                "screening_type": "Standard",
                "date": "2026-11-09",
                "target_p95_minutes": 30,
                "forecast_source": "synthetic_weekday",
            }
            plan = await tool("staffing_required_for_forecast", args)
            scenario = await tool("staffing_simulate_scenario", {**args, "changes": []})
            assert plan["data_label"] == scenario["data_label"] == "synthetic"
            assert len(plan["hours"]) == len(scenario["hours"]) == 20
            assert plan["model"]["calibration"]["last"] == "2026-11-08"
            assert all(
                "observed_active_modsets" not in row and row["interval_high"] is None
                for row in plan["hours"]
            )
            assert [row["min_modsets"] for row in plan["hours"]] == [
                row["modsets_before"] for row in scenario["hours"]
            ]
            print("PASS: staged stdio, 15 tools: public, SQLite, forecast, scenario")


if __name__ == "__main__":
    asyncio.run(smoke(Path(sys.argv[1]).resolve()))
