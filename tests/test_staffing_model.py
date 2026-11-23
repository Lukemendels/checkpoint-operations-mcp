"""Pure-function tests for the staffing model against the synthetic fixture.

The fixture generator uses known constants (capacities 170/140/230, knee at
u = 0.85), so steps 1 and 3 can be checked for recovery, not just for shape.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from example_server.models import DateRange
from example_server.staffing.model import (
    CardinalityError,
    aggregate_to_checkpoint_hour,
    capacity_lookup_from_estimate,
    capacity_lookup_from_rules,
    estimate_modset_capacity,
    filter_rows,
    fit_wait_response,
    predict_p95,
    quantile,
)
from example_server.staffing.provider import SyntheticCsvProvider, default_data_dir

TRUE_CAPACITY = {
    ("AIT", "Standard"): 170.0,
    ("WTMD", "Standard"): 140.0,
    ("WTMD", "PreCheck"): 230.0,
}


@pytest.fixture(scope="module")
def provider() -> SyntheticCsvProvider:
    return SyntheticCsvProvider(default_data_dir())


def test_quantile_interpolates():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([5], 0.9) == 5
    assert quantile([1, 2, 3, 4, 5], 0.0) == 1
    assert quantile([1, 2, 3, 4, 5], 1.0) == 5


def test_filter_rows_by_date_and_fields(provider):
    rows = filter_rows(
        provider.throughput(),
        airport_code="DCA",
        checkpoint_id="North",
        screening_type="PreCheck",
        start=date(2026, 10, 6),
        end=date(2026, 10, 6),
    )
    assert rows
    assert {r["date"] for r in rows} == {"2026-10-06"}
    assert {r["machine_type"] for r in rows} == {"WTMD"}


def test_capacity_proxy_recovers_constants(provider):
    estimate = estimate_modset_capacity(
        provider.throughput(), group_by=("machine_type", "screening_type")
    )
    lookup = capacity_lookup_from_estimate(estimate)
    assert set(lookup) == set(TRUE_CAPACITY)
    # Staffing to ~80% utilization keeps observed throughput below capacity, so
    # the p90 proxy is a lower bound on the true constant, never above it.
    for key, true_cap in TRUE_CAPACITY.items():
        assert 0.5 * true_cap <= lookup[key] <= 1.02 * true_cap, (key, lookup[key])
    by_key = {(g["machine_type"], g["screening_type"]): g for g in estimate["groups"]}
    for key, true_cap in TRUE_CAPACITY.items():
        assert by_key[key]["max"] <= 1.02 * true_cap, key
    # The AIT modsets saturate in under-deployed hours, so their max reaches
    # capacity while p90 sits well below it (the lower-bound signal).
    ait = by_key[("AIT", "Standard")]
    assert ait["max"] >= 0.95 * TRUE_CAPACITY[("AIT", "Standard")]
    assert ait["p90_to_max_ratio"] < 0.85
    assert estimate["excluded_zero_denominator_hours"] == 0
    assert estimate["active_modsets_mismatch_hours"] == 0


def test_capacity_counts_zero_denominator_and_mismatch():
    rows = [
        {
            "date": "2026-10-05",
            "hour": 6,
            "airport_code": "BWI",
            "checkpoint_id": "A",
            "modset_id": "M1",
            "machine_type": "AIT",
            "screening_type": "Standard",
            "passenger_throughput": 100,
            "active_modsets": 2,  # declares 2 but only one row present
        },
        {
            "date": "2026-10-05",
            "hour": 7,
            "airport_code": "BWI",
            "checkpoint_id": "A",
            "modset_id": "M1",
            "machine_type": "AIT",
            "screening_type": "Standard",
            "passenger_throughput": 0,
            "active_modsets": 0,  # zero denominator → excluded, counted
        },
    ]
    est = estimate_modset_capacity(rows, group_by=("machine_type", "screening_type"))
    assert est["excluded_zero_denominator_hours"] == 1
    assert est["active_modsets_mismatch_hours"] == 1
    assert est["groups"][0]["n_modset_hours"] == 1


def test_aggregate_sums_to_checkpoint_hour(provider):
    rows = filter_rows(provider.throughput(), airport_code="IAD", checkpoint_id="East")
    agg = aggregate_to_checkpoint_hour(
        rows, capacity_lookup_from_rules(provider.staffing_rules())
    )
    key = ("2026-10-05", 6, "IAD", "East", "Standard")
    assert key in agg
    manual = [
        r
        for r in rows
        if r["date"] == "2026-10-05"
        and r["hour"] == 6
        and r["screening_type"] == "Standard"
    ]
    assert agg[key]["pax"] == sum(r["passenger_throughput"] for r in manual)
    assert agg[key]["rows"] == len(manual)
    assert agg[key]["utilization"] == pytest.approx(
        agg[key]["pax"] / agg[key]["capacity"]
    )


def test_aggregate_marks_unknown_capacity_as_none():
    rows = [
        {
            "date": "2026-10-05",
            "hour": 6,
            "airport_code": "BWI",
            "checkpoint_id": "A",
            "modset_id": "M1",
            "machine_type": "AIT",
            "screening_type": "Standard",
            "passenger_throughput": 100,
            "active_modsets": 1,
        }
    ]
    agg = aggregate_to_checkpoint_hour(rows, {})
    slot = agg[("2026-10-05", 6, "BWI", "A", "Standard")]
    assert slot["utilization"] is None
    assert slot["unknown_capacity_rows"] == 1


def test_fit_recovers_knee_and_steeper_slope(provider):
    rules = capacity_lookup_from_rules(provider.staffing_rules())
    result = fit_wait_response(provider.throughput(), provider.wait_times(), rules)
    assert result["join"]["unmatched_throughput_hours"] == 0
    assert result["join"]["unmatched_wait_hours"] == 0
    assert result["n_points"] == 1596
    fit = result["fit"]
    assert 0.75 <= fit["knee_utilization"] <= 0.95
    assert fit["above_knee"]["slope"] > fit["below_knee"]["slope"] * 3
    assert fit["r2"] > 0.7
    # Prediction at low utilization sits near the generator's 3 + 8u line.
    assert abs(predict_p95(fit, 0.5) - 7.0) < 2.5
    assert result["bins"][0]["n"] > 0


def test_fit_refuses_duplicate_wait_rows(provider):
    waits = provider.wait_times()
    dup = waits + [dict(waits[0])]
    with pytest.raises(CardinalityError):
        fit_wait_response(
            provider.throughput(),
            dup,
            capacity_lookup_from_rules(provider.staffing_rules()),
        )


def test_fit_with_no_overlap_returns_no_fit(provider):
    t_rows = filter_rows(provider.throughput(), airport_code="BWI")
    w_rows = filter_rows(provider.wait_times(), airport_code="IAD")
    result = fit_wait_response(
        t_rows, w_rows, capacity_lookup_from_rules(provider.staffing_rules())
    )
    assert result["n_points"] == 0
    assert result["fit"] is None
    assert result["join"]["unmatched_throughput_hours"] == len(
        {
            (r["date"], r["hour"], r["checkpoint_id"], r["screening_type"])
            for r in t_rows
        }
    )


def test_date_range_validation():
    DateRange(start=date(2026, 10, 5), end=date(2026, 10, 5))
    with pytest.raises(ValidationError):
        DateRange(start=date(2026, 10, 6), end=date(2026, 10, 5))
    with pytest.raises(ValidationError):
        DateRange(start=date(2026, 1, 1), end=date(2026, 6, 1))
