from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.olist.catalog import category_risks_by_name, full_orders_by_id, load_dataset, load_order_facts_index
from app.olist.retrieval import adaptive_category_retrieval


@dataclass(frozen=True)
class OrderStatusView:
    order_id: str
    status: str
    customer_state: str
    purchase_timestamp: str
    estimated_delivery_date: str
    delivered_customer_date: str
    delay_days: int | None
    payment_value: float
    review_score: int | None
    category_summary: str


class OlistService:
    def __init__(self) -> None:
        self._by_id = full_orders_by_id()
        self._category_risks = category_risks_by_name()

    def metadata(self) -> dict:
        metadata = dict(load_dataset()["metadata"])
        metadata["full_order_count"] = load_order_facts_index().get("metadata", {}).get("order_count")
        metadata["category_risk_count"] = len(self._category_risks)
        return metadata

    def find_order_id(self, text: str) -> str | None:
        match = re.search(r"[0-9a-f]{32}", text.lower())
        return match.group(0) if match else None

    def get_order_status(self, order_id: str) -> OrderStatusView | None:
        order = self._by_id.get(order_id)
        if order is None:
            return None
        categories = [p["category"] for p in order["products"]]
        return OrderStatusView(
            order_id=order["order_id"],
            status=order["status"],
            customer_state=order["customer_state"],
            purchase_timestamp=order["purchase_timestamp"],
            estimated_delivery_date=order["estimated_delivery_date"],
            delivered_customer_date=order["delivered_customer_date"],
            delay_days=order["delay_days"],
            payment_value=order["payment_value"],
            review_score=order["review_score"],
            category_summary="、".join(sorted(set(categories))),
        )

    def category_insights(self, query: str) -> list[dict[str, object]]:
        hits = adaptive_category_retrieval(query, sorted(self._category_risks))
        matched_categories = [hit.category for hit in hits if hit.category in self._category_risks]
        if matched_categories:
            categories = matched_categories[:4]
        else:
            categories = sorted(
                self._category_risks,
                key=lambda name: int(self._category_risks[name]["order_count"]),
                reverse=True,
            )[:4]

        insights: list[dict[str, object]] = []
        for cat in categories:
            risk = self._category_risks[cat]
            insights.append(
                {
                    "name": cat,
                    "price": float(risk["avg_payment_value"]),
                    "description": (
                        f"{cat} category has {risk['order_count']} full-data orders, "
                        f"delay_rate={risk['delay_rate']:.2%}, "
                        f"low_review_rate={risk['low_review_rate']:.2%}, "
                        f"cancellation_rate={risk['cancellation_rate']:.2%}."
                    ),
                    "rules": (
                        "Use order_status for exact order facts; use escalation for delayed "
                        "or low-review follow-up."
                    ),
                    "sample_order_ids": risk["sample_order_ids"][:3],
                }
            )
        return insights

    def after_sales_priority_report(
        self,
        query: str = "",
        top_categories: int = 5,
        top_orders: int = 8,
    ) -> dict[str, object]:
        category_rows = []
        for category, risk in self._category_risks.items():
            order_count = int(risk["order_count"])
            risk_score = _ops_risk_score(risk)
            category_rows.append(
                {
                    "category": category,
                    "risk_score": risk_score,
                    "order_count": order_count,
                    "delay_rate": risk["delay_rate"],
                    "low_review_rate": risk["low_review_rate"],
                    "cancellation_rate": risk["cancellation_rate"],
                    "avg_delay_days": risk.get("avg_delay_days", 0),
                    "sample_order_ids": risk["sample_order_ids"][:3],
                    "recommended_action": _category_action(risk),
                }
            )
        high_risk_categories = sorted(
            category_rows,
            key=lambda item: (float(item["risk_score"]), int(item["order_count"])),
            reverse=True,
        )[:top_categories]

        order_rows = []
        for order in self._by_id.values():
            priority_score, reasons = _order_priority(order)
            if priority_score <= 0:
                continue
            order_rows.append(
                {
                    "order_id": order["order_id"],
                    "priority_score": priority_score,
                    "status": order["status"],
                    "category_summary": "、".join(sorted({p["category"] for p in order["products"]})),
                    "delay_days": order["delay_days"],
                    "review_score": order["review_score"],
                    "payment_value": order["payment_value"],
                    "reasons": reasons,
                    "recommended_action": _order_action(order),
                }
            )
        priority_orders = sorted(
            order_rows,
            key=lambda item: (float(item["priority_score"]), float(item["payment_value"] or 0)),
            reverse=True,
        )[:top_orders]

        return {
            "query": query,
            "summary": (
                f"Identified {len(high_risk_categories)} high-risk categories and "
                f"{len(priority_orders)} priority after-sales orders from Olist facts."
            ),
            "high_risk_categories": high_risk_categories,
            "priority_orders": priority_orders,
            "decision_rules": [
                "delay_rate, low_review_rate, cancellation_rate, and order_count drive category priority",
                "delayed/canceled/low-review/high-value orders are ranked for after-sales follow-up",
                "recommended actions are read-only suggestions; refunds/cancellations still require HITL",
            ],
        }

    def escalation_draft(self, order_id: str) -> dict | None:
        status = self.get_order_status(order_id)
        if status is None:
            return None

        reasons = []
        if status.delay_days is not None and status.delay_days > 0:
            reasons.append(f"delivery delayed by {status.delay_days} day(s)")
        if status.review_score is not None and status.review_score <= 2:
            reasons.append(f"low review score {status.review_score}")
        if status.status == "canceled":
            reasons.append("order was canceled")
        if not reasons:
            reasons.append("customer follow-up requested")

        message = (
            f"您好，我们正在跟进您的订单 {order_id[:8]}。"
            f"当前状态为 {status.status}，类目：{status.category_summary}。"
            f"本次跟进原因：{'; '.join(reasons)}。"
            "我们会核查物流与售后记录，并在确认后提供补偿或后续处理方案。"
        )
        return {
            "order_id": order_id,
            "reason": "; ".join(reasons),
            "message_text": message,
        }


class InMemoryCaseService:
    def __init__(self) -> None:
        self._cases: dict[str, dict] = {}

    def open_case(self, order_id: str, message_text: str) -> str:
        return self.execute_action(
            action_type="open_support_case",
            order_id=order_id,
            message_text=message_text,
        )

    def execute_action(self, action_type: str, order_id: str, message_text: str) -> str:
        digest = hashlib.sha1(f"{order_id}:{message_text}".encode()).hexdigest()[:10]
        prefixes = {
            "open_support_case": "CASE",
            "refund_request": "REFUND",
            "cancel_order": "CANCEL",
            "change_address": "ADDR",
            "invoice_request": "INV",
        }
        prefix = prefixes.get(action_type, "CASE")
        case_id = f"{prefix}-{digest}"
        self._cases[case_id] = {
            "case_id": case_id,
            "action_type": action_type,
            "order_id": order_id,
            "message_text": message_text,
            "status": "opened",
        }
        return case_id

    def reset(self) -> None:
        self._cases.clear()


    def get(self, case_id: str) -> dict | None:
        return self._cases.get(case_id)


def format_order_status(status: OrderStatusView | None) -> str:
    if status is None:
        return "没有找到该订单。请确认 order_id 是否完整。"
    delay = "暂无延迟信息" if status.delay_days is None else f"延迟 {status.delay_days} 天"
    return (
        f"订单 {status.order_id} 当前状态：{status.status}。\n"
        f"客户州：{status.customer_state}；类目：{status.category_summary}。\n"
        f"下单时间：{status.purchase_timestamp}；预计送达：{status.estimated_delivery_date}；"
        f"实际送达：{status.delivered_customer_date or '未送达'}；{delay}。\n"
        f"支付金额：{status.payment_value:.2f}；"
        f"评价分：{status.review_score if status.review_score is not None else '暂无'}。"
    )


def format_after_sales_report(report: dict[str, object]) -> str:
    categories = report.get("high_risk_categories", [])
    orders = report.get("priority_orders", [])
    lines = ["售后运营决策建议：", str(report.get("summary", "")), "", "高风险类目 Top："]
    for item in categories[:5]:
        lines.append(
            "- {category}: risk_score={risk_score:.3f}, delay={delay_rate:.2%}, "
            "low_review={low_review_rate:.2%}, cancel={cancellation_rate:.2%}; {action}".format(
                category=item["category"],
                risk_score=float(item["risk_score"]),
                delay_rate=float(item["delay_rate"]),
                low_review_rate=float(item["low_review_rate"]),
                cancellation_rate=float(item["cancellation_rate"]),
                action=item["recommended_action"],
            )
        )
    lines.extend(["", "优先跟进订单 Top："])
    for item in orders[:8]:
        lines.append(
            "- {order_id}: score={priority_score:.2f}, status={status}, delay={delay}, "
            "review={review}; {action}".format(
                order_id=item["order_id"],
                priority_score=float(item["priority_score"]),
                status=item["status"],
                delay=item["delay_days"],
                review=item["review_score"],
                action=item["recommended_action"],
            )
        )
    lines.append("")
    lines.append("注意：以上是只读运营建议；退款、取消、改地址、发票和工单创建仍需 HITL 确认。")
    return "\n".join(lines)


def _ops_risk_score(risk: dict) -> float:
    volume_factor = min(int(risk["order_count"]) / 10000, 1.0)
    return round(
        float(risk["delay_rate"]) * 0.35
        + float(risk["low_review_rate"]) * 0.40
        + float(risk["cancellation_rate"]) * 0.15
        + volume_factor * 0.10,
        4,
    )


def _category_action(risk: dict) -> str:
    actions = []
    if float(risk["delay_rate"]) >= 0.08:
        actions.append("check logistics SLA and delayed-order follow-up")
    if float(risk["low_review_rate"]) >= 0.12:
        actions.append("review customer feedback and improve support scripts")
    if float(risk["cancellation_rate"]) >= 0.02:
        actions.append("inspect cancellation reasons before promotion")
    return "; ".join(actions) if actions else "monitor weekly trend"


def _order_priority(order: dict) -> tuple[float, list[str]]:
    score = 0.0
    reasons = []
    delay_days = order.get("delay_days")
    review_score = order.get("review_score")
    if isinstance(delay_days, int) and delay_days > 0:
        score += min(delay_days, 30) * 0.5
        reasons.append(f"delayed {delay_days} day(s)")
    if isinstance(review_score, int) and review_score <= 2:
        score += (3 - review_score) * 4
        reasons.append(f"low review score {review_score}")
    if order.get("status") == "canceled":
        score += 5
        reasons.append("canceled order")
    payment_value = float(order.get("payment_value") or 0)
    if payment_value >= 300:
        score += 2
        reasons.append(f"high value {payment_value:.2f}")
    return round(score, 2), reasons


def _order_action(order: dict) -> str:
    if order.get("status") == "canceled":
        return "prepare cancellation explanation and retention check"
    review_score = order.get("review_score")
    delay_days = order.get("delay_days")
    if isinstance(delay_days, int) and delay_days > 0 and isinstance(review_score, int) and review_score <= 2:
        return "prepare escalation draft and proactive apology"
    if isinstance(delay_days, int) and delay_days > 0:
        return "send delayed-delivery follow-up"
    if isinstance(review_score, int) and review_score <= 2:
        return "inspect review issue and support recovery"
    return "monitor"
