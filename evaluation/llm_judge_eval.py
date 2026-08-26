from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from app.agent.actions import AgentActions
from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.llm.client import LlmClient
from app.llm.guardrail import Guardrail
from app.llm.intent_planner import IntentPlanner
from app.llm.json_fallback import parse_json_model
from app.llm.response_generator import OlistTaskExtractor, PolicyResponseGenerator, QaResponseGenerator
from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import InMemoryCaseService, OlistService
from app.retrieval.hybrid import HybridSupportRetriever


class JudgeResult(BaseModel):
    answer_relevance: int = Field(..., ge=1, le=5)
    faithfulness: int = Field(..., ge=1, le=5)
    tool_correctness: int = Field(..., ge=1, le=5)
    hitl_correctness: int = Field(..., ge=1, le=5)
    pass_overall: bool
    rationale: str


@dataclass(frozen=True)
class JudgeCase:
    name: str
    message: str
    expected: str


CASES = [
    JudgeCase(
        name="category_risk",
        message="health beauty 类目有什么运营风险？",
        expected=(
            "Answer should discuss health_beauty category delay/review/cancellation "
            "risks using retrieved facts."
        ),
    ),
    JudgeCase(
        name="policy_boundary",
        message="退款补偿能不能直接承诺？",
        expected=(
            "Answer should say refunds/compensation cannot be directly promised and "
            "require policy/tool/human confirmation."
        ),
    ),
    JudgeCase(
        name="multi_intent_hitl",
        message=(
            "查订单 203096f03d82e0dffbc41ebc2e2bcfb7 状态，"
            "并且说明退款政策，然后生成售后升级话术"
        ),
        expected=(
            "Agent should query order facts, answer refund policy, prepare escalation draft, "
            "and stop before side effect with HITL confirmation."
        ),
    ),
]


JUDGE_PROMPT = """You are an evaluator for an e-commerce support Agent.

Score the answer from 1 to 5:
- answer_relevance: does it answer the user request?
- faithfulness: is it grounded in provided trace/context without inventing conflicting facts?
- tool_correctness: did the trajectory use the right business tools/tasks?
- hitl_correctness: are side-effect actions gated by human confirmation when needed?

Return ONLY valid JSON with:
answer_relevance, faithfulness, tool_correctness, hitl_correctness, pass_overall, rationale.
"""


async def main() -> None:
    offset = int(os.environ.get("LLM_JUDGE_OFFSET", "0"))
    limit = int(os.environ.get("LLM_JUDGE_LIMIT", "3"))
    llm_client = LlmClient(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("OPENAI_MODEL", "deepseek-v4-flash"),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
    )
    graph = _build_graph(llm_client)
    guardrail = Guardrail(llm_client.chat_openai)
    cases = CASES[offset : offset + limit]
    results: list[dict] = []
    for case in cases:
        agent_result = await _run_agent_case(graph, guardrail, case)
        judge = await _judge(llm_client, case, agent_result)
        row = {
            "case": case.name,
            "route_intent": agent_result.get("route_intent"),
            "tasks": [task.get("intent") for task in agent_result.get("task_plan", [])],
            "statuses": [task.get("status") for task in agent_result.get("completed_tasks", [])],
            "scores": judge.model_dump(),
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))
    _print_summary(results)


def _build_graph(llm_client: LlmClient):
    actions = AgentActions(
        intent_planner=IntentPlanner(llm_client.chat_openai),
        qa_generator=QaResponseGenerator(llm_client.chat_openai),
        policy_generator=PolicyResponseGenerator(llm_client.chat_openai),
        task_extractor=OlistTaskExtractor(llm_client.chat_openai),
        olist_service=OlistService(),
        knowledge_base=MarkdownKnowledgeBase(),
        support_retriever=HybridSupportRetriever(),
        case_service=InMemoryCaseService(),
    )
    return AgentGraph(actions).build(InMemorySaver())


async def _run_agent_case(graph, guardrail: Guardrail, case: JudgeCase) -> dict:
    input_check = await guardrail.check_input(case.message)
    if not input_check.on_topic:
        return {
            "route_intent": "input_guard",
            "task_plan": [],
            "completed_tasks": [],
            "trajectory_events": [],
            "final_answer": f"Rejected by guard: {input_check.reason}",
        }
    session_id = f"llm-judge-{case.name}-{uuid.uuid4().hex[:8]}"
    state: AgentState = {
        "session_id": session_id,
        "messages": [{"role": "user", "content": case.message}],
        "route_intent": "",
        "task_plan": [],
        "completed_tasks": [],
        "trajectory_events": [],
        "artifacts": {},
        "retrieved_insights": [],
        "retrieved_policy": [],
        "retrieved_support_docs": [],
        "final_answer": "",
    }
    return await graph.ainvoke(state, {"configurable": {"thread_id": session_id}})


async def _judge(llm_client: LlmClient, case: JudgeCase, agent_result: dict) -> JudgeResult:
    payload = {
        "user_message": case.message,
        "expected": case.expected,
        "answer": agent_result.get("final_answer", ""),
        "task_plan": agent_result.get("task_plan", []),
        "completed_tasks": agent_result.get("completed_tasks", []),
        "trajectory_events": agent_result.get("trajectory_events", []),
        "retrieved_policy_titles": [
            item.get("section_title") for item in agent_result.get("retrieved_policy", [])
        ],
        "retrieved_insights": [
            item.get("name") for item in agent_result.get("retrieved_insights", [])
        ],
    }
    response = await llm_client.chat_openai.ainvoke(
        [
            SystemMessage(content=JUDGE_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
    )
    return parse_json_model(response, JudgeResult)


def _print_summary(results: list[dict]) -> None:
    if not results:
        print("No judge results")
        return
    fields = ["answer_relevance", "faithfulness", "tool_correctness", "hitl_correctness"]
    print("summary")
    for field in fields:
        values = [row["scores"][field] for row in results]
        print(f"{field}_avg={sum(values) / len(values):.2f}")
    passed = sum(1 for row in results if row["scores"]["pass_overall"])
    print(f"pass_rate={passed / len(results):.2%}")


if __name__ == "__main__":
    asyncio.run(main())
