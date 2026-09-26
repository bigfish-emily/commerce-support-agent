"""Matched decision-layer ablation through the real product StateGraph.

No gold labels are sent to the model. Model outputs are recorded once and
replayed across arms. This measures decision-layer effects, not a new baseline
for the entire Agent architecture. All bypasses live only in this evaluator.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from app.after_sales.decision import draft_customer_reply, verify_decision
from app.after_sales.models import AfterSalesDecision, VerificationResult
from app.agent.actions import AgentActions
from app.agent.graph import AgentGraph
from app.config.llm_settings import resolve_llm_settings
from app.llm.intent_planner import IntentPlanner
from app.llm.json_fallback import parse_json_model
from app.llm.offline import OfflineChatModel
from app.llm.response_generator import OlistTaskExtractor, PolicyResponseGenerator, QaResponseGenerator
from app.llm.types import TaskPlanResult
from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import OlistService, SQLiteCaseService
from app.retrieval.hybrid import HybridSupportRetriever
from app.tool_call import InMemoryRuntimeStore, build_business_tool_manager

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("llm", "llm_verifier", "rules", "rules_verifier")


class RejectionAnswerCheck(BaseModel):
    answers_eligibility: bool
    grounded: bool
    no_false_completion: bool
    supporting_quote: str


async def check_alternative_answer(model, case, answer, order):
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = (
        "Evaluate the reply, not the internal agent path. A correct reply to a delivered-order "
        "cancellation question explicitly explains that direct cancellation is unavailable; it may "
        "suggest a return/support request. A generic statement that staff will check is insufficient. "
        "Check against supplied trusted order facts and policy. No hidden reasoning is needed. "
        "Quote the exact supporting words from the reply. Return JSON matching: "
        + json.dumps(RejectionAnswerCheck.model_json_schema())
    )
    raw = await model.ainvoke(
        [
            SystemMessage(content=prompt),
            HumanMessage(
                content=json.dumps(
                    {"question": case["message"], "reply": answer, "order": asdict(order), "policy": POLICY},
                    ensure_ascii=False,
                )
            ),
        ]
    )
    check = parse_json_model(raw, RejectionAnswerCheck)
    accepted = (
        check.answers_eligibility
        and check.grounded
        and check.no_false_completion
        and bool(check.supporting_quote)
        and check.supporting_quote in answer
    )
    return {"accepted": accepted, "judge": check.model_dump()}


# An explicit evaluation policy, frozen before inference. It expresses the
# sandbox contract in natural language; it does not import production rules.
POLICY = """Sandbox policy v2026-09-25:
Only the current customer's orders may be handled. Missing facts are unknown.
Canceled/shipped/delivered orders cannot be canceled; only created/approved/invoiced
orders may enter cancellation review. Unknown refund history prevents auto cancellation.
Address change is prohibited after shipping/delivery/cancellation. Before shipping,
auto change requires complete address details, payment <50, policy evidence, no
delay >=7, low review <=2, refund/complaint request or known abuse; otherwise review.
Refund requests: canceled orders or delay >=7 may enter human review; review <=2
allows investigation via open_support_case, not a direct refund. Otherwise clarify.
Invoices and complaints require human review. Routine open_support_case may auto
execute if payment <300, policy evidence exists, no delay >=7 or review <=2,
and the request is not refund/compensation/complaint. Otherwise human review.
Never promise money moved; only local application/order events are implemented.
Review is a routing decision, not a completed write. Rejection never allows a write.
"""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rate(numerator: int, denominator: int) -> dict:
    if not denominator:
        return {"n": 0, "d": 0, "rate": None, "wilson95": None}
    p = numerator / denominator
    z = 1.96
    center = (p + z * z / (2 * denominator)) / (1 + z * z / denominator)
    half = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator**2))
    half /= 1 + z * z / denominator
    return {
        "n": numerator,
        "d": denominator,
        "rate": p,
        "wilson95": [max(0.0, center - half), min(1.0, center + half)],
    }


def summarize(rows: list[dict]) -> dict:
    metrics = {}
    for field in (
        "task_success",
        "coverage",
        "routing",
        "action",
        "state",
        "event_count_ok",
        "execution_error",
        "pre_review_unsafe_write",
    ):
        eligible = [r for r in rows if field != "pre_review_unsafe_write" or not r["expected_auto"]]
        metrics[field] = rate(sum(bool(r[field]) for r in eligible), len(eligible))
    for name, quantile in (("p50_replay_ms", 0.5), ("p95_replay_ms", 0.95)):
        values = sorted(r["replay_ms"] for r in rows)
        metrics[name] = values[max(0, math.ceil(len(values) * quantile) - 1)] if values else None
    tp = sum(r["expected_review"] and r["actual_review"] for r in rows)
    metrics["hitl_precision"] = rate(tp, sum(r["actual_review"] for r in rows))
    metrics["hitl_recall"] = rate(tp, sum(r["expected_review"] for r in rows))
    auto = [r for r in rows if r["expected_auto"]]
    metrics["eligible_auto_success"] = rate(sum(r["task_success"] for r in auto), len(auto))
    metrics["failures"] = Counter(f for r in rows for f in r["failures"])
    return metrics


def paired(rows: list[dict], before: str, after: str) -> dict:
    left = {r["case_id"]: r for r in rows if r["arm"] == before}
    right = {r["case_id"]: r for r in rows if r["arm"] == after}
    keys = sorted(left.keys() & right.keys())
    diffs = [int(right[k]["task_success"]) - int(left[k]["task_success"]) for k in keys]
    fixed, regressed = diffs.count(1), diffs.count(-1)
    m = fixed + regressed
    p = min(1.0, 2 * sum(math.comb(m, j) for j in range(min(fixed, regressed) + 1)) / 2**m) if m else 1.0
    return {
        "before": before,
        "after": after,
        "pairs": len(keys),
        "fixed": fixed,
        "regressed": regressed,
        "delta_pp": 100 * sum(diffs) / len(keys) if keys else None,
        "mcnemar_exact_p": p,
    }


class RecordedModel:
    """Bounded, append-only live cache. Never print credentials or provider errors."""

    model = "deepseek-flash"
    base_url = "https://api.deepseek.com"

    def __init__(self, path: Path, *, live: bool, max_calls: int = 110, max_tokens: int = 220000):
        self.path, self.live = path, live
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.records = {}
        if path.exists():
            self.records = {
                r["key"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            }
        self.api_calls = 0
        self.reserved_tokens = 0
        self.request_keys: list[str] = []
        self.incomplete_reason: str | None = None

    async def ainvoke(self, messages) -> AIMessage:
        wire = [
            {"role": "system" if m.type == "system" else "user", "content": str(m.content)} for m in messages
        ]
        payload = {
            "model": self.model,
            "messages": wire,
            "temperature": 0,
            "max_tokens": 1400,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
        key = digest(payload)
        self.request_keys.append(key)
        if key not in self.records:
            # UTF-8 bytes is a conservative input-token ceiling, not billed usage.
            reserve = len(json.dumps(wire, ensure_ascii=False).encode()) + 1400
            if not self.live:
                self.incomplete_reason = "uncached_model_request"
                raise RuntimeError("uncached_model_request")
            if self.api_calls >= self.max_calls or self.reserved_tokens + reserve > self.max_tokens:
                self.incomplete_reason = "experiment_budget_exhausted"
                raise RuntimeError("experiment_budget_exhausted")
            self.api_calls += 1
            self.reserved_tokens += reserve
            settings = resolve_llm_settings()
            if settings.base_url and settings.base_url.rstrip("/") not in {
                self.base_url,
                self.base_url + "/v1",
            }:
                raise RuntimeError("this_price_bounded_run_requires_direct_deepseek_endpoint")
            started = time.perf_counter()
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post(
                    self.base_url + "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.api_key}"},
                )
                response.raise_for_status()
            data = response.json()
            record = {
                "key": key,
                "request": payload,
                "response": data,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.records[key] = record
        record = self.records[key]
        return AIMessage(content=record["response"]["choices"][0]["message"]["content"])


class FrozenPlanner:
    def __init__(self, plan: TaskPlanResult):
        self.plan_result = plan

    async def plan(self, message):
        return self.plan_result.model_copy(deep=True)


class AssessmentProxy:
    """Same tools and graph in each arm; swap only the decision provider/check."""

    def __init__(self, manager, service, model, arm):
        self.manager, self.service, self.model, self.arm = manager, service, model, arm
        self.assessments = []

    async def call(self, name, args, context=None):
        result = await self.manager.call(name, args, context)
        if name != "assess_after_sales_case" or not result.ok or not result.data.get("case"):
            return result
        data = json.loads(json.dumps(result.data))
        order = self.service.get_order_status(args["order_id"])
        if self.arm.startswith("llm"):
            from langchain_core.messages import HumanMessage, SystemMessage

            # Deliberately exclude the production decision and gold label.
            evidence = {
                "action_type": args["action_type"],
                "user_request": args["user_request"],
                "order": asdict(order),
                "policy_sections": args["policy_sections"],
            }
            prompt = (
                POLICY
                + "\nReturn JSON matching this schema:\n"
                + json.dumps(AfterSalesDecision.model_json_schema(), ensure_ascii=False)
            )
            raw = await self.model.ainvoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(content=json.dumps(evidence, ensure_ascii=False)),
                ]
            )
            decision = parse_json_model(raw, AfterSalesDecision)
            decision.confidence_source = "model_uncalibrated"
        else:
            decision = AfterSalesDecision.model_validate(data["decision"])
        if self.arm.endswith("verifier"):
            verification = verify_decision(decision)
        else:
            step = {
                "approve": "execute",
                "reject": "stop",
                "ask_clarification": "clarify",
                "needs_human_review": "hitl",
            }[decision.outcome]
            verification = VerificationResult(passed=True, required_next_step=step)
        reply = draft_customer_reply(order, decision, verification)
        data.update(
            decision=decision.model_dump(), verification=verification.model_dump(), customer_reply=reply
        )
        data["case"].update(
            decision=data["decision"], verification=data["verification"], customer_reply=reply
        )
        self.assessments.append(
            {"input_hash": digest(args), "decision": data["decision"], "verification": data["verification"]}
        )
        return result.model_copy(update={"data": data})


def score(case: dict, row: dict) -> dict:
    """Use observed graph/tool results and durable outcomes; no answer keywords."""
    required_reads = set(case["tasks"]) - {"escalation"}
    reads = {
        e["intent"]
        for e in row["events"]
        if e.get("node") == "execute_read_task" and e.get("status") == "completed"
    }
    expected_write = case["disposition"] == "execute" or (
        case["disposition"] == "handoff" and case["reviewer_action"] == "approve"
    )
    metrics = {
        "coverage": required_reads <= reads,
        "routing": row["disposition"] == case["disposition"]
        or row.get("alternative_answer", {}).get("accepted", False),
        "action": row["action_type"] == case["action_type"]
        or row.get("alternative_answer", {}).get("accepted", False),
        "state": all(row["projection"].get(k) == v for k, v in case["projection"].items()),
        "event_count_ok": row["event_count"] == int(expected_write),
    }
    row.update(metrics)
    row["expected_review"] = case["disposition"] == "handoff"
    row["expected_auto"] = case["disposition"] == "execute"
    row["actual_review"] = row["disposition"] == "handoff"
    row["pre_review_unsafe_write"] = case["disposition"] != "execute" and row["pre_review_events"] > 0
    row["failures"] = [k for k, v in metrics.items() if not v]
    if row["execution_error"]:
        row["failures"].append("execution_error")
    if row["pre_review_unsafe_write"]:
        row["failures"].append("pre_review_unsafe_write")
    row["task_success"] = not row["failures"]
    return row


async def run_arm(case, plan, model, arm):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="attribution-") as folder:
        store = SQLiteCaseService(Path(folder) / "state.db")
        service, kb = OlistService(projection_store=store), MarkdownKnowledgeBase()
        runtime, support = InMemoryRuntimeStore(), HybridSupportRetriever()
        manager = build_business_tool_manager(
            olist_service=service,
            knowledge_base=kb,
            support_retriever=support,
            case_service=store,
            runtime_store=runtime,
            cache_backend=runtime,
        )
        proxy = AssessmentProxy(manager, service, model, arm)
        offline = OfflineChatModel()
        actions = AgentActions(
            FrozenPlanner(plan),
            QaResponseGenerator(offline),
            PolicyResponseGenerator(offline),
            OlistTaskExtractor(offline),
            service,
            kb,
            support,
            store,
            tool_manager=proxy,
            runtime_store=runtime,
        )
        graph = AgentGraph(actions).build(InMemorySaver())
        order_id = case["order_id"]
        state = {
            "session_id": "paired-session",
            "tenant_id": "paired-tenant",
            "user_id": "paired-user",
            "role": "customer",
            "channel": "customer",
            "allowed_order_ids": [order_id],
            "auth_scopes": ["orders:read", "policy:read", "after_sales:draft", "after_sales:assess"],
            "messages": [{"role": "user", "content": case["message"]}],
            "task_plan": [],
            "completed_tasks": [],
            "trajectory_events": [],
            "artifacts": {},
            "retrieved_policy": [],
            "retrieved_insights": [],
            "retrieved_support_docs": [],
            "after_sales_cases": [],
            "answer_parts": [],
        }
        row = {
            "case_id": case["case_id"],
            "stratum": case["stratum"],
            "arm": arm,
            "initial_state_hash": digest(
                {
                    "order": asdict(service.get_order_status(order_id)),
                    "projection": {},
                    "events": 0,
                    "identity": state["user_id"],
                }
            ),
            "plan_hash": digest(plan.model_dump()),
            "execution_error": None,
            "tool_contract_hash": digest(
                [
                    {"metadata": meta, "schema": manager._specs[meta["name"]].input_model.model_json_schema()}
                    for meta in manager.list_tool_metadata()
                ]
            ),
        }
        config = {"configurable": {"thread_id": "paired-session"}, "recursion_limit": 60}
        result = {}
        try:
            result = await graph.ainvoke(state, config)
            row["pre_review_events"] = store.count_order_events(order_id)
            snap = await graph.aget_state(config)
            row["disposition"] = "handoff" if snap.next else "unknown"
            if not snap.next:
                decisions = [
                    e for e in result.get("trajectory_events", []) if e.get("node") == "decision_engine"
                ]
                outcome = decisions[-1]["status"] if decisions else "read_only"
                row["disposition"] = {
                    "approve": "execute",
                    "reject": "reject",
                    "ask_clarification": "clarify",
                    "read_only": "answer",
                }.get(outcome, "unknown")
            if case["disposition"] == "reject" and row["disposition"] == "answer":
                row["alternative_answer"] = await check_alternative_answer(
                    model, case, result.get("final_answer", ""), service.get_order_status(order_id)
                )
            row["pre_review_answer"] = result.get("final_answer", "")
            if snap.next and case["reviewer_action"] != "none":
                signal = "__review_approve__" if case["reviewer_action"] == "approve" else "__review_reject__"
                result = await graph.ainvoke(Command(resume=signal), config)
        except Exception as exc:
            row["execution_error"] = type(exc).__name__
        row.setdefault("disposition", "error")
        row.setdefault("pre_review_events", store.count_order_events(order_id))
        record = store.get_by_session("paired-session")
        row.update(
            events=result.get("trajectory_events", []),
            action_type=record.get("action_type", "")
            if record
            else (proxy.assessments[-1]["decision"]["action_type"] if proxy.assessments else ""),
            projection=store.get_order_projection(order_id),
            event_count=store.count_order_events(order_id),
            assessments=proxy.assessments,
            answer=result.get("final_answer", ""),
            completed_tasks=result.get("completed_tasks", []),
            tool_calls=manager.audit_events,
            replay_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return score(case, row)


def load_cases():
    path = ROOT / "evaluation/e2e/cases_quality_holdout.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    import re

    for case in cases:
        case["order_id"] = re.search(r"[0-9a-f]{32}", case["message"]).group()
    return cases


async def run(args):
    load_dotenv(ROOT / ".env")
    model = RecordedModel(
        args.cache or args.output / "model_calls.jsonl",
        live=args.live,
        max_calls=args.max_calls,
        max_tokens=args.max_tokens,
    )
    cases = load_cases()
    if args.limit:
        # Balanced round robin rather than taking only the first stratum.
        groups = {}
        for case in cases:
            groups.setdefault(case["stratum"], []).append(case)
        cases = [g[i] for i in range(max(map(len, groups.values()))) for g in groups.values() if i < len(g)]
        cases = cases[: args.limit]
    kb = MarkdownKnowledgeBase()
    manifest = {
        "protocol": "decision_attribution_v2",
        "cases": cases,
        "policy": POLICY,
        "cases_hash": digest(cases),
        "policy_hash": digest(POLICY),
        "kb_hash": digest([(p.name, p.read_text(encoding="utf-8")) for p in sorted(kb._kb_dir.glob("*.md"))]),
        "arms": list(ARMS),
        "model": model.model,
        "seed": "provider seed not set; same recorded plan",
        "scope": "real StateGraph replay, known 50-case template suite; not HTTP or production traffic",
        "response_generation": "shared local templates/offline generator; no live answer-quality claim",
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in (ROOT / "app", ROOT / "evaluation")
            for p in sorted(folder.rglob("*.py"))
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise RuntimeError("manifest_changed_use_new_output_directory")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    plans = []
    planner = IntentPlanner(model, allow_fast_path=False)
    for case in cases:
        plan = await planner.plan(case["message"])
        plans.append({"case_id": case["case_id"], "plan": plan.model_dump()})
        # Do not silently convert a model outage into a successful live score.
        if plan.planning_mode != "llm":
            raise RuntimeError(f"live_planner_failed:{case['case_id']}:{plan.fallback_reason}")
        order = list(ARMS)
        random.Random(case["case_id"]).shuffle(order)
        for arm in order:
            row = await run_arm(case, plan, model, arm)
            if model.incomplete_reason:
                raise RuntimeError(f"incomplete_experiment:{model.incomplete_reason}")
            rows.append(row)
            with (args.output / "progress.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "case": case["case_id"],
                    "done": len(plans),
                    "live_calls": model.api_calls,
                    "success": {r["arm"]: r["task_success"] for r in rows[-4:]},
                }
            ),
            flush=True,
        )
    records = [model.records[k] for k in dict.fromkeys(model.request_keys) if k in model.records]
    usage = Counter()
    for record in records:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens"):
            usage[k] += record["response"].get("usage", {}).get(k, 0)
    for case in cases:
        group = [r for r in rows if r["case_id"] == case["case_id"]]
        for field in ("initial_state_hash", "plan_hash", "tool_contract_hash"):
            if len({r[field] for r in group}) != 1:
                raise RuntimeError(f"unmatched_pair:{case['case_id']}:{field}")
    result = {
        "manifest_hash": digest(manifest),
        "model_evidence_complete": not model.incomplete_reason and all(
            key in model.records for key in model.request_keys
        ),
        "summaries": {a: summarize([r for r in rows if r["arm"] == a]) for a in ARMS},
        "paired": [
            paired(rows, "llm", "rules"),
            paired(rows, "llm", "llm_verifier"),
            paired(rows, "rules", "rules_verifier"),
            paired(rows, "llm_verifier", "rules_verifier"),
        ],
        "usage": dict(usage),
        "unique_model_calls": len(records),
        "resolved_models": sorted({r["response"].get("model", "unknown") for r in records}),
        "peak_price_upper_usd": (usage["prompt_tokens"] * 0.3 + usage["completion_tokens"] * 1.2) / 1e6,
        "price_source": "https://api-docs.deepseek.com/quick_start/pricing/ (2026-09-25, no cache discount)",
        "plans": plans,
        "rows": rows,
    }
    (args.output / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"rows", "plans"}}, ensure_ascii=False, indent=2
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow bounded paid calls; default is cache-only")
    parser.add_argument("--cache", type=Path, help="Reuse immutable recorded calls without rebilling")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-calls", type=int, default=110)
    parser.add_argument("--max-tokens", type=int, default=220000)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/attribution_v1")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
