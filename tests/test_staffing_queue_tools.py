"""Tools 4-6 through the FastMCP API, on the synthetic PIMS seed."""

from __future__ import annotations

import math
import sqlite3
from typing import Any

import pytest

from example_server.app import mcp

ATL_A = {
    "airport_code": "ATL",
    "checkpoint_id": "Checkpoint A",
    "date": "2026-11-16",
    "screening_type": "Standard",
}
NEW_TOOLS = {
    "staffing_required_for_forecast",
    "staffing_simulate_scenario",
    "staffing_find_historical_gaps",
}


async def call(name: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(name, arguments)
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured
    return result.content[0].text


def assert_envelope(out: dict[str, Any]) -> None:
    assert out["data_label"] == "synthetic"
    assert out["assumptions"] and out["limits"]
    assert isinstance(out["warnings"], list)
    assert any("causal" in limit for limit in out["limits"])


@pytest.mark.asyncio
async def test_new_tools_are_discoverable_and_read_only():
    tools = {t.name: t for t in await mcp.list_tools()}
    assert NEW_TOOLS <= set(tools)
    for name in NEW_TOOLS:
        ann = tools[name].annotations
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
        assert ann.open_world_hint is False


# ── tool 4 ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_required_seed_replay_contract():
    out = await call("staffing_required_for_forecast", **ATL_A, target_p95_minutes=30)
    assert_envelope(out)
    hours = out["hours"]
    assert [h["hour"] for h in hours] == list(range(4, 24))
    cal = out["model"]["calibration"]
    assert cal["dates"] == 29 and cal["last"] == "2026-11-15"
    assert cal["target_date_excluded"] is True
    for h in hours:
        assert 1 <= h["min_modsets"] <= h["installed_modsets"] == 5
        assert h["conservative_modsets"] >= h["min_modsets"]
        lo, hi = h["p95_band_minutes"]
        assert lo <= h["modeled_p95_minutes"] <= hi
        if not h["unreachable_at_installed"]:
            assert h["meets_target"] and h["modeled_p95_minutes"] <= 30
            assert h["gap_modsets_beyond_installed"] == 0
        else:
            assert h["min_modsets"] == 5
        s = h["staff"]
        assert s["fixed_posts"] == 3
        assert s["total_staff"] == math.ceil(s["screening_staff_with_relief"]) + 3
        assert "observed_active_modsets" in h
    assert out["totals"]["unreachable_hours"]  # ATL is overloaded by design
    assert any("not reached" in w for w in out["warnings"])
    bt = out["model"]["backtest"]
    assert {"replay", "profile", "same_hour_fit", "verdict"} <= set(bt)


@pytest.mark.asyncio
async def test_required_inline_forecast():
    forecast = [
        {"hour": 4, "predicted_pax": 30},
        {"hour": 5, "predicted_pax": 120, "interval_low": 100, "interval_high": 160},
        {"hour": 6, "predicted_pax": 250, "interval_high": 330},
        {"hour": 7, "predicted_pax": 60},
    ]
    out = await call(
        "staffing_required_for_forecast",
        **{**ATL_A, "date": "2026-11-20"},
        target_p95_minutes=15,
        forecast_source="inline",
        forecast=forecast,
    )
    assert_envelope(out)
    assert [h["forecast_demand_pax"] for h in out["hours"]] == [30, 120, 250, 60]
    assert out["hours"][0]["min_modsets"] == 1
    assert all(h["meets_target"] for h in out["hours"])
    assert all(h["conservative_modsets"] >= h["min_modsets"] for h in out["hours"])
    assert "observed_active_modsets" not in out["hours"][0]
    # A date outside the seed is fine for inline: calibrate on all 30 seed days.
    assert out["model"]["calibration"]["dates"] == 30


@pytest.mark.asyncio
async def test_required_target_monotone():
    strict = await call(
        "staffing_required_for_forecast", **ATL_A, target_p95_minutes=15
    )
    loose = await call("staffing_required_for_forecast", **ATL_A, target_p95_minutes=30)
    assert (
        loose["totals"]["min_plan_modset_hours"]
        <= strict["totals"]["min_plan_modset_hours"]
    )


@pytest.mark.asyncio
async def test_required_markdown():
    text = await call(
        "staffing_required_for_forecast",
        **ATL_A,
        target_p95_minutes=30,
        response_format="markdown",
    )
    assert isinstance(text, str)
    assert text.startswith("# Modeled modsets for p95 <= 30 min")
    assert "synthetic" in text.lower() and "Backtest" in text


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"forecast_source": "inline"}, "needs a non-empty forecast"),
        ({"date": "2025-01-01"}, "not in the synthetic seed"),
        ({"checkpoint_id": "Z"}, "not configured"),
        (
            {
                "forecast_source": "inline",
                "forecast": [
                    {"hour": 5, "predicted_pax": 10},
                    {"hour": 7, "predicted_pax": 10},
                ],
            },
            "contiguous",
        ),
        (
            {
                "forecast_source": "inline",
                "forecast": [
                    {"hour": 5, "predicted_pax": 10},
                    {"hour": 5, "predicted_pax": 12},
                ],
            },
            "same hour",
        ),
        (
            {
                "forecast_source": "inline",
                "forecast": [{"hour": 5, "predicted_pax": 10, "interval_low": 20}],
            },
            "interval",
        ),
        (
            {"calibration_range": {"start": "2026-11-15", "end": "2026-11-16"}},
            "calibration day",
        ),
    ],
)
@pytest.mark.asyncio
async def test_required_invalid_inputs_return_error_dicts(overrides, needle):
    out = await call(
        "staffing_required_for_forecast",
        **{**ATL_A, **overrides},
        target_p95_minutes=15,
    )
    assert set(out) >= {"error", "hint", "data_label"}
    assert needle in out["error"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_p95_minutes": 20},
        {"target_p95_minutes": 15, "screening_type": "Express"},
        {"target_p95_minutes": 15, "airport_code": "JFK"},
        {
            "target_p95_minutes": 15,
            "forecast_source": "inline",
            "forecast": [{"hour": 24, "predicted_pax": 10}],
        },
        {
            "target_p95_minutes": 15,
            "forecast_source": "inline",
            "forecast": [{"hour": 5, "predicted_pax": -1}],
        },
    ],
)
@pytest.mark.asyncio
async def test_required_rejects_out_of_schema_inputs(overrides):
    with pytest.raises(Exception):
        await call("staffing_required_for_forecast", **{**ATL_A, **overrides})


# ── tool 5 ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_zero_change_reproduces_baseline():
    out = await call(
        "staffing_simulate_scenario", **ATL_A, changes=[], target_p95_minutes=30
    )
    assert_envelope(out)
    assert out["baseline"].startswith("observed active modsets")
    for h in out["hours"]:
        assert h["modsets_before"] == h["modsets_after"]
        assert h["modeled_p95_before_minutes"] == h["modeled_p95_after_minutes"]
        assert h["modeled_p95_change_minutes"] == 0
        assert h["staff_before"] == h["staff_after"]
        assert h["feasible"]
    assert out["totals"]["hours_changed_by_carryover_only"] == []


@pytest.mark.asyncio
async def test_scenario_adding_modsets_never_increases_backlog():
    base = await call(
        "staffing_simulate_scenario", **ATL_A, changes=[], target_p95_minutes=30
    )
    short = [h["hour"] for h in base["hours"] if h["modsets_before"] < 5]
    assert short, "seed day should have some hours below installed"
    out = await call(
        "staffing_simulate_scenario",
        **ATL_A,
        changes=[{"hour": hr, "delta_modsets": 1} for hr in short],
        target_p95_minutes=30,
    )
    for h in out["hours"]:
        assert h["modeled_backlog_after_pax"] <= h["modeled_backlog_before_pax"]
        assert h["modeled_p95_change_minutes"] <= 0
    # Backlog carries: some hour without a change still shows a modeled change.
    assert out["totals"]["hours_changed_by_carryover_only"]


@pytest.mark.asyncio
async def test_scenario_constraints_bind():
    out = await call(
        "staffing_simulate_scenario",
        **ATL_A,
        changes=[{"hour": 4, "delta_modsets": 3}, {"hour": 5, "delta_modsets": -9}],
        target_p95_minutes=30,
        available_staff=20,
    )
    h4, h5 = out["hours"][0], out["hours"][1]
    assert "installed_limit" in h4["binding_constraints"]
    assert h4["modsets_after"] == 5
    assert "minimum_one_modset" in h5["binding_constraints"]
    assert h5["modsets_after"] == 1
    assert any("available_staff" in h["binding_constraints"] for h in out["hours"])
    assert out["totals"]["infeasible_hours"]


@pytest.mark.asyncio
async def test_scenario_inline_baseline_is_tool4_min_plan():
    forecast = [{"hour": hr, "predicted_pax": p} for hr, p in [(6, 300), (7, 420)]]
    common = {
        **ATL_A,
        "target_p95_minutes": 30,
        "forecast_source": "inline",
        "forecast": forecast,
    }
    req = await call("staffing_required_for_forecast", **common)
    sim = await call("staffing_simulate_scenario", **common, changes=[])
    assert [h["modsets_before"] for h in sim["hours"]] == [
        h["min_modsets"] for h in req["hours"]
    ]
    assert [h["modeled_p95_before_minutes"] for h in sim["hours"]] == [
        h["modeled_p95_minutes"] for h in req["hours"]
    ]


@pytest.mark.parametrize(
    "changes, needle",
    [
        ([{"hour": 2, "delta_modsets": 1}], "not in the plan"),
        (
            [{"hour": 6, "delta_modsets": 1}, {"hour": 6, "delta_modsets": 1}],
            "same hour",
        ),
    ],
)
@pytest.mark.asyncio
async def test_scenario_invalid_changes_return_error_dicts(changes, needle):
    out = await call(
        "staffing_simulate_scenario", **ATL_A, changes=changes, target_p95_minutes=30
    )
    assert "error" in out and "hint" in out and needle in out["error"]


@pytest.mark.asyncio
async def test_scenario_rejects_out_of_schema_inputs():
    with pytest.raises(Exception):
        await call(
            "staffing_simulate_scenario",
            **ATL_A,
            changes=[{"hour": 6, "delta_modsets": 50}],
            target_p95_minutes=30,
        )
    with pytest.raises(Exception):
        await call(
            "staffing_simulate_scenario",
            **ATL_A,
            changes=[],
            target_p95_minutes=30,
            available_staff=-1,
        )


@pytest.mark.asyncio
async def test_scenario_markdown():
    text = await call(
        "staffing_simulate_scenario",
        **ATL_A,
        changes=[{"hour": 17, "delta_modsets": 1}],
        target_p95_minutes=30,
        response_format="markdown",
    )
    assert text.startswith("# Modeled scenario")
    assert "carries" in text


# ── tool 6 ───────────────────────────────────────────────────────────────────

GAP_SQL = """
WITH inst AS (
  SELECT checkpoint_id, screening_type, COUNT(DISTINCT modset_id) AS n
  FROM customer_throughput WHERE airport_code = :ap
  GROUP BY checkpoint_id, screening_type
), act AS (
  SELECT date, hour, checkpoint_id, screening_type, COUNT(DISTINCT modset_id) AS n
  FROM customer_throughput WHERE airport_code = :ap
  GROUP BY date, hour, checkpoint_id, screening_type
)
SELECT w.checkpoint_id, w.screening_type,
       COUNT(*) AS considered,
       SUM(w.p95_wait_minutes > :thr) AS above,
       SUM(w.p95_wait_minutes > :thr AND a.n < i.n) AS under,
       SUM(w.p95_wait_minutes > :thr AND a.n = i.n) AS at_cap
FROM checkpoint_wait_times AS w
JOIN act AS a ON a.date = w.date AND a.hour = w.hour
  AND a.checkpoint_id = w.checkpoint_id AND a.screening_type = w.screening_type
JOIN inst AS i ON i.checkpoint_id = w.checkpoint_id
  AND i.screening_type = w.screening_type
WHERE w.airport_code = :ap AND w.date BETWEEN :start AND :end
GROUP BY w.checkpoint_id, w.screening_type
ORDER BY w.checkpoint_id, w.screening_type
"""


@pytest.mark.parametrize(
    "airport, threshold, start, end",
    [
        ("ATL", 30, "2026-10-18", "2026-11-16"),
        ("ORD", 15, "2026-11-01", "2026-11-07"),
        ("BWI", 30, "2026-10-18", "2026-11-16"),
    ],
)
@pytest.mark.asyncio
async def test_gaps_match_direct_sql(pims_seed, airport, threshold, start, end):
    out = await call(
        "staffing_find_historical_gaps",
        airport_code=airport,
        date_range={"start": start, "end": end},
        threshold_minutes=threshold,
    )
    assert_envelope(out)
    with sqlite3.connect(pims_seed) as db:
        expected = db.execute(
            GAP_SQL, {"ap": airport, "thr": threshold, "start": start, "end": end}
        ).fetchall()
    got = [
        (
            g["checkpoint_id"],
            g["screening_type"],
            g["hours_considered"],
            g["hours_above_threshold"],
            g["under_deployed"],
            g["at_capacity"],
        )
        for g in out["by_checkpoint_screening"]
    ]
    assert got == [tuple(r) for r in expected]
    assert out["totals"]["no_throughput_rows"] == 0
    p95s = [h["p95_wait_minutes"] for h in out["top_hours"]]
    assert p95s == sorted(p95s, reverse=True)
    assert all(p > threshold for p in p95s)


@pytest.mark.asyncio
async def test_gaps_filters_limit_and_classes():
    out = await call(
        "staffing_find_historical_gaps",
        airport_code="ATL",
        date_range={"start": "2026-10-18", "end": "2026-11-16"},
        threshold_minutes=15,
        checkpoint_id="Checkpoint A",
        screening_type="Standard",
        limit=5,
    )
    assert len(out["by_checkpoint_screening"]) == 1
    assert out["returned"] == 5 and out["has_more"] is True
    for h in out["top_hours"]:
        expected = (
            "under_deployed"
            if h["active_modsets"] < h["installed_modsets"]
            else "at_capacity"
        )
        assert h["classification"] == expected
    assert "not evidence" in out["interpretation"]


@pytest.mark.asyncio
async def test_gaps_invalid_inputs():
    out = await call(
        "staffing_find_historical_gaps",
        airport_code="ATL",
        date_range={"start": "2026-10-18", "end": "2026-10-19"},
        threshold_minutes=15,
        checkpoint_id="Z",
    )
    assert "error" in out and "Checkpoint A" in out["hint"]
    out = await call(
        "staffing_find_historical_gaps",
        airport_code="ATL",
        date_range={"start": "2025-01-01", "end": "2025-01-02"},
        threshold_minutes=15,
    )
    assert "error" in out and "hint" in out
    for bad in (
        {"threshold_minutes": 20},
        {"threshold_minutes": 15, "limit": 0},
        {"threshold_minutes": 15, "limit": 201},
        {
            "threshold_minutes": 15,
            "date_range": {"start": "2026-11-02", "end": "2026-11-01"},
        },
    ):
        args = {
            "airport_code": "ATL",
            "date_range": {"start": "2026-10-18", "end": "2026-10-19"},
            **bad,
        }
        with pytest.raises(Exception):
            await call("staffing_find_historical_gaps", **args)


@pytest.mark.asyncio
async def test_gaps_markdown():
    text = await call(
        "staffing_find_historical_gaps",
        airport_code="DCA",
        date_range={"start": "2026-10-18", "end": "2026-11-16"},
        threshold_minutes=15,
        response_format="markdown",
    )
    assert text.startswith("# Hours with p95 > 15 min")


# ── explain ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_explain_reports_queue_backtest_honestly():
    out = await call("staffing_explain_model")
    steps = " ".join(s["how"] for s in out["model_steps"])
    assert "Little's law" in steps and "backlog" in steps
    summ = out["backtest"]["summary"]
    assert summ["groups"] == 24
    assert len(out["backtest"]["rows"]) == 24
    assert "same-hour fit" in summ["verdict"]
    if summ["pooled_profile_mae"] >= summ["pooled_same_hour_mae"]:
        assert "does not beat" in summ["verdict"]


@pytest.mark.asyncio
async def test_weekday_estimate_is_prior_only_and_feeds_scenario(pims_seed):
    args = {
        **ATL_A,
        "date": "2026-11-09",
        "target_p95_minutes": 30,
        "forecast_source": "synthetic_weekday",
    }
    plan = await call("staffing_required_for_forecast", **args)
    assert_envelope(plan)
    assert plan["model"]["calibration"]["last"] == "2026-11-08"
    meta = plan["model"]["forecast"]
    assert meta["training_cutoff_exclusive"] == args["date"]
    assert meta["rows"][0]["training_dates"] == [
        "2026-10-19",
        "2026-10-26",
        "2026-11-02",
    ]
    with sqlite3.connect(pims_seed) as db:
        (expected,) = db.execute(
            """
            SELECT AVG(pax) FROM (
              SELECT SUM(passenger_throughput) AS pax
              FROM customer_throughput
              WHERE airport_code = 'ATL' AND checkpoint_id = 'Checkpoint A'
                AND screening_type = 'Standard' AND hour = 4
                AND date < '2026-11-09' AND strftime('%w', date) = '1'
              GROUP BY date
            )
            """
        ).fetchone()
    assert plan["hours"][0]["forecast_demand_pax"] == round(expected, 1)
    assert all(row["interval_high"] is None for row in plan["hours"])

    scenario = await call("staffing_simulate_scenario", **args, changes=[])
    assert_envelope(scenario)
    assert [row["modsets_before"] for row in scenario["hours"]] == [
        row["min_modsets"] for row in plan["hours"]
    ]


@pytest.mark.asyncio
async def test_weekday_estimate_does_not_backfill_missing_history():
    out = await call(
        "staffing_required_for_forecast",
        **{**ATL_A, "date": "2026-10-27"},
        target_p95_minutes=30,
        forecast_source="synthetic_weekday",
    )
    assert "Insufficient comparable synthetic history" in out["error"]
    assert "not treated as zero" in out["hint"]
