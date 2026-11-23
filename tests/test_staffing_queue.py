"""Pure-function tests for the fluid queue model (staffing/queue.py)."""

from __future__ import annotations

import math
import random

import pytest

from example_server.staffing.model import capacity_lookup_from_rules, filter_rows
from example_server.staffing.provider import PimsSeedProvider
from example_server.staffing.queue import (
    HourObs,
    QueueParams,
    adjust_modsets,
    backtest,
    build_days,
    calibrate,
    gap_beyond_installed,
    infer_backlog,
    installed_modsets,
    modeled_avg_wait,
    reconstruct_day,
    reconstruct_demand,
    replay_day,
    required_plan,
    simulate,
    verdict,
)

PARAMS = QueueParams(
    base_wait_minutes=5.0,
    minutes_per_backlog=60.0,
    capacity_factor=1.0,
    p95_slope=1.5,
    p95_intercept=0.0,
)


# ── Little's law inversion ───────────────────────────────────────────────────


def test_littles_law_hand_case():
    # 20 minutes above base at 300 pax/h effective capacity -> 100 pax queued.
    assert infer_backlog(25.0, 300.0, 5.0, 60.0) == pytest.approx(100.0)
    assert modeled_avg_wait(100.0, 300.0, 5.0, 60.0) == pytest.approx(25.0)
    # At or below base wait there is no backlog.
    assert infer_backlog(4.0, 300.0, 5.0, 60.0) == 0.0


@pytest.mark.parametrize("backlog", [0.0, 1.0, 37.5, 250.0, 1200.0])
@pytest.mark.parametrize("capacity", [76.0, 300.0, 398.0])
def test_littles_law_round_trip(backlog, capacity):
    avg = modeled_avg_wait(backlog, capacity, 4.2, 60.0)
    assert infer_backlog(avg, capacity, 4.2, 60.0) == pytest.approx(backlog)


def test_inversion_rejects_zero_capacity():
    with pytest.raises(ValueError):
        infer_backlog(10.0, 0.0, 5.0, 60.0)
    with pytest.raises(ValueError):
        simulate([10.0], [0.0], PARAMS)


def test_reconstruct_demand_hand_case():
    served = [100.0, 300.0, 300.0, 200.0]
    backlog = [0.0, 50.0, 80.0, 0.0]
    assert reconstruct_demand(served, backlog) == [100.0, 350.0, 330.0, 120.0]


def test_reconstruct_demand_clips_at_zero():
    # A backlog that falls faster than served allows would imply negative demand.
    assert reconstruct_demand([10.0, 10.0], [100.0, 0.0]) == [110.0, 0.0]


def test_replay_round_trips_a_consistent_day():
    """Simulate a known demand, observe it, invert it, replay it: same day."""
    truth = [200.0, 400.0, 350.0, 100.0, 50.0]
    cap = [300.0] * len(truth)
    sim = simulate(truth, cap, PARAMS)
    assert [round(s["backlog"]) for s in sim] == [0, 100, 150, 0, 0]
    hours = [
        HourObs(
            hour=6 + i,
            served=s["served"],
            nominal_capacity=c,
            modsets=(("M1", "AIT"),),
            avg_wait=s["avg_wait"],
            p95_wait=s["p95"],
        )
        for i, (s, c) in enumerate(zip(sim, cap))
    ]
    rec = reconstruct_day(hours, PARAMS)
    assert rec["demand"] == pytest.approx(truth)
    assert rec["backlog"] == pytest.approx([s["backlog"] for s in sim])
    replay = replay_day(hours, PARAMS)
    assert [r["p95"] for r in replay] == pytest.approx([h.p95_wait for h in hours])


# ── simulation properties ────────────────────────────────────────────────────


def test_simulate_conserves_passengers():
    demand = [120.0, 500.0, 480.0, 90.0]
    sim = simulate(demand, [300.0] * 4, PARAMS)
    assert sum(s["served"] for s in sim) + sim[-1]["backlog"] == pytest.approx(
        sum(demand)
    )


def test_simulate_same_plan_is_reproducible():
    demand = [100.0, 420.0, 390.0, 60.0]
    cap = [300.0, 300.0, 376.0, 300.0]
    assert simulate(demand, cap, PARAMS) == simulate(demand, list(cap), PARAMS)


def test_adding_capacity_never_increases_backlog():
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(1, 20)
        demand = [rng.uniform(0, 800) for _ in range(n)]
        before = [rng.choice([76.0, 152.0, 228.0, 322.0, 398.0]) for _ in range(n)]
        after = [c + rng.choice([0.0, 0.0, 76.0, 94.0]) for c in before]
        sb = simulate(demand, before, PARAMS)
        sa = simulate(demand, after, PARAMS)
        for b, a in zip(sb, sa):
            assert a["backlog"] <= b["backlog"] + 1e-9
            assert a["avg_wait"] <= b["avg_wait"] + 1e-9


def test_band_brackets_the_point_estimate():
    params = QueueParams(5.0, 60.0, 1.0, 1.4, 2.0, band_low=-0.2, band_high=0.25)
    for s in simulate([100.0, 900.0, 50.0], [300.0] * 3, params):
        assert s["p95_low"] <= s["p95"] <= s["p95_high"]


# ── planning ─────────────────────────────────────────────────────────────────

INSTALLED = [("W1", "WTMD", 94.0), ("A1", "AIT", 76.0), ("A2", "AIT", 76.0)]


def test_required_plan_meets_target_where_reachable():
    demand = [40.0, 150.0, 230.0, 60.0]
    plan = required_plan(demand, INSTALLED, PARAMS, 15.0)
    assert all(p["reachable"] and p["meets_target"] for p in plan)
    assert all(p["p95"] <= 15.0 for p in plan)
    assert plan[0]["n"] == 1  # 40 pax fit in one modset


def test_required_plan_flags_unreachable_hours():
    demand = [100.0, 2000.0, 100.0]
    plan = required_plan(demand, INSTALLED, PARAMS, 15.0)
    assert plan[1]["reachable"] is False
    assert plan[1]["n"] == len(INSTALLED)
    gaps = gap_beyond_installed(demand, INSTALLED, PARAMS, 15.0, max_extra=3)
    assert gaps[0] == {"gap": 0, "capped": False}
    assert gaps[1]["capped"] is True


def test_required_plan_does_not_strand_later_hours():
    """A minimal early hour must not leave a backlog that breaks a later hour.

    Hour 0 alone needs 2 modsets (170 pax/h leaves 10 queued, p95 12.7). But
    then hour 1 at full capacity ends with 64 queued (p95 30.9 > 30), whereas
    starting hour 1 with an empty queue ends with 54 (p95 27.3). So hour 0
    must use all 3.
    """
    demand = [180.0, 300.0]
    one_hour = required_plan(demand[:1], INSTALLED, PARAMS, 30.0)
    assert one_hour[0]["n"] == 2
    plan = required_plan(demand, INSTALLED, PARAMS, 30.0)
    assert [p["n"] for p in plan] == [3, 3]
    assert all(p["meets_target"] for p in plan)


def test_higher_target_needs_no_more_modsets():
    demand = [60.0, 200.0, 240.0, 230.0, 120.0]
    strict = required_plan(demand, INSTALLED, PARAMS, 15.0)
    loose = required_plan(demand, INSTALLED, PARAMS, 30.0)
    assert sum(p["n"] for p in loose) <= sum(p["n"] for p in strict)


def test_adjust_modsets_order_and_clipping():
    active = [("A1", "AIT", 76.0)]
    assert [m[0] for m in adjust_modsets(active, INSTALLED, 1)] == ["W1", "A1"]
    assert [m[0] for m in adjust_modsets(active, INSTALLED, 5)] == ["W1", "A1", "A2"]
    two = [("W1", "WTMD", 94.0), ("A2", "AIT", 76.0)]
    assert [m[0] for m in adjust_modsets(two, INSTALLED, -1)] == ["W1"]
    assert [m[0] for m in adjust_modsets(two, INSTALLED, -5)] == ["W1"]


def test_verdict_says_when_queue_model_loses():
    bt = {
        "replay": {"mae": 3.0},
        "profile": {"mae": 9.0},
        "same_hour_fit": {"mae": 5.0},
    }
    text = verdict(bt)
    assert "queue model lower" in text
    assert "NOT lower than the same-hour fit" in text


# ── calibration on the synthetic seed ────────────────────────────────────────


@pytest.fixture(scope="module")
def seed_group(pims_seed):
    provider = PimsSeedProvider()
    capacity = capacity_lookup_from_rules(provider.staffing_rules())
    out = {}
    for ap, cp in (("ATL", "Checkpoint A"), ("DCA", "North")):
        sel = {"airport_code": ap, "checkpoint_id": cp, "screening_type": "Standard"}
        t = filter_rows(provider.throughput(), **sel)
        w = filter_rows(provider.wait_times(), **sel)
        days, diag = build_days(t, w, capacity)
        out[ap] = (days, diag, t, w, capacity)
    return out


def test_build_days_is_complete_on_seed(seed_group):
    days, diag, *_ = seed_group["ATL"]
    assert len(days) == 30
    assert all(len(hs) == 20 for hs in days.values())
    assert all(
        [h.hour for h in hs] == sorted(h.hour for h in hs) for hs in days.values()
    )
    assert diag == {
        "unmatched_throughput_hours": 0,
        "unmatched_wait_hours": 0,
        "unknown_capacity_hours": 0,
    }


def test_calibration_recovers_generator_constants(seed_group):
    days, *_ = seed_group["ATL"]
    p = calibrate(days)
    # Generator: Standard base 3.8 + U(0, 2.2); served = capacity x U(.89, 1)
    # x U(.93, 1.07); p95 = avg x U(1.23, 1.68) + U(0, 3); caps 150 / 180.
    assert 4.3 < p.base_wait_minutes < 5.6
    assert 0.9 < p.capacity_factor < 1.0
    assert 1.3 < p.p95_slope < 1.6
    assert p.p95_ceiling == 180.0 and p.avg_ceiling == 150.0
    assert p.censored_hours > 0


def test_installed_modsets_puts_wtmd_first(seed_group):
    days, _diag, _t, _w, capacity = seed_group["ATL"]
    inst = installed_modsets(days, capacity, "Standard")
    assert len(inst) == 5
    assert inst[0][1] == "WTMD"
    assert {m[1] for m in inst[1:]} == {"AIT"}


def test_backtest_reports_both_models(seed_group):
    for ap in ("ATL", "DCA"):
        days, _diag, t, w, capacity = seed_group[ap]
        bt = backtest(
            days, calibrate(days), throughput_rows=t, wait_rows=w, capacity=capacity
        )
        assert bt["replay"]["n"] == bt["profile"]["n"] == 600
        assert bt["same_hour_fit"]["n"] == 600
        assert set(bt["queue_beats_same_hour"]) == {"replay", "profile"}
        assert "same-hour fit" in bt["verdict"]
        assert bt["residual_band"]["low"] < 0 < bt["residual_band"]["high"]
        assert not math.isnan(bt["replay"]["mae"])
    # The busy airport is where backlog carry-over matters most.
    days, _diag, t, w, capacity = seed_group["ATL"]
    bt = backtest(
        days, calibrate(days), throughput_rows=t, wait_rows=w, capacity=capacity
    )
    assert bt["replay"]["mae"] < bt["same_hour_fit"]["mae"]
