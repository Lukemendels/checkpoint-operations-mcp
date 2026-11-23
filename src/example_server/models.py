"""Pydantic models and enums for tool parameters and responses.

Typed parameters give MCP clients a precise schema, which helps the LLM call
your tools correctly. Prefer enums for closed sets of values and Pydantic
models for structured inputs/outputs.

These are illustrative — replace them with models for your own service.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ResponseFormat(str, Enum):
    """How a tool should shape its return value.

    - JSON     → machine-readable dict/list (best for chaining tool calls)
    - MARKDOWN → human-readable text (best for direct display)
    """

    JSON = "json"
    MARKDOWN = "markdown"


class PaginationParams(BaseModel):
    """Reusable pagination inputs for list-style tools.

    Federal APIs almost always paginate. Expose these to the caller (or handle
    paging internally) and return `has_more` / `next_offset` in your response.
    """

    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of items to return (1–100).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip from the start of the result set.",
    )


# ── Staffing tools ───────────────────────────────────────────────────────────


class AirportCode(str, Enum):
    """Airports in the synthetic PIMS seed (BWI, DCA, IAD also in the CSV fixture)."""

    ATL = "ATL"
    ORD = "ORD"
    LAX = "LAX"
    BWI = "BWI"
    DCA = "DCA"
    IAD = "IAD"


class MachineType(str, Enum):
    """Screening machine (modset) type."""

    WTMD = "WTMD"
    AIT = "AIT"


class ScreeningType(str, Enum):
    """Screening lane population."""

    STANDARD = "Standard"
    PRECHECK = "PreCheck"


MAX_DATE_RANGE_DAYS = 90


class DateRange(BaseModel):
    """Inclusive ISO date range, at most 90 days."""

    start: date = Field(description="First date, inclusive (YYYY-MM-DD).")
    end: date = Field(description="Last date, inclusive (YYYY-MM-DD).")

    @model_validator(mode="after")
    def _check_order_and_span(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        if (self.end - self.start).days + 1 > MAX_DATE_RANGE_DAYS:
            raise ValueError(f"range must be at most {MAX_DATE_RANGE_DAYS} days")
        return self


class ForecastSource(str, Enum):
    """Where tools 4-5 take hourly demand from."""

    SEED_REPLAY = "seed_replay"  # demand reconstructed from the synthetic seed day
    SYNTHETIC_WEEKDAY = "synthetic_weekday"  # same-weekday processed-volume proxy
    INLINE = "inline"  # demand supplied by the caller (e.g. a wait-time prediction)


MAX_PAX_PER_HOUR = 20000


class ForecastHour(BaseModel):
    """One hour of an inline demand forecast (passengers arriving that hour)."""

    hour: int = Field(ge=0, le=23, description="Local hour 0-23.")
    predicted_pax: float = Field(
        ge=0, le=MAX_PAX_PER_HOUR, description="Predicted passengers arriving."
    )
    interval_low: float | None = Field(
        default=None, ge=0, le=MAX_PAX_PER_HOUR, description="Lower interval bound."
    )
    interval_high: float | None = Field(
        default=None, ge=0, le=MAX_PAX_PER_HOUR, description="Upper interval bound."
    )


class ScenarioChange(BaseModel):
    """Add (positive) or remove (negative) modsets at one hour."""

    hour: int = Field(ge=0, le=23, description="Local hour 0-23.")
    delta_modsets: int = Field(
        ge=-10, le=10, description="Modsets to add (+) or remove (-) at this hour."
    )
