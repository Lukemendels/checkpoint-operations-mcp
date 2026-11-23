"""Tools submodule — one tool per file.

Each tool module exposes a `register(mcp)` function that attaches its
`@mcp.tool`-decorated function to the server. `register_tools(mcp)` (called by
`app.py`) registers them all.

Synthetic PIMS/PMIS tool groups:
  - pims_*      read-only data access at the correct table grains
  - staffing_*  scenario model built on top of that data (simulation estimates)
Separately, public_tsa_* reads a historical public report sample.
"""

from __future__ import annotations

from example_server.tools import (
    pims_get_airport_day_summary,
    pims_get_checkpoint_hourly_summary,
    pims_get_checkpoint_wait_times,
    pims_get_customer_throughput,
    pims_get_dataset_coverage,
    pims_get_national_daily_baseline,
    pims_get_schema,
    pims_run_data_quality_checks,
    public_tsa_airport_day_throughput,
    staffing_estimate_modset_capacity,
    staffing_explain_model,
    staffing_find_historical_gaps,
    staffing_fit_wait_response,
    staffing_required_for_forecast,
    staffing_simulate_scenario,
)

_MODULES = (
    pims_get_schema,
    pims_get_dataset_coverage,
    pims_get_customer_throughput,
    pims_get_checkpoint_wait_times,
    pims_get_national_daily_baseline,
    pims_get_checkpoint_hourly_summary,
    pims_get_airport_day_summary,
    pims_run_data_quality_checks,
    public_tsa_airport_day_throughput,
    staffing_explain_model,
    staffing_estimate_modset_capacity,
    staffing_find_historical_gaps,
    staffing_fit_wait_response,
    staffing_required_for_forecast,
    staffing_simulate_scenario,
)


def register_tools(mcp) -> None:
    """Register all tools with the MCP server."""
    for module in _MODULES:
        module.register(mcp)


__all__ = ["register_tools"]
