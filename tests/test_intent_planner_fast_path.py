import pytest

from app.llm.intent_planner import _fast_plan, fallback_plan

ORDER_ID = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    "message",
    [
        f"订单 {ORDER_ID} 想做一次普通售后跟进，请帮我创建服务工单。",
        f"请为订单 {ORDER_ID} 记录一个常规客服跟进事项。",
        f"我想咨询订单 {ORDER_ID} 的后续服务，请建立普通售后工单。",
    ],
)
def test_explicit_support_case_phrases_use_write_fast_path(message: str) -> None:
    plan = _fast_plan(message)
    assert plan is not None
    assert plan.planning_mode == "deterministic_fast_path"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].intent == "escalation"
    assert plan.tasks[0].action_type == "open_support_case"


def test_fallback_preserves_explicit_policy_before_refund_write() -> None:
    plan = fallback_plan(
        "先查订单 027a84a16c22c706669f35d578853f3e 的物流，再说明退款条件，然后提交退款申请。"
    )

    assert [task.intent for task in plan.tasks] == ["order_status", "policy", "escalation"]
    assert plan.tasks[-1].action_type == "refund_request"
