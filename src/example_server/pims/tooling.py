"""Shared parameter types and error handling for the pims_* tools."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Callable, Literal

from pydantic import Field

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

FromDate = Annotated[str, Field(description="First local date, inclusive, YYYY-MM-DD.")]
ToDate = Annotated[
    str,
    Field(description="Last local date, inclusive, YYYY-MM-DD (window <= 366 days)."),
]
Airport = Annotated[
    str | None,
    Field(
        description="IATA code: ATL, ORD, LAX, IAD, DCA or BWI in the synthetic seed."
    ),
]
Checkpoint = Annotated[
    str | None,
    Field(
        description="Checkpoint id, e.g. 'Checkpoint A', 'North'. "
        "Call pims_get_dataset_coverage to list them."
    ),
]
Screening = Annotated[
    Literal["Standard", "PreCheck"] | None,
    Field(description="Screening type filter."),
]
Machine = Annotated[
    Literal["WTMD", "AIT"] | None, Field(description="Screening machine type filter.")
]
Hour = Annotated[int | None, Field(ge=0, le=23, description="Local clock hour 0-23.")]
Limit = Annotated[
    int, Field(ge=1, le=500, description="Maximum rows to return (1-500).")
]


EMPTY_NOTE = (
    "No rows match. Absent rows are NOT zero: the hour may be closed, unrecorded "
    "or outside coverage, and the data cannot say which. Report the value as "
    "unavailable; do not report 0 passengers or 0 minutes."
)


def safe_call(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict:
    """Run a repository query, turning expected failures into sanitized errors.

    An empty result gets an explicit note: absent rows are not zero.
    """
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, dict) and result.get("rows") == []:
            result["note"] = EMPTY_NOTE
        return result
    except FileNotFoundError:
        return {
            "error": "the synthetic PIMS seed database was not found.",
            "hint": "Build it with `uv run python scripts/build_pims_seed.py`, "
            "or set PIMS_DB_PATH to an existing seed.",
            "synthetic": True,
        }
    except ValueError as exc:
        return {
            "error": str(exc),
            "hint": "Dates are YYYY-MM-DD with from_date <= to_date; see "
            "pims_get_dataset_coverage for valid airports, checkpoints and dates.",
            "synthetic": True,
        }
    except sqlite3.Error:
        return {
            "error": "the seed database could not be read.",
            "hint": "Run pims_run_data_quality_checks or rebuild the seed.",
            "synthetic": True,
        }
