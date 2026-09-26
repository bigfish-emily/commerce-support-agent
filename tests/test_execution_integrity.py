import asyncio
from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel

from app.after_sales.decision import _risk_signals
from app.after_sales.execution_guard import action_fingerprint, order_fingerprint
from app.agent.actions import AgentActions, _terminal_task_indices
from app.llm.semantic_decision import CRITERIA, classify_update
from app.olist.service import OlistService
from app.tool_call.framework import ToolCallContext, ToolCallManager, ToolSpec
from app.tool_call.write_journal import WriteJournal


@pytest.mark.anyio
async def test_coordination_outage_does_not_dispatch_or_quarantine_unstarted_write(tmp_path):
    from types import SimpleNamespace

    async def unavailable(*args):
        raise ConnectionError("redis unavailable")

    calls = []
    spec = ToolSpec(
        name="write",
        description="test",
        input_model=WriteArgs,
        handler=lambda args, ctx: calls.append(args) or {"ok": True},
        side_effect=True,
    )
    path = tmp_path / "journal.db"
    args = {"order_id": "a", "action_type": "refund", "message_text": "test"}
    manager = ToolCallManager(
        [spec], runtime_store=SimpleNamespace(acquire_lock=unavailable), write_journal=WriteJournal(path)
    )
    first = await manager.call("write", args)
    assert first.error_code == "runtime_unavailable"
    assert not calls
    recovered = ToolCallManager([spec], write_journal=WriteJournal(path))
    assert (await recovered.call("write", args)).ok
    assert len(calls) == 1


class WriteArgs(BaseModel):
    order_id: str
    action_type: str
    message_text: str


def test_unknown_risk_is_not_false():
    order = OlistService().get_order_status("203096f03d82e0dffbc41ebc2e2bcfb7")
    assert _risk_signals(order, "refund")["already_refunded"] is None


def test_approval_binding_changes_with_action_and_principal():
    draft = {"order_id": "a", "action_type": "refund_request", "message_text": "one item"}
    state = {"user_id": "alice", "tenant_id": "one"}
    fingerprint = action_fingerprint(draft, state)
    assert fingerprint != action_fingerprint({**draft, "message_text": "two items"}, state)
    assert fingerprint != action_fingerprint(draft, {**state, "user_id": "bob"})
    order = OlistService().get_order_status("203096f03d82e0dffbc41ebc2e2bcfb7")
    assert order_fingerprint(order) != order_fingerprint(replace(order, status="canceled"))


@pytest.mark.anyio
async def test_unknown_write_persists_across_restart_and_rephrasing(tmp_path):
    calls = []

    async def handler(args, ctx):
        calls.append(args)
        raise TimeoutError("remote may have committed")

    spec = ToolSpec(
        name="write", description="test", input_model=WriteArgs, handler=handler, side_effect=True, retries=3
    )
    path = tmp_path / "journal.db"
    args = {"order_id": "a", "action_type": "refund", "message_text": "please"}
    manager = ToolCallManager([spec], write_journal=WriteJournal(path))
    result = await manager.call("write", args, ToolCallContext())
    assert result.error_code == "outcome_unknown"
    assert result.attempts == 1
    restarted = ToolCallManager([spec], write_journal=WriteJournal(path))
    repeated = await restarted.call("write", {**args, "message_text": "again"})
    assert repeated.error_code == "outcome_unknown"
    assert len(calls) == 1


@pytest.mark.anyio
async def test_journal_excludes_concurrent_writes(tmp_path):
    journal = WriteJournal(tmp_path / "journal.db")
    winners = await asyncio.gather(*(asyncio.to_thread(journal.begin, "same") for _ in range(8)))
    assert sum(winners) == 1
    with pytest.raises(ValueError):
        journal.resolve("same", evidence_ref="")
    journal.resolve("same", evidence_ref="provider receipt checked")
    assert journal.begin("same")


@pytest.mark.anyio
async def test_jev_wire_schema_and_actual_model(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-placeholder")
    monkeypatch.setenv("JEV_MODEL", "test-version")

    def handler(request):
        import json

        body = json.loads(request.content)
        assert body["questions"]["update"]["criteria"] == CRITERIA
        return httpx.Response(
            200,
            json={
                "model": "resolved-version",
                "answers": {
                    "update": {
                        "type": "choice",
                        "choice": "modify",
                        "confidence": 0.8,
                        "probabilities": {key: float(key == "modify") for key in CRITERIA},
                    }
                },
                "usage": {"input_tokens": 30, "output_tokens": 5},
            },
        )

    result = await classify_update(
        {"message": "yes, but only one"}, backend="jev", transport=httpx.MockTransport(handler)
    )
    assert result.choice == "modify"
    assert result.model == "resolved-version"
    assert result.input_tokens == 30


@pytest.mark.anyio
async def test_missing_jev_key_is_observed_not_disguised_as_rules(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = await classify_update({"message": "cancel"}, backend="jev")
    assert result.error == "ValueError"
    assert result.backend == "jev"
    assert result.choice == "ambiguous"


@pytest.mark.anyio
async def test_failed_review_execution_is_not_marked_executed():
    from types import SimpleNamespace

    from app.tool_call.framework import ToolCallResult

    actions = object.__new__(AgentActions)
    actions._case_service = None
    actions._olist_service = OlistService()
    actions._runtime_store = None
    review_statuses = []
    actions._case_service = SimpleNamespace(
        mark_review_result=lambda session, status, reviewer_id: review_statuses.append(status)
    )

    async def failed_call(*args):
        return ToolCallResult(
            tool_name="execute_side_effect",
            ok=False,
            error_code="outcome_unknown",
            error_message="unconfirmed",
        )

    actions._tool_manager = SimpleNamespace(call=failed_call)
    draft = {
        "action_type": "refund_request",
        "order_id": "203096f03d82e0dffbc41ebc2e2bcfb7",
        "message_text": "refund",
        "task_index": 1,
    }
    state = {
        "session_id": "test",
        "messages": [],
        "user_response": "__review_approve__",
        "escalation_draft": draft,
    }
    order = actions._olist_service.get_order_status(draft["order_id"])
    draft["execution_binding"] = {
        "action": action_fingerprint(draft, state),
        "order": order_fingerprint(order),
    }
    result = await actions.finalize_escalation(state)
    assert review_statuses == ["outcome_unknown"]
    assert result["completed_tasks"][0]["status"] == "outcome_unknown"
    assert _terminal_task_indices(result["completed_tasks"]) == {1}
    assert "已执行" not in result["final_answer"]


@pytest.mark.anyio
async def test_changed_order_is_blocked_before_write():
    from types import SimpleNamespace

    actions = object.__new__(AgentActions)
    actions._case_service = None
    order = OlistService().get_order_status("203096f03d82e0dffbc41ebc2e2bcfb7")
    actions._olist_service = SimpleNamespace(get_order_status=lambda _: replace(order, status="canceled"))
    draft = {"order_id": order.order_id, "action_type": "refund_request", "message_text": "refund"}
    state = {"session_id": "test"}
    draft["execution_binding"] = {
        "action": action_fingerprint(draft, state),
        "order": order_fingerprint(order),
    }
    answer = await actions._execute_escalation_draft(draft, state)
    assert draft["execution_status"] == "failed"
    assert "原确认不可继续使用" in answer
