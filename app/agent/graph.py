from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from app.agent.actions import AgentActions
from app.agent.state import AgentState


class AgentGraph:
    """Build and compile the Olist marketplace support graph."""

    def __init__(self, actions: AgentActions) -> None:
        self._actions = actions

    def build(self, checkpointer: BaseCheckpointSaver) -> StateGraph:
        """Graph topology:

        plan_tasks -> execute_task_plan -> END
        execute_task_plan -> await_confirmation -> finalize_escalation -> END
        """
        graph: StateGraph = StateGraph(AgentState)

        graph.add_node("plan_tasks", self._actions.plan_tasks)
        graph.add_node("execute_task_plan", self._actions.execute_task_plan)
        graph.add_node("await_confirmation", self._actions.await_confirmation)
        graph.add_node("finalize_escalation", self._actions.finalize_escalation)

        graph.set_entry_point("plan_tasks")
        graph.add_edge("plan_tasks", "execute_task_plan")
        graph.add_conditional_edges(
            "execute_task_plan",
            self._route_after_execution,
            {
                "await_confirmation": "await_confirmation",
                "end": END,
            },
        )
        graph.add_edge("await_confirmation", "finalize_escalation")
        graph.add_edge("finalize_escalation", END)

        return graph.compile(checkpointer=checkpointer)

    @staticmethod
    def _route_after_execution(state: AgentState) -> str:
        if state.get("pending_side_effect", {}).get("requires_confirmation"):
            return "await_confirmation"
        return "end"
