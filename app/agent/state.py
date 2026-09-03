from typing import NotRequired, TypedDict


class AgentState(TypedDict):
    """Serializable state that flows through LangGraph nodes.

    Runtime dependencies live in DI. This state stores only durable business
    data that can be checkpointed and resumed after HITL interrupts.
    """

    session_id: str
    messages: list[dict[str, str]]
    route_intent: str
    task_plan: list[dict[str, object]]
    completed_tasks: list[dict[str, object]]
    trajectory_events: list[dict[str, object]]
    artifacts: dict[str, object]
    retrieved_insights: list[dict[str, object]]
    retrieved_policy: list[dict[str, object]]
    retrieved_support_docs: list[dict[str, object]]
    after_sales_cases: list[dict[str, object]]
    escalation_draft: NotRequired[dict[str, object]]
    pending_side_effect: NotRequired[dict[str, object]]
    user_response: NotRequired[str]
    final_answer: str
