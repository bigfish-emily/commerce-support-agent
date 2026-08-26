import pytest

from app.llm.guardrail import Guardrail
from app.llm.intent_planner import IntentPlanner
from app.llm.response_generator import OlistTaskExtractor, PolicyResponseGenerator, QaResponseGenerator


class _FakeStructuredLlm:
    async def ainvoke(self, messages):
        raise RuntimeError("llm down")


class _FakeChatLlm:
    def with_structured_output(self, model):
        return _FakeStructuredLlm()

    async def ainvoke(self, messages):
        raise RuntimeError("llm down")


@pytest.mark.anyio
async def test_intent_planner_classify_falls_back_to_heuristic() -> None:
    planner = IntentPlanner(_FakeChatLlm())
    result = await planner.classify("查订单 203096f03d82e0dffbc41ebc2e2bcfb7 状态")
    assert result.intent == "order_status"


@pytest.mark.anyio
async def test_intent_planner_falls_back_to_multi_task_rules() -> None:
    planner = IntentPlanner(_FakeChatLlm())
    result = await planner.plan("查订单状态，并且说明退款政策，然后我要升级人工")
    assert [task.intent for task in result.tasks] == ["order_status", "policy", "escalation"]


@pytest.mark.anyio
async def test_guardrail_falls_back_to_deterministic_checks() -> None:
    guardrail = Guardrail(_FakeChatLlm())
    assert (await guardrail.check_input("退款政策是什么")).on_topic is True
    assert (await guardrail.check_input("帮我写一个操作系统内核")).on_topic is False
    assert (await guardrail.check_output("订单状态正常")).valid is True
    assert (await guardrail.check_output("")).valid is False


@pytest.mark.anyio
async def test_task_extractor_falls_back_to_regex() -> None:
    extractor = OlistTaskExtractor(_FakeChatLlm())
    result = await extractor.extract("订单 203096F0 3D82 E0DF FBC41EBC2E2BCFB7 延迟了吗")
    assert result.order_id == "203096f03d82e0dffbc41ebc2e2bcfb7"


@pytest.mark.anyio
async def test_answer_generators_fall_back_to_grounded_templates() -> None:
    qa = QaResponseGenerator(_FakeChatLlm())
    qa_answer = await qa.generate(
        "health_beauty 风险",
        [{"name": "health_beauty", "price": 1.0, "description": "delay_rate=10%", "rules": "rule"}],
    )
    assert "结构化运营事实" in qa_answer
    assert "health_beauty" in qa_answer

    policy = PolicyResponseGenerator(_FakeChatLlm())
    policy_answer = await policy.generate(
        "退款政策是什么",
        [{"section_title": "Refund Policy", "source": "support_policy.md", "text": "refund rules"}],
    )
    assert "Refund Policy" in policy_answer
