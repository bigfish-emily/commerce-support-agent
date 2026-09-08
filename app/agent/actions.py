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
)
from app.retrieval.hybrid import HybridSupportRetriever
from app.tool_call import RuntimeStore, ToolCallContext, ToolCallManager, build_business_tool_manager
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
        tool_manager: ToolCallManager | None = None,
        runtime_store: RuntimeStore | None = None,
    ) -> None:
        self._intent_planner = intent_planner
        self._qa_generator = qa_generator
        self._policy_generator = policy_generator
        self._task_extractor = task_extractor
        self._olist_service = olist_service
        self._knowledge_base = knowledge_base
        self._support_retriever = support_retriever
        self._case_service = case_service
        self._runtime_store = runtime_store
        self._tool_manager = tool_manager or build_business_tool_manager(
            olist_service=olist_service,
            knowledge_base=knowledge_base,
            support_retriever=support_retriever,
            case_service=case_service,
            runtime_store=runtime_store,
        )

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

    def select_next_task(self, state: AgentState) -> dict:
        completed = list(state.get("completed_tasks", []))
        terminal_indices = _terminal_task_indices(completed)
        completed_indices = _completed_task_indices(completed)
        tasks = state.get("task_plan", [])

        for idx, task in _execution_order(tasks):
            if idx in terminal_indices:
                continue
            dependencies = [int(dep) for dep in task.get("depends_on", []) if isinstance(dep, int)]
            missing = [dep for dep in dependencies if dep not in completed_indices]
            if missing:
                completed = _upsert_task_status(
                    completed,
                    idx,
                    str(task.get("intent", "policy")),
                    "blocked",
                )
                answer_parts = [
                    *state.get("answer_parts", []),
                    "[任务阻断]\n前置任务没有完成，已停止后续可能有副作用的动作。",
                ]
                return {
                    "completed_tasks": completed,
                    "answer_parts": answer_parts,
                    "workflow_complete": True,
                    "trajectory_events": [
                        *state.get("trajectory_events", []),
                        _event(
                            "select_next_task",
                            str(task.get("intent", "policy")),
                            "blocked",
                            {"task_index": idx, "missing_dependencies": missing},
                        ),
                    ],
                }

            return {
                "current_task_index": idx,
                "current_task": task,
                "current_task_text": str(task.get("text") or _latest_user_task_message(state)),
                "workflow_complete": False,
                "trajectory_events": [
                    *state.get("trajectory_events", []),
                    _event(
                        "select_next_task",
                        str(task.get("intent", "policy")),
                        "selected",
                        {"task_index": idx, "side_effect": bool(task.get("side_effect"))},
                    ),
                ],
            }

        return {
            "current_task": {},
            "current_task_text": "",
            "workflow_complete": True,
            "trajectory_events": [
                *state.get("trajectory_events", []),
                _event("select_next_task", "workflow", "completed"),
            ],
        }

    async def extract_slots(self, state: AgentState) -> dict:
        if state.get("workflow_complete"):
            return {}
        task = dict(state.get("current_task", {}))
        intent = str(task.get("intent", "policy"))
        text = str(state.get("current_task_text") or _latest_user_task_message(state))
        slot_text = _with_order_context(text, _latest_user_task_message(state))
        extracted = await self._task_extractor.extract(slot_text)
        order_repair = repair_order_id(extracted.order_id or slot_text)
        slots = {
            "order_id": order_repair.value if order_repair.ok else "",
            "order_id_ok": order_repair.ok,
            "order_id_error": "" if order_repair.ok else order_repair.message,
            "category": extracted.category,
            "user_goal": extracted.user_goal,
        }
        if intent == "escalation":
            slots["action_type"] = str(task.get("action_type") or self._infer_action_type(text))
        return {
            "current_slots": slots,
            "trajectory_events": [
                *state.get("trajectory_events", []),
                _event(
                    "extract_slots",
                    intent,
                    "completed",
                    {
                        "task_index": state.get("current_task_index"),
                        "has_order_id": bool(slots["order_id"]),
                        "category": slots["category"],
                    },
                ),
            ],
        }

    async def retrieve_context(self, state: AgentState) -> dict:
        if state.get("workflow_complete"):
            return {}
        task = dict(state.get("current_task", {}))
        intent = str(task.get("intent", "policy"))
        text = str(state.get("current_task_text") or _latest_user_task_message(state))
        slots = dict(state.get("current_slots", {}))
        context: dict[str, object] = {}
        update: dict[str, object] = {}

        if intent == "order_status":
            context["primary_tool"] = "get_order_status"
            if slots.get("order_id_ok"):
                order_id = str(slots["order_id"])
                result = await self._tool_manager.call(
                    "get_order_status",
                    {"order_id": order_id},
                    self._tool_context(state),
                )
                context["order_status_result"] = result.model_dump()
            else:
                context["order_id_error"] = str(slots.get("order_id_error", "没有检测到有效订单号。"))
        elif intent == "qa":
            context["primary_tool"] = "search_category_risk"
            insights = await self._category_insights(text, state)
            support_docs = await self._search_support_docs(text, state)
            context["insights"] = insights
            context["support_docs"] = support_docs
            update["retrieved_insights"] = [*state.get("retrieved_insights", []), *insights]
            update["retrieved_support_docs"] = [*state.get("retrieved_support_docs", []), *support_docs]
        elif intent == "ops_decision":
            context["primary_tool"] = "generate_after_sales_priority_report"
            report = await self._after_sales_priority_report(text, state)
            context["ops_report"] = report
            update["retrieved_insights"] = [
                *state.get("retrieved_insights", []),
                *list(report.get("high_risk_categories", [])),
            ]
        elif intent in {"policy", "escalation"}:
            context["primary_tool"] = (
                "search_policy_knowledge" if intent == "policy" else "prepare_side_effect"
            )
            policy_query = text
            if intent == "escalation":
                action_type = str(slots.get("action_type") or task.get("action_type") or "open_support_case")
                policy_query = f"{text}\n售后动作：{_ACTION_LABELS.get(action_type, action_type)}"
            sections = await self._search_policy_knowledge(policy_query, state)
            support_docs = await self._search_support_docs(text, state)
            context["policy_sections"] = sections
            context["support_docs"] = support_docs
            update["retrieved_policy"] = [*state.get("retrieved_policy", []), *sections]
            update["retrieved_support_docs"] = [*state.get("retrieved_support_docs", []), *support_docs]

        update["current_context"] = context
        update["trajectory_events"] = [
            *state.get("trajectory_events", []),
            _event(
                "retrieve_context",
                intent,
                "completed",
                {
                    "task_index": state.get("current_task_index"),
                    "tool": context.get("primary_tool"),
                    "policy_hits": len(context.get("policy_sections", []))
                    if isinstance(context.get("policy_sections"), list)
                    else 0,
                    "support_hits": len(context.get("support_docs", []))
                    if isinstance(context.get("support_docs"), list)
                    else 0,
                    "insight_hits": len(context.get("insights", []))
                    if isinstance(context.get("insights"), list)
                    else 0,
                },
            ),
        ]
        if intent == "policy":
            update["trajectory_events"].append(
                _event(
                    "search_policy_knowledge",
                    intent,
                    "completed",
                    {
                        "task_index": state.get("current_task_index"),
                        "tool": "search_policy_knowledge",
                        "hit_count": len(context.get("policy_sections", []))
                        if isinstance(context.get("policy_sections"), list)
                        else 0,
                    },
                )
            )
        return update

    async def execute_read_task(self, state: AgentState) -> dict:
        task = dict(state.get("current_task", {}))
        intent = str(task.get("intent", "policy"))
        text = str(state.get("current_task_text") or _latest_user_task_message(state))
        context = dict(state.get("current_context", {}))
        answer = ""

        if intent == "order_status":
            if context.get("order_id_error"):
                answer = str(context["order_id_error"])
            else:
                result = context.get("order_status_result")
                data = dict(result.get("data", {})) if isinstance(result, dict) else {}
                answer = str(data.get("answer", "订单事实工具没有返回结果。"))
        elif intent == "qa":
            answer = await self._qa_generator.generate(
                text,
                list(context.get("insights", [])),
                list(context.get("support_docs", [])),
            )
        elif intent == "ops_decision":
            answer = format_after_sales_report(dict(context.get("ops_report", {})))
        elif intent == "policy":
            answer = await self._policy_generator.generate(
                text,
                list(context.get("policy_sections", [])),
                list(context.get("support_docs", [])),
                completed_context="\n\n".join(state.get("answer_parts", [])),
            )

        idx = int(state.get("current_task_index", 0))
        completed = _upsert_task_status(list(state.get("completed_tasks", [])), idx, intent, "completed")
        return {
            "completed_tasks": completed,
            "answer_parts": [
                *state.get("answer_parts", []),
                f"[{_INTENT_LABELS.get(intent, intent)}]\n{answer}",
            ],
            "trajectory_events": [
                *state.get("trajectory_events", []),
                _event(
                    "execute_read_task",
                    intent,
                    "completed",
                    {
                        "task_index": idx,
                        "tool": context.get("primary_tool"),
                        "answer_chars": len(answer),
                    },
                ),
            ],
        }

    async def build_after_sales_case(self, state: AgentState) -> dict:
        task = dict(state.get("current_task", {}))
        idx = int(state.get("current_task_index", 0))
        text = str(state.get("current_task_text") or _latest_user_task_message(state))
        slots = dict(state.get("current_slots", {}))
        action_type = str(
            slots.get("action_type") or task.get("action_type") or self._infer_action_type(text)
        )
        slot_text = _with_order_context(text, _latest_user_task_message(state))
        answer, draft = await self._prepare_escalation_from_text(slot_text, action_type, state)
        completed = list(state.get("completed_tasks", []))
        after_sales_cases = list(state.get("after_sales_cases", []))
        events = list(state.get("trajectory_events", []))

        if draft is None:
            completed = _upsert_task_status(completed, idx, "escalation", "completed")
            return {
                "completed_tasks": completed,
                "answer_parts": [*state.get("answer_parts", []), f"[售后升级]\n{answer}"],
                "trajectory_events": [
                    *events,
                    _event(
                        "decision_engine",
                        "escalation",
                        "ask_clarification",
                        {
                            "task_index": idx,
                            "tool": "prepare_side_effect",
                            "action_type": action_type,
                        },
                    ),
                ],
            }

        draft["task_index"] = idx
        if isinstance(draft.get("after_sales_case"), dict):
            after_sales_cases.append(dict(draft["after_sales_case"]))
        decision = dict(draft.get("decision", {}))
        verification = dict(draft.get("verification", {}))
        events.extend(
            [
                _event(
                    "decision_engine",
                    "escalation",
                    str(decision.get("outcome", "unknown")),
                    {
                        "task_index": idx,
                        "tool": "prepare_side_effect",
                        "action_type": action_type,
                        "risk_level": decision.get("risk_level"),
                        "requires_human": decision.get("requires_human"),
                    },
                ),
                _event(
                    "verifier",
                    "escalation",
                    str(verification.get("required_next_step", "unknown")),
                    {"task_index": idx, "flags": verification.get("flags", [])},
                ),
            ]
        )

        if _decision_stops_execution(decision, verification):
            completed = _upsert_task_status(completed, idx, "escalation", "completed")
            return {
                "completed_tasks": completed,
                "after_sales_cases": after_sales_cases,
                "answer_parts": [*state.get("answer_parts", []), f"[售后升级]\n{answer}"],
                "trajectory_events": events,
            }

        if draft.get("requires_confirmation") is False:
            execution_answer = await self._execute_escalation_draft(draft, state)
            completed = _upsert_task_status(completed, idx, "escalation", "completed")
            answer_parts = [
                *state.get("answer_parts", []),
                f"[售后升级]\n{answer}\n\n{execution_answer}",
            ]
            return {
                "completed_tasks": completed,
                "after_sales_cases": after_sales_cases,
                "answer_parts": answer_parts,
                "final_answer": "\n\n".join(answer_parts),
                "trajectory_events": [
                    *events,
                    _event(
                        "execute_write_action",
                        "escalation",
                        "completed",
                        {
                            "task_index": idx,
                            "tool": "execute_side_effect",
                            "action_type": action_type,
                            "auto_execute": True,
                        },
                    ),
                ],
            }

        created_at = time.time()
        timeout_seconds = _hitl_timeout_seconds()
        pending_side_effect = {
            "type": draft.get("action_type", "open_support_case"),
            "requires_confirmation": True,
            "task_intent": "escalation",
            "task_index": idx,
            "created_at": created_at,
            "expires_at": created_at + timeout_seconds,
            "timeout_seconds": timeout_seconds,
        }
        if self._runtime_store is not None:
            await self._runtime_store.put_pending_confirmation(
                str(state.get("session_id", "unknown")),
                pending_side_effect,
                timeout_seconds,
            )
        completed = _upsert_task_status(completed, idx, "escalation", "awaiting_confirmation")
        answer_parts = [*state.get("answer_parts", []), f"[售后升级]\n{answer}"]
        final_answer = "\n\n".join(answer_parts)
        return {
            "completed_tasks": completed,
            "after_sales_cases": after_sales_cases,
            "answer_parts": answer_parts,
            "final_answer": final_answer,
            "messages": [*state["messages"], {"role": "assistant", "content": final_answer}],
            "escalation_draft": draft,
            "pending_side_effect": pending_side_effect,
            "trajectory_events": [
                *events,
                _event(
                    "hitl_gate",
                    "escalation",
                    "awaiting_confirmation",
                    {"task_index": idx, "tool": "prepare_side_effect"},
                ),
            ],
        }

    async def finalize_answer(self, state: AgentState) -> dict:
        answers = list(state.get("answer_parts", []))
        final_answer = (
            "\n\n".join(answers)
            if answers
            else "没有识别到可执行的电商客服任务，请补充订单号或问题。"
        )
        retrieved_policy = list(state.get("retrieved_policy", []))
        retrieved_insights = list(state.get("retrieved_insights", []))
        retrieved_support_docs = list(state.get("retrieved_support_docs", []))
        return {
            "final_answer": final_answer,
            "messages": [*state["messages"], {"role": "assistant", "content": final_answer}],
            "artifacts": {
                "policy_sources": [str(item["section_title"]) for item in retrieved_policy],
                "insight_sources": [
                    str(item.get("name") or item.get("category") or "")
                    for item in retrieved_insights
                    if item.get("name") or item.get("category")
                ],
                "support_sources": [str(item["doc_id"]) for item in retrieved_support_docs],
            },
            "trajectory_events": [
                *state.get("trajectory_events", []),
                _event("finalize_answer", "workflow", "completed", {"answer_parts": len(answers)}),
            ],
        }

    async def _answer_order_status(self, user_message: str, state: AgentState) -> str:
        task = await self._task_extractor.extract(user_message)
        repair = repair_order_id(task.order_id or user_message)
        if not repair.ok:
            return repair.message
        result = await self._tool_manager.call(
            "get_order_status",
            {"order_id": repair.value},
            self._tool_context(state),
        )
        if result.ok:
            return str(result.data.get("answer", "订单事实工具没有返回结果。"))
        return f"订单事实工具调用失败：{result.error_message}"

    async def _prepare_escalation_from_text(
        self,
        user_message: str,
        action_type: str,
        state: AgentState,
    ) -> tuple[str, dict[str, object] | None]:
        task = await self._task_extractor.extract(user_message)
        repair = repair_order_id(task.order_id or user_message)
        if not repair.ok:
            return repair.message, None
        order_id = repair.value

        policy_sections = await self._search_policy_knowledge(
            f"{user_message}\n售后动作：{_ACTION_LABELS.get(action_type, action_type)}",
            state,
        )
        case_result = await self._tool_manager.call(
            "assess_after_sales_case",
            {
                "action_type": action_type,
                "order_id": order_id,
                "user_request": user_message,
                "policy_sections": policy_sections,
            },
            self._tool_context(state),
        )
        result = await self._tool_manager.call(
            "prepare_side_effect",
            {"order_id": order_id},
            self._tool_context(state),
        )
        raw_draft = result.data.get("draft") if result.ok else None
        draft = dict(raw_draft) if isinstance(raw_draft, dict) else None
        if draft is None:
            return "没有找到该订单，无法生成升级处理草稿。", None
        draft["action_type"] = action_type
        if case_result.ok:
            after_sales_case = case_result.data.get("case")
            decision = dict(case_result.data.get("decision", {}))
            verification = dict(case_result.data.get("verification", {}))
            customer_reply = str(case_result.data.get("customer_reply", ""))
            if isinstance(after_sales_case, dict):
                draft["after_sales_case"] = after_sales_case
            draft["decision"] = decision
            draft["verification"] = verification
            if customer_reply:
                draft["message_text"] = customer_reply
        else:
            decision = {}
            verification = {}
        draft["requires_confirmation"] = str(verification.get("required_next_step", "hitl")) == "hitl"

        if _decision_stops_execution(decision, verification):
            draft["requires_confirmation"] = False
            answer = _format_after_sales_decision_answer(
                order_id,
                action_type,
                draft,
                requires_confirmation=False,
                customer_view=_is_customer_channel(state),
            )
            return answer, draft

        action_label = _ACTION_LABELS.get(action_type, "售后升级处理")
        decision_block = _format_after_sales_decision_answer(
            order_id,
            action_type,
            draft,
            requires_confirmation=bool(draft.get("requires_confirmation")),
            customer_view=_is_customer_channel(state),
        )
        if draft.get("requires_confirmation"):
            if _is_customer_channel(state):
                answer = decision_block
            else:
                answer = f"我准备为订单 {order_id} 执行：{action_label}。\n\n{decision_block}"
        else:
            answer = f"订单 {order_id} 的{action_label}通过低风险自动执行门禁。\n\n{decision_block}"
        return answer, draft

    def await_confirmation(self, state: AgentState) -> dict:
        user_response: str = interrupt("Waiting for escalation confirmation")
        return {
            "user_response": user_response,
            "messages": [*state["messages"], {"role": "user", "content": user_response}],
        }

    async def finalize_escalation(self, state: AgentState) -> dict:
        user_response: str = state.get("user_response", "")
        normalized_response = user_response.lower().strip()
        confirmed: bool = normalized_response in (
            "yes",
            "yeah",
            "y",
            "confirm",
            "ok",
            "okay",
            "确认",
            "创建",
            "升级",
            "__review_approve__",
        )
        draft = state.get("escalation_draft", {})
        pending = state.get("pending_side_effect", {})
        task_index = _maybe_int(pending.get("task_index") or draft.get("task_index"))
        if confirmed and draft:
            reviewer_override = (
                "after_sales_operator" if normalized_response == "__review_approve__" else None
            )
            answer = await self._execute_escalation_draft(
                draft,
                state,
                role_override=reviewer_override,
                auth_scopes_override=["after_sales:write"] if reviewer_override else None,
            )
            status = "completed"
        elif confirmed:
            answer = "没有找到可提交的升级草稿，请重新发起。"
            status = "failed"
        elif normalized_response == "__hitl_timeout__":
            answer = "待确认的售后动作已超时自动取消；如果仍需处理，请重新发起任务。"
            status = "timeout_canceled"
        else:
            answer = "已取消创建售后升级 case。"
            status = "canceled"

        completed = list(state.get("completed_tasks", []))
        if task_index is not None:
            completed = _upsert_task_status(completed, task_index, "escalation", status)
        if self._runtime_store is not None:
            await self._runtime_store.clear_pending_confirmation(str(state.get("session_id", "unknown")))

        return {
            "final_answer": answer,
            "messages": [*state["messages"], {"role": "assistant", "content": answer}],
            "completed_tasks": completed,
            "answer_parts": [*state.get("answer_parts", []), answer],
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
                        "task_index": task_index,
                    },
                ),
            ],
        }

    async def _execute_escalation_draft(
        self,
        draft: dict[str, object],
        state: AgentState,
        *,
        role_override: str | None = None,
        auth_scopes_override: list[str] | None = None,
    ) -> str:
        action_type = str(draft.get("action_type", "open_support_case"))
        tool_result = await self._tool_manager.call(
            "execute_side_effect",
            {
                "action_type": action_type,
                "order_id": str(draft["order_id"]),
                "message_text": str(draft["message_text"]),
            },
            self._tool_context(state, role=role_override, auth_scopes=auth_scopes_override),
        )
        action_label = _ACTION_LABELS.get(action_type, "售后升级处理")
        if not tool_result.ok:
            reason = tool_result.error_message or tool_result.error_code or "unknown error"
            return f"未执行{action_label}：{reason}。"
        result = tool_result.data["result"]
        duplicate_hint = "（重复请求，已返回已有结果）" if result["duplicate"] else ""
        return f"已执行{action_label}：{result['result_id']}。{duplicate_hint}"

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
        if any(token in lowered for token in ("complaint", "投诉", "升级投诉", "转人工", "人工介入")):
            return "complaint_escalation"
        return "open_support_case"

    async def _category_insights(self, query: str, state: AgentState) -> list[dict[str, object]]:
        result = await self._tool_manager.call(
            "search_category_risk",
            {"query": query},
            self._tool_context(state),
        )
        return list(result.data.get("insights", [])) if result.ok else []

    async def _after_sales_priority_report(self, query: str, state: AgentState) -> dict[str, object]:
        result = await self._tool_manager.call(
            "generate_after_sales_priority_report",
            {"query": query},
            self._tool_context(state),
        )
        if result.ok:
            return dict(result.data.get("report", {}))
        return {
            "summary": f"权限不足或运营报表工具不可用：{result.error_message or result.error_code}",
            "high_risk_categories": [],
            "priority_orders": [],
        }

    async def _search_policy_knowledge(
        self,
        query: str,
        state: AgentState,
        k: int = 3,
    ) -> list[dict[str, object]]:
        result = await self._tool_manager.call(
            "search_policy_knowledge",
            {"query": query, "k": k},
            self._tool_context(state),
        )
        return list(result.data.get("sections", [])) if result.ok else []

    async def _search_support_docs(
        self,
        query: str,
        state: AgentState,
        k: int = 3,
    ) -> list[dict[str, object]]:
        result = await self._tool_manager.call(
            "search_support_examples",
            {"query": query, "k": k},
            self._tool_context(state),
        )
        return list(result.data.get("docs", [])) if result.ok else []

    @staticmethod
    def _tool_context(
        state: AgentState,
        role: str | None = None,
        auth_scopes: list[str] | None = None,
    ) -> ToolCallContext:
        return ToolCallContext(
            session_id=state.get("session_id", "unknown"),
            tenant_id=state.get("tenant_id", "olist-demo"),
            user_id=state.get("user_id", "demo-user"),
            role=role or state.get("role", "support_agent"),
            auth_scopes=list(auth_scopes if auth_scopes is not None else state.get("auth_scopes", [])),
        )


def _decision_stops_execution(decision: dict[str, object], verification: dict[str, object]) -> bool:
    outcome = str(decision.get("outcome", ""))
    next_step = str(verification.get("required_next_step", ""))
    return outcome in {"reject", "ask_clarification"} or next_step in {"clarify", "stop"}


def _format_after_sales_decision_answer(
    order_id: str,
    action_type: str,
    draft: dict[str, object],
    *,
    requires_confirmation: bool,
    customer_view: bool = False,
) -> str:
    decision = dict(draft.get("decision", {}))
    verification = dict(draft.get("verification", {}))
    action_label = _ACTION_LABELS.get(action_type, "售后处理")
    outcome = str(decision.get("outcome", "needs_human_review"))
    risk = str(decision.get("risk_level", "medium"))
    confidence = decision.get("confidence", "")
    refs = decision.get("policy_refs", [])
    refs_text = "、".join(str(ref) for ref in refs[:3]) if isinstance(refs, list) else ""
    evidence = decision.get("evidence", [])
    evidence_text = "；".join(str(item) for item in evidence[:4]) if isinstance(evidence, list) else ""
    flags = verification.get("flags", [])
    flags_text = "；".join(str(flag) for flag in flags) if isinstance(flags, list) and flags else "无"
    next_step = str(verification.get("required_next_step", "hitl"))

    if customer_view:
        return _format_customer_after_sales_answer(
            order_id=order_id,
            action_label=action_label,
            outcome=outcome,
            next_step=next_step,
            draft=draft,
            requires_confirmation=requires_confirmation,
        )

    lines = [
        "售后 case 决策：",
        f"- 动作：{action_label}",
        f"- 结论：{outcome}；风险等级：{risk}；置信度：{confidence}",
        f"- 证据：{evidence_text}",
        f"- 政策依据：{refs_text or '未命中明确政策，需谨慎处理'}",
        f"- Verifier：next_step={next_step}；flags={flags_text}",
        "",
        f"客户回复草稿：{draft['message_text']}",
    ]
    if requires_confirmation:
        lines.extend(
            [
                "",
                "该动作会改变业务状态，必须经过 HITL 确认后才会调用企业工具。",
                "是否确认执行？(yes/no)",
            ]
        )
    elif next_step == "execute":
        lines.extend(
            [
                "",
                "该动作满足低风险自动执行门禁，系统会继续调用企业写工具并记录审计日志。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                f"因此本轮不会执行 {order_id} 的副作用工具；可补充信息后重新发起。",
            ]
        )
    return "\n".join(lines)


def _is_customer_channel(state: AgentState) -> bool:
    return state.get("channel") == "customer_self_service" or state.get("role") == "customer"


def _format_customer_after_sales_answer(
    *,
    order_id: str,
    action_label: str,
    outcome: str,
    next_step: str,
    draft: dict[str, object],
    requires_confirmation: bool,
) -> str:
    reply = str(draft.get("message_text", "")).strip().replace("。。", "。")
    short_order_id = order_id[:8]

    if outcome == "reject" or next_step == "stop":
        return (
            f"订单 {short_order_id} 的{action_label}暂时无法直接办理。\n"
            f"{reply or '我已经根据订单状态和售后规则完成核查，如需继续处理可以补充新的凭证或诉求。'}"
        )
    if outcome == "ask_clarification" or next_step == "clarify":
        return (
            f"订单 {short_order_id} 的{action_label}还需要补充信息。\n"
            f"{reply or '请补充问题描述、期望处理方式或必要凭证，我会继续帮你生成售后申请。'}"
        )
    if requires_confirmation:
        return (
            f"已为订单 {short_order_id} 生成售后处理单：{action_label}，并提交给售后人员审核。\n"
            f"{reply or '工作人员会结合订单事实、物流状态和平台政策复核后处理。'}\n"
            "该类请求涉及退款、取消、改地址或投诉升级，系统会先冻结为待审核 case，审核通过后再调用企业工具。"
        )
    return (
        f"订单 {short_order_id} 的{action_label}已通过低风险自动处理门禁。\n"
        f"{reply or '系统会继续完成后续处理，并保留审计记录。'}"
    )


_ACTION_LABELS = {
    "open_support_case": "创建售后工单",
    "refund_request": "提交退款/补偿申请",
    "cancel_order": "提交取消订单申请",
    "change_address": "提交改地址申请",
    "invoice_request": "提交发票申请",
    "complaint_escalation": "提交投诉升级工单",
}

_INTENT_LABELS = {
    "order_status": "订单查询",
    "qa": "运营分析",
    "ops_decision": "审核台优先处理",
    "policy": "政策问答",
    "escalation": "售后处理",
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


def _latest_user_task_message(state: AgentState) -> str:
    confirmation_replies = {
        "yes",
        "yeah",
        "y",
        "confirm",
        "ok",
        "okay",
        "no",
        "n",
        "cancel",
        "确认",
        "创建",
        "升级",
        "取消",
        "不用",
        "不要",
        "算了",
        "放弃",
        "否",
        "__hitl_timeout__",
    }
    for message in reversed(state.get("messages", [])):
        if message.get("role") != "user":
            continue
        content = str(message.get("content", ""))
        if content.lower().strip() not in confirmation_replies:
            return content
    return str(state["messages"][-1]["content"])


def _completed_task_indices(completed: list[dict[str, object]]) -> set[int]:
    return {
        int(item["index"])
        for item in completed
        if item.get("status") == "completed" and _maybe_int(item.get("index")) is not None
    }


def _terminal_task_indices(completed: list[dict[str, object]]) -> set[int]:
    terminal = {"completed", "canceled", "timeout_canceled", "failed", "blocked"}
    return {
        int(item["index"])
        for item in completed
        if item.get("status") in terminal and _maybe_int(item.get("index")) is not None
    }


def _upsert_task_status(
    completed: list[dict[str, object]],
    index: int,
    intent: str,
    status: str,
) -> list[dict[str, object]]:
    row = {"index": index, "intent": intent, "status": status}
    updated = []
    replaced = False
    for item in completed:
        if item.get("index") == index:
            updated.append(row)
            replaced = True
        else:
            updated.append(item)
    if not replaced:
        updated.append(row)
    return updated


def _should_include_previous_answer(state: AgentState) -> bool:
    events = state.get("trajectory_events", [])
    return bool(
        state.get("final_answer")
        and events
        and events[-1].get("node") == "finalize_escalation"
    )


def _maybe_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


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
