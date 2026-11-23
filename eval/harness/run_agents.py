"""Headless agent runs over the evaluation cases, via the `claude` CLI.

Two arms receive the same system instruction and the same question:

  tools     the MCP server over stdio (only mcp__pims__* tools allowed,
            built-in tools disabled)
  baseline  no MCP server and no tools; the system instruction is followed by
            the schema data dictionary, so the model knows the tables but has
            no data rows

    uv run python eval/harness/run_agents.py --arm both --split dev --repeats 1
    uv run python eval/harness/run_agents.py --cases lookup-01,invalid-01

Writes eval/runs/<run_id>/results.jsonl (one line per run), raw/ (the CLI's
stream-json output per run) and manifest.json. eval/runs/ is gitignored: the
manifest records the model id the CLI reports, which must not be committed.
All data the agents see is SYNTHETIC.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS_DIR))

from eval_common import (  # noqa: E402
    CASES_PATH,
    REPO_ROOT,
    RUNS_DIR,
    load_cases,
    seed_content_sha256,
)

SYSTEM_INSTRUCTION = """\
You are assisting a TSA checkpoint operations analyst with questions about \
checkpoint passenger throughput, wait times and screening staffing.
Use only data you can actually retrieve in this session. If you cannot retrieve \
what the question needs, say so instead of estimating or inventing values.
End your reply with one final line in exactly this form:
ANSWER: <value>
where <value> is a plain number (no units, no thousands separators; percentages \
in percent points, e.g. -1.25), an airport code when asked which airport, \
UNAVAILABLE if the data cannot be retrieved or the input is invalid, or REFUSED \
if you decline the request."""

ARMS = ("tools", "baseline")


def baseline_instruction() -> str:
    from example_server.pims.schema import DATA_DICTIONARY

    return (
        SYSTEM_INSTRUCTION
        + "\n\nData dictionary for the checkpoint dataset (schema only):\n"
        + json.dumps(DATA_DICTIONARY, indent=1)
    )


def mcp_config(arm: str, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    if arm == "baseline":
        return {"mcpServers": {}}
    return {
        "mcpServers": {
            "pims": {
                "command": "uv",
                "args": ["run", "--directory", str(repo_root), "python", "main.py"],
            }
        }
    }


def build_command(
    claude: str, arm: str, question: str, model: str, mcp_path: Path
) -> list[str]:
    instruction = SYSTEM_INSTRUCTION if arm == "tools" else baseline_instruction()
    allowed = "mcp__pims__*" if arm == "tools" else ""
    return [
        claude,
        "-p",
        question,
        "--append-system-prompt",
        instruction,
        "--mcp-config",
        str(mcp_path),
        "--strict-mcp-config",
        "--tools",
        "",
        "--allowedTools",
        allowed,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--disable-slash-commands",
    ]


def parse_stream(lines: list[str]) -> dict[str, Any]:
    """Extract the record fields from the CLI's stream-json output."""
    record: dict[str, Any] = {
        "tool_calls": [],
        "tool_errors": 0,
        "final_text": None,
        "num_turns": None,
        "duration_ms": None,
        "duration_api_ms": None,
        "cost_usd": None,
        "usage": None,
        "models": [],
        "is_error": True,
        "subtype": None,
        "mcp_servers": None,
        "tools_available": None,
    }
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            record["mcp_servers"] = event.get("mcp_servers")
            record["tools_available"] = event.get("tools")
        elif kind == "assistant":
            for block in event.get("message", {}).get("content", []) or []:
                if block.get("type") == "tool_use":
                    record["tool_calls"].append(block.get("name"))
        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result" and block.get("is_error"):
                        record["tool_errors"] += 1
        elif kind == "result":
            usage = event.get("modelUsage") or {}
            record.update(
                final_text=event.get("result"),
                num_turns=event.get("num_turns"),
                duration_ms=event.get("duration_ms"),
                duration_api_ms=event.get("duration_api_ms"),
                cost_usd=event.get("total_cost_usd"),
                usage=event.get("usage"),
                models=sorted({v.get("canonicalModel") or k for k, v in usage.items()}),
                is_error=bool(event.get("is_error")),
                subtype=event.get("subtype"),
            )
    return record


def run_one(
    case: dict[str, Any],
    arm: str,
    repeat: int,
    args: argparse.Namespace,
    claude: str,
    mcp_paths: dict[str, Path],
    raw_dir: Path,
    workdir: Path,
) -> dict[str, Any]:
    cmd = build_command(claude, arm, case["question"], args.model, mcp_paths[arm])
    started = datetime.now(timezone.utc)
    stdout, stderr, returncode = "", "", None
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
            env=os.environ.copy(),
        )
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = f"timeout after {args.timeout}s"
    raw_path = raw_dir / f"{case['id']}__{arm}__r{repeat}.jsonl"
    raw_path.write_text(stdout or "", encoding="utf-8")
    record = parse_stream((stdout or "").splitlines())
    record.update(
        case_id=case["id"],
        arm=arm,
        repeat=repeat,
        split=case["split"],
        kind=case["kind"],
        category=case["category"],
        model_alias=args.model,
        returncode=returncode,
        stderr_tail=(stderr or "")[-500:],
        started_utc=started.isoformat(),
        wall_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        raw_file=str(raw_path.relative_to(raw_dir.parent)),
    )
    if returncode not in (0, None) or record["final_text"] is None:
        record["is_error"] = True
    return record


def _git(*parts: str) -> str:
    try:
        return subprocess.run(
            ["git", *parts],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_path() -> Path:
    default = REPO_ROOT / "data" / "synthetic" / "pims" / "pims_pmis_synthetic.sqlite"
    return Path(os.environ.get("PIMS_DB_PATH", str(default))).resolve()


def select_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_cases()
    if args.split != "all":
        cases = [c for c in cases if c["split"] == args.split]
    if args.cases:
        wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
        known = {c["id"] for c in load_cases()}
        unknown = sorted(set(wanted) - known)
        if unknown:
            raise SystemExit(f"unknown case ids: {', '.join(unknown)}")
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless agent eval runs")
    parser.add_argument("--arm", choices=["tools", "baseline", "both"], default="both")
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="haiku", help="CLI model alias")
    parser.add_argument("--cases", help="comma-separated case ids (within --split)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=600, help="seconds per run")
    parser.add_argument("--out", help="run directory (default eval/runs/<run_id>)")
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("--repeats and --concurrency must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("claude CLI not found on PATH")
    cases = select_cases(args)
    if not cases:
        raise SystemExit("no cases selected")
    arms = ARMS if args.arm == "both" else (args.arm,)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = (Path(args.out) if args.out else RUNS_DIR / run_id).resolve()
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    mcp_paths = {}
    for arm in arms:
        mcp_paths[arm] = out_dir / f"mcp_{arm}.json"
        mcp_paths[arm].write_text(json.dumps(mcp_config(arm)), encoding="utf-8")
    # Run from an empty directory outside the repo so no project files or
    # CLAUDE.md are visible to the agent.
    workdir = Path(tempfile.mkdtemp(prefix="pims-eval-"))

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "note": "Agent runs over the SYNTHETIC PIMS/PMIS seed; not real TSA data.",
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "seed_path": str(seed_path()),
        "seed_content_sha256": seed_content_sha256(seed_path()),
        "cases_sha256": _sha256(CASES_PATH),
        "system_instruction_sha256": hashlib.sha256(
            SYSTEM_INSTRUCTION.encode()
        ).hexdigest(),
        "model_alias": args.model,
        "cli_version": subprocess.run(
            [claude, "--version"], capture_output=True, text=True
        ).stdout.strip(),
        "args": vars(args),
        "arms": list(arms),
        "case_ids": [c["id"] for c in cases],
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    jobs = [(c, arm, r) for r in range(args.repeats) for arm in arms for c in cases]
    lock = threading.Lock()
    results_path = out_dir / "results.jsonl"
    models: set[str] = set()
    errors = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(run_one, c, arm, r, args, claude, mcp_paths, raw_dir, workdir)
            for c, arm, r in jobs
        ]
        for done, future in enumerate(as_completed(futures), 1):
            record = future.result()
            with lock:
                with results_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
                models.update(record.get("models") or [])
                errors += int(bool(record["is_error"]))
            print(
                f"[{done}/{len(jobs)}] {record['case_id']} {record['arm']} "
                f"r{record['repeat']} error={record['is_error']} "
                f"turns={record['num_turns']} tools={len(record['tool_calls'])}",
                flush=True,
            )
    shutil.rmtree(workdir, ignore_errors=True)

    manifest.update(
        finished_utc=datetime.now(timezone.utc).isoformat(),
        runs=len(jobs),
        runs_with_error=errors,
        canonical_models=sorted(models),
    )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
