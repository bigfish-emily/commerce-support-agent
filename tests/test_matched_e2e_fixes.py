import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm.customer_presenter import present
from app.llm.intent_planner import IntentPlanner
from app.llm.json_fallback import json_instruction
from app.llm.types import TaskPlanResult


def test_json_prompt_contains_nested_schema():
    instruction = json_instruction(TaskPlanResult)
    assert '"$defs"' in instruction
    assert '"depends_on"' in instruction
    assert '"order_filter"' in instruction


@pytest.mark.anyio
async def test_planner_receives_selected_order_and_preserves_request():
    llm = SimpleNamespace(model="deepseek-flash", ainvoke=AsyncMock(return_value=SimpleNamespace(
        content=json.dumps({"tasks": [{"intent": "escalation", "action_type": "invoice_request"}]})
    )))
    result = await IntentPlanner(llm, allow_fast_path=False).plan(
        "登记开票申请", context={"selected_order_id": "a" * 32}
    )
    assert result.tasks[0].text == "登记开票申请"
    assert "a" * 32 in str(llm.ainvoke.call_args)


@pytest.mark.anyio
async def test_compound_reply_keeps_read_results_alongside_receipt():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
        content="订单已送达但有延误，可以申请核查。退款核查申请已受理。"
    )))
    state = {"completed_tasks": [{"intent": "order_status"}, {"intent": "escalation"}],
             "answer_parts": ["delivered", "延误可申请核查"], "final_answer": "完整结果"}
    reply = await present(llm, "查物流然后申请退款", state,
                          {"status": "executed", "action_type": "refund_request"})
    assert "已送达" in reply
    assert "verified_action_receipt" in str(llm.ainvoke.call_args)


@pytest.mark.anyio
async def test_committed_application_does_not_prove_money_movement():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="退款成功，已到账。")))
    reply = await present(llm, "查物流然后退款", {"completed_tasks": [{"intent": "order_status"}]},
                          {"status": "executed", "action_type": "refund_request"})
    assert "退款成功" not in reply
