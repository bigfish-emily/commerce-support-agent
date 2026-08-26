from app.mcp_server import (
    draft_escalation,
    generate_after_sales_priority_report,
    get_order_status,
    search_category_risk,
)

ORDER_ID = "203096f03d82e0dffbc41ebc2e2bcfb7"


def test_mcp_order_status_tool() -> None:
    result = get_order_status(ORDER_ID)
    assert ORDER_ID in result
    assert "delivered" in result


def test_mcp_category_risk_tool() -> None:
    result = search_category_risk("health beauty 类目")
    assert result[0]["name"] == "health_beauty"


def test_mcp_after_sales_priority_report_tool() -> None:
    result = generate_after_sales_priority_report("生成售后运营风险日报")
    assert result["high_risk_categories"]
    assert result["priority_orders"]
    assert "HITL" in result["decision_rules"][-1]


def test_mcp_escalation_tool() -> None:
    result = draft_escalation(ORDER_ID)
    assert result["found"] is True
    assert result["order_id"] == ORDER_ID
