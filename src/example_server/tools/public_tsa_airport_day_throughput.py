"""Bounded read-only lookup of public TSA airport-day throughput aggregates."""

from __future__ import annotations

import csv
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from example_server.pims.tooling import READ_ONLY

SOURCE = {
    "catalog": (
        "https://catalog.data.gov/dataset/"
        "tsa-foia-reading-room-weekly-passenger-throughput-data"
    ),
    "report_mirror": (
        "https://raw.githubusercontent.com/mikelor/TsaThroughput/main/"
        "data/december_30_2018_to_january_5_2019.pdf"
    ),
    "report_git_blob_sha1": "f2b6c5a30d29eed112a8f2dfa82d0ee6718eb0fa",
    "archive_commit": "cb0d85a4f8ecd8a938016eb276a22a6fb135b799",
}
SAMPLE_DATES = ("2018-12-30", "2018-12-31")


@lru_cache(maxsize=1)
def _sample() -> dict[tuple[str, str], tuple[int, int]]:
    table = files("example_server.public_data").joinpath("airport_day.csv")
    with table.open(encoding="utf-8", newline="") as source:
        return {
            (row["airport"], row["date"]): (
                int(row["reported_screened"]),
                int(row["checkpoint_hour_rows"]),
            )
            for row in csv.DictReader(source)
        }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="public_tsa_airport_day_throughput",
        annotations={"title": "Historical public TSA screened throughput", **READ_ONLY},
    )
    def public_tsa_airport_day_throughput(
        airport: Annotated[str, Field(pattern=r"^[A-Z]{3}$", description="IATA code.")],
        day: Annotated[date, Field(description="Date printed in the TSA report.")],
    ) -> dict:
        """Read public-origin historical counts for Dec 30 or 31, 2018.

        Sums available checkpoint-hour rows by airport and date. These are
        screened throughput, not arrivals, unique travelers, waits or staffing.
        No live TSA access. Never use this sample for the synthetic staffing tools.
        """
        result = {
            "data_classification": "public_origin_historical_tsa_report",
            "source": SOURCE,
            "metric": "PMIS - Total Customer Throughput (Unadjusted)",
            "coverage_dates": list(SAMPLE_DATES),
            "airport": airport,
            "date": day.isoformat(),
            "note": (
                "Sum of available checkpoint-hour observations from a third-party "
                "mirror of a public TSA report; completeness is not asserted. "
                "These are not arrivals, unique travelers, waits or staffing."
            ),
        }
        try:
            observation = _sample().get((airport, day.isoformat()))
        except (OSError, ValueError, KeyError, csv.Error):
            result.update(status="sample_unavailable", reported_screened=None)
            return result
        if observation is None:
            result.update(
                status="no_observation",
                reported_screened=None,
                checkpoint_hour_rows=None,
            )
        else:
            result.update(
                status="observed",
                reported_screened=observation[0],
                checkpoint_hour_rows=observation[1],
            )
        return result
