import json

import pytest

from evaluation.after_sales_quality_gate import build_fixture, evaluate, write_live_holdout_fixture
from evaluation.e2e.after_sales_e2e_bench import (
    EvaluationCase,
    _error_row,
    _planner_provenance_from_trace,
    _planning_mode_from_trace,
    _score,
    _summary,
)


def test_quality_fixture_has_versioned_capability_coverage() -> None:
    rows = build_fixture()
    assert len(rows) == 250
    assert len({row.order_id for row in rows}) == 250
    capabilities = {row.capability for row in rows}
    assert {
        "decision_auto_execute",
        "decision_reject",
        "decision_clarify",
        "hitl_routing",
        "multi_intent",
        "input_guard",
    } <= capabilities


def test_live_holdout_has_balanced_e2e_strata(tmp_path) -> None:
    source = tmp_path / "quality.jsonl"
    source.write_text(
        "\n".join(json.dumps(row.__dict__, ensure_ascii=False) for row in build_fixture()) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "holdout.jsonl"
    rows = write_live_holdout_fixture(source, output)
    assert len(rows) == 50
    assert {row["stratum"] for row in rows} == {
        "quality_safe_auto",
        "quality_reject",
        "quality_refund_handoff",
        "quality_invoice_handoff",
        "quality_multi_intent",
    }


@pytest.mark.anyio
async def test_quality_gate_exposes_component_metrics(tmp_path) -> None:
    fixture = tmp_path / "quality.jsonl"
    fixture.write_text(
        "\n".join(json.dumps(row.__dict__, ensure_ascii=False) for row in build_fixture()) + "\n",
        encoding="utf-8",
    )
    report = await evaluate(fixture)
    assert report["decision_engine"]["disposition_accuracy"]["total"] == 194
    assert report["verifier"]["fault_detection"]["rate"] == 1.0
    assert report["tool_governance"]["sqlite_idempotent_write"]["rate"] == 1.0


def test_live_eval_keeps_provider_errors_in_the_report() -> None:
    case = EvaluationCase(
        case_id="provider-timeout",
        stratum="quality_safe_auto",
        message="订单 0123456789abcdef0123456789abcdef 创建普通服务工单。",
        tasks=("escalation",),
        action_type="open_support_case",
        disposition="execute",
        reviewer_action="none",
        projection=(("support_status", "opened"),),
    )
    row = _score(
        case,
        _error_row(case, stage="langgraph_workflow", latency_ms=12000, exc=TimeoutError("gateway timeout")),
    )
    summary = _summary([row], "langgraph_workflow")
    assert row["crs"] is False
    assert summary["completed_cases"] == 0
    assert summary["execution_error_rate"] == 100.0


def test_live_eval_reads_planner_provenance_from_trace() -> None:
    payload = {
        "traces": [
            {
                "trajectory_json": json.dumps(
                    [{"node": "plan_tasks", "details": {"planning_mode": "deterministic_fallback"}}]
                )
            }
        ]
    }
    assert _planning_mode_from_trace(payload) == "deterministic_fallback"
    assert _planner_provenance_from_trace(payload) == {
        "planning_mode": "deterministic_fallback",
        "llm_attempted": False,
        "fallback_reason": None,
    }
