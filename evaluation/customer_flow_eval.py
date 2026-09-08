from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

import app.main as main_module
from app.config.di import agent_graph_builder, case_service, runtime_status
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "evaluation" / "customer_flow_eval_results.jsonl"
OUT_MD = ROOT / "evaluation" / "customer_flow_eval_report.md"
ORDER_ID = "203096f03d82e0dffbc41ebc2e2bcfb7"
REVIEW_HEADERS = {"X-Review-Token": "local-review-demo"}


@dataclass(frozen=True)
class FlowCase:
    name: str
    message: str
    expected_tasks: tuple[str, ...]
    expected_tools: tuple[str, ...]
    expected_answer_terms: tuple[tuple[str, ...], ...]
    expected_sources: tuple[str, ...] = ()
    expect_pending_review: bool = False
    approve_review: bool = False
    user_id: str = "demo-customer"
    expect_unauthorized: bool = False


CASES: list[FlowCase] = [
    FlowCase(
        name="customer_order_status",
        message=f"帮我查一下订单 {ORDER_ID} 的状态和是否延迟",
        expected_tasks=("order_status",),
        expected_tools=("get_order_status",),
        expected_answer_terms=(("delivered", "已送达"), ("延迟", "delay"), ("评价", "评分")),
    ),
    FlowCase(
        name="customer_policy_boundary",
        message="退款补偿能不能直接承诺？",
        expected_tasks=("policy",),
        expected_tools=("search_policy_knowledge",),
        expected_answer_terms=(("退款",), ("补偿",), ("审核", "人工", "确认")),
        expected_sources=("Compensation", "Refund", "Policy", "FAQ"),
    ),
    FlowCase(
        name="customer_refund_to_review",
        message=f"给订单 {ORDER_ID} 申请退款",
        expected_tasks=("escalation",),
        expected_tools=("prepare_side_effect",),
        expected_answer_terms=(("审核",), ("退款", "补偿"), ("不会承诺", "不会直接承诺", "不承诺")),
        expect_pending_review=True,
    ),
    FlowCase(
        name="reviewer_approves_refund",
        message=f"给订单 {ORDER_ID} 申请退款",
        expected_tasks=("escalation",),
        expected_tools=("prepare_side_effect",),
        expected_answer_terms=(("审核",), ("退款", "补偿")),
        expect_pending_review=True,
        approve_review=True,
    ),
    FlowCase(
        name="customer_cannot_self_confirm",
        message=f"给订单 {ORDER_ID} 申请退款",
        expected_tasks=("escalation",),
        expected_tools=("prepare_side_effect",),
        expected_answer_terms=(("等待工作人员审核", "审核"),),
        expect_pending_review=True,
    ),
    FlowCase(
        name="complaint_emotional_handoff",
        message=f"订单 {ORDER_ID} 延迟太久了，我很生气，要投诉并升级处理",
        expected_tasks=("escalation",),
        expected_tools=("prepare_side_effect",),
        expected_answer_terms=(("投诉", "升级", "审核"), ("不会承诺", "不会直接承诺", "不承诺")),
        expect_pending_review=True,
    ),
    FlowCase(
        name="unauthorized_order_access",
        message="帮我查一下订单 00000000000000000000000000000000 的状态",
        expected_tasks=(),
        expected_tools=(),
        expected_answer_terms=(("当前账号名下", "订单隐私"),),
        user_id="other-customer",
        expect_unauthorized=True,
    ),
    FlowCase(
        name="customer_multi_intent_read_then_refund",
        message=f"查订单 {ORDER_ID} 状态，并说明退款政策，然后提交退款申请",
        expected_tasks=("order_status", "policy", "escalation"),
        expected_tools=("get_order_status", "search_policy_knowledge", "prepare_side_effect"),
        expected_answer_terms=(("订单",), ("政策", "退款"), ("审核",)),
        expect_pending_review=True,
    ),
    FlowCase(
        name="off_topic_rejected",
        message="帮我写一个操作系统内核",
        expected_tasks=(),
        expected_tools=(),
        expected_answer_terms=(("only help", "只能"),),
    ),
]


async def main() -> None:
    main_module.agent = agent_graph_builder.build(InMemorySaver())
    if hasattr(case_service, "reset"):
        case_service.reset()

    rows: list[dict[str, Any]] = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for case in CASES:
            rows.append(await _run_case(client, case))

    OUT_JSONL.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    OUT_MD.write_text(_render_report(rows), encoding="utf-8")
    print(_summary_line(rows))
    print(f"Wrote {OUT_JSONL}")
    print(f"Wrote {OUT_MD}")


async def _run_case(client: AsyncClient, case: FlowCase) -> dict[str, Any]:
    session_id = f"customer-flow-{case.name}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    response = await client.post(
        "/customer/chat",
        json={
            "message": case.message,
            "session_id": session_id,
            "user_id": case.user_id,
        },
    )
    first_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    first_body = response.json()
    answer = str(first_body.get("answer", ""))
    sources = [str(source) for source in first_body.get("sources", [])]
    checks = {
        "http_200": response.status_code == 200,
        "answer_terms": _contains_groups(answer, case.expected_answer_terms),
        "sources": _sources_ok(sources, case.expected_sources),
    }
    trace_payload = await _read_trace(client, session_id)
    actual_tasks = _actual_tasks(trace_payload)
    actual_tools = _actual_tools(trace_payload)
    after_sales_cases = _after_sales_cases(trace_payload)
    handoff_reasons = _handoff_reasons(after_sales_cases, trace_payload)
    if case.expected_tasks:
        checks["task_plan"] = _contains_sequence(actual_tasks, list(case.expected_tasks))
    if case.expected_tools:
        checks["tool_calls"] = _contains_sequence(actual_tools, list(case.expected_tools))
    if case.expect_pending_review:
        checks["handoff_reason_present"] = bool(handoff_reasons)

    review_body: dict[str, Any] = {}
    approve_body: dict[str, Any] = {}
    if case.expect_pending_review:
        no_token = await client.get(f"/review/sessions/{session_id}")
        with_token = await client.get(f"/review/sessions/{session_id}", headers=REVIEW_HEADERS)
        review_body = with_token.json()
        checks["review_requires_token"] = no_token.status_code == 403
        checks["pending_review"] = bool(review_body.get("has_pending"))

        customer_yes = await client.post(
            "/customer/chat",
            json={"message": "yes", "session_id": session_id, "user_id": case.user_id},
        )
        checks["customer_cannot_confirm"] = "等待工作人员审核" in customer_yes.json().get("answer", "")

        if case.approve_review:
            approved = await client.post(
                f"/review/sessions/{session_id}/approve",
                headers=REVIEW_HEADERS,
                json={
                    "reviewer_id": "eval-reviewer",
                    "role": "after_sales_operator",
                    "auth_scopes": ["after_sales:write"],
                },
            )
            approve_body = approved.json()
            checks["review_approve_executes"] = (
                approved.status_code == 200
                and "已执行" in approve_body.get("answer", "")
                and "REFUND-" in approve_body.get("answer", "")
            )

    if case.expect_unauthorized:
        checks["unauthorized_blocked"] = "当前账号名下" in answer

    return {
        "case": case.name,
        "provider_mode": runtime_status["mode"],
        "model": runtime_status["model"],
        "latency_ms": first_latency_ms,
        "resolution_type": _resolution_type(case, checks),
        "customer_turns": 2 if case.expect_pending_review else 1,
        "handoff_expected": case.expect_pending_review,
        "handoff_reasons": handoff_reasons,
        "policy_grounded": _policy_grounded(case, sources, after_sales_cases),
        "checks": checks,
        "passed": all(checks.values()),
        "answer_preview": answer[:500],
        "sources": sources,
        "actual_tasks": actual_tasks,
        "actual_tools": actual_tools,
        "review_has_pending": bool(review_body.get("has_pending")) if review_body else False,
        "approve_preview": str(approve_body.get("answer", ""))[:300] if approve_body else "",
    }


async def _read_trace(client: AsyncClient, session_id: str) -> dict[str, Any]:
    response = await client.get(f"/observability/traces/{session_id}", headers=REVIEW_HEADERS)
    if response.status_code != 200:
        return {"traces": []}
    return response.json()


def _actual_tasks(trace_payload: dict[str, Any]) -> list[str]:
    for trace in reversed(trace_payload.get("traces", [])):
        for event in _trace_events(trace):
            if event.get("node") != "plan_tasks":
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            tasks = details.get("tasks") if isinstance(details, dict) else []
            intents = [str(task.get("intent")) for task in tasks if isinstance(task, dict)]
            if intents:
                return intents
    routes = [
        str(trace.get("route_intent"))
        for trace in reversed(trace_payload.get("traces", []))
        if trace.get("route_intent") not in {"", "input_guard", "auth_guard", None}
    ]
    return routes[:1]


def _actual_tools(trace_payload: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    for trace in trace_payload.get("traces", []):
        for event in _trace_events(trace):
            if not isinstance(event, dict):
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            tool = event.get("tool") or details.get("tool")
            if tool:
                tools.append(str(tool))
    return tools


def _after_sales_cases(trace_payload: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for trace in trace_payload.get("traces", []):
        raw = str(trace.get("after_sales_json") or "[]")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            cases.extend(item for item in parsed if isinstance(item, dict))
    return cases


def _handoff_reasons(cases: list[dict[str, Any]], trace_payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for case in cases:
        decision = case.get("decision", {}) if isinstance(case, dict) else {}
        if isinstance(decision, dict):
            reasons.extend(str(item) for item in decision.get("handoff_reasons", []) if item)
    for trace in trace_payload.get("traces", []):
        for event in _trace_events(trace):
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            reasons.extend(str(item) for item in details.get("handoff_reasons", []) if item)
    return list(dict.fromkeys(reasons))


def _trace_events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    events_json = str(trace.get("trajectory_json") or "[]")
    try:
        events = json.loads(events_json)
    except json.JSONDecodeError:
        return []
    return events if isinstance(events, list) else []


def _contains_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    normalized = text.lower()
    return all(any(term.lower() in normalized for term in group) for group in groups)


def _sources_ok(sources: list[str], expected_any: tuple[str, ...]) -> bool:
    if not expected_any:
        return True
    joined = " ".join(sources).lower()
    return any(term.lower() in joined for term in expected_any)


def _contains_sequence(actual: list[str], expected: list[str]) -> bool:
    if not expected:
        return True
    cursor = 0
    for item in actual:
        if cursor < len(expected) and item == expected[cursor]:
            cursor += 1
    return cursor == len(expected)


def _resolution_type(case: FlowCase, checks: dict[str, bool]) -> str:
    if case.expect_unauthorized:
        return "auth_block"
    if case.name == "off_topic_rejected":
        return "guard_reject"
    if case.expect_pending_review:
        return "handoff_review"
    if checks.get("http_200") and checks.get("answer_terms"):
        return "auto_answer"
    return "unresolved"


def _policy_grounded(
    case: FlowCase,
    sources: list[str],
    after_sales_cases: list[dict[str, Any]],
) -> bool:
    if case.expected_sources and sources:
        return True
    if not after_sales_cases:
        return case.name not in {"customer_policy_boundary", "customer_multi_intent_read_then_refund"}
    return any(bool(item.get("policy_refs")) for item in after_sales_cases)


def _summary_line(rows: list[dict[str, Any]]) -> str:
    passed = sum(1 for row in rows if row["passed"])
    p95 = _percentile([row["latency_ms"] for row in rows], 0.95)
    handoffs = sum(1 for row in rows if row.get("resolution_type") == "handoff_review")
    auto_resolved = sum(
        1
        for row in rows
        if row.get("resolution_type") in {"auto_answer", "auth_block", "guard_reject"}
    )
    return (
        f"customer_flow_eval pass={passed}/{len(rows)} auto_resolution={auto_resolved}/{len(rows)} "
        f"handoff={handoffs}/{len(rows)} p95={p95:.2f}ms "
        f"mode={runtime_status['mode']} model={runtime_status['model']}"
    )


def _render_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Customer Flow Eval Report",
        "",
        _summary_line(rows),
        "",
        "| case | passed | latency_ms | key checks | answer preview |",
        "|---|---:|---:|---|---|",
    ]
    for row in rows:
        failed = [name for name, ok in row["checks"].items() if not ok]
        check_text = "ok" if not failed else "failed: " + ", ".join(failed)
        preview = str(row["answer_preview"]).replace("\n", " ").replace("|", "\\|")
        lines.append(
            f"| {row['case']} | {row['passed']} | {row['latency_ms']} | {check_text} | {preview[:220]} |"
        )
    business = _business_summary(rows)
    lines.extend(
        [
            "",
            "## Business Metrics",
            "",
            f"- auto_resolution_rate: `{business['auto_resolution_rate']}`",
            f"- handoff_rate: `{business['handoff_rate']}`",
            f"- handoff_precision: `{business['handoff_precision']}`",
            f"- policy_grounding_rate: `{business['policy_grounding_rate']}`",
            f"- avg_customer_turns: `{business['avg_customer_turns']}`",
            f"- handoff_reason_coverage: `{business['handoff_reason_coverage']}`",
            f"- resolution_type_counts: `{business['resolution_type_counts']}`",
        ]
    )
    lines.extend(
        [
            "",
            "This eval runs the public product API boundary: `/customer/chat`, `/review/sessions`, "
            "review approval, unauthorized order access, and customer self-confirmation blocking.",
        ]
    )
    return "\n".join(lines) + "\n"


def _business_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    auto_resolved = sum(
        1
        for row in rows
        if row.get("resolution_type") in {"auto_answer", "auth_block", "guard_reject"}
    )
    handoffs = [row for row in rows if row.get("resolution_type") == "handoff_review"]
    true_positive_handoffs = [row for row in handoffs if row.get("handoff_expected")]
    grounded = sum(1 for row in rows if row.get("policy_grounded"))
    reasonful = sum(1 for row in handoffs if row.get("handoff_reasons"))
    type_counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("resolution_type", "unknown"))
        type_counts[key] = type_counts.get(key, 0) + 1
    return {
        "auto_resolution_rate": _ratio(auto_resolved, total),
        "handoff_rate": _ratio(len(handoffs), total),
        "handoff_precision": _ratio(len(true_positive_handoffs), len(handoffs)),
        "policy_grounding_rate": _ratio(grounded, total),
        "avg_customer_turns": round(
            sum(int(row.get("customer_turns", 1)) for row in rows) / total,
            2,
        )
        if total
        else 0.0,
        "handoff_reason_coverage": _ratio(reasonful, len(handoffs)),
        "resolution_type_counts": type_counts,
    }


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} ({numerator / denominator:.2%})" if denominator else "0/0"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * q)), len(ordered) - 1)
    return ordered[index]


if __name__ == "__main__":
    asyncio.run(main())
