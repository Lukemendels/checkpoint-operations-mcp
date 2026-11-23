# Quickstart — Your First MCP Server in 5 Minutes

New to MCP? This gets you from clone to a running server connected to a client.

## 1. Install prerequisites

You need [uv](https://docs.astral.sh/uv/) (a fast Python package manager):

```bash
pip install uv     # or: brew install uv
```

## 2. Set up the project

```bash
cp .env.example .env
uv sync
uv run python scripts/build_pims_seed.py   # synthetic seed the tools read
```

## 3. Run the server

```bash
uv run python main.py
```

The server starts in **stdio** mode and waits for a client to connect over
stdin/stdout. That's correct — it doesn't print a URL, because a local MCP
server is launched *by* the client as a subprocess. Press `Ctrl-C` to stop it.

## 4. Connect a client

### Claude Code

Create or edit `.mcp.json` (or your user MCP settings) with the **absolute path**
to this repo:

```json
{
  "mcpServers": {
    "checkpoint-ops-synthetic": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/checkpoint-operations-mcp", "python", "main.py"]
    }
  }
}
```

### Claude Desktop

Add the same block to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude
Desktop.

Once connected, ask a question the tools answer, e.g.:

> "Using the pims tools, how many Standard passengers did IAD Checkpoint A screen in the 07:00 hour on 2026-11-16, and what was the p95 wait?" (synthetic data: 282 passengers)

The answer should cite the synthetic label. For the staffing side, try: "For ATL
Checkpoint A Standard on 2026-11-16, what is the modeled minimum number of modsets
per hour to keep p95 wait at or under 30 minutes?"

## 5. Verify everything

```bash
uv sync --group dev
uv run pytest tests/ -v      # tests pass
uv run ruff check .          # lint clean
```

## 6. Test HTTP mode (optional)

Cloud hosts run the server over HTTP. Try it locally:

```bash
MCP_TRANSPORT=streamable-http uv run python main.py
# in another terminal:
curl http://localhost:8000/health      # {"status":"healthy",...}
```

---

## Next steps

- **Inspect the tools** in [the tool directory](src/example_server/tools/).
- **Run the demo** using the prompts and expected outcomes in [SUBMISSION.md](SUBMISSION.md).
- **Evaluate locally** with [the evaluation harness](eval/harness/README.md).
- **Use an authorized sandbox** for hosted testing. See [SECURITY.md](SECURITY.md).

### Optional synthetic weekday-to-staffing demo

Call both `staffing_required_for_forecast` and `staffing_simulate_scenario`
with `forecast_source="synthetic_weekday"` for ATL / Checkpoint A / Standard
on 2026-11-09, targeting 30 minutes. Use `changes: []` on the scenario call to
check that it starts from the same minimum plan, then try a bounded modset
change. The default remains `seed_replay`; caller forecasts use `inline`.

The optional path averages complete checkpoint-hour processed volumes from
at least two earlier matching weekdays and calibrates the queue on prior
dates only. Processed volume is a proxy, not arrivals. Historical p95 ranges
are descriptive, not a forecast interval; unknown hours are not treated as
zero. These are synthetic workflow results, not operational staffing advice.
