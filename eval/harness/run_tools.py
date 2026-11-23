"""Deterministic tool checks: call the MCP tools in-process, compare to references.

For every answer case, this runs the tool path an expert would use and checks
that it reproduces the raw-SQL reference in references.json. For boundary
cases with a `tool_check`, it checks the tool returns a sanitized error (or,
where the case allows it, an empty result). No model is involved.

    uv run python eval/harness/reference.py     # refresh references first
    uv run python eval/harness/run_tools.py     # writes eval/runs/tools-check.json

Staffing tools from the parallel track are checked only when the server
registers them; otherwise those checks are reported as skipped.
All data is SYNTHETIC.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from fastmcp import Client

HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS_DIR))

from eval_common import (  # noqa: E402
    CAUSAL_PATTERNS,
    SYNTHETIC_PATTERNS,
    load_cases,
    load_references,
    matches_any,
)

DEFAULT_OUT = HARNESS_DIR.parents[1] / "eval" / "runs" / "tools-check.json"


class ToolSession:
    """Thin wrapper that returns each tool's structured payload as a dict."""

    def __init__(self, client: Client, tool_names: set[str]) -> None:
        self.client = client
        self.tool_names = tool_names
        self.calls: list[str] = []

    async def __call__(self, name: str, **args: Any) -> dict[str, Any]:
        self.calls.append(name)
        result = await self.client.call_tool(name, args, raise_on_error=False)
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            return {"error": text[:500], "_protocol_error": True}
        data = result.structured_content
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        if data is None:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"text": text}
        return data if isinstance(data, dict) else {"result": data}


def _one(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") or []
    if len(rows) != 1:
        raise AssertionError(f"expected exactly one row, got {len(rows)}: {payload}")
    return rows[0]


def _all(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "error" in payload:
        raise AssertionError(f"tool error: {payload}")
    if payload.get("has_more"):
        raise AssertionError("result truncated (has_more); raise limit or page")
    return payload.get("rows") or []


def _key(airport: str, checkpoint: str, screening: str) -> dict[str, str]:
    return {
        "airport_code": airport,
        "checkpoint_id": checkpoint,
        "screening_type": screening,
    }


WAITS = "pims_get_checkpoint_wait_times"
HOURLY = "pims_get_checkpoint_hourly_summary"
DAY = "pims_get_airport_day_summary"
NATIONAL = "pims_get_national_daily_baseline"


async def lookup_01(t: ToolSession) -> Any:
    d = "2026-11-02"
    p = await t(
        WAITS,
        from_date=d,
        to_date=d,
        hour=17,
        **_key("ATL", "Checkpoint A", "Standard"),
    )
    return _one(p)["p95_wait_minutes"]


async def lookup_02(t: ToolSession) -> Any:
    d = "2026-10-25"
    p = await t(
        HOURLY, from_date=d, to_date=d, hour=8, **_key("ORD", "North", "PreCheck")
    )
    return _one(p)["passenger_throughput"]


async def lookup_03(t: ToolSession) -> Any:
    d = "2026-11-14"
    p = await t(
        WAITS,
        from_date=d,
        to_date=d,
        hour=16,
        **_key("BWI", "Checkpoint A", "Standard"),
    )
    return _one(p)["max_wait_minutes"]


async def lookup_04(t: ToolSession) -> Any:
    d = "2026-10-30"
    p = await t(
        "pims_get_customer_throughput",
        from_date=d,
        to_date=d,
        hour=18,
        machine_type="AIT",
        limit=500,
        **_key("IAD", "Checkpoint B", "Standard"),
    )
    return len({r["modset_id"] for r in _all(p)})


async def lookup_05(t: ToolSession) -> Any:
    p = await t("pims_get_dataset_coverage")
    table = p["tables"]["customer_throughput"]
    first = date.fromisoformat(table["first_date"])
    last = date.fromisoformat(table["last_date"])
    return (last - first).days + 1


async def aggregation_01(t: ToolSession) -> Any:
    d = "2026-11-05"
    p = await t(
        HOURLY, from_date=d, to_date=d, hour=12, **_key("LAX", "South", "Standard")
    )
    return _one(p)["active_modsets"]


async def aggregation_02(t: ToolSession) -> Any:
    d = "2026-11-01"
    p = await t(
        HOURLY, from_date=d, to_date=d, limit=500, **_key("DCA", "North", "Standard")
    )
    return sum(r["active_modsets"] for r in _all(p))


async def aggregation_03(t: ToolSession) -> Any:
    d = "2026-11-08"
    p = await t(DAY, from_date=d, to_date=d, airport_code="IAD")
    return _one(p)["synthetic_airport_throughput"]


async def aggregation_04(t: ToolSession) -> Any:
    p = await t(
        WAITS,
        from_date="2026-11-09",
        to_date="2026-11-15",
        limit=500,
        **_key("BWI", "Checkpoint B", "PreCheck"),
    )
    rows = _all(p)
    return sum(r["p95_wait_minutes"] for r in rows) / len(rows)


async def aggregation_05(t: ToolSession) -> Any:
    d = "2026-11-05"
    p = await t(WAITS, from_date=d, to_date=d, airport_code="ATL", limit=500)
    return sum(1 for r in _all(p) if r["exceeded_15m_flag"])


async def aggregation_06(t: ToolSession) -> Any:
    d = "2026-11-12"
    return _one(await t(NATIONAL, from_date=d, to_date=d))["national_throughput"]


async def join_grain_01(t: ToolSession) -> Any:
    d = "2026-10-28"
    p = await t(
        HOURLY,
        from_date=d,
        to_date=d,
        hour=7,
        **_key("ATL", "Checkpoint B", "Standard"),
    )
    row = _one(p)
    return row["passenger_throughput"] / row["active_modsets"]


async def join_grain_02(t: ToolSession) -> Any:
    p = await t(
        HOURLY,
        from_date="2026-11-09",
        to_date="2026-11-15",
        limit=500,
        **_key("LAX", "North", "Standard"),
    )
    hot = [r["passenger_throughput"] for r in _all(p) if r["p95_wait_minutes"] > 20]
    return sum(hot) / len(hot)


async def join_grain_03(t: ToolSession) -> Any:
    d = "2026-11-03"
    p = await t(
        WAITS, from_date=d, to_date=d, limit=500, **_key("ORD", "South", "Standard")
    )
    return sum(1 for r in _all(p) if r["exceeded_30m_flag"])


async def comparison_01(t: ToolSession) -> Any:
    d = "2026-11-10"
    row = _one(await t(NATIONAL, from_date=d, to_date=d))
    base = row["prior_year_same_weekday_throughput"]
    return 100.0 * (row["national_throughput"] - base) / base


async def comparison_02(t: ToolSession) -> Any:
    a = _one(
        await t(DAY, from_date="2026-11-02", to_date="2026-11-02", airport_code="ATL")
    )
    b = _one(
        await t(DAY, from_date="2026-11-09", to_date="2026-11-09", airport_code="ATL")
    )
    base = a["synthetic_airport_throughput"]
    return 100.0 * (b["synthetic_airport_throughput"] - base) / base


async def comparison_03(t: ToolSession) -> Any:
    cov = await t("pims_get_dataset_coverage")
    table = cov["tables"]["customer_throughput"]
    p = await t(
        DAY, from_date=table["first_date"], to_date=table["last_date"], limit=500
    )
    totals: dict[str, int] = {}
    for r in _all(p):
        totals[r["airport_code"]] = (
            totals.get(r["airport_code"], 0) + r["synthetic_airport_throughput"]
        )
    return max(totals, key=totals.get)


async def comparison_04(t: ToolSession) -> Any:
    p = await t(
        WAITS,
        from_date="2026-11-09",
        to_date="2026-11-15",
        airport_code="DCA",
        checkpoint_id="South",
        limit=500,
    )
    rows = _all(p)

    def mean(lane: str) -> float:
        vals = [r["p95_wait_minutes"] for r in rows if r["screening_type"] == lane]
        return sum(vals) / len(vals)

    return mean("Standard") - mean("PreCheck")


async def staffing_03(t: ToolSession) -> Any:
    # 560 rows for the week exceed the 500-row cap, so page by checkpoint.
    total = 0
    for checkpoint in ("North", "South"):
        p = await t(
            WAITS,
            from_date="2026-11-09",
            to_date="2026-11-15",
            airport_code="LAX",
            checkpoint_id=checkpoint,
            limit=500,
        )
        total += sum(1 for r in _all(p) if r["p95_wait_minutes"] > 30)
    return total


async def staffing_04(t: ToolSession) -> Any:
    p = await t(
        WAITS,
        from_date="2026-11-01",
        to_date="2026-11-07",
        limit=500,
        **_key("DCA", "North", "PreCheck"),
    )
    return sum(1 for r in _all(p) if r["p95_wait_minutes"] > 15)


async def adversarial_01(t: ToolSession) -> Any:
    d = "2026-11-10"
    return _one(await t(NATIONAL, from_date=d, to_date=d))["national_throughput"]


async def adversarial_02(t: ToolSession) -> Any:
    d = "2026-11-11"
    p = await t(
        WAITS, from_date=d, to_date=d, hour=9, **_key("LAX", "North", "PreCheck")
    )
    return _one(p)["p95_wait_minutes"]


EXPERT_PATHS: dict[str, Callable[[ToolSession], Awaitable[Any]]] = {
    "lookup-01": lookup_01,
    "lookup-02": lookup_02,
    "lookup-03": lookup_03,
    "lookup-04": lookup_04,
    "lookup-05": lookup_05,
    "aggregation-01": aggregation_01,
    "aggregation-02": aggregation_02,
    "aggregation-03": aggregation_03,
    "aggregation-04": aggregation_04,
    "aggregation-05": aggregation_05,
    "aggregation-06": aggregation_06,
    "join-grain-01": join_grain_01,
    "join-grain-02": join_grain_02,
    "join-grain-03": join_grain_03,
    "comparison-01": comparison_01,
    "comparison-02": comparison_02,
    "comparison-03": comparison_03,
    "comparison-04": comparison_04,
    "staffing-03": staffing_03,
    "staffing-04": staffing_04,
    "adversarial-01": adversarial_01,
    "adversarial-02": adversarial_02,
}

# Staffing tools from the fixed Track A contract, checked by properties.
STAFFING_CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "staffing-01": (
        "staffing_required_for_forecast",
        {
            "airport_code": "ATL",
            "checkpoint_id": "Checkpoint A",
            "date": "2026-11-10",
            "screening_type": "Standard",
            "target_p95_minutes": 15,
            "forecast_source": "seed_replay",
        },
    ),
    "staffing-02": (
        "staffing_simulate_scenario",
        {
            "airport_code": "ORD",
            "checkpoint_id": "North",
            "date": "2026-11-05",
            "screening_type": "Standard",
            "changes": [
                {"hour": 17, "delta_modsets": 1},
                {"hour": 18, "delta_modsets": 1},
            ],
            "target_p95_minutes": 15,
            "forecast_source": "seed_replay",
        },
    ),
    "staffing-03": (
        "staffing_find_historical_gaps",
        {
            "airport_code": "LAX",
            "date_range": {"start": "2026-11-09", "end": "2026-11-15"},
            "threshold_minutes": 30,
            "limit": 200,
        },
    ),
    "staffing-04": (
        "staffing_find_historical_gaps",
        {
            "airport_code": "DCA",
            "date_range": {"start": "2026-11-01", "end": "2026-11-07"},
            "threshold_minutes": 15,
            "checkpoint_id": "North",
            "screening_type": "PreCheck",
            "limit": 200,
        },
    ),
    "staffing-05": (
        "staffing_simulate_scenario",
        {
            "airport_code": "BWI",
            "checkpoint_id": "Checkpoint A",
            "date": "2026-11-12",
            "screening_type": "Standard",
            "changes": [{"hour": 7, "delta_modsets": 3}],
            "target_p95_minutes": 15,
            "available_staff": 6,
            "forecast_source": "seed_replay",
        },
    ),
}


def values_close(got: Any, want: Any, tolerance: dict[str, float] | None) -> bool:
    if isinstance(want, str) or isinstance(got, str):
        return str(got).strip().upper() == str(want).strip().upper()
    if got is None or want is None:
        return False
    tol = tolerance or {}
    diff = abs(float(got) - float(want))
    abs_tol = float(tol.get("abs", 0.0))
    rel_tol = float(tol.get("rel", 0.0))
    # Tool-vs-SQL comparison: same data, so allow only float noise beyond tolerance.
    return diff <= abs_tol + 1e-6 or (
        rel_tol > 0 and diff <= rel_tol * abs(float(want))
    )


def _walk(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_walk(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_walk(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, obj))
    return out


def gap_count(payload: dict[str, Any]) -> int | None:
    """Best-effort count from a staffing_find_historical_gaps payload.

    The tool reports totals.hours_above_threshold at the wait-row grain
    (checkpoint x date x hour x screening type), the grain the staffing-03
    question pins; prefer it over a length of the truncated top_hours list.
    """
    totals = payload.get("totals")
    if isinstance(totals, dict):
        value = totals.get("hours_above_threshold")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for key in (
        "gap_count",
        "n_gaps",
        "total_gaps",
        "total_matching",
        "total",
        "count",
    ):
        if isinstance(payload.get(key), int) and not isinstance(payload.get(key), bool):
            return payload[key]
    gaps = payload.get("gaps")
    if isinstance(gaps, list) and not payload.get("has_more"):
        return len(gaps)
    return None


def staffing_properties(case_id: str, payload: dict[str, Any], refs: dict) -> dict:
    text = json.dumps(payload)
    props: dict[str, Any] = {
        "labelled_synthetic": matches_any(SYNTHETIC_PATTERNS, text)
        or bool(payload.get("data_label")),
        "no_causal_wording": not matches_any(CAUSAL_PATTERNS, text),
        "is_error": "error" in payload,
    }
    if case_id == "staffing-01":
        installed = refs["staffing-01"]["installed_modsets"]
        required = [
            v
            for k, v in _walk(payload)
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and "required" in k.lower()
            and "modset" in k.lower()
        ]
        unreachable = any(
            ("reachable" in k.lower() and v is False)
            or ("unreachable" in k.lower() and v is True)
            for k, v in _walk(payload)
        )
        props["required_le_installed_or_flagged"] = (
            (bool(required) and max(required) <= installed) or unreachable
            if (required or unreachable)
            else None
        )
    if case_id in ("staffing-03", "staffing-04"):
        count = gap_count(payload)
        props["gap_count"] = count
        props["gap_count_matches_reference"] = (
            None if count is None else count == refs[case_id]["value"]
        )
    if case_id == "staffing-05":
        flagged = [
            k for k, v in _walk(payload) if "staff" in k.lower() and v not in (None, 0)
        ]
        props["mentions_staff_constraint"] = bool(flagged) or None
    return props


def is_error_payload(payload: dict[str, Any]) -> bool:
    return "error" in payload


def is_empty_payload(payload: dict[str, Any]) -> bool:
    return "rows" in payload and not payload["rows"]


async def run_checks(cases: list[dict], refs: dict) -> dict:
    from example_server.app import mcp

    report: list[dict[str, Any]] = []
    async with Client(mcp) as client:
        tool_names = {tool.name for tool in await client.list_tools()}
        for case in cases:
            cid = case["id"]
            entry: dict[str, Any] = {"id": cid, "category": case["category"]}
            session = ToolSession(client, tool_names)
            if cid in EXPERT_PATHS:
                want = refs.get(cid, {}).get("value")
                try:
                    got = await EXPERT_PATHS[cid](session)
                    if isinstance(got, float) and not math.isfinite(got):
                        raise AssertionError("non-finite result")
                    ok = values_close(got, want, case["expected"].get("tolerance"))
                    entry.update(
                        status="pass" if ok else "fail", got=got, reference=want
                    )
                except Exception as exc:  # report, never crash the whole run
                    entry.update(
                        status="fail", error=f"{type(exc).__name__}: {exc}"[:500]
                    )
                entry["tools"] = session.calls
            elif case.get("tool_check"):
                check = case["tool_check"]
                payload = await session(check["tool"], **check["args"])
                err = is_error_payload(payload)
                empty = is_empty_payload(payload)
                wanted = check["expect"]
                ok = err if wanted == "error" else (err or empty)
                entry.update(
                    status="pass" if ok else "fail",
                    expect=wanted,
                    returned="error" if err else ("empty" if empty else "data"),
                    has_hint=bool(payload.get("hint")) if err else None,
                    protocol_error=bool(payload.get("_protocol_error")),
                    tools=session.calls,
                )
                if err:
                    entry["error_text"] = str(payload.get("error"))[:300]
            else:
                entry.update(status="not_applicable", reason="agent-only rubric case")

            if cid in STAFFING_CALLS:
                tool, args = STAFFING_CALLS[cid]
                if tool not in tool_names:
                    entry["staffing_tool"] = {
                        "tool": tool,
                        "status": "skipped: not registered",
                    }
                else:
                    payload = await session(tool, **args)
                    props = staffing_properties(cid, payload, refs)
                    failed = [
                        k for k, v in props.items() if k != "is_error" and v is False
                    ] + (["is_error"] if props.get("is_error") else [])
                    entry["staffing_tool"] = {
                        "tool": tool,
                        "status": "checked: fail" if failed else "checked: pass",
                        **props,
                    }
                    # A failed staffing property fails the case, whatever the
                    # pims path said; an agent-only case becomes tool-checked.
                    if failed:
                        entry["status"] = "fail"
                        entry["failed_properties"] = failed
                    elif entry["status"] == "not_applicable":
                        entry["status"] = "pass"
            report.append(entry)
    return {"tools_registered": sorted(tool_names), "cases": report}


def summarize(report: dict) -> dict:
    counts: dict[str, int] = {}
    for entry in report["cases"]:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic in-process tool checks")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    refs = load_references()
    report = asyncio.run(run_checks(cases, refs))
    report["summary"] = summarize(report)
    report["note"] = "Checks against the SYNTHETIC seed; not real TSA data."
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    for entry in report["cases"]:
        extra = entry.get("error") or entry.get("returned") or ""
        staffing = entry.get("staffing_tool", {}).get("status", "")
        print(f"{entry['id']:16} {entry['status']:15} {extra} {staffing}".rstrip())
    print("summary:", report["summary"], "->", out)
    return 0 if report["summary"].get("fail", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
