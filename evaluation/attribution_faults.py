"""Controlled fault-injection ablations; no live models or real payment writes."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel

from app.after_sales.decision import verify_decision
from app.after_sales.execution_guard import action_fingerprint, order_fingerprint
from app.after_sales.models import AfterSalesDecision
from app.agent.actions import AgentActions
from app.olist.service import OlistService
from app.tool_call.framework import ToolCallManager, ToolCallResult, ToolSpec
from app.tool_call.write_journal import WriteJournal
from evaluation.attribution import load_cases, rate


class WriteArgs(BaseModel):
    order_id: str
    action_type: str
    message_text: str


def verifier_rows():
    rows = []
    for action in (
        "refund_request",
        "cancel_order",
        "change_address",
        "invoice_request",
        "complaint_escalation",
    ):
        healthy = AfterSalesDecision(
            outcome="needs_human_review",
            action_type=action,
            reason_code="fixture",
            confidence=0.8,
            risk_level="medium",
            requires_human=True,
            allowed_actions=[action],
            policy_refs=["policy"],
        )
        mutations = {
            "missing_policy": {"policy_refs": []},
            "reject_with_write": {"outcome": "reject"},
            "review_flag_missing": {"requires_human": False},
            "calibrated_low_confidence": {
                "outcome": "approve",
                "confidence": 0.4,
                "confidence_source": "model_calibrated",
            },
            "healthy": {},
        }
        for fault, change in mutations.items():
            decision = healthy.model_copy(update=change)
            for enabled in (False, True):
                verification = verify_decision(decision) if enabled else None
                blocked = verification is not None and not verification.passed
                rows.append(
                    {
                        "component": "verifier",
                        "fault": fault,
                        "action": action,
                        "enabled": enabled,
                        "expected_block": fault != "healthy",
                        "blocked": blocked,
                        "passed": blocked == (fault != "healthy"),
                        "flags": verification.flags if verification else [],
                    }
                )
    return rows


async def binding_rows():
    rows = []
    for case in load_cases()[:5]:
        original = OlistService().get_order_status(case["order_id"])
        for fault in ("message_changed", "principal_changed", "order_changed", "healthy"):
            for enabled in (False, True):
                current = replace(original, status="canceled") if fault == "order_changed" else original
                draft = {
                    "action_type": "cancel_order",
                    "order_id": original.order_id,
                    "message_text": "approved request",
                }
                state = {"session_id": "fixed", "user_id": "alice", "tenant_id": "tenant"}
                draft["execution_binding"] = {
                    "action": action_fingerprint(draft, state),
                    "order": order_fingerprint(original),
                }
                if fault == "message_changed":
                    draft["message_text"] = "different request"
                if fault == "principal_changed":
                    state["user_id"] = "bob"
                if not enabled:
                    # Eval-only removal of the old snapshot: approve current content.
                    draft["execution_binding"] = {
                        "action": action_fingerprint(draft, state),
                        "order": order_fingerprint(current),
                    }
                calls = []

                async def write(*args):
                    calls.append(args)
                    return ToolCallResult(
                        tool_name="execute_side_effect",
                        ok=True,
                        data={"result": {"duplicate": False, "result_id": "sink"}},
                    )

                actions = object.__new__(AgentActions)
                actions._case_service = None
                actions._olist_service = SimpleNamespace(get_order_status=lambda _: current)
                actions._tool_manager = SimpleNamespace(call=write)
                answer = await actions._execute_escalation_draft(draft, state)
                rows.append(
                    {
                        "component": "execution_binding",
                        "case_id": case["case_id"],
                        "fault": fault,
                        "enabled": enabled,
                        "write_calls": len(calls),
                        "expected_block": fault != "healthy",
                        "blocked": not calls,
                        "passed": (not calls) == (fault != "healthy"),
                        "answer": answer,
                    }
                )
    return rows


async def journal_rows():
    rows = []
    for case in load_cases()[:5]:
        for fault in ("timeout_after_commit", "crash_after_reservation", "healthy"):
            for enabled in (False, True):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "journal.db"
                    writes = []

                    async def handler(args, context):
                        writes.append(dict(args))
                        if fault == "timeout_after_commit":
                            raise TimeoutError("simulated provider committed then lost receipt")
                        return {"receipt": "fake-provider-receipt"}

                    spec = ToolSpec(
                        name="write",
                        description="fault injection",
                        input_model=WriteArgs,
                        handler=handler,
                        side_effect=True,
                        retries=3,
                    )
                    args = {"order_id": case["order_id"], "action_type": "refund", "message_text": "one"}
                    journal = WriteJournal(path) if enabled else None
                    manager = ToolCallManager([spec], write_journal=journal)
                    if fault == "crash_after_reservation":
                        # Persistent submitted left by a killed worker before an observable receipt.
                        scope = {"order_id": args["order_id"]}
                        from app.tool_call.framework import ToolCallContext

                        if journal:
                            journal.begin(
                                manager._cache_key("order-write", scope, ToolCallContext().tenant_id)
                            )
                    else:
                        await manager.call("write", args)
                    restarted = ToolCallManager([spec], write_journal=WriteJournal(path) if enabled else None)
                    if fault != "healthy":
                        result = await restarted.call("write", {**args, "message_text": "rephrased"})
                    else:
                        result = None
                    expected = 1 if fault in {"timeout_after_commit", "healthy"} else 0
                    rows.append(
                        {
                            "component": "write_quarantine",
                            "case_id": case["case_id"],
                            "fault": fault,
                            "enabled": enabled,
                            "provider_writes": len(writes),
                            "expected_writes": expected,
                            "passed": len(writes) == expected,
                            "retry_error": result.error_code if result else None,
                        }
                    )
    return rows


async def run():
    rows = verifier_rows() + await binding_rows() + await journal_rows()
    summary = {}
    for component in sorted({r["component"] for r in rows}):
        summary[component] = {}
        for enabled in (False, True):
            subset = [r for r in rows if r["component"] == component and r["enabled"] == enabled]
            faults = [r for r in subset if r["fault"] != "healthy"]
            healthy = [r for r in subset if r["fault"] == "healthy"]
            summary[component][str(enabled)] = {
                "fault_handling": rate(sum(r["passed"] for r in faults), len(faults)),
                "healthy_controls": rate(sum(r["passed"] for r in healthy), len(healthy)),
            }
    return {
        "scope": "controlled injected faults with fake write sink; not live error prevalence",
        "summary": summary,
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/attribution_faults.json"))
    args = parser.parse_args()
    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
