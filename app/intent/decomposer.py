from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessTask:
    intent: str
    text: str
    side_effect: bool
    action_type: str = "none"


ORDER_PATTERNS = (
    "order status",
    "current status",
    "track order",
    "track my order",
    "eta",
    "delivered",
    "purchase",
    "订单状态",
    "查订单",
    "状态",
    "送达",
    "物流",
)

QA_PATTERNS = (
    "category risk",
    "review risk",
    "logistics risk",
    "operations",
    "operational risk",
    "category",
    "类目",
    "运营",
    "风险",
    "评价风险",
    "物流风险",
    "低分率",
    "延迟率",
    "取消率",
)

POLICY_PATTERNS = (
    "policy",
    "fee",
    "charge",
    "penalty",
    "payment method",
    "invoice",
    "delivery period",
    "refund policy",
    "termination",
    "withdrawal",
    "手续费",
    "政策",
    "发票",
    "支付方式",
    "退款规则",
    "配送时效",
    "补偿边界",
)

POLICY_QUESTION_CUES = (
    "what",
    "how",
    "can ",
    "could ",
    "should ",
    "是否",
    "能否",
    "能不能",
    "可不可以",
    "可以吗",
    "怎么",
    "规则",
    "政策",
    "边界",
    "直接承诺",
)

ESCALATION_PATTERNS = (
    "cancel order",
    "canceling order",
    "cancelling order",
    "cancel purchase",
    "canceling purchase",
    "cancelling purchase",
    "cancel it",
    "change order",
    "change shipping address",
    "get refund",
    "complaint",
    "human agent",
    "customer service",
    "open a case",
    "escalat",
    "compensat",
    "取消订单",
    "改地址",
    "退款",
    "投诉",
    "人工",
    "升级",
    "延迟",
    "低分",
    "安抚",
    "跟进",
    "话术",
    "创建 case",
    "发券",
)

SPLIT_RE = re.compile(
    r"(?:，另外|另外|并且|而且|顺便|然后|再帮我|再|同时|接着|;|；|, and |\balso\b|\band also\b|\bthen\b)",
    re.IGNORECASE,
)


def decompose_business_message(message: str) -> list[BusinessTask]:
    """Split a user message into ordered business tasks with duplicate task suppression."""
    segments = [part.strip() for part in SPLIT_RE.split(message) if part.strip()] or [message]
    tasks: list[BusinessTask] = []
    seen: set[tuple[str, str]] = set()
    for segment in segments:
        intent = _classify_segment(segment)
        signature = (intent, _task_signature(segment))
        if signature in seen:
            continue
        seen.add(signature)
        tasks.append(
            BusinessTask(
                intent=intent,
                text=segment,
                side_effect=(intent == "escalation"),
                action_type=_action_type(segment, intent),
            )
        )
    return tasks


def _classify_segment(segment: str) -> str:
    text = segment.lower()
    if _has_any(text, QA_PATTERNS):
        return "qa"
    if _looks_like_order_cancel_action(text):
        return "escalation"
    if _has_any(text, POLICY_PATTERNS) or _looks_like_policy_question(text):
        return "policy"
    if _has_any(text, ESCALATION_PATTERNS):
        return "escalation"
    if _has_any(text, ORDER_PATTERNS) or "{{order number}}" in text:
        return "order_status"
    return "policy"


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _looks_like_policy_question(text: str) -> bool:
    side_effect_topic = _has_any(
        text,
        (
            "refund",
            "退款",
            "补偿",
            "赔付",
            "cancel",
            "取消",
            "address",
            "地址",
            "invoice",
            "发票",
        ),
    )
    return side_effect_topic and _has_any(text, POLICY_QUESTION_CUES)


def _looks_like_order_cancel_action(text: str) -> bool:
    has_cancel_verb = "cancel" in text or "cancell" in text
    has_order_object = _has_any(text, ("{{order number}}", "order", "purchase", "oorder", "puchase"))
    policy_only = _has_any(text, ("fee", "charge", "penalty", "termination", "withdrawal"))
    return has_cancel_verb and has_order_object and not policy_only


def _task_signature(segment: str) -> str:
    normalized = re.sub(r"\s+", " ", segment.lower()).strip()
    order_ids = re.findall(r"\b[a-f0-9]{32}\b", normalized)
    if order_ids:
        return "|".join(order_ids)
    return normalized


def _action_type(segment: str, intent: str) -> str:
    if intent != "escalation":
        return "none"
    text = segment.lower()
    if _has_any(text, ("refund", "退款", "退钱", "赔付", "compensat", "coupon", "发券")):
        return "refund_request"
    if _has_any(text, ("cancel order", "cancel purchase", "取消订单", "取消这个订单")):
        return "cancel_order"
    if _has_any(text, ("change shipping address", "改地址", "修改地址", "change address")):
        return "change_address"
    if _has_any(text, ("invoice", "发票", "开票")):
        return "invoice_request"
    return "open_support_case"
