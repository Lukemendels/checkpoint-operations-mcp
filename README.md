# Checkpoint Operations SI Data Service

An MCP server that lets an SI agent answer checkpoint-operations questions at the
**right data grain**, and lets a supervisor compare **modeled** staffing scenarios.
It is the shared core of the "How TSA could use SI to advance the Golden Age of Air
Travel" concept in [ideas/](ideas/README.md).

> **Two separate data paths.** The PIMS and staffing tools use a fictional,
> deterministic PIMS/PMIS-shaped seed. Staffing outputs are simulations, not
> operational recommendations or causal evidence. The public TSA tool reads only
> a small historical airport-day sample from a public TSA report mirror. It is
> not an input to staffing and does not measure arrivals or waits. No restricted
> TSA records, rosters, credentials or internal endpoints are used.

See the [submission and verification guide](SUBMISSION.md).
Evaluation: [eval/harness/README.md](eval/harness/README.md) and
[docs/evaluation.md](docs/evaluation.md).

## Try the demo

1. Look up IAD on Dec. 30, 2018. The public tool reports **27,089 screenings**
   across 83 source rows and returns provenance.
2. Look up Dec. 29, 2018. It reports **no observation**, not zero.
3. Switch explicitly to the [synthetic ATL staffing scenario](SUBMISSION.md#two-minute-demo).
   At 10 a.m., capacity is back at baseline, but the modeled p95 wait is
   **63.6 minutes vs. 14.6** because the earlier backlog has not cleared.

## What it exposes (15 read-only tools)

| Group | Tool | Answers |
|---|---|---|
| Data | `pims_get_schema` | table grains, keys, joining rules (start here) |
| | `pims_get_dataset_coverage` | dates, airports, checkpoint ids, provenance |
| | `pims_get_customer_throughput` | modset-hour passenger rows |
| | `pims_get_checkpoint_wait_times` | checkpoint-hour avg / p95 / max waits |
| | `pims_get_checkpoint_hourly_summary` | throughput summed over modsets, joined to waits on the exact keys |
| | `pims_get_airport_day_summary` | daily airport totals beside the national day |
| | `pims_get_national_daily_baseline` | national daily totals and prior-year same weekday |
| | `pims_run_data_quality_checks` | structural checks on the seed |
| Public history | `public_tsa_airport_day_throughput` | historical reported screenings, Dec. 30–31, 2018; [source and demo](docs/public-throughput-demo.md) |
| Staffing | `staffing_explain_model` | the model, its constants, its backtest and limits |
| | `staffing_estimate_modset_capacity` | passengers per modset-hour by machine type |
| | `staffing_fit_wait_response` | same-hour p95-vs-utilization fit (kept for comparison) |
| | `staffing_required_for_forecast` | minimum modsets and staff per hour to meet a p95 target |
| | `staffing_simulate_scenario` | before/after modeled p95 for a proposed modset change, with feasibility |
| | `staffing_find_historical_gaps` | past hours over a wait threshold: under-deployed vs at capacity |

The data grain rules are enforced in code: modset rows are summed to checkpoint-hour
before any join to wait times; `active_modsets` is counted, never summed; queries are
parameterized, bounded (≤ 500 rows, ≤ 366 days) and read-only; errors come back as
sanitized dicts with a hint.

The staffing model carries backlog between hours (an hour-by-hour fluid queue with
Little's-law backlog), because the same-hour fit explains the seed's waits poorly.
`staffing_explain_model` reports its backtest against that fit, including where it
does not win.

## Getting started

```bash
uv sync --group dev
uv run python scripts/build_pims_seed.py   # builds data/synthetic/pims/*.sqlite (seconds)
uv run python main.py                      # stdio server
```

The seed is rebuilt deterministically (2026-10-18 to 2026-11-16, six airports,
random seed 20261117), so it is not committed. Connect a client with
[QUICKSTART.md](QUICKSTART.md).

### Verify

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q                           # builds its own seed in a temp dir
uv run python eval/harness/run_tools.py    # deterministic tool-vs-reference checks
```

## Layout

```
src/example_server/
├── app.py            FastMCP instance, server instructions, transport choice
├── pims/             synthetic PIMS/PMIS data layer: schema, generator, repository
├── staffing/         staffing model: provider, capacity, queue model, planning
├── tools/            one file per tool (pims_*.py, staffing_*.py)
data/synthetic/
├── pims/             seed location (gitignored) + national CSV import template
├── pims_staffing/    staffing-owned config for the seed's 12 checkpoints
└── staffing/         small CSV fixture used by the original staffing unit tests
eval/harness/         cases, independent references, tool + agent runners, scorer
scripts/              seed and fixture builders
```

## Sandbox and license

Use the [IBM local stdio toolkit](deploy/ibm/local-mcp-toolkit/README.md) for the
private watsonx Orchestrate sandbox. A public source repository does not authorize
a public service. See [SECURITY.md](SECURITY.md) for scope and limitations.

The code is under the [MIT license](LICENSE). Public-source data provenance is
documented separately in [the throughput sample guide](docs/public-throughput-demo.md).
Built from the [GSA hackathon template](https://github.com/GSA-TTS/mcp-hackathon-template).
This is a hackathon prototype, not an official TSA service or endorsement.
