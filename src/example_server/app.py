"""MCP server entry point.

Keep this file THIN. It only:
  1. creates the FastMCP instance,
  2. registers tools / prompts / resources / routes from the submodules, and
  3. decides which transport to run (stdio locally vs. HTTP when deployed).

All real logic lives in the submodules:
  - tools/      one file per tool (each exposes `register(mcp)`)
  - prompts/    reusable conversation starters
  - resources/  static/semi-static data the client can read directly
  - routes.py   HTTP-only infrastructure (health check, etc.)
  - config.py   settings loaded from env vars / .env
  - models.py   Pydantic models & enums for tool parameters
  - utils.py    shared helpers (HTTP client, formatting, pagination)
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from example_server.config import settings
from example_server.prompts import register_prompts
from example_server.resources import register_resources
from example_server.routes import register_routes
from example_server.tools import register_tools

SERVER_NAME = "Checkpoint Operations SI Data Service"

# The server name is what MCP clients display.
mcp = FastMCP(
    name=SERVER_NAME,
    instructions=(
        "Read-only access to a SYNTHETIC, PIMS/PMIS-shaped dataset of airport "
        "checkpoint throughput and wait times (six sample airports, 30 days), plus "
        "a staffing scenario model built on it. The pims_* and staffing_* tools "
        "do not use real TSA data. Separately, public_tsa_airport_day_throughput "
        "returns historical screened counts for a bounded sample from the public "
        "TSA report mirror; never mix these with synthetic waits or staffing. "
        "Start with pims_get_schema and pims_get_dataset_coverage. Use "
        "pims_get_checkpoint_hourly_summary for checkpoint-level questions; never "
        "sum active_modsets. If a query returns no rows, the value is unavailable, "
        "not zero. Call national figures synthetic when you report them. "
        "staffing_* tools return simulation estimates, not "
        "operational recommendations or causal claims. Say so when answering, and "
        "refuse requests for real rosters, named officers or nonpublic data."
    ),
)

# Wire up everything the server exposes. Each aggregator lives in its submodule.
register_tools(mcp)
register_prompts(mcp)
register_resources(mcp)
register_routes(mcp)


def main() -> None:
    """Start the server, choosing a transport based on the environment.

    Precedence:
      1. A platform-injected port (Databricks `DATABRICKS_APP_PORT` or a generic
         `PORT`) → run HTTP on that port. Cloud hosts set this for you.
      2. `MCP_TRANSPORT=streamable-http` in the environment → run HTTP on
         `MCP_HOST`:`MCP_PORT` (see config.py / .env).
      3. Otherwise → stdio, which is how local clients (Claude Desktop, Claude
         Code, Zed) launch the server as a subprocess.
    """
    platform_port = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")

    if platform_port:
        mcp.run(transport="http", host="0.0.0.0", port=int(platform_port))
    elif settings.mcp_transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=settings.mcp_host,
            port=settings.mcp_port,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
