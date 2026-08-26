import os
import time

from langgraph.types import interrupt

from app.agent.state import AgentState
from app.llm.intent_planner import IntentPlanner
from app.llm.response_generator import OlistTaskExtractor, PolicyResponseGenerator, QaResponseGenerator
from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import (
    InMemoryCaseService,
    OlistService,
    format_after_sales_report,
    format_order_status,
)
from app.retrieval.hybrid import HybridSupportRetriever
from app.tools.repair import repair_order_id


class AgentActions:
    """Business actions wired as LangGraph nodes."""

    def __init__(
        self,
        intent_planner: IntentPlanner,
        qa_generator: QaResponseGenerator,
        policy_generator: PolicyResponseGenerator,
        task_extractor: OlistTaskExtractor,
        olist_service: OlistService,
        knowledge_base: MarkdownKnowledgeBase,
        support_retriever: HybridSupportRetriever,
        case_service: InMemoryCaseService,
    ) -> None:
        self._intent_planner = intent_planner
        self._qa_generator = qa_generator
        self._policy_generator = policy_generator
        self._task_extractor = task_extractor
        self._olist_service = olist_service
        self._knowledge_base = knowledge_base
        self._support_retriever = support_retriever
        self._case_service = case_service

    async def plan_tasks(self, state: AgentState) -> dict:
        last_message: str = state["messages"][-1]["content"]
        plan = await self._intent_planner.plan(last_message)
        tasks = [task.model_dump() for task in plan.tasks]
        route_intent = str(tasks[0]["intent"]) if tasks else "policy"
        return {
            "route_intent": route_intent,
            "task_plan": tasks,
            "trajectory_events": [
                *state.get("trajectory_events", []),
                _event("plan_tasks", "planner", "completed", {"task_count": len(tasks), "tasks": tasks}),
            ],
        }

    async def execute_task_plan(self, state: AgentState) -> dict:
        answers: list[str] = []
        completed: list[dict[str, object]] = []
        retrieved_insights: list[dict[str, object]] = []
        retrieved_policy: list[dict[str, object]] = []
        retrieved_support_docs: list[dict[str, object]] = []
        escalation_draft: dict[str, object] | None = None
        trajectory_events = list(state.get("trajectory_events", []))

        tasks = state.get("task_plan", [])
        for idx, task in _execution_order(tasks):
            dependencies = [int(dep) for dep in task.get("depends_on", []) if isinstance(dep, int)]
            completed_indices = {
                int(item["index"]) for item in completed if item.get("status") == "completed"
            }
            if any(dep not in completed_indices for dep in dependencies):
                completed.append({"index": idx, "intent": task.get("intent", "policy"), "status": "blocked"})
                answers.append("[任务阻断]\n前置任务没有完成，已停止后续可能有副作用的动作。")
                trajectory_events.append(
                    _event(
                        "execute_task_plan",
                        str(task.get("intent", "policy")),
                        "blocked",
                        {"task_index": idx, "missing_dependencies": dependencies},
                    )
                )
                break

            intent = str(task.get("intent", "policy"))
            text = str(task.get("text") or state["messages"][-1]["content"])
            event_details: dict[str, object] = {"task_index": idx, "text": text[:300]}
            if intent == "order_status":
                answer = await self._answer_order_status(text)
                answers.append(f"[订单查询]\n{answer}")
                event_details["tool"] = "get_order_status"
            elif intent == "qa":
                insights = self._olist_service.category_insights(text)
                support_docs = self._search_support_docs(text)
                retrieved_insights.extend(insights)
                retrieved_support_docs.extend(support_docs)
                answer = await self._qa_generator.generate(text, insights, support_docs)
                answers.append(f"[运营分析]\n{answer}")
                event_details["tool"] = "search_category_risk"
                event_details["hit_count"] = len(insights)
                event_details["support_doc_count"] = len(support_docs)
            elif intent == "ops_decision":
                report = self._olist_service.after_sales_priority_report(text)
                answers.append(f"[售后运营决策]\n{format_after_sales_report(report)}")
                retrieved_insights.extend(report.get("high_risk_categories", []))
                event_details["tool"] = "generate_after_sales_priority_report"
                event_details["category_count"] = len(report.get("high_risk_categories", []))
                event_details["order_count"] = len(report.get("priority_orders", []))
            elif intent == "policy":
                hits = self._knowledge_base.search(text, k=3)
                sections = [
                    {
                        "source": hit.source,
                        "section_title": hit.section_title,
                        "text": hit.text,
                        "score": hit.score,
                    }
                    for hit in hits
                ]
                support_docs = self._search_support_docs(text)
                retrieved_policy.extend(sections)
                retrieved_support_docs.extend(support_docs)
                answer = await self._policy_generator.generate(
                    text,
                    sections,
                    support_docs,
                    completed_context="\n\n".join(answers),
                )
                answers.append(f"[政策问答]\n{answer}")
                event_details["tool"] = "search_policy_knowledge"
                event_details["hit_count"] = len(sections)
                event_details["support_doc_count"] = len(support_docs)
            elif intent == "escalation":
                action_type = str(task.get("action_type") or self._infer_action_type(text))
                slot_text = _with_order_context(text, state["messages"][-1]["content"])
                draft_answer, draft = await self._prepare_escalation_from_text(slot_text, action_type)
                answers.append(f"[售后升级]\n{draft_answer}")
                escalation_draft = draft
                completed.append({"index": idx, "intent": intent, "status": "awaiting_confirmation"})
                event_details["tool"] = "prepare_side_effect"
                event_details["action_type"] = action_type
                trajectory_events.append(
                    _event("execute_task_plan", intent, "awaiting_confirmation", event_details)
                )
                break
            completed.append({"index": idx, "intent": intent, "status": "completed"})
            trajectory_events.append(_event("execute_task_plan", intent, "completed", event_details))

        final_answer = (
            "\n\n".join(answers)
            if answers
            else "没有识别到可执行的电商客服任务，请补充订单号或问题。"
        )
        update: dict[str, object] = {
            "completed_tasks": completed,
            "trajectory_events": trajectory_events,
            "artifacts": {
                "policy_sources": [str(item["section_title"]) for item in retrieved_policy],
                "insight_sources": [
                    str(item.get("name") or item.get("category") or "")
                    for item in retrieved_insights
                    if item.get("name") or item.get("category")
                ],
                "support_sources": [str(item["doc_id"]) for item in retrieved_support_docs],
            },
            "retrieved_insights": retrieved_insights,
            "retrieved_policy": retrieved_policy,
            "retrieved_support_docs": retrieved_support_docs,
            "final_answer": final_answer,
            "messages": [*state["messages"], {"role": "assistant", "content": final_answer}],
        }
        if escalation_draft:
            created_at = time.time()
            timeout_seconds = _hitl_timeout_seconds()
            update["escalation_draft"] = escalation_draft
            update["pending_side_effect"] = {
                "type": escalation_draft.get("action_type", "open_support_case"),
                "requires_confirmation": True,
                "task_intent": "escalation",
                "created_at": created_at,
                "expires_at": created_at + timeout_seconds,
                "timeout_seconds": timeout_seconds,
            }
        return update

    def search_marketplace_insights(self, state: AgentState) -> dict:
        query: str = state["messages"][-1]["content"]
        results = self._olist_service.category_insights(query)
        return {"retrieved_insights": results}

    async def generate_qa_answer(self, state: AgentState) -> dict:
        insights = state.get("retrieved_insights", [])
        support_docs = state.get("retrieved_support_docs", [])
        user_message: str = state["messages"][-1]["content"]
        answer: str = await self._qa_generator.generate(user_message, insights, support_docs)
        sources = [str(item["name"]) for item in insights] if insights else []
        return {
            "final_answer": answer,
            "messages": [*state["messages"], {"role": "assistant", "content": answer}],
            "retrieved_insights": sources,
        }

    def search_policy_knowledge(self, state: AgentState) -> dict:
        query: str = state["messages"][-1]["content"]
        hits = self._knowledge_base.search(query, k=3)
        return {
            "retrieved_policy": [
                {
                    "source": hit.source,
                    "section_title": hit.section_title,
                    "text": hit.text,
                    "score": hit.score,
                }
                for hit in hits
            ]
        }

    async def generate_policy_answer(self, state: AgentState) -> dict:
        policy_sections = state.get("retrieved_policy", [])
        support_docs = state.get("retrieved_support_docs", [])
        user_message: str = state["messages"][-1]["content"]
        answer: str = await self._policy_generator.generate(user_message, policy_sections, support_docs)
        sources = [str(item["section_title"]) for item in policy_sections]
        return {
            "final_answer": answer,
            "messages": [*state["messages"], {"role": "assistant", "content": answer}],
            "retrieved_policy": sources,
        }

    async def check_order_status(self, state: AgentState) -> dict:
        user_message: str = state["messages"][-1]["content"]
        answer = await self._answer_order_status(user_message)
        return {
            "final_answer": answer,
            "messages": [*state["messages"], {"role": "assistant", "content": answer}],
        }

    async def prepare_escalation(self, state: AgentState) -> dict:
        user_message: str = state["messages"][-1]["content"]
        answer, draft = await self._prepare_escalation_from_text(
            user_message,
            self._infer_action_type(user_message),
        )
        if draft is None:
            return {
                "final_answer": answer,
                "messages": [*state["messages"], {"role": "assistant", "content": answer}],
            }
        created_at = time.time()
        timeout_seconds = _hitl_timeout_seconds()
        return {
            "escalation_draft": draft,
            "pending_side_effect": {
                "type": draft.get("action_type", "open_support_case"),
                "requires_confirmation": True,
                "task_intent": "escalation",
                "created_at": created_at,
                "expires_at": created_at + timeout_seconds,
                "timeout_seconds": timeout_seconds,
            },
            "final_answer": answer,
            "messages": [*state["messages"], {"role": "assistant", "content": answer}],
        }

    async def _answer_order_status(self, user_message: str) -> str:
        task = await self._task_extractor.extract(user_message)
        repair = repair_order_id(task.order_id or user_message)
        if not repair.ok:
            return repair.message
        return format_order_status(self._olist_service.get_order_status(repair.value))

    async def _prepare_escalation_from_text(
        self,
        user_message: str,
        action_type: str,
    ) -> tuple[str, dict[str, object] | None]:
        task = await self._task_extractor.extract(user_message)
        repair = repair_order_id(task.order_id or user_message)
        if not repair.ok:
            return repair.message, None
        order_id = repair.value

        draft = self._olist_service.escalation_draft(order_id)
        if draft is None:
            return "没有找到该订单，无法生成升级处理草稿。", None
        draft["action_type"] = action_type

        action_label = _ACTION_LABELS.get(action_type, "售后升级处理")
        answer = (
            f"我准备为订单 {order_id} 执行：{action_label}。\n\n"
            f"原因：{draft['reason']}\n"
            f"草稿：{draft['message_text']}\n\n"
            "该动作会改变业务状态，是否确认执行？(yes/no)"
        )
        return answer, draft

    def await_confirmation(self, state: AgentState) -> dict:
        user_response: str = interrupt("Waiting for escalation confirmation")
        return {
            "user_response": user_response,
            "messages": [*state["messages"], {"role": "user", "content": user_response}],
        }

    def finalize_escalation(self, state: AgentState) -> dict:
        user_response: str = state.get("user_response", "")
        confirmed: bool = user_response.lower().strip() in (
            "yes",
            "yeah",
            "y",
            "confirm",
            "ok",
            "okay",
            "确认",
            "创建",
            "升级",
        )
        draft = state.get("escalation_draft", {})
        if confirmed and draft:
            action_type = str(draft.get("action_type", "open_support_case"))
            result_id = self._case_service.execute_action(
                action_type=action_type,
                order_id=str(draft["order_id"]),
                message_text=str(draft["message_text"]),
            )
            action_label = _ACTION_LABELS.get(action_type, "售后升级处理")
            answer = f"已执行{action_label}：{result_id}。"
            status = "completed"
        elif confirmed:
            answer = "没有找到可提交的升级草稿，请重新发起。"
            status = "failed"
        elif user_response.lower().strip() == "__hitl_timeout__":
            answer = "待确认的售后动作已超时自动取消；如果仍需处理，请重新发起任务。"
            status = "timeout_canceled"
        else:
            answer = "已取消创建售后升级 case。"
            status = "canceled"

        return {
            "final_answer": answer,
            "messages": [*state["messages"], {"role": "assistant", "content": answer}],
            "pending_side_effect": {},
            "escalation_draft": {},
            "trajectory_events": [
                *state.get("trajectory_events", []),
                _event(
                    "finalize_escalation",
                    "escalation",
                    status,
                    {
                        "confirmed": confirmed,
                        "action_type": str(draft.get("action_type", "")) if draft else "",
                    },
                ),
            ],
        }

    @staticmethod
    def _infer_action_type(text: str) -> str:
        lowered = text.lower()
        if any(token in lowered for token in ("refund", "退款", "赔付", "compensat", "coupon", "发券")):
            return "refund_request"
        if any(token in lowered for token in ("cancel order", "cancel purchase", "取消订单")):
            return "cancel_order"
        if any(
            token in lowered
            for token in ("change shipping address", "change address", "改地址", "修改地址")
        ):
            return "change_address"
        if any(token in lowered for token in ("invoice", "发票", "开票")):
            return "invoice_request"
        return "open_support_case"

    def _search_support_docs(self, query: str, k: int = 3) -> list[dict[str, object]]:
        return [
            {
                "doc_id": hit.doc.get("doc_id", ""),
                "intent": hit.doc.get("intent", ""),
                "capability": hit.doc.get("capability", ""),
                "text": hit.doc.get("text", ""),
                "score": hit.score,
            }
            for hit in self._support_retriever.hybrid_search(query, k=k)
        ]


_ACTION_LABELS = {
    "open_support_case": "创建售后工单",
    "refund_request": "提交退款/补偿申请",
    "cancel_order": "提交取消订单申请",
    "change_address": "提交改地址申请",
    "invoice_request": "提交发票申请",
}


def _execution_order(tasks: list[dict[str, object]]) -> list[tuple[int, dict[str, object]]]:
    indexed = list(enumerate(tasks))
    read_only = [item for item in indexed if not item[1].get("side_effect")]
    side_effects = [item for item in indexed if item[1].get("side_effect")]
    return [*read_only, *side_effects]


def _with_order_context(task_text: str, full_message: str) -> str:
    if repair_order_id(task_text).ok:
        return task_text
    repair = repair_order_id(full_message)
    if not repair.ok:
        return task_text
    return f"{task_text}\n上下文订单号：{repair.value}"


def _hitl_timeout_seconds() -> int:
    raw = os.environ.get("HITL_TIMEOUT_SECONDS", "900")
    try:
        value = int(raw)
    except ValueError:
        return 900
    return max(value, 1)


def _event(
    node: str,
    intent: str,
    status: str,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "node": node,
        "intent": intent,
        "status": status,
        "details": details or {},
    }
