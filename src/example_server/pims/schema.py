"""Authoritative PIMS/PMIS field models and validation (Pydantic v2).

Grains:
  customer_throughput: date x hour x airport x checkpoint x modset x
                       machine_type x screening_type
  checkpoint_wait_times: date x hour x airport x checkpoint x screening_type
  national_daily_baseline: date
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AirportCategory = Literal["Cat X", "Cat I", "Cat II"]
MachineType = Literal["WTMD", "AIT"]
ScreeningType = Literal["Standard", "PreCheck"]


class StrictRow(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CustomerThroughput(StrictRow):
    date: date
    hour: int = Field(ge=0, le=23)
    airport_code: str = Field(pattern=r"^[A-Z]{3}$")
    airport_category: AirportCategory
    checkpoint_id: str = Field(min_length=1, max_length=80)
    modset_id: str = Field(min_length=1, max_length=80)
    machine_type: MachineType
    screening_type: ScreeningType
    passenger_throughput: int = Field(ge=0)
    active_modsets: int = Field(ge=1)


class CheckpointWaitTime(StrictRow):
    date: date
    hour: int = Field(ge=0, le=23)
    airport_code: str = Field(pattern=r"^[A-Z]{3}$")
    checkpoint_id: str = Field(min_length=1, max_length=80)
    screening_type: ScreeningType
    avg_wait_minutes: float = Field(ge=0, allow_inf_nan=False)
    p95_wait_minutes: float = Field(ge=0, allow_inf_nan=False)
    max_wait_minutes: float = Field(ge=0, allow_inf_nan=False)
    exceeded_15m_flag: bool
    exceeded_30m_flag: bool

    @model_validator(mode="after")
    def check_waits(self) -> "CheckpointWaitTime":
        if not (
            self.avg_wait_minutes <= self.p95_wait_minutes <= self.max_wait_minutes
        ):
            raise ValueError("Wait metrics must satisfy avg <= p95 <= max")
        # Demonstration semantics: any observed maximum STRICTLY above threshold.
        if self.exceeded_15m_flag != (self.max_wait_minutes > 15.0):
            raise ValueError("exceeded_15m_flag must equal (max_wait_minutes > 15)")
        if self.exceeded_30m_flag != (self.max_wait_minutes > 30.0):
            raise ValueError("exceeded_30m_flag must equal (max_wait_minutes > 30)")
        return self


class NationalDailyBaseline(StrictRow):
    date: date
    national_throughput: int = Field(ge=0)
    prior_year_same_weekday_throughput: int = Field(ge=0)


TABLE_GRAINS = {
    "customer_throughput": [
        "date",
        "hour",
        "airport_code",
        "checkpoint_id",
        "modset_id",
        "machine_type",
        "screening_type",
    ],
    "checkpoint_wait_times": [
        "date",
        "hour",
        "airport_code",
        "checkpoint_id",
        "screening_type",
    ],
    "national_daily_baseline": ["date"],
}


DATA_DICTIONARY = {
    "customer_throughput": {
        "grain": "Airport / checkpoint / modset (screening machine) / hour / screening type",
        "key": TABLE_GRAINS["customer_throughput"],
        "fields": {
            "date": "Local airport calendar date, YYYY-MM-DD; timezone is the airport's own local time",
            "hour": "Local clock hour from 0 through 23; DST fall-back duplicate hour is not distinguishable under this schema",
            "airport_code": "Three-letter IATA airport code",
            "airport_category": "TSA-style category label; sample mappings are fictional and are NOT official airport categorizations",
            "checkpoint_id": "Checkpoint identifier; fictional labels in the seed",
            "modset_id": "Screening-machine/modset identifier; NOT an X-ray-lane identifier",
            "machine_type": "WTMD (walk-through metal detector) or AIT (advanced imaging technology)",
            "screening_type": "Standard or PreCheck",
            "passenger_throughput": "Count of passengers processed by this modset in the hour (not arrivals)",
            "active_modsets": "Distinct active modsets for the same airport/checkpoint/hour/screening_type. REPEATED on each modset row: do NOT SUM this column; use MAX or COUNT(DISTINCT modset_id).",
        },
    },
    "checkpoint_wait_times": {
        "grain": "Airport / checkpoint / hour / screening type; no modset breakdown",
        "key": TABLE_GRAINS["checkpoint_wait_times"],
        "fields": {
            "date": "Local calendar date, YYYY-MM-DD",
            "hour": "Local hour 0-23",
            "airport_code": "Three-letter IATA airport code",
            "checkpoint_id": "Checkpoint identifier",
            "screening_type": "Standard or PreCheck",
            "avg_wait_minutes": "Synthetic average wait in minutes; not an official observed metric",
            "p95_wait_minutes": "Synthetic 95th-percentile wait in minutes",
            "max_wait_minutes": "Synthetic maximum wait in minutes",
            "exceeded_15m_flag": "1 iff max_wait_minutes > 15.0 for this demonstration (not a duration fraction)",
            "exceeded_30m_flag": "1 iff max_wait_minutes > 30.0 for this demonstration",
        },
    },
    "national_daily_baseline": {
        "grain": "United States / calendar day",
        "key": ["date"],
        "fields": {
            "date": "U.S. TSA national screening date",
            "national_throughput": "National passenger count; synthetic unless sourced through an independently verified TSA feed",
            "prior_year_same_weekday_throughput": "Corresponding same-weekday prior-year comparison count; not same calendar date",
        },
    },
}
