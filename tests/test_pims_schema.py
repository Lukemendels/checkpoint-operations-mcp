from datetime import date

import pytest
from pydantic import ValidationError

from example_server.pims.schema import (
    CheckpointWaitTime,
    CustomerThroughput,
    NationalDailyBaseline,
)


def sample_throughput():
    return dict(
        date="2026-11-16",
        hour=7,
        airport_code="IAD",
        airport_category="Cat I",
        checkpoint_id="North",
        modset_id="STD-Modset-01",
        machine_type="AIT",
        screening_type="Standard",
        passenger_throughput=42,
        active_modsets=3,
    )


def test_customer_model_schema():
    obj = CustomerThroughput(**sample_throughput())
    assert obj.date == date(2026, 11, 16)
    assert obj.machine_type == "AIT"


@pytest.mark.parametrize(
    "update",
    [
        {"hour": 24},
        {"airport_code": "Iad"},
        {"machine_type": "X-RAY"},
        {"screening_type": "Crew"},
        {"active_modsets": 0},
        {"passenger_throughput": -2},
        {"airport_category": "Cat III"},
        {"nonexistent_field": "bad"},
    ],
)
def test_invalid_throughput_rejected(update):
    with pytest.raises(ValidationError):
        CustomerThroughput(**(sample_throughput() | update))


def test_wait_invariants():
    valid = dict(
        date="2026-11-16",
        hour=7,
        airport_code="IAD",
        checkpoint_id="North",
        screening_type="Standard",
        avg_wait_minutes=5,
        p95_wait_minutes=11,
        max_wait_minutes=17,
        exceeded_15m_flag=True,
        exceeded_30m_flag=False,
    )
    assert CheckpointWaitTime(**valid).exceeded_15m_flag
    with pytest.raises(ValidationError):
        CheckpointWaitTime(**(valid | {"max_wait_minutes": 9}))
    with pytest.raises(ValidationError):
        CheckpointWaitTime(**(valid | {"exceeded_30m_flag": True}))
    # Strictly greater than threshold, not >=.
    exact = valid | {"max_wait_minutes": 15, "exceeded_15m_flag": False}
    CheckpointWaitTime(**exact)


def test_national_nonnegative():
    with pytest.raises(ValidationError):
        NationalDailyBaseline(
            date="2026-11-16",
            national_throughput=-1,
            prior_year_same_weekday_throughput=100,
        )
