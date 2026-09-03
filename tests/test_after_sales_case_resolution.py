import pytest

from app.after_sales import AfterSalesDecisionEngine
from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import InMemoryCaseService, OlistService
from app.retrieval.hybrid import HybridSupportRetriever
from app.tool_call import ToolCallContext, build_business_tool_manager

ORDER_ID = "203096f03d82e0dffbc41ebc2e2bcfb7"


def test_after_sales_engine_requires_hitl_for_refund_case() -> None:
    service = OlistService()
    order = service.get_order_status(ORDER_ID)
    assert order is not None
    policies = MarkdownKnowledgeBase().search("退款 延迟 补偿 人工确认", k=3)
    case = AfterSalesDecisionEngine().assess(
        action_type="refund_request",
        order=order,
        user_request=f"我的订单 {ORDER_ID} 晚到很多天，我要退款",
        policy_sections=[
            {
                "section_title": hit.section_title,
                "source": hit.source,
                "text": hit.text,
                "score": hit.score,
            }
            for hit in policies
        ],
    )

    assert case.decision.action_type == "refund_request"
    assert case.decision.outcome == "needs_human_review"
    assert case.decision.requires_human is True
    assert "delay_days=11" in case.decision.evidence
    assert case.verification.required_next_step == "hitl"
    assert "不会承诺退款" in case.customer_reply


def test_after_sales_engine_rejects_delivered_order_cancellation() -> None:
    service = OlistService()
    order = service.get_order_status(ORDER_ID)
    assert order is not None

    case = AfterSalesDecisionEngine().assess(
        action_type="cancel_order",
        order=order,
        user_request=f"取消订单 {ORDER_ID}",
        policy_sections=[{"section_title": "Address Change Policy"}],
    )

    assert case.decision.outcome == "reject"
    assert case.decision.requires_human is False
    assert "cancel_order" in case.decision.blocked_actions
    assert case.verification.required_next_step == "stop"
    assert "不能直接执行" in case.customer_reply


@pytest.mark.anyio
async def test_tool_manager_exposes_after_sales_case_assessment() -> None:
    manager = build_business_tool_manager(
        olist_service=OlistService(),
        knowledge_base=MarkdownKnowledgeBase(),
        support_retriever=HybridSupportRetriever(),
        case_service=InMemoryCaseService(),
    )

    result = await manager.call(
        "assess_after_sales_case",
        {
            "action_type": "refund_request",
            "order_id": ORDER_ID,
            "user_request": f"给订单 {ORDER_ID} 申请退款",
            "policy_sections": [{"section_title": "Compensation Boundary Policy"}],
        },
        ToolCallContext(role="support_agent"),
    )

    assert result.ok
    assert result.data["found"] is True
    assert result.data["decision"]["outcome"] == "needs_human_review"
    assert result.data["verification"]["required_next_step"] == "hitl"
