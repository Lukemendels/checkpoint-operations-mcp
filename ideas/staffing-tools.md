# Staffing scenario tools — design sketch

**Status:** sketch (2026-11-13). Tools 1-6 implemented on the synthetic PIMS seed (2026-11-18); see [Implemented: queue model](#implemented-queue-model-tools-4-6) for what changed from this sketch. Jay's piece of the paired demo in
[README.md](README.md). Builds on the synthetic PMIS-shaped schema Paul is seeding and
consumes the wait-time prediction Luke is building. Everything here runs on labeled
synthetic data; outputs are simulation estimates, not operational recommendations.

## Position in the architecture

```text
Table 1 throughput (modset-hour) ─┐
Table 2 wait times (ckpt-hour)   ─┼─► wait-time prediction (Luke) ─► predicted demand + wait per ckpt-hour
Table 3 national daily baseline  ─┘                                              │
                                                                                 ▼
checkpoint_config + roster (synthetic, staffing-owned) ─► staffing scenario tools (Jay) ─► required modsets/staff,
                                                                                         scenario deltas, feasibility
```

The staffing tools are a **client of the prediction**, not a second forecaster. Story:
TSA needs predicted waits to staff effectively; the same prediction is what lets a
traveler plan their trip. One core, two interfaces.

## Model (deliberately simple and explainable)

Per checkpoint-hour and screening type:

1. **Capacity per modset** `c` (pax/hour) — estimated from Table 1 as
   `passenger_throughput / active_modsets`, by airport, checkpoint, machine_type,
   screening_type. Report median and IQR; refuse hours with `active_modsets = 0`.
   Finding from the fixture: when staffing holds utilization below 1, observed
   throughput understates capacity, so the observed p90 is a **lower bound**; use
   the assumed capacities in `staffing_rules` for utilization and report the
   observed proxy beside them.
2. **Utilization** `u = demand / (active_modsets × c)`.
3. **Wait response** `p95_wait = f(u)` — fit from Tables 1+2 joined at checkpoint-hour
   (Table 1 aggregated to checkpoint-hour **first**; see join guard below). Default form:
   piecewise-linear/convex in `u` with a knee near 0.85; fall back to a documented
   constant table if the fit is poor. Residual spread becomes the uncertainty band.
4. **Required modsets** for a target: smallest `n` with `f(demand / (n × c)) ≤ target`.
5. **Staff** = `n × staff_per_modset[machine_type]` + fixed posts (TDC, exit lane),
   × a break/relief factor. All three constants are synthetic configuration, stated in
   every response.

Not modeled in this sketch (superseded for tools 4-5 below): queue carry-over between hours, lane-level x-ray
throughput, PreCheck/Standard reassignment mid-hour, labor rules, real schedules.

## Additional synthetic inputs (staffing-owned, not in Paul's three tables)

Keep these as package fixtures under `tests/fixtures/` or a staffing-owned seed so
Paul's scope doesn't grow:

- `checkpoint_config`: airport_code, checkpoint_id, installed_modsets_wtmd,
  installed_modsets_ait, fixed_posts, open_hour, close_hour.
- `staffing_rules`: staff_per_modset by machine_type, relief_factor, max_shift_hours.
- `roster_availability`: date, hour, airport_code, checkpoint_id, available_staff
  (synthetic headcount on duty; no names, no real schedules).

## Tools (one file each under `src/<pkg>/tools/`)

All read-only, idempotent, `openWorldHint: false`. Every response carries
`data_label: "synthetic"`, `assumptions`, `as_of`, `warnings`, and `row_limit`.

| # | Tool | Inputs | Returns | Depends on |
|---|------|--------|---------|------------|
| 1 | `staffing_explain_model` | none | model steps 1–5, constants, limits, what is synthetic | — |
| 2 | `staffing_estimate_modset_capacity` | airport, checkpoint?, machine_type?, screening_type?, date range | median/IQR pax per active modset-hour, n hours, zero-denominator count | Table 1 |
| 3 | `staffing_fit_wait_response` | airport, checkpoint, screening_type, date range | fitted `f(u)` points, residual band, n, join cardinality check | Tables 1+2 |
| 4 | `staffing_required_for_forecast` | airport, checkpoint, date, target (`p95 ≤ 15` or `30`), forecast (inline list or "call prediction") | per-hour required modsets + staff, vs installed and roster, gap hours | Luke's prediction, 2, 3, config |
| 5 | `staffing_simulate_scenario` | baseline from 4 + proposed changes (add/remove modsets at hours, move staff between checkpoints) | modeled p95 before/after per hour with band, staff delta, feasibility verdict + which constraint binds | 4, rules, roster |
| 6 | `staffing_find_historical_gaps` | airport, date range, threshold | hours where flag exceeded while `active_modsets < installed` (under-deployed) vs at capacity (demand-bound) | Tables 1+2, config |

Tool 6 overlaps Luke's `detect_modset_utilization_anomalies`; coordinate so one of
them owns it or 6 narrows to "installed-but-idle" hours.

**Forecast input contract for tool 4** (what we need from Luke's prediction, or accept
inline): list of `{date, hour, airport_code, checkpoint_id, screening_type,
predicted_pax, predicted_p95_wait, interval_low, interval_high, model_version}`.

**Supervisor flow in the demo:** 4 → 5 → human approves/rejects. The tools never write
a roster; "approve" is a demo state, not an action.

## Guardrails (carry PLAN.md rules into code)

- Aggregate Table 1 to checkpoint-hour before joining Table 2; refuse if join
  cardinality ≠ 1:1. Percentiles over hours are not passenger percentiles.
- Reject zero/missing denominators; distinguish missing, suppressed and zero.
- Typed bounded inputs (enums for airport/screening/machine, date ranges ≤ 90 days,
  row limits). Sanitized error dicts with `hint`.
- Percent change only with nonzero baseline; absolute change always.
- No causal language: "modeled wait change", never "will reduce wait".

## Evaluation cases (feed the shared harness)

- Ordinary: required modsets for a known synthetic peak hour; capacity estimate
  matches seed constants within tolerance.
- Difficult: scenario where moving staff fixes one checkpoint and breaks another.
- Invalid: unknown airport, hour 24, end < start, target not in {15, 30}.
- Unavailable: checkpoint with no Table 2 rows → qualified refusal.
- Adversarial: prompt asks for a real roster / "which officer" → refusal; asks to
  claim causal benefit → qualified wording.
- Baseline: no-tool agent given the same tables as CSV text; compare correctness of
  required-modset answers and refusal behavior.

## Next slice (when Paul's seed lands)

1. Check seed columns/grain against the schema; note any drift.
2. Add staffing fixtures + `models.py` enums/BaseModels for inputs above.
3. Implement 1, 2, 3 with tests (pure functions over the seed; no I/O).
4. Implement 4 against an inline forecast; swap to Luke's tool when available.
5. Implement 5, then 6 if still ours. Run `uv run ruff check .` and `uv run pytest`.

## Implemented: queue model (tools 4-6)

Added 2026-11-18. Code: `src/example_server/staffing/queue.py` (pure functions),
`staffing/planning.py` (calibration set-up), `tools/staffing_required_for_forecast.py`,
`tools/staffing_simulate_scenario.py`, `tools/staffing_find_historical_gaps.py`.

**Why it changed.** On the seed the same-hour fit `p95 = f(u)` explains little
(r2 0.21-0.26 at ATL, ORD and LAX) because the seed generator carries a backlog from
one hour to the next and waits follow that backlog. Steps 3-4 above became an
hour-by-hour fluid queue:

- capacity per hour = capacity factor x sum of `staffing_rules` capacities of the
  active modsets (factor = median served/nominal over saturated hours, about 0.94);
- backlog at end of hour by Little's law, `(avg wait - base wait) / 60 x capacity`
  (base wait = median avg wait in hours with served/nominal <= 0.70);
- demand = served + backlog - previous backlog, clipped at 0;
- simulation: `served = min(backlog + demand, capacity)`, modeled avg wait
  `base + 60 x backlog / capacity`, p95 = slope x avg + intercept fitted below the
  seed's reporting ceiling (avg 150, p95 180 min), band = 10th-90th percentile of
  relative replay residuals.

Installed modsets are the distinct `modset_id`s observed for the checkpoint and
screening type (the seed has 5 Standard: 1 WTMD + 4 AIT; 3 PreCheck), because the
counts in `checkpoint_config.csv` do not match the seed.

**Backtest (p95 MAE in minutes, 24 checkpoint/screening groups, 600 hours each,
in sample).** `replay` = observed modsets + demand reconstructed from the same day's
waits (close to circular, an upper bound); `profile` = observed modsets + mean
reconstructed demand at that hour on other dates (out of sample for demand);
`same-hour` = the step-3 fit, in sample.

| | replay | profile | same-hour |
| --- | --- | --- | --- |
| pooled | 4.24 | 16.56 | 14.75 |
| ATL Checkpoint A Standard | 7.90 | 22.69 | 37.76 |
| ORD South Standard | 6.28 | 24.01 | 17.49 |
| DCA North Standard | 1.20 | 2.28 | 1.30 |

The profile test beats the same-hour fit in 6 of 24 groups (the four ATL groups and
ORD North and LAX North Standard). **On the out-of-sample test the queue model does not
beat the same-hour fit overall on this seed**; the tools say so in
`model.backtest.verdict` and `staffing_explain_model` prints the full table. Two
seed artifacts weigh on this: the generator scales arrivals with the hour's active
capacity (so demand is not fixed when modsets change, unlike the model's
assumption), and its wait coefficient is 28 rather than Little's law's 60. A
profile-backtest grid over the coefficient is lowest at about 10-20. The tools keep
60, so the modeled wait response to modset changes is likely overstated relative to
the seed.

**Other limits.** The plan in tool 4 is a per-hour minimum with a look-ahead (a low
early choice may not push a later hour over target); it is not a staff-hour optimum.
The band does not include demand-forecast error. `seed_replay` uses the target
day's own waits to reconstruct demand, so it is a replay, not a forecast. Tool 6 is
descriptive; an under-deployed hour does not show that more modsets would have
changed the wait.

