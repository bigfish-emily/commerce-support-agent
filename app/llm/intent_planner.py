from __future__ import annotations

import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.intent.decomposer import decompose_business_message
from app.llm.json_fallback import add_json_instruction, native_structured_output_enabled, parse_json_model
from app.llm.prompts import INTENT_PLANNER_PROMPT
from app.llm.types import IntentRouteResult, PlannedTask, TaskPlanResult

VALID_INTENTS = {"qa", "order_status", "policy", "ops_decision", "escalation"}
logger = logging.getLogger(__name__)


class IntentPlanner:
    """Plan ordered business tasks from a user request."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self._base_llm = llm
        self._llm = (
            llm.with_structured_output(TaskPlanResult)
            if native_structured_output_enabled(llm)
            else None
        )

    async def plan(self, message: str) -> TaskPlanResult:
        fast_plan = _fast_plan(message)
        if fast_plan is not None:
            return fast_plan
        messages = [
            SystemMessage(content=INTENT_PLANNER_PROMPT),
            HumanMessage(content=message),
        ]
        try:
            if self._llm is None:
                raw = await self._base_llm.ainvoke(add_json_instruction(messages, TaskPlanResult))
                result = parse_json_model(raw, TaskPlanResult)
            else:
                result = await self._llm.ainvoke(messages)
        except Exception as exc:
            logger.warning("Structured planner failed, trying JSON-text fallback: %s", exc)
            if _is_transport_error(exc):
                logger.warning("Planner transport error; using deterministic task plan without retry")
                return fallback_plan(message)
            try:
                raw = await self._base_llm.ainvoke(add_json_instruction(messages, TaskPlanResult))
                result = parse_json_model(raw, TaskPlanResult)
            except Exception as fallback_exc:
                logger.warning("LLM intent planner failed, using deterministic fallback: %s", fallback_exc)
                return fallback_plan(message)
        tasks = _dedupe_redundant_tasks([task for task in result.tasks if task.intent in VALID_INTENTS])
        if not tasks:
            return fallback_plan(message)
        return TaskPlanResult(tasks=_normalize_dependencies(tasks), planning_mode="llm")

    async def classify(self, message: str) -> IntentRouteResult:
        """Compatibility helper for evals that still measure single-label routing."""
        plan = await self.plan(message)
        intent = plan.tasks[0].intent if plan.tasks else "policy"
        return IntentRouteResult(intent=intent)


def fallback_plan(message: str) -> TaskPlanResult:
    tasks = [
        PlannedTask(
            intent=task.intent,
            text=task.text,
            side_effect=task.side_effect,
            action_type=task.action_type,
            depends_on=[],
        )
        for task in decompose_business_message(message)
    ]
    return TaskPlanResult(
        tasks=_normalize_dependencies(tasks),
        planning_mode="deterministic_fallback",
    )


def _fast_plan(message: str) -> TaskPlanResult | None:
    """Use a deterministic fast lane only for unambiguous one-step requests."""

    text = message.lower()
    has_order_id = bool(re.search(r"[0-9a-f]{32}", text))
    action_type = _direct_action_type(text)
    compound_markers = ("并且", "然后", "同时", "先", "政策", "状态", "物流", "配送", "以及")
    if has_order_id and action_type and not any(marker in text for marker in compound_markers):
        return TaskPlanResult(
            tasks=[
                PlannedTask(
                    intent="escalation",
                    text=message,
                    side_effect=True,
                    action_type=action_type,
                )
            ],
            planning_mode="deterministic_fast_path",
        )
    if has_order_id and not action_type:
        return TaskPlanResult(
            tasks=[PlannedTask(intent="order_status", text=message)],
            planning_mode="deterministic_fast_path",
        )

    policy_terms = ("退款政策", "退款规则", "发票需要", "取消订单规则", "改地址规则", "补偿能不能")
    if (
        not has_order_id
        and not any(marker in text for marker in compound_markers)
        and any(term in text for term in policy_terms)
    ):
        return TaskPlanResult(
            tasks=[PlannedTask(intent="policy", text=message)],
            planning_mode="deterministic_fast_path",
        )
    return None


def _direct_action_type(text: str) -> str:
    if "退款" in text or "补偿申请" in text:
        return "refund_request"
    if "取消订单" in text or "申请取消" in text:
        return "cancel_order"
    if "改地址" in text or "修改地址" in text:
        return "change_address"
    if "申请发票" in text or "开票申请" in text:
        return "invoice_request"
    if "投诉" in text:
        return "complaint_escalation"
    return ""


def _is_transport_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return any(marker in name for marker in ("timeout", "connection", "network"))


def _normalize_dependencies(tasks: list[PlannedTask]) -> list[PlannedTask]:
    order_status_indices = [idx for idx, task in enumerate(tasks) if task.intent == "order_status"]
    if not order_status_indices:
        return tasks
    first_order_idx = order_status_indices[0]
    normalized = []
    for idx, task in enumerate(tasks):
        if task.intent == "escalation" and idx > first_order_idx and first_order_idx not in task.depends_on:
            normalized.append(task.model_copy(update={"depends_on": [*task.depends_on, first_order_idx]}))
        else:
            normalized.append(task)
    return normalized


def _dedupe_redundant_tasks(tasks: list[PlannedTask]) -> list[PlannedTask]:
    deduped: list[PlannedTask] = []
    seen_read_only_without_order: set[str] = set()
    for task in tasks:
        if not task.side_effect and not _contains_order_id(task.text):
            if task.intent in seen_read_only_without_order:
                continue
            seen_read_only_without_order.add(task.intent)
        deduped.append(task)
    return deduped


def _contains_order_id(text: str) -> bool:
    import re

    return bool(re.search(r"[0-9a-fA-F]{32}", text))
