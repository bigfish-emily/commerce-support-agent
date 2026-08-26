from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.intent.decomposer import decompose_business_message
from app.llm.json_fallback import add_json_instruction, parse_json_model
from app.llm.prompts import INTENT_PLANNER_PROMPT
from app.llm.types import IntentRouteResult, PlannedTask, TaskPlanResult

VALID_INTENTS = {"qa", "order_status", "policy", "ops_decision", "escalation"}
logger = logging.getLogger(__name__)


class IntentPlanner:
    """Plan ordered business tasks from a user request."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self._base_llm = llm
        self._llm = llm.with_structured_output(TaskPlanResult)

    async def plan(self, message: str) -> TaskPlanResult:
        messages = [
            SystemMessage(content=INTENT_PLANNER_PROMPT),
            HumanMessage(content=message),
        ]
        try:
            result = await self._llm.ainvoke(messages)
        except Exception as exc:
            logger.warning("Structured planner failed, trying JSON-text fallback: %s", exc)
            try:
                raw = await self._base_llm.ainvoke(add_json_instruction(messages, TaskPlanResult))
                result = parse_json_model(raw, TaskPlanResult)
            except Exception as fallback_exc:
                logger.warning("LLM intent planner failed, using deterministic fallback: %s", fallback_exc)
                return fallback_plan(message)
        tasks = [task for task in result.tasks if task.intent in VALID_INTENTS]
        if not tasks:
            return fallback_plan(message)
        return TaskPlanResult(tasks=_normalize_dependencies(tasks))

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
    return TaskPlanResult(tasks=_normalize_dependencies(tasks))


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
