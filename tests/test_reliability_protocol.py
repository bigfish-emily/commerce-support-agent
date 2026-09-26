import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.after_sales.execution_guard import action_fingerprint, order_fingerprint
from app.agent.actions import AgentActions
from app.olist.service import OlistService, SQLiteCaseService
from app.review_recovery import ReviewCommandStore
from app.tool_call.framework import InMemoryRuntimeStore, ToolCallContext, ToolCallManager, ToolSpec

ORDER = "203096f03d82e0dffbc41ebc2e2bcfb7"


class Empty(BaseModel):
    pass


def pending(cases, expires):
    return cases.submit_for_review(
        action_type="refund_request",
        order_id=ORDER,
        message_text="refund",
        session_id="s",
        user_id="u",
        expires_at=expires,
    )["record"]


def test_timeout_enqueued_without_customer_request_and_survives_restart(tmp_path):
    cases = SQLiteCaseService(tmp_path / "cases.db")
    pending(cases, time.time() - 10)
    queue = ReviewCommandStore(cases.path)
    assert queue.enqueue_expired(time.time()) == 1
    assert queue.enqueue_expired(time.time()) == 0
    restarted = ReviewCommandStore(cases.path)
    command = restarted.claim()
    assert command["payload"]["resume_value"] == "__hitl_timeout__"
    assert queue.claim() is None
    restarted.finish(command)
    assert queue.claim() is None


def test_expired_worker_cannot_overwrite_new_owner(tmp_path):
    cases = SQLiteCaseService(tmp_path / "cases.db")
    pending(cases, 0)
    queue = ReviewCommandStore(cases.path)
    queue.enqueue_expired(time.time())
    old = queue.claim(lease=-1)
    new = queue.claim()
    assert not queue.renew(old)
    queue.finish(old)
    assert queue.renew(new)
    queue.finish(new)


@pytest.mark.anyio
async def test_cache_isolates_principals_and_order_grants():
    async def handler(args, ctx):
        return {"orders": ctx.allowed_order_ids}

    manager = ToolCallManager(
        [
            ToolSpec(
                name="owned", description="owned", input_model=Empty, handler=handler, cache_ttl_seconds=30
            )
        ]
    )
    first = ToolCallContext(user_id="a", allowed_order_ids=["a"])
    second = ToolCallContext(user_id="b", allowed_order_ids=["b"])
    assert (await manager.call("owned", {}, first)).data["orders"] == ["a"]
    assert (await manager.call("owned", {}, second)).data["orders"] == ["b"]
    assert (await manager.call("owned", {}, first)).cached
    first.allowed_order_ids = ["c"]
    assert (await manager.call("owned", {}, first)).data["orders"] == ["c"]


@pytest.mark.anyio
async def test_lease_renewal_requires_live_owner():
    store = InMemoryRuntimeStore()
    token = await store.acquire_lock("x", 3)
    assert await store.renew_lock("x", token, 10)
    assert not await store.renew_lock("x", "other", 10)
    await store.release_lock("x", token)
    assert not await store.renew_lock("x", token, 10)


@pytest.mark.anyio
async def test_parallel_reads_overlap_and_merge_in_plan_order():
    actions = object.__new__(AgentActions)
    entered = set()
    both = asyncio.Event()
    actions.extract_slots = AsyncMock(return_value={})

    async def retrieve(state):
        entered.add(state["current_task_index"])
        if len(entered) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1)
        return {}

    async def execute(state):
        idx = state["current_task_index"]
        return {"completed_tasks": [{"task_index": idx, "status": "completed"}], "answer_parts": [str(idx)]}

    actions.retrieve_context = retrieve
    actions.execute_read_task = execute
    result = await actions.execute_ready_reads(
        {
            "task_plan": [{"intent": "policy", "text": "a"}, {"intent": "order_status", "text": "b"}],
            "messages": [],
        }
    )
    assert entered == {0, 1}
    assert result["answer_parts"] == ["0", "1"]


@pytest.mark.anyio
async def test_committed_receipt_recovers_before_stale_order_check(tmp_path):
    cases = SQLiteCaseService(tmp_path / "cases.db")
    service = OlistService(projection_store=cases)
    order = service.get_order_status(ORDER)
    actions = object.__new__(AgentActions)
    actions._case_service = cases
    actions._olist_service = service
    actions._tool_manager = SimpleNamespace(call=AsyncMock(side_effect=AssertionError("must not reexecute")))
    state = {"session_id": "s"}
    draft = dict(action_type="cancel_order", order_id=ORDER, message_text="cancel")
    draft["execution_binding"] = dict(action=action_fingerprint(draft, state), order=order_fingerprint(order))
    cases.execute_action("cancel_order", ORDER, "cancel")
    assert cases.count_order_events(ORDER) == 1
    await actions._execute_escalation_draft(draft, state)
    assert draft["execution_status"] == "completed"
    assert cases.count_order_events(ORDER) == 1


@pytest.mark.anyio
async def test_real_stdio_policy_mcp_in_product_tool_manager(monkeypatch, tmp_path):
    from app.olist.knowledge import MarkdownKnowledgeBase
    from app.tool_call.framework import build_business_tool_manager

    monkeypatch.setenv("POLICY_TOOL_TRANSPORT", "mcp_stdio")
    manager = build_business_tool_manager(
        olist_service=OlistService(),
        knowledge_base=MarkdownKnowledgeBase(),
        support_retriever=None,
        case_service=SQLiteCaseService(tmp_path / "c.db"),
    )
    result = await manager.call(
        "search_policy_knowledge",
        {"query": "退款政策", "k": 3},
        ToolCallContext(role="customer", auth_scopes=["policy:read"]),
    )
    assert result.ok and not result.error_code, result
    assert result.data["sections"]


@pytest.mark.anyio
async def test_timeout_worker_recovers_real_sqlite_checkpoint_after_restart(monkeypatch, tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, StateGraph

    import app.main as main
    from app.agent.state import AgentState

    cases = SQLiteCaseService(tmp_path / "cases.db")
    record = pending(cases, time.time() - 10)
    queue = ReviewCommandStore(cases.path)
    actions = object.__new__(AgentActions)
    actions._case_service = cases
    actions._runtime_store = None
    builder = StateGraph(AgentState)
    builder.add_node("await", actions.await_confirmation)
    builder.add_node("finalize", actions.finalize_escalation)
    builder.set_entry_point("await")
    builder.add_edge("await", "finalize")
    builder.add_edge("finalize", END)
    config = {"configurable": {"thread_id": "s"}}
    path = str(tmp_path / "checkpoint.db")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke(
            {
                "session_id": "s",
                "messages": [],
                "final_answer": "pending",
                "pending_side_effect": {
                    "requires_confirmation": True,
                    "expires_at": record["expires_at"],
                    "task_index": 1,
                },
                "escalation_draft": {"review_case_id": record["case_id"], "action_type": "refund_request"},
            },
            config,
        )
        assert (await graph.aget_state(config)).next
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        monkeypatch.setattr(main, "agent", builder.compile(checkpointer=saver))
        monkeypatch.setattr(main, "case_service", cases)
        monkeypatch.setattr(main, "review_commands", queue)
        monkeypatch.setattr(main, "record_trace", lambda **kwargs: None)
        monkeypatch.setattr(main, "_append_customer_review_update", AsyncMock())
        queue.enqueue_expired(time.time())
        await main._run_review_command(queue.claim())
        assert cases.get_by_session("s")["status"] == "timeout_canceled"
        assert not (await main.agent.aget_state(config)).next
        assert cases.count_order_events(ORDER) == 0
        assert queue.claim() is None


@pytest.mark.anyio
async def test_old_review_command_never_approves_next_case(monkeypatch, tmp_path):
    import app.main as main

    cases = SQLiteCaseService(tmp_path / "cases.db")
    first = pending(cases, time.time() + 100)
    cases.execute_action("refund_request", ORDER, "refund")
    other = cases.submit_for_review(
        action_type="invoice_request",
        order_id=ORDER,
        message_text="invoice",
        session_id="s",
        user_id="u",
        expires_at=time.time() + 100,
    )["record"]
    graph = SimpleNamespace(
        aget_state=AsyncMock(
            return_value=SimpleNamespace(
                values={"pending_side_effect": {"review_case_id": other["case_id"]}}, next=("await",)
            )
        ),
        ainvoke=AsyncMock(side_effect=AssertionError("old approval must not resume new case")),
    )
    monkeypatch.setattr(main, "agent", graph)
    monkeypatch.setattr(main, "case_service", cases)
    await main._apply_review_command(
        "s",
        resume_value="__review_approve__",
        status="review_approved",
        reviewer_id="r",
        expected_case_id=first["case_id"],
    )
    assert cases.get(other["case_id"])["status"] == "pending_review"


@pytest.mark.anyio
async def test_unlock_outage_does_not_erase_successful_receipt():
    class BrokenRelease(InMemoryRuntimeStore):
        async def release_lock(self, key, token):
            raise ConnectionError("redis down after handler")

    manager = ToolCallManager(
        [
            ToolSpec(
                name="write",
                description="write",
                input_model=Empty,
                side_effect=True,
                handler=lambda args, ctx: {"receipt": "ok"},
            )
        ],
        runtime_store=BrokenRelease(),
    )
    result = await manager.call("write", {})
    assert result.ok and result.data["receipt"] == "ok"
