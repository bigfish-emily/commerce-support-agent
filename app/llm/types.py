"""Pydantic models for LLM structured output."""

from pydantic import BaseModel, Field


class IntentRouteResult(BaseModel):
    intent: str = Field(
        ...,
        description="Either 'qa', 'order_status', 'policy', 'ops_decision', or 'escalation'",
    )


class PlannedTask(BaseModel):
    intent: str = Field(
        ...,
        description="Either 'qa', 'order_status', 'policy', 'ops_decision', or 'escalation'",
    )
    text: str = Field("", description="User sub-request for this task")
    side_effect: bool = Field(False, description="Whether the task may change external business state")
    action_type: str = Field(
        "none",
        description=(
            "For side effects: open_support_case, refund_request, cancel_order, "
            "change_address, invoice_request, complaint_escalation, or none"
        ),
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="Zero-based indices of prerequisite tasks",
    )


class TaskPlanResult(BaseModel):
    tasks: list[PlannedTask] = Field(default_factory=list)


class OlistTaskResult(BaseModel):
    order_id: str = Field("", description="32-character Olist order id if present")
    category: str = Field("", description="Product category if present")
    user_goal: str = Field("", description="Short summary of the user's marketplace support goal")


class InputGuardResult(BaseModel):
    on_topic: bool
    reason: str


class OutputGuardResult(BaseModel):
    valid: bool
    reason: str
