from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.actions import AgentActions
from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.llm.client import LlmClient
from app.llm.guardrail import Guardrail
from app.llm.intent_planner import IntentPlanner
from app.llm.response_generator import OlistTaskExtractor, PolicyResponseGenerator, QaResponseGenerator
from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import InMemoryCaseService, OlistService
from app.retrieval.hybrid import HybridSupportRetriever

ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "evaluation" / "live_agent_eval_results.jsonl"
OUT_MD = ROOT / "evaluation" / "live_agent_eval_report.md"


@dataclass(frozen=True)
class LiveCase:
    name: str
    message: str
    expected_tasks: list[str]
    expected_tools: list[str]
    requires_hitl: bool
    answer_keyword_groups: tuple[tuple[str, ...], ...]


CASES = [
    LiveCase(
        name="order_status",
        message="帮我查一下订单 203096f03d82e0dffbc41ebc2e2bcfb7 的状态",
        expected_tasks=["order_status"],
        expected_tools=["get_order_status"],
        requires_hitl=False,
        answer_keyword_groups=(("203096f03d82e0dffbc41ebc2e2bcfb7",), ("delivered", "已送达")),
    ),
    LiveCase(
        name="category_risk",
        message="health beauty 类目有什么运营风险？",
        expected_tasks=["qa"],
        expected_tools=["search_category_risk"],
        requires_hitl=False,
        answer_keyword_groups=(("health_beauty", "健康美妆"), ("延迟", "delay"), ("评分", "评价", "review")),
    ),
    LiveCase(
        name="policy_boundary",
        message="退款补偿能不能直接承诺？",
        expected_tasks=["policy"],
        expected_tools=["search_policy_knowledge"],
        requires_hitl=False,
        answer_keyword_groups=(("退款",), ("补偿",), ("人工", "确认", "审核")),
    ),
    LiveCase(
        name="ops_decision",
        message="生成售后运营风险日报，列出优先跟进类目和订单",
        expected_tasks=["ops_decision"],
        expected_tools=["generate_after_sales_priority_report"],
        requires_hitl=False,
        answer_keyword_groups=(("高风险类目",), ("优先跟进订单",), ("HITL", "人工确认")),
    ),
    LiveCase(
        name="multi_intent_hitl",
        message=(
            "查订单 203096f03d82e0dffbc41ebc2e2bcfb7 状态，并且说明退款政策，"
            "然后生成售后升级话术"
        ),
        expected_tasks=["order_status", "policy", "escalation"],
        expected_tools=["get_order_status", "search_policy_knowledge", "prepare_side_effect"],
        requires_hitl=True,
        answer_keyword_groups=(("订单查询",), ("政策问答",), ("售后升级",), ("是否确认执行",)),
    ),
]


async def main() -> None:
    limit = int(os.environ.get("LIVE_AGENT_EVAL_LIMIT", "5"))
    offset = int(os.environ.get("LIVE_AGENT_EVAL_OFFSET", "0"))
    cases = CASES[offset : offset + limit]
    llm_client = LlmClient(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("OPENAI_MODEL", "deepseek-v4-flash"),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
    )
    graph = _build_graph(llm_client)
    guardrail = Guardrail(llm_client.chat_openai)

    rows = []
    for case in cases:
        t0 = time.perf_counter()
        result = await _run_case(graph, guardrail, case)
        latency_ms = (time.perf_counter() - t0) * 1000
        row = _score_case(case, result, latency_ms, llm_client)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    OUT_JSONL.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    OUT_MD.write_text(_render_report(rows, llm_client), encoding="utf-8")
    print("")
    print(_summary_line(rows))
    print(f"Wrote {OUT_JSONL}")
    print(f"Wrote {OUT_MD}")


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


async def _run_case(graph, guardrail: Guardrail, case: LiveCase) -> dict:
    input_check = await guardrail.check_input(case.message)
    if not input_check.on_topic:
        return {
            "route_intent": "input_guard",
            "task_plan": [],
            "completed_tasks": [],
            "trajectory_events": [],
            "final_answer": f"Rejected by guard: {input_check.reason}",
            "output_valid": False,
        }
    session_id = f"live-agent-eval-{case.name}-{uuid.uuid4().hex[:8]}"
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
    result = await graph.ainvoke(state, {"configurable": {"thread_id": session_id}})
    output_check = await guardrail.check_output(str(result.get("final_answer", "")))
    return {**result, "output_valid": output_check.valid, "output_guard_reason": output_check.reason}


def _score_case(case: LiveCase, result: dict, latency_ms: float, llm_client: LlmClient) -> dict:
    tasks = [str(task.get("intent")) for task in result.get("task_plan", [])]
    tools = [str(event.get("details", {}).get("tool", "")) for event in result.get("trajectory_events", [])]
    statuses = [str(event.get("status")) for event in result.get("trajectory_events", [])]
    answer = str(result.get("final_answer", ""))
    checks = {
        "task_exact": tasks == case.expected_tasks,
        "tools_used": set(case.expected_tools).issubset(set(tools)),
        "hitl_correct": bool(result.get("pending_side_effect", {}).get("requires_confirmation"))
        == case.requires_hitl,
        "no_failed_event": "failed" not in statuses and "blocked" not in statuses,
        "output_valid": bool(result.get("output_valid")),
        "answer_keywords": all(
            any(keyword.lower() in answer.lower() for keyword in group)
            for group in case.answer_keyword_groups
        ),
    }
    return {
        "case": case.name,
        "provider_mode": llm_client.mode,
        "model": llm_client.model,
        "latency_ms": round(latency_ms, 2),
        "expected_tasks": case.expected_tasks,
        "actual_tasks": tasks,
        "expected_tools": case.expected_tools,
        "actual_tools": [tool for tool in tools if tool],
        "requires_hitl": case.requires_hitl,
        "answer_preview": answer.replace("\n", " ")[:260],
        "checks": checks,
        "passed": all(checks.values()),
    }


def _render_report(rows: list[dict], llm_client: LlmClient) -> str:
    lines = [
        "# Live Agent Eval Report",
        "",
        (
            "This report is generated by `python -m evaluation.live_agent_eval` "
            "with a real LLM in the Agent loop."
        ),
        f"mode: `{llm_client.mode}`; model: `{llm_client.model}`; cases: `{len(rows)}`.",
        "",
        "| case | passed | tasks | tools | hitl | latency_ms | failed_checks |",
        "|---|---:|---|---|---:|---:|---|",
    ]
    for row in rows:
        failed = [name for name, ok in row["checks"].items() if not ok]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["case"],
                    str(row["passed"]),
                    ",".join(row["actual_tasks"]),
                    ",".join(row["actual_tools"]),
                    str(row["requires_hitl"]),
                    str(row["latency_ms"]),
                    ",".join(failed) or "-",
                ]
            )
            + " |"
        )
    lines.extend(["", _summary_line(rows)])
    return "\n".join(lines)


def _summary_line(rows: list[dict]) -> str:
    if not rows:
        return "No live eval rows."
    check_names = list(rows[0]["checks"])
    parts = [f"case_pass_rate={sum(row['passed'] for row in rows) / len(rows):.2%}"]
    for name in check_names:
        parts.append(f"{name}={sum(row['checks'][name] for row in rows) / len(rows):.2%}")
    return "; ".join(parts)


if __name__ == "__main__":
    asyncio.run(main())
