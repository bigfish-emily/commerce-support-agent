from app.olist.service import InMemoryCaseService, OlistService, format_after_sales_report


def test_after_sales_priority_report_is_ranked_and_read_only() -> None:
    service = OlistService()
    report = service.after_sales_priority_report("生成售后运营风险日报")

    categories = report["high_risk_categories"]
    orders = report["priority_orders"]

    assert categories
    assert orders
    assert [item["risk_score"] for item in categories] == sorted(
        [item["risk_score"] for item in categories],
        reverse=True,
    )
    assert [item["priority_score"] for item in orders] == sorted(
        [item["priority_score"] for item in orders],
        reverse=True,
    )
    assert any("HITL" in rule for rule in report["decision_rules"])

    rendered = format_after_sales_report(report)
    assert "高风险类目 Top" in rendered
    assert "优先跟进订单 Top" in rendered
    assert "只读运营建议" in rendered


def test_case_service_returns_duplicate_for_same_business_action() -> None:
    service = InMemoryCaseService()
    first = service.execute_action(
        action_type="refund_request",
        order_id="203096f03d82e0dffbc41ebc2e2bcfb7",
        message_text="delivery delayed by 11 day(s); low review score 2",
    )
    second = service.execute_action(
        action_type="refund_request",
        order_id="203096f03d82e0dffbc41ebc2e2bcfb7",
        message_text="delivery delayed by 11 day(s); low review score 2",
    )

    assert first["result_id"] == second["result_id"]
    assert first["duplicate"] is False
    assert second["duplicate"] is True
