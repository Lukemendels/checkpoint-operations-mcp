"""Response envelope and error helpers shared by the staffing_* tools."""

from __future__ import annotations

from datetime import date
from typing import Any

from example_server.models import DateRange
from example_server.staffing.model import filter_rows
from example_server.staffing.provider import DATA_LABEL, DataUnavailableError

MODEL_VERSION = "staffing-queue-0.2"

STANDARD_LIMITS = [
    "Synthetic data: outputs are simulation estimates, not operational evidence.",
    "Table 1 is read as one row per active modset-hour; active_modsets is used "
    "only for consistency checks.",
    "Percentiles are over hours (equal weight per hour), not over passengers.",
    "Queue carry-over between hours is modeled only as a fluid backlog in "
    "staffing_required_for_forecast and staffing_simulate_scenario; lane-level "
    "x-ray throughput, demand response to staffing, labor rules and real "
    "schedules are not modeled.",
    "A modeled wait change is not evidence of a causal staffing benefit.",
]


def envelope(
    payload: dict[str, Any],
    *,
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a tool payload with the labels every staffing response carries."""
    return {
        "data_label": DATA_LABEL,
        "model_version": MODEL_VERSION,
        "as_of": date.today().isoformat(),
        "filters": filters or {},
        "assumptions": assumptions or [],
        "warnings": warnings or [],
        "limits": STANDARD_LIMITS,
        **payload,
    }


def error(message: str, hint: str) -> dict[str, str]:
    """Actionable, sanitized error dict (no stack traces, no upstream bodies)."""
    return {"error": message, "hint": hint, "data_label": DATA_LABEL}


def unavailable(exc: DataUnavailableError) -> dict[str, str]:
    return error(
        str(exc),
        "Check STAFFING_DATA_DIR or regenerate the fixture with "
        "`uv run python scripts/make_staffing_fixture.py`.",
    )


def apply_filters(
    rows: list[dict[str, Any]],
    *,
    airport_code: str,
    checkpoint_id: str | None,
    screening_type: str | None,
    machine_type: str | None = None,
    date_range: DateRange | None,
) -> list[dict[str, Any]]:
    return filter_rows(
        rows,
        airport_code=airport_code,
        checkpoint_id=checkpoint_id,
        screening_type=screening_type,
        machine_type=machine_type,
        start=date_range.start if date_range else None,
        end=date_range.end if date_range else None,
    )


def describe_filters(
    *,
    airport_code: str,
    checkpoint_id: str | None,
    screening_type: str | None,
    machine_type: str | None = None,
    date_range: DateRange | None,
) -> dict[str, Any]:
    return {
        "airport_code": airport_code,
        "checkpoint_id": checkpoint_id,
        "screening_type": screening_type,
        "machine_type": machine_type,
        "date_range": (
            {"start": date_range.start.isoformat(), "end": date_range.end.isoformat()}
            if date_range
            else None
        ),
    }


def known_checkpoints(config_rows: list[dict[str, Any]], airport: str) -> list[str]:
    return sorted(
        r["checkpoint_id"] for r in config_rows if r["airport_code"] == airport
    )


def checkpoint_error(
    config_rows: list[dict[str, Any]], airport: str, checkpoint: str
) -> dict[str, str]:
    return error(
        f"checkpoint '{checkpoint}' is not configured for {airport}.",
        "Known checkpoints: " + ", ".join(known_checkpoints(config_rows, airport)),
    )
