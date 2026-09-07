import asyncio
import os

# Must be set BEFORE importing app — LlmClient reads OPENAI_API_KEY at import time.
os.environ["OPENAI_API_KEY"] = "test-key"

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import _resolve_auth_scopes, app

ORDER_ID = "203096f03d82e0dffbc41ebc2e2bcfb7"


@pytest.fixture(autouse=True)
def setup_graph_and_state() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    import app.main as main_module
    from app.config.di import agent_graph_builder, case_service

    main_module.agent = agent_graph_builder.build(InMemorySaver())
    case_service.reset()


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_web_console_available(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "E-Commerce After-Sales Case Agent" in response.text
    assert "/observability/summary" in response.text


def _mock_guard(input_on_topic: bool, output_valid: bool = True):
    from app.llm.types import InputGuardResult, OutputGuardResult

    async def check_input(self, message: str, history=None) -> InputGuardResult:
        reason = "ok" if input_on_topic else "off-topic"
        return InputGuardResult(on_topic=input_on_topic, reason=reason)

    async def check_output(self, answer: str) -> OutputGuardResult:
        return OutputGuardResult(valid=output_valid, reason="ok" if output_valid else "invalid")

    return (
        patch("app.llm.guardrail.Guardrail.check_input", check_input),
        patch("app.llm.guardrail.Guardrail.check_output", check_output),
    )


def _mock_plan(*intents: str):
    from app.llm.types import PlannedTask, TaskPlanResult

    tasks = [
        PlannedTask(
            intent=intent,
            text="",
            side_effect=(intent == "escalation"),
            action_type="open_support_case" if intent == "escalation" else "none",
        )
        for intent in intents
    ]
    return patch(
        "app.llm.intent_planner.IntentPlanner.plan",
        AsyncMock(return_value=TaskPlanResult(tasks=tasks)),
    )


def _mock_plan_action(action_type: str):
    from app.llm.types import PlannedTask, TaskPlanResult

    task = PlannedTask(
        intent="escalation",
        text="",
        side_effect=True,
        action_type=action_type,
    )
    return patch(
        "app.llm.intent_planner.IntentPlanner.plan",
        AsyncMock(return_value=TaskPlanResult(tasks=[task])),
    )


def _mock_qa_answer(response: str):
    return patch("app.llm.response_generator.QaResponseGenerator.generate", AsyncMock(return_value=response))


def _mock_policy_answer(response: str):
    return patch(
        "app.llm.response_generator.PolicyResponseGenerator.generate",
        AsyncMock(return_value=response),
    )


def _mock_task(order_id: str = ORDER_ID, category: str = "health_beauty"):
    from app.llm.types import OlistTaskResult

    result = OlistTaskResult(order_id=order_id, category=category, user_goal="support")
    return patch("app.llm.response_generator.OlistTaskExtractor.extract", AsyncMock(return_value=result))


def _operator_payload(message: str, session_id: str) -> dict[str, object]:
    return {
        "message": message,
        "session_id": session_id,
        "role": "after_sales_operator",
        "auth_scopes": ["*"],
    }


def _ops_payload(message: str, session_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "message": message,
        "role": "ops_manager",
    }
    if session_id:
        payload["session_id"] = session_id
    return payload


def test_chat_default_scopes_are_role_aware() -> None:
    support_scopes = _resolve_auth_scopes("support_agent", None)
    ops_scopes = _resolve_auth_scopes("ops_manager", None)
    operator_scopes = _resolve_auth_scopes("after_sales_operator", None)

    assert "after_sales:draft" in support_scopes
    assert "after_sales:write" not in support_scopes
    assert "after_sales:ops_report" in ops_scopes
    assert "after_sales:write" not in ops_scopes
    assert "after_sales:write" in operator_scopes
    assert _resolve_auth_scopes("after_sales_operator", []) == []


@pytest.mark.anyio
async def test_qa_category_insights(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("qa"), _mock_qa_answer("LLM: health_beauty has delay and review risk"):
        response = await client.post("/chat", json={"message": "health_beauty 类目有什么运营风险？"})
    assert response.status_code == 200
    assert "health_beauty" in response.json()["answer"]
    assert response.json()["sources"] == ["health_beauty"]


@pytest.mark.anyio
async def test_after_sales_ops_decision_report(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("ops_decision"):
        response = await client.post(
            "/chat",
            json=_ops_payload("生成售后运营风险日报，列出优先跟进类目和订单"),
        )
    assert response.status_code == 200
    body = response.json()
    assert "[售后运营决策]" in body["answer"]
    assert "高风险类目" in body["answer"]
    assert "优先跟进订单" in body["answer"]
    assert "HITL" in body["answer"]
    assert body["sources"]


@pytest.mark.anyio
async def test_offline_after_sales_ops_decision_report(client: AsyncClient) -> None:
    response = await client.post(
        "/chat",
        json=_ops_payload("生成售后运营风险日报，列出优先跟进类目和订单"),
    )
    assert response.status_code == 200
    assert "[售后运营决策]" in response.json()["answer"]


@pytest.mark.anyio
async def test_order_status_lookup(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("order_status"), _mock_task():
        response = await client.post("/chat", json={"message": f"帮我查订单 {ORDER_ID} 状态"})
    assert response.status_code == 200
    body = response.json()["answer"]
    assert ORDER_ID in body
    assert "delivered" in body
    assert "延迟 11 天" in body
    assert "评价分：2" in body


@pytest.mark.anyio
async def test_policy_question_uses_policy_knowledge(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("policy"), _mock_policy_answer("LLM: compensation needs human approval"):
        response = await client.post("/chat", json={"message": "退款补偿能不能直接承诺？"})
    assert response.status_code == 200
    assert "human approval" in response.json()["answer"]
    assert "Compensation Boundary Policy" in response.json()["sources"]


@pytest.mark.anyio
async def test_escalation_first_turn_requires_confirmation(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        response = await client.post(
            "/chat",
            json={"message": f"订单 {ORDER_ID} 延迟且低分，生成客服跟进话术", "session_id": "s1"},
        )
    assert response.status_code == 200
    assert "创建售后工单" in response.json()["answer"]
    assert "是否确认执行" in response.json()["answer"]


@pytest.mark.anyio
async def test_default_support_agent_cannot_execute_confirmed_side_effect(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        await client.post(
            "/chat",
            json={"message": f"订单 {ORDER_ID} 延迟且低分，生成客服跟进话术", "session_id": "s-deny"},
        )
    with g1, g2:
        response = await client.post("/chat", json={"message": "yes", "session_id": "s-deny"})

    assert response.status_code == 200
    assert "未执行创建售后工单" in response.json()["answer"]
    assert "permission_denied" in response.json()["answer"] or "not allowed" in response.json()["answer"]


@pytest.mark.anyio
async def test_escalation_second_turn_confirm(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        await client.post(
            "/chat",
            json=_operator_payload(f"订单 {ORDER_ID} 延迟且低分，生成客服跟进话术", "s2"),
        )
    with g1, g2:
        response = await client.post("/chat", json={"message": "yes", "session_id": "s2"})
    assert response.status_code == 200
    assert "已执行创建售后工单" in response.json()["answer"]
    assert "CASE-" in response.json()["answer"]


@pytest.mark.anyio
async def test_confirmed_hitl_session_can_accept_new_policy_task(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        await client.post(
            "/chat",
            json=_operator_payload(f"订单 {ORDER_ID} 延迟且低分，生成客服跟进话术", "s2-next"),
        )
    with g1, g2:
        confirm_response = await client.post("/chat", json={"message": "yes", "session_id": "s2-next"})
    assert confirm_response.status_code == 200

    with g1, g2, _mock_plan("policy"), _mock_policy_answer("LLM: compensation requires approval"):
        response = await client.post(
            "/chat",
            json={"message": "退款补偿能不能直接承诺？", "session_id": "s2-next"},
        )
    assert response.status_code == 200
    assert "[政策问答]" in response.json()["answer"]
    assert "order_id" not in response.json()["answer"]


@pytest.mark.anyio
async def test_escalation_second_turn_cancel(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        await client.post(
            "/chat",
            json={"message": f"订单 {ORDER_ID} 延迟且低分，生成客服跟进话术", "session_id": "s3"},
        )
    with g1, g2:
        response = await client.post("/chat", json={"message": "no", "session_id": "s3"})
    assert response.status_code == 200
    assert "已取消创建售后升级 case" in response.json()["answer"]


@pytest.mark.anyio
async def test_escalation_confirmation_timeout_cancels_pending_action(
    client: AsyncClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HITL_TIMEOUT_SECONDS", "1")
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        await client.post(
            "/chat",
            json={"message": f"订单 {ORDER_ID} 延迟且低分，生成客服跟进话术", "session_id": "timeout-s1"},
        )

    await asyncio.sleep(1.1)
    with g1, g2:
        response = await client.post("/chat", json={"message": "yes", "session_id": "timeout-s1"})
    assert response.status_code == 200
    assert "已超时自动取消" in response.json()["answer"]
    assert "CASE-" not in response.json()["answer"]


@pytest.mark.anyio
async def test_pending_hilt_does_not_consume_unrelated_message(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        await client.post(
            "/chat",
            json={"message": f"订单 {ORDER_ID} 延迟且低分，生成客服跟进话术", "session_id": "pending-s1"},
        )
    with g1, g2:
        response = await client.post(
            "/chat",
            json={"message": "health beauty 类目有什么运营风险？", "session_id": "pending-s1"},
        )
    assert response.status_code == 200
    assert "待确认" in response.json()["answer"]
    assert "换一个 Session ID" in response.json()["answer"]


@pytest.mark.anyio
async def test_off_topic_rejected_by_guardrail(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=False)
    with g1, g2:
        response = await client.post("/chat", json={"message": "帮我写一个操作系统内核"})
    assert response.status_code == 200
    assert "only help" in response.json()["answer"]


@pytest.mark.anyio
async def test_chat_rate_limit_uses_runtime_store(
    client: AsyncClient,
    monkeypatch,
) -> None:
    import app.main as main_module
    from app.tool_call import InMemoryRuntimeStore

    monkeypatch.setattr(main_module, "runtime_store", InMemoryRuntimeStore())
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MINUTE", "1")
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("policy"), _mock_policy_answer("LLM: policy answer"):
        first = await client.post("/chat", json={"message": "退款政策是什么？", "session_id": "rate-s1"})
        second = await client.post("/chat", json={"message": "退款政策是什么？", "session_id": "rate-s2"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert "请求过于频繁" in second.json()["answer"]


@pytest.mark.anyio
async def test_empty_message_rejected_by_pydantic(client: AsyncClient) -> None:
    response = await client.post("/chat", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_session_id_preserved(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("qa"), _mock_qa_answer("LLM: hello"):
        response = await client.post("/chat", json={"message": "Hi", "session_id": "my-session-123"})
    assert response.status_code == 200
    assert response.json()["session_id"] == "my-session-123"


@pytest.mark.anyio
async def test_multi_task_plan_executes_read_only_tasks_before_escalation(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with (
        g1,
        g2,
        _mock_plan("order_status", "policy", "escalation"),
        _mock_task(),
        _mock_policy_answer("LLM: refund policy says confirm first"),
    ):
        response = await client.post(
            "/chat",
            json={
                "message": f"查订单 {ORDER_ID} 状态，并且说明退款政策，然后生成售后升级话术",
                "session_id": "multi-task",
            },
        )
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "[订单查询]" in answer
    assert "[政策问答]" in answer
    assert "[售后升级]" in answer
    assert "是否确认执行" in answer


@pytest.mark.anyio
async def test_offline_multi_intent_escalation_inherits_order_context(client: AsyncClient) -> None:
    response = await client.post(
        "/chat",
        json={
            "message": f"查订单 {ORDER_ID} 状态，并且说明退款政策，然后生成售后升级话术",
            "session_id": "offline-context-carry",
        },
    )
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "[订单查询]" in answer
    assert "[政策问答]" in answer
    assert "[售后升级]" in answer
    assert "是否确认执行" in answer
    assert "没有检测到有效 order_id" not in answer


@pytest.mark.anyio
async def test_executor_defers_side_effect_until_read_only_tasks_finish(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with (
        g1,
        g2,
        _mock_plan("escalation", "policy"),
        _mock_task(),
        _mock_policy_answer("LLM: refund policy says confirm first"),
    ):
        response = await client.post(
            "/chat",
            json={
                "message": f"给订单 {ORDER_ID} 申请退款，并且说明退款政策",
                "session_id": "side-effect-ordering",
            },
        )
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert answer.index("[政策问答]") < answer.index("[售后升级]")
    assert "是否确认执行" in answer


@pytest.mark.anyio
async def test_refund_side_effect_uses_refund_tool(client: AsyncClient) -> None:
    from app.llm.types import PlannedTask, TaskPlanResult

    g1, g2 = _mock_guard(input_on_topic=True)
    plan = TaskPlanResult(
        tasks=[
            PlannedTask(
                intent="escalation",
                text=f"给订单 {ORDER_ID} 申请退款",
                side_effect=True,
                action_type="refund_request",
            )
        ]
    )
    with (
        g1,
        g2,
        patch("app.llm.intent_planner.IntentPlanner.plan", AsyncMock(return_value=plan)),
        _mock_task(),
    ):
        await client.post(
            "/chat",
            json=_operator_payload(f"给订单 {ORDER_ID} 申请退款", "refund-task"),
        )
    with g1, g2:
        response = await client.post("/chat", json={"message": "确认", "session_id": "refund-task"})
    assert response.status_code == 200
    assert "REFUND-" in response.json()["answer"]


@pytest.mark.anyio
async def test_delivered_order_cancel_is_rejected_before_hitl(client: AsyncClient) -> None:
    from app.llm.types import PlannedTask, TaskPlanResult

    g1, g2 = _mock_guard(input_on_topic=True)
    plan = TaskPlanResult(
        tasks=[
            PlannedTask(
                intent="escalation",
                text=f"取消订单 {ORDER_ID}",
                side_effect=True,
                action_type="cancel_order",
            )
        ]
    )
    with (
        g1,
        g2,
        patch("app.llm.intent_planner.IntentPlanner.plan", AsyncMock(return_value=plan)),
        _mock_task(),
    ):
        response = await client.post(
            "/chat",
            json={"message": f"取消订单 {ORDER_ID}", "session_id": "delivered-cancel"},
        )

    answer = response.json()["answer"]
    assert "售后 case 决策" in answer
    assert "结论：reject" in answer
    assert "本轮不会执行" in answer
    assert "是否确认执行" not in answer


@pytest.mark.anyio
async def test_multiple_escalations_continue_after_first_confirmation(client: AsyncClient) -> None:
    from app.llm.types import PlannedTask, TaskPlanResult

    g1, g2 = _mock_guard(input_on_topic=True)
    plan = TaskPlanResult(
        tasks=[
            PlannedTask(
                intent="escalation",
                text=f"给订单 {ORDER_ID} 申请退款",
                side_effect=True,
                action_type="refund_request",
            ),
            PlannedTask(
                intent="escalation",
                text=f"给订单 {ORDER_ID} 申请发票",
                side_effect=True,
                action_type="invoice_request",
            ),
        ]
    )
    with (
        g1,
        g2,
        patch("app.llm.intent_planner.IntentPlanner.plan", AsyncMock(return_value=plan)),
        _mock_task(),
    ):
        first = await client.post(
            "/chat",
            json=_operator_payload(f"给订单 {ORDER_ID} 申请退款，再申请发票", "two-effects"),
        )
    assert first.status_code == 200
    assert "提交退款/补偿申请" in first.json()["answer"]

    with g1, g2:
        second = await client.post("/chat", json={"message": "yes", "session_id": "two-effects"})
    assert second.status_code == 200
    assert "已执行提交退款/补偿申请" in second.json()["answer"]
    assert "提交发票申请" in second.json()["answer"]
    assert "是否确认执行" in second.json()["answer"]

    with g1, g2:
        third = await client.post("/chat", json={"message": "yes", "session_id": "two-effects"})
    assert third.status_code == 200
    assert "已执行提交发票申请" in third.json()["answer"]
    assert "INV-" in third.json()["answer"]


@pytest.mark.anyio
async def test_chat_trace_contains_trajectory_events(client: AsyncClient, tmp_path, monkeypatch) -> None:
    import app.trace_store as trace_store
    from app.trace_store import list_session_traces

    monkeypatch.setattr(trace_store, "TRACE_DB", tmp_path / "agent_traces.db")
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("policy"), _mock_policy_answer("LLM: policy answer"):
        response = await client.post("/chat", json={"message": "退款政策是什么？", "session_id": "trace-s1"})

    assert response.status_code == 200
    traces = list_session_traces("trace-s1")
    assert traces
    assert "plan_tasks" in traces[0]["trajectory_json"]
    assert "search_policy_knowledge" in traces[0]["trajectory_json"]


@pytest.mark.anyio
async def test_observability_case_metrics_endpoint(client: AsyncClient, tmp_path, monkeypatch) -> None:
    import app.trace_store as trace_store

    monkeypatch.setattr(trace_store, "TRACE_DB", tmp_path / "traces.db")
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan("escalation"), _mock_task():
        await client.post(
            "/chat",
            json={"message": f"给订单 {ORDER_ID} 申请退款", "session_id": "metrics-s1"},
        )

    response = await client.get("/observability/case-metrics")
    body = response.json()
    assert response.status_code == 200
    assert body["total_cases"] == 1
    assert body["hitl_rate"] == 1.0
    assert body["policy_hit_rate"] == 1.0


@pytest.mark.anyio
async def test_chat_complaint_escalation_enters_after_sales_case_lifecycle(client: AsyncClient) -> None:
    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2, _mock_plan_action("complaint_escalation"), _mock_task():
        response = await client.post(
            "/chat",
            json=_operator_payload(f"我要投诉升级订单 {ORDER_ID} 的延迟问题", "complaint-s1"),
        )

    body = response.json()
    assert response.status_code == 200
    assert "售后 case 决策" in body["answer"]
    assert "提交投诉升级工单" in body["answer"]
    assert "是否确认执行" in body["answer"]

    g1, g2 = _mock_guard(input_on_topic=True)
    with g1, g2:
        confirmed = await client.post("/chat", json={"message": "yes", "session_id": "complaint-s1"})
    assert confirmed.status_code == 200
    assert "COMP-" in confirmed.json()["answer"]
