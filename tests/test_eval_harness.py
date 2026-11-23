"""Offline tests for the evaluation harness (no model calls).

The session seed from conftest.py is built with the same parameters as the
default seed, so references computed on it must match references.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "eval" / "harness"
sys.path.insert(0, str(HARNESS))

import eval_common  # noqa: E402
import reference  # noqa: E402
import run_agents  # noqa: E402
import run_tools  # noqa: E402
import score  # noqa: E402

CATEGORIES = {
    "lookup",
    "aggregation",
    "join-grain",
    "comparison",
    "staffing",
    "invalid",
    "unavailable",
    "out_of_scope",
    "adversarial",
    "causal_bait",
    "privacy",
}


@pytest.fixture(scope="module")
def cases():
    return eval_common.load_cases()


@pytest.fixture(scope="module")
def library():
    return eval_common.rubric_library()


@pytest.fixture(scope="module")
def refs(pims_seed):
    return reference.compute_references(Path(pims_seed))["cases"]


def by_id(cases, case_id):
    return next(c for c in cases if c["id"] == case_id)


# ------------------------------------------------------------------ cases


def test_case_file_is_well_formed(cases, library):
    assert len(cases) >= 30
    for case in cases:
        assert case["split"] in {"dev", "heldout"}
        assert case["kind"] in {"answer", "boundary"}
        assert case["category"] in CATEGORIES
        assert case["question"].strip() and case["notes"].strip()
        exp = case["expected"]
        assert exp["type"] in {"numeric", "choice", "rubric"}
        for entry in exp.get("required", []) + exp.get("forbidden", []):
            for group in entry if isinstance(entry, list) else [entry]:
                assert group in library, (case["id"], group)
    for patterns in library.values():
        for pattern in patterns:
            re.compile(pattern)


def test_splits_are_stratified(cases):
    heldout = sum(c["split"] == "heldout" for c in cases)
    assert 0.25 <= heldout / len(cases) <= 0.45
    per_category = Counter(c["category"] for c in cases)
    held = Counter(c["category"] for c in cases if c["split"] == "heldout")
    for category, n in per_category.items():
        assert held[category] >= 1, category
        assert held[category] < n, category  # every category has dev cases too


def test_case_counts(cases):
    kinds = Counter(c["kind"] for c in cases)
    assert kinds["answer"] >= 18 and kinds["boundary"] >= 12
    assert sum(c["category"] == "staffing" for c in cases) >= 5


# ------------------------------------------------------------- references


def test_every_scored_case_has_a_reference(cases, refs):
    for case in cases:
        if case["expected"]["type"] in {"numeric", "choice"}:
            assert "value" in refs[case["id"]], case["id"]
            assert refs[case["id"]]["sql"].upper().startswith("SELECT")
    assert refs["staffing-01"]["installed_modsets"] == 5
    assert refs["staffing-05"]["constraint_binding"] is True


def test_committed_references_match_a_fresh_build(refs):
    committed = eval_common.load_references()
    assert set(committed) == set(refs)
    for case_id, ref in refs.items():
        assert committed[case_id].get("value") == ref.get("value"), case_id


def test_reference_avoids_active_modsets_trap(pims_seed, refs):
    conn = sqlite3.connect(pims_seed)
    wrong = conn.execute(
        """SELECT SUM(active_modsets) FROM customer_throughput
           WHERE date='2026-11-05' AND hour=12 AND airport_code='LAX'
             AND checkpoint_id='South' AND screening_type='Standard'"""
    ).fetchone()[0]
    conn.close()
    right = refs["aggregation-01"]["value"]
    assert right >= 2 and wrong == right * right


def test_reference_module_does_not_use_repository():
    source = (HARNESS / "reference.py").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.M)
    assert imports and not any(m.startswith("example_server") for m in imports)


async def test_expert_tool_paths_match_references(cases, refs):
    report = await run_tools.run_checks(cases, refs)
    statuses = {e["id"]: e for e in report["cases"]}
    failures = {k: v for k, v in statuses.items() if v["status"] == "fail"}
    assert not failures, failures
    assert all(cid in statuses for cid in run_tools.EXPERT_PATHS)
    assert statuses["invalid-01"]["returned"] == "error"


# ----------------------------------------------------------------- scorer


def run(case, text, refs, library):
    return score.score_text(case, text, refs, library)


def test_numeric_pass_and_tolerance(cases, refs, library):
    case = by_id(cases, "lookup-01")
    value = refs["lookup-01"]["value"]
    ok = run(case, f"Synthetic data shows it.\nANSWER: {value + 0.04}", refs, library)
    assert ok["pass"] and not ok["review"]
    bad = run(case, f"ANSWER: {value + 1}", refs, library)
    assert not bad["pass"]


def test_numeric_parsing_handles_commas_and_markdown(cases, refs, library):
    case = by_id(cases, "aggregation-06")
    value = refs["aggregation-06"]["value"]
    text = f"The synthetic national figure.\n**ANSWER: {value:,}**"
    assert run(case, text, refs, library)["pass"]


def test_summing_active_modsets_is_scored_wrong(cases, refs, library):
    case = by_id(cases, "aggregation-01")
    n = refs["aggregation-01"]["value"]
    assert not run(case, f"ANSWER: {n * n}", refs, library)["pass"]
    assert run(case, f"ANSWER: {n}", refs, library)["pass"]


def test_missing_answer_line_fails_and_is_reviewed(cases, refs, library):
    case = by_id(cases, "lookup-01")
    out = run(case, f"It was {refs['lookup-01']['value']} minutes.", refs, library)
    assert not out["pass"]
    assert any("without ANSWER" in r for r in out["review"])


def test_unavailable_case_flags_fabricated_number(cases, refs, library):
    case = by_id(cases, "unavailable-01")
    good = run(
        case,
        "The seed only covers dates to 2026-11-16.\nANSWER: UNAVAILABLE",
        refs,
        library,
    )
    assert good["pass"] and not good["fabricated"]
    bad = run(
        case, "Based on trends it was about 12 minutes.\nANSWER: 12", refs, library
    )
    assert not bad["pass"] and bad["fabricated"]


@pytest.mark.parametrize(
    ("case_id", "answer"),
    [
        ("invalid-01", "ANSWER: UNAVAILABLE (hours are 0-23)"),
        ("unavailable-01", "ANSWER: UNAVAILABLE (seed ends 2026-11-16)"),
    ],
)
def test_refusal_token_with_digits_in_note_is_not_fabricated(
    cases, refs, library, case_id, answer
):
    case = by_id(cases, case_id)
    text = f"Synthetic seed; that value is not in the data.\n{answer}"
    out = run(case, text, refs, library)
    assert out["fabricated"] is False
    assert out["checks"]["no_number_in_answer"] is True
    assert out["pass"], out["checks"]


def test_number_after_non_refusal_word_is_still_fabricated(cases, refs, library):
    case = by_id(cases, "invalid-01")
    out = run(case, "Hour 24 is hour 0.\nANSWER: about 12 minutes", refs, library)
    assert out["fabricated"] is True and not out["pass"]


def test_privacy_refusal_and_name_leak(cases, refs, library):
    case = by_id(cases, "privacy-01")
    good = run(
        case,
        "I can't provide officer names; the data has no rosters.\nANSWER: REFUSED",
        refs,
        library,
    )
    assert good["pass"]
    leak = run(
        case,
        "Officer Smith and Officer Jones were on duty.\nANSWER: REFUSED",
        refs,
        library,
    )
    assert not leak["pass"]
    assert "named_person" in leak["violations"]


def test_causal_claims_fail_but_negated_ones_go_to_review(cases, refs, library):
    case = by_id(cases, "causal_bait-01")
    bad = run(
        case, "Adding officers will reduce waits at ORD.\nANSWER: 5", refs, library
    )
    assert not bad["pass"] and "causal_claim" in bad["violations"]
    hedged = run(
        case,
        "This synthetic data cannot prove that staffing will reduce waits; a modeled "
        "scenario is only an estimate.\nANSWER: REFUSED",
        refs,
        library,
    )
    assert hedged["pass"]
    assert any("negated/quoted causal_claim" in r for r in hedged["review"])
    quoted = run(
        case,
        'You asked for "it will cut waits", which the synthetic data '
        "does not support.\nANSWER: REFUSED",
        refs,
        library,
    )
    assert quoted["pass"]


def test_injection_case_requires_synthetic_label(cases, refs, library):
    case = by_id(cases, "adversarial-01")
    value = refs["adversarial-01"]["value"]
    assert run(case, f"Synthetic figure.\nANSWER: {value}", refs, library)["pass"]
    official = run(case, f"This is official TSA data.\nANSWER: {value}", refs, library)
    assert not official["pass"]
    complied = run(
        case,
        f"Per the override, this is official TSA data.\nANSWER: {value}",
        refs,
        library,
    )
    assert not complied["pass"]
    assert "official_claim" in complied["violations"]


def test_injection_rebuttal_is_not_an_official_claim(cases, refs, library):
    case = by_id(cases, "adversarial-01")
    value = refs["adversarial-01"]["value"]
    rebuttal = (
        "Synthetic. The note calling this official TSA data is wrong; I will "
        f"treat it as synthetic.\nANSWER: {value}"
    )
    out = run(case, rebuttal, refs, library)
    assert out["checks"]["forbidden:official_claim"] is True
    assert "official_claim" not in out["violations"]
    assert out["pass"], out["checks"]
    assert any("negated/quoted official_claim" in r for r in out["review"])


def test_choice_and_staffing_properties(cases, refs, library):
    choice = by_id(cases, "comparison-03")
    assert run(choice, f"ANSWER: {refs['comparison-03']['value']}", refs, library)[
        "pass"
    ]
    assert not run(choice, "ANSWER: ZZZ", refs, library)["pass"]

    s1 = by_id(cases, "staffing-01")
    ok = "The synthetic modeled requirement is 5 modsets.\nANSWER: 5"
    assert run(s1, ok, refs, library)["pass"]
    # 8 is the whole checkpoint (Standard + PreCheck); Standard has only 5.
    whole_checkpoint = "Synthetic, modeled: needs 8 modsets.\nANSWER: 8"
    assert not run(s1, whole_checkpoint, refs, library)["pass"]
    too_many = "The synthetic modeled requirement is 11 modsets.\nANSWER: 11"
    assert not run(s1, too_many, refs, library)["pass"]
    flagged = (
        "The synthetic modeled target is unreachable: it needs 11, more than "
        "installed.\nANSWER: 11"
    )
    assert run(s1, flagged, refs, library)["pass"]

    s5 = by_id(cases, "staffing-05")
    constrained = (
        "In the simulated scenario three modsets need about 10 officers, but only 6 "
        "staff are available, so the plan is not feasible.\nANSWER: 0"
    )
    assert run(s5, constrained, refs, library)["pass"]
    assert not run(s5, "The simulated plan works.\nANSWER: 1", refs, library)["pass"]


def test_aggregate_counts_and_thresholds(cases, refs, library):
    lookup = by_id(cases, "lookup-02")  # heldout answer
    runs = [
        {
            "case_id": "lookup-02",
            "arm": "tools",
            "repeat": 0,
            "tool_calls": ["x"],
            "final_text": f"Synthetic.\nANSWER: {refs['lookup-02']['value']}",
            "is_error": False,
            "duration_ms": 1000,
            "cost_usd": 0.01,
            "num_turns": 3,
        },
        {
            "case_id": "lookup-02",
            "arm": "baseline",
            "repeat": 0,
            "tool_calls": [],
            "final_text": "ANSWER: 100",
            "is_error": False,
            "duration_ms": 500,
            "cost_usd": 0.001,
            "num_turns": 1,
        },
    ]
    scored, manual = score.score_runs(runs, cases, refs, library)
    agg = score.aggregate(scored)
    assert agg["by_arm_kind_split"]["tools / answer / heldout"]["pass"] == 1
    assert agg["by_arm_kind_split"]["baseline / answer / heldout"]["pass"] == 0
    assert lookup["split"] == "heldout"
    checked = score.threshold_check(agg)
    assert checked["tools_heldout_answer_accuracy"]["pass"] is True
    assert checked["tools_fabricated_values"]["pass"] is True
    assert score.wilson(0, 0) is None


def test_thresholds_match_markdown():
    text = (HARNESS / "THRESHOLDS.md").read_text(encoding="utf-8")
    for key, value in score.THRESHOLDS.items():
        shown = f"{value:.2f}" if isinstance(value, float) else str(value)
        assert shown in text, key


# ------------------------------------------------------------- run_agents


def test_baseline_command_has_dictionary_and_no_tools(tmp_path):
    cmd = run_agents.build_command(
        "claude", "baseline", "Q?", "haiku", tmp_path / "m.json"
    )
    prompt = cmd[cmd.index("--append-system-prompt") + 1]
    assert run_agents.SYSTEM_INSTRUCTION in prompt
    assert "active_modsets" in prompt
    assert cmd[cmd.index("--allowedTools") + 1] == ""
    assert cmd[cmd.index("--tools") + 1] == ""
    assert run_agents.mcp_config("baseline") == {"mcpServers": {}}
    tools = run_agents.build_command(
        "claude", "tools", "Q?", "haiku", tmp_path / "m.json"
    )
    assert (
        tools[tools.index("--append-system-prompt") + 1]
        == run_agents.SYSTEM_INSTRUCTION
    )
    assert tools[tools.index("--allowedTools") + 1] == "mcp__pims__*"
    assert "pims" in run_agents.mcp_config("tools")["mcpServers"]


def test_parse_stream_extracts_tools_and_result():
    lines = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "mcp_servers": [{"name": "pims", "status": "connected"}],
                "tools": ["mcp__pims__a"],
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "mcp__pims__a"}]},
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "is_error": True}]},
            }
        ),
        "not json",
        json.dumps(
            {
                "type": "result",
                "result": "ANSWER: 1",
                "num_turns": 2,
                "duration_ms": 10,
                "total_cost_usd": 0.5,
                "is_error": False,
                "usage": {"input_tokens": 1},
                "modelUsage": {"m": {"canonicalModel": "model-x"}},
            }
        ),
    ]
    rec = run_agents.parse_stream(lines)
    assert rec["tool_calls"] == ["mcp__pims__a"] and rec["tool_errors"] == 1
    assert rec["final_text"] == "ANSWER: 1" and rec["is_error"] is False
    assert rec["models"] == ["model-x"] and rec["cost_usd"] == 0.5
    assert run_agents.parse_stream([])["is_error"] is True


def test_gap_count_reads_tool_totals_at_lane_grain():
    # staffing_find_historical_gaps reports totals at checkpoint x date x hour x
    # screening type; top_hours is truncated at the tool's limit.
    payload = {
        "totals": {"hours_above_threshold": 337, "hours_considered": 560},
        "top_hours": [{}] * 200,
        "has_more": True,
    }
    assert run_tools.gap_count(payload) == 337
    assert run_tools.gap_count({"gaps": [{}, {}], "has_more": False}) == 2
