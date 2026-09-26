import copy

import pytest

from evaluation.attribution import paired, rate, score
from evaluation.attribution_faults import binding_rows, journal_rows, verifier_rows


def fixture():
    case = {
        "tasks": ["order_status", "policy", "escalation"],
        "disposition": "handoff",
        "reviewer_action": "approve",
        "action_type": "refund_request",
        "projection": {"refund_status": "requested"},
    }
    row = {
        "events": [
            {"node": "execute_read_task", "intent": task, "status": "completed"}
            for task in ("policy", "order_status")
        ],
        "disposition": "handoff",
        "action_type": "refund_request",
        "event_count": 1,
        "pre_review_events": 0,
        "projection": {"refund_status": "requested"},
        "execution_error": None,
    }
    return case, row


def test_score_allows_read_reordering_and_ignores_reply_wording():
    case, row = fixture()
    row["answer"] = "Different but valid wording"
    assert score(case, row)["task_success"]


def test_success_requires_actual_event_projection_and_no_early_write():
    case, good = fixture()
    for changes in (
        {"event_count": 0},
        {"projection": {}},
        {"pre_review_events": 1},
        {"execution_error": "TimeoutError"},
    ):
        row = copy.deepcopy(good)
        row.update(changes)
        assert not score(case, row)["task_success"]


def test_paired_keeps_regressions_and_empty_denominator():
    rows = [
        {"case_id": "a", "arm": "before", "task_success": True},
        {"case_id": "a", "arm": "after", "task_success": False},
    ]
    assert paired(rows, "before", "after")["regressed"] == 1
    assert rate(0, 0)["rate"] is None
    assert rate(0, 10)["wilson95"][0] >= 0


@pytest.mark.anyio
async def test_fault_ablation_has_healthy_controls():
    rows = verifier_rows() + await binding_rows() + await journal_rows()
    assert all(row["passed"] for row in rows if row["enabled"])
    assert all(row["passed"] for row in rows if row["fault"] == "healthy")
    assert any(not row["passed"] for row in rows if not row["enabled"])
