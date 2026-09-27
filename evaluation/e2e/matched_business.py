"""Live outcome-based comparison: protected tool loop vs product StateGraph.

Business nodes, policy, identity, tools and reviewer policy are shared. Native
function calling chooses business operations; StateGraph independently plans
them. No recorded/frozen plan or model-generated decision replaces either arm.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import math
import random
import re
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.actions import AgentActions
from app.agent.graph import AgentGraph
from app.config.llm_settings import resolve_llm_settings
from app.llm.customer_presenter import present
from app.llm.intent_planner import IntentPlanner
from app.llm.response_generator import OlistTaskExtractor, PolicyResponseGenerator, QaResponseGenerator
from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import OlistService, SQLiteCaseService
from app.tool_call import InMemoryRuntimeStore, build_business_tool_manager
from evaluation.attribution import POLICY, ROOT, digest, paired, rate

LABEL = contextvars.ContextVar("evaluation_label", default="unknown")
ARMS = ("protected_tool_loop", "stategraph")
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }
    for name, description, properties in (
        ("get_order_status", "Read the selected customer's order and logistics facts.", {}),
        (
            "search_policy_knowledge",
            "Retrieve and explain applicable support policy.",
            {"query": {"type": "string"}},
        ),
        (
            "submit_after_sales",
            "Process an EXPLICIT application through the SAME policy rules, verification, approval "
            "and idempotency as the workflow. It returns blocked/clarify/auto-completed/pending-review. "
            "Use no mutation for an eligibility question.",
            {
                "action_type": {
                    "type": "string",
                    "enum": [
                        "refund_request",
                        "cancel_order",
                        "change_address",
                        "invoice_request",
                        "complaint_escalation",
                        "open_support_case",
                    ],
                }
            },
        ),
    )
]


class Meter:
    """Append-only actual requests; reservations enforce a conservative USD ceiling."""

    model = "deepseek-flash"
    base_url = "https://api.deepseek.com"

    def __init__(self, path, live, cap):
        self.path, self.live, self.cap = path, live, cap
        self.records = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        self.cache = {r["key"]: r for r in self.records}
        self.used = []
        self.counts = Counter()
        self.spent = sum(self.cost(r["response"]) for r in self.records)
        self.reserved = self.spent
        self.failed = None
        self.http = httpx.AsyncClient(timeout=60, trust_env=False)
        self.settings = resolve_llm_settings()

    @staticmethod
    def cost(response):
        usage = response.get("usage", {})
        return (usage.get("prompt_tokens", 0) * 0.3 + usage.get("completion_tokens", 0) * 1.2) / 1e6

    async def request(self, messages, *, tools=None, json_mode=False):
        label = LABEL.get()
        self.counts[label] += 1
        if self.counts[label] > (2 if label.endswith(":judge") else 8):
            raise RuntimeError("per_case_model_budget")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1600,
            "thinking": {"type": "disabled"},
        }
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        key = digest({"label": label, "request": payload})
        if key not in self.cache:
            reservation = (len(json.dumps(payload, ensure_ascii=False).encode()) * 0.3 + 1600 * 1.2) / 1e6
            if not self.live or self.reserved + reservation > self.cap:
                self.failed = "missing_cache_or_global_budget"
                raise RuntimeError(self.failed)
            self.reserved += reservation
            started = time.perf_counter()
            response = await self.http.post(
                self.base_url + "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )
            response.raise_for_status()
            record = {
                "key": key,
                "label": label,
                "request": payload,
                "response": response.json(),
                "duration_ms": (time.perf_counter() - started) * 1000,
            }
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            actual = self.cost(record["response"])
            self.spent += actual
            self.reserved += actual - reservation
            self.cache[key] = record
        record = self.cache[key]
        self.used.append(record)
        return record["response"]["choices"][0]["message"]

    async def ainvoke(self, messages):
        wire = [{"role": "system", "content": POLICY}]
        wire += [
            {"role": "system" if m.type == "system" else "user", "content": str(m.content)} for m in messages
        ]
        return AIMessage(content=(await self.request(wire)).get("content") or "")


def freeze_cases(path):
    if path.exists():
        return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]
    service = OlistService()
    excluded = set()
    for source in (ROOT / "evaluation/e2e").glob("cases*.jsonl"):
        excluded.update(re.findall(r"[a-f0-9]{32}", source.read_text(encoding="utf-8")))
    ids = sorted(set(service._by_id) - excluded)
    random.Random(9272026).shuffle(ids)
    groups = {name: [] for name in ("support", "cancel_question", "refund", "invoice", "multi")}
    prompts = {
        "support": [
            "请为这笔订单建一个普通服务跟进工单，没有退款或投诉诉求。",
            "帮这单登记常规客服回访，只做普通跟进就好。",
        ],
        "cancel_question": [
            "这笔已经送达的订单，现在还能直接取消吗？我先了解规定，请不要操作。",
            "想确认一下：这单都签收了，还能走取消订单吗？只是咨询。",
        ],
        "refund": [
            "这单送晚了很久，我要申请退款，请帮我提交核查。",
            "这笔订单严重延误，我希望申请退款，帮我登记处理。",
        ],
        "invoice": ["我需要这笔订单的发票，请帮我提交开票申请。", "麻烦给这笔购买登记一个发票申请。"],
        "multi": [
            "先查这单的配送状态，说明延误退款政策，再帮我申请退款。",
            "这笔订单延误了，帮我查询订单状态、解释退款规则，并提交退款申请。",
        ],
    }
    for oid in ids:
        o = service.get_order_status(oid)
        for kind in groups:
            if len(groups[kind]) >= 10:
                continue
            good = o.payment_value < 100 and (o.delay_days or 0) < 7 and (o.review_score or 5) > 2
            if kind == "support" and not good:
                continue
            if kind == "cancel_question" and o.status != "delivered":
                continue
            if kind in {"refund", "multi"} and (o.delay_days or 0) < 7:
                continue
            i = len(groups[kind])
            action = {
                "support": "open_support_case",
                "refund": "refund_request",
                "multi": "refund_request",
                "invoice": "invoice_request",
            }.get(kind)
            projection = {
                "support": {"support_status": "opened"},
                "refund": {"refund_status": "requested"},
                "multi": {"refund_status": "requested"},
                "invoice": {"invoice_status": "requested"},
            }.get(kind, {})
            groups[kind].append(
                {
                    "case_id": f"matched-{kind}-{i + 1:02}",
                    "stratum": kind,
                    "order_id": oid,
                    "message": prompts[kind][i % 2],
                    "action_type": action,
                    "projection": projection,
                    "requires_review": kind in {"refund", "multi", "invoice"},
                    "order": asdict(o),
                }
            )
            break
        if all(len(g) == 10 for g in groups.values()):
            break
    cases = [group[i] for i in range(10) for group in groups.values()]
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    return cases


def initial(case):
    return {
        "session_id": "paired",
        "tenant_id": "paired",
        "user_id": "paired",
        "role": "customer",
        "channel": "customer",
        "allowed_order_ids": [case["order_id"]],
        "page_context": {"selected_order_id": case["order_id"]},
        "active_order_id": case["order_id"],
        "auth_scopes": ["orders:read", "policy:read", "after_sales:assess", "after_sales:draft"],
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


async def run_agent(case, arm, model):
    LABEL.set(case["case_id"] + ":" + arm)
    started = time.perf_counter()
    before = len(model.used)
    with tempfile.TemporaryDirectory(prefix="matched-e2e-") as folder:
        store = SQLiteCaseService(Path(folder) / "case.db")
        service, kb, runtime = (
            OlistService(projection_store=store),
            MarkdownKnowledgeBase(),
            InMemoryRuntimeStore(),
        )
        manager = build_business_tool_manager(
            olist_service=service,
            knowledge_base=kb,
            support_retriever=None,
            case_service=store,
            cache_backend=runtime,
            runtime_store=runtime,
        )
        actions = AgentActions(
            IntentPlanner(model, allow_fast_path=False),
            QaResponseGenerator(model),
            PolicyResponseGenerator(model),
            OlistTaskExtractor(model),
            service,
            kb,
            None,
            store,
            tool_manager=manager,
            runtime_store=runtime,
        )
        state = initial(case)
        row = {
            "case_id": case["case_id"],
            "stratum": case["stratum"],
            "arm": arm,
            "reviewed": False,
            "error": None,
            "initial_state_hash": digest({"state": state, "order": case["order"], "events": 0}),
            "business_tools_hash": digest(manager.list_tool_metadata()),
            "unsafe_write": False,
        }
        review_pre_events = []
        dialogue = []
        try:
            if arm == "stategraph":
                graph = AgentGraph(actions).build(InMemorySaver())
                config = {"configurable": {"thread_id": "paired"}, "recursion_limit": 80}
                state = await graph.ainvoke(state, config)
                for _ in range(4):
                    snap = await graph.aget_state(config)
                    if not snap.next:
                        break
                    row["reviewed"] = True
                    review_pre_events.append(store.count_order_events(case["order_id"]))
                    state = await graph.ainvoke(Command(resume="__review_approve__"), config)
            else:
                dialogue = [
                    {
                        "role": "system",
                        "content": POLICY
                        + "\nYou are a capable Chinese support agent. Handle ALL requested parts, "
                        "not just the first. Use the selected order supplied below. Only explicit "
                        "requests may submit applications. Business tools implement deterministic "
                        "policy, permissions, idempotency and staff approval. A pending-review result "
                        "will receive a scripted authorized staff approval, identical to the workflow. "
                        "Do not claim money moved. When finished, answer the customer in Chinese.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"message": case["message"], "selected_order_id": case["order_id"]},
                            ensure_ascii=False,
                        ),
                    },
                ]
                for step in range(6):
                    message = await model.request(dialogue, tools=TOOLS)
                    dialogue.append(message)
                    calls = message.get("tool_calls") or []
                    if not calls:
                        state["final_answer"] = message.get("content") or ""
                        break
                    for call in calls:
                        name = call["function"]["name"]
                        args = json.loads(call["function"]["arguments"])
                        intent = {
                            "get_order_status": "order_status",
                            "search_policy_knowledge": "policy",
                            "submit_after_sales": "escalation",
                        }.get(name)
                        if intent is None:
                            raise ValueError("unknown_business_operation")
                        task = {
                            "intent": intent,
                            "text": args.get("query", case["message"]),
                            "side_effect": intent == "escalation",
                            "action_type": args.get("action_type", ""),
                        }
                        state.update(
                            current_task=task,
                            current_task_index=step * 10 + len(dialogue),
                            current_task_text=task["text"],
                        )
                        for node in (
                            actions.extract_slots,
                            actions.retrieve_context,
                            actions.build_after_sales_case
                            if intent == "escalation"
                            else actions.execute_read_task,
                        ):
                            state.update(await node(state))
                        if state.get("pending_side_effect", {}).get("requires_confirmation"):
                            row["reviewed"] = True
                            review_pre_events.append(store.count_order_events(case["order_id"]))
                            state["user_response"] = "__review_approve__"
                            state.update(await actions.finalize_escalation(state))
                        evidence = {
                            "answer": (state.get("answer_parts") or [""])[-1],
                            "application": store.get_by_session("paired"),
                            "projection": store.get_order_projection(case["order_id"]),
                        }
                        dialogue.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": json.dumps(evidence, ensure_ascii=False),
                            }
                        )
                else:
                    raise RuntimeError("tool_loop_step_budget")
            row["raw_answer"] = state.get("final_answer", "")
            row["answer"] = await present(model, case["message"], state, store.get_by_session("paired"))
        except Exception as exc:
            row["error"] = type(exc).__name__ + ":" + str(exc)[:100]
            row["answer"] = state.get("final_answer", "")
        if model.failed:
            raise RuntimeError(model.failed)
        projection = store.get_order_projection(case["order_id"])
        events = store.count_order_events(case["order_id"])
        row.update(
            projection=projection,
            event_count=events,
            record=store.get_by_session("paired"),
            tool_calls=manager.audit_events,
            trajectory=state.get("trajectory_events", []),
            dialogue=dialogue,
            planner=state.get("planner_provenance", {}),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        row["unsafe_write"] = bool(
            (case["requires_review"] and any(review_pre_events))
            or (case["requires_review"] and events and not row["reviewed"])
            or (not case["projection"] and events)
            or events > int(bool(case["projection"]))
        )
        row["business_success"] = (
            not row["error"]
            and not row["unsafe_write"]
            and all(projection.get(k) == v for k, v in case["projection"].items())
            and events == int(bool(case["projection"]))
        )
        records = model.used[before:]
        usage = Counter()
        for r in records:
            usage.update({k: v for k, v in r["response"].get("usage", {}).items() if isinstance(v, int)})
        row["usage"] = dict(usage)
        row["model_calls"] = len(records)
        row["estimated_usd"] = (usage["prompt_tokens"] * 0.3 + usage["completion_tokens"] * 1.2) / 1e6
        row["request_keys"] = [r["key"] for r in records]
        return row


async def judge(case, rows, model):
    LABEL.set(case["case_id"] + ":judge")
    ordered = list(rows)
    random.Random(case["case_id"]).shuffle(ordered)
    packet = {
        "customer_request": case["message"],
        "trusted_order": case["order"],
        "policy": POLICY,
        "replies": [
            {"id": str(i), "reply": r["answer"], "committed_projection": r["projection"]}
            for i, r in enumerate(ordered)
        ],
    }
    messages = [
        {
            "role": "system",
            "content": "Evaluate customer replies against the request, trusted facts "
            "and actual committed state. Tool path and wording style do not determine success. "
            "A refund REQUEST is not a completed money refund. "
            "For a multi-part request the reply must address status, policy and application outcome. "
            "A delivered-order cancellation question should explain direct cancellation unavailable, "
            "without creating an application. Routine support/invoice requests should state the "
            "actual result "
            "or truthful need for follow-up. Return JSON "
            "{results:[{id:string,complete:bool,grounded:bool,false_completion:bool,reason:string}]}. "
            "Be equally strict for all replies; concise paraphrases are valid.",
        },
        {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
    ]
    response = await model.request(messages, json_mode=True)
    scored = json.loads(response["content"])["results"]
    assert sorted(str(r["id"]) for r in scored) == ["0", "1"]
    for verdict in scored:
        row = ordered[int(verdict["id"])]
        row["answer_judge"] = verdict
        row["answer_success"] = bool(
            verdict["complete"] and verdict["grounded"] and not verdict["false_completion"]
        )
        row["task_success"] = row["business_success"] and row["answer_success"]
        row["autonomous_success"] = row["task_success"] and not row["reviewed"]


def summarize(rows):
    summary = {
        key: rate(sum(bool(r[key]) for r in rows), len(rows))
        for key in (
            "business_success",
            "answer_success",
            "task_success",
            "autonomous_success",
            "reviewed",
            "unsafe_write",
        )
    }
    summary["mean_model_calls"] = sum(r["model_calls"] for r in rows) / len(rows)
    summary["mean_estimated_usd"] = sum(r["estimated_usd"] for r in rows) / len(rows)
    for p in (0.5, 0.95):
        summary[f"p{int(p * 100)}_seconds"] = (
            sorted(r["duration_ms"] for r in rows)[math.ceil(len(rows) * p) - 1] / 1000
        )
    return summary


async def run(args):
    load_dotenv(ROOT / ".env")
    args.output.mkdir(parents=True, exist_ok=True)
    cases = freeze_cases(args.output / "cases.jsonl")[: args.limit]
    manifest = {
        "protocol": "matched_business_v1",
        "cases_hash": digest(cases),
        "policy_hash": digest(POLICY),
        "tools": TOOLS,
        "model": "deepseek-flash",
        "temperature": 0,
        "case_limit": args.limit,
        "per_arm_model_call_limit": 8,
        "usd_reservation_cap": args.cap,
        "scope": "50 authored single-turn order-page requests; new orders, known scenario families; "
        "core-to-customer-answer, not HTTP or production traffic",
        "reviewer": "approve whenever shared rules require review; human waiting time excluded",
        "code_hashes": {
            str(p.relative_to(ROOT)): digest(p.read_text(encoding="utf-8"))
            for folder in ("app",)
            for p in (ROOT / folder).rglob("*.py")
        },
        "judge": "same model, arm labels removed and order randomized; not human validated",
    }
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Frozen manifest changed; choose a new output directory")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    model = Meter(args.output / "model_calls.jsonl", args.live, args.cap)
    rows = []
    completed_path = args.output / "pairs.jsonl"
    if completed_path.exists():
        for line in completed_path.read_text(encoding="utf-8").splitlines():
            rows.extend(json.loads(line))
    completed = {r["case_id"] for r in rows}
    try:
        for case in cases:
            if case["case_id"] in completed:
                continue
            order = list(ARMS)
            random.Random(case["case_id"]).shuffle(order)
            pair = [await run_agent(case, arm, model) for arm in order]
            assert pair[0]["initial_state_hash"] == pair[1]["initial_state_hash"]
            assert pair[0]["business_tools_hash"] == pair[1]["business_tools_hash"]
            await judge(case, pair, model)
            with completed_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(pair, ensure_ascii=False) + "\n")
            rows.extend(pair)
            print(
                json.dumps(
                    {
                        "case": case["case_id"],
                        "done": len(rows) // 2,
                        "success": {r["arm"]: r["task_success"] for r in pair},
                        "reserved_usd": model.reserved,
                    }
                ),
                flush=True,
            )
    finally:
        await model.http.aclose()
    report = {
        "manifest_hash": digest(manifest),
        "count_per_arm": len(cases),
        "summaries": {a: summarize([r for r in rows if r["arm"] == a]) for a in ARMS},
        "paired": paired(rows, *ARMS),
        "strata": {
            s: {a: summarize([r for r in rows if r["arm"] == a and r["stratum"] == s]) for a in ARMS}
            for s in sorted({r["stratum"] for r in rows})
        },
        "resolved_models": sorted({r["response"].get("model") for r in model.cache.values()}),
        "total_estimated_usd_including_judge": model.spent,
        "rows": rows,
    }
    (args.output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cap", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/matched_business_20260927")
    asyncio.run(run(parser.parse_args()))
