"""Export reviewed public-data evidence and recomputable attribution tables."""

import argparse
import json
import math
import shutil
from collections import Counter
from pathlib import Path

from evaluation.attribution import ARMS, digest, summarize


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fraction(metric):
    return f"{metric['n']}/{metric['d']} ({100 * metric['rate']:.2f}%)" if metric["d"] else "N/A"


def publish(initial, final, calls, faults, output):
    before, after, fault_data = read(initial / "results.json"), read(final / "results.json"), read(faults)
    for result in (before, after):
        if result.get("model_evidence_complete") is not True:
            raise ValueError(
                "Missing complete-model-evidence attestation; review or rerun before publication"
            )
        encoded = json.dumps(result)
        if any(reason in encoded for reason in ("experiment_budget_exhausted", "uncached_model_request")):
            raise ValueError("Incomplete model evidence cannot be published as a comparison")
    output.mkdir(parents=True, exist_ok=True)
    for name, folder in (("initial", initial), ("regression", final)):
        for filename in ("manifest.json", "results.json"):
            shutil.copyfile(folder / filename, output / f"{name}_{filename}")
    shutil.copyfile(calls, output / "model_calls.jsonl")
    shutil.copyfile(faults, output / "faults.json")
    records = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    usage = Counter()
    for record in records:
        usage.update({k: v for k, v in record["response"].get("usage", {}).items() if isinstance(v, int)})
    durations = sorted(r["duration_ms"] for r in records)
    cost_upper = (usage["prompt_tokens"] * 0.3 + usage["completion_tokens"] * 1.2) / 1e6
    report = [
        "# Decision attribution and execution reliability",
        "",
        "Run date: 2026-09-25. This is a controlled component study through the real product",
        "StateGraph, not a full HTTP/customer-UX benchmark or a production automation claim.",
        "",
        "## Results",
        "",
        "All four arms share recorded live planner outputs, identity, tool schemas, policy pack,",
        "logical initial SQLite state, local answer generation, and scripted reviewer responses.",
        "Only the assessment provider and Verifier switch differ. Rules arms do not pay for a",
        "model decision call. Read ordering and final phrasing do not determine task success.",
        "",
        "| Arm | Initial task success | After policy-answer repair |",
        "|---|---:|---:|",
    ]
    for arm in ARMS:
        report.append(
            f"| {arm} | {fraction(before['summaries'][arm]['task_success'])} | "
            f"{fraction(after['summaries'][arm]['task_success'])} |"
        )
    report += [
        "",
        "Initial = frozen first outcome-based run; regression = inspected cases after repair.",
        "Do not label the regression set unseen or pool the two runs as independent samples.",
        "",
        "## Where the change comes from",
        "",
        "| Initial paired intervention | Fixed | Regressed | Delta (pp) | Exact McNemar p |",
        "|---|---:|---:|---:|---:|",
    ]
    for pair in before["paired"]:
        report.append(
            f"| {pair['before']} -> {pair['after']} | {pair['fixed']} | {pair['regressed']} | "
            f"{pair['delta_pp']:.2f} | {pair['mcnemar_exact_p']:.4f} |"
        )
    report += [
        "",
        "Rule-vs-model differences are about policy decisions conditional on the same plan.",
        "They cannot be attributed to LangGraph, MCP, Redis, RAG or a better LLM planner.",
        "Verifier has no measured incremental task-success gain when the observed decisions are",
        "internally consistent. Its injected-fault result below answers a different question.",
        "",
        "## Layer metrics (regression run)",
        "",
        "| Metric | llm | llm_verifier | rules | rules_verifier |",
        "|---|---:|---:|---:|---:|",
    ]
    fields = [
        "task_success",
        "coverage",
        "routing",
        "action",
        "state",
        "event_count_ok",
        "pre_review_unsafe_write",
        "execution_error",
        "hitl_precision",
        "hitl_recall",
        "eligible_auto_success",
    ]
    for field in fields:
        report.append(
            "| " + field + " | " + " | ".join(fraction(after["summaries"][a][field]) for a in ARMS) + " |"
        )
    report += [
        "",
        "Definitions:",
        "",
        "- task_success: requested reads covered, legal disposition (including a judged equivalent",
        "  rejection answer), correct action when needed, correct persisted projection and event count,",
        "  no pre-approval write, and no execution error. This is a declared suite contract.",
        "- coverage: completed read obligations regardless of read order; not overall NLU accuracy.",
        "- routing/action: expected business disposition/action or an equivalent evidence-grounded",
        "  rejection answer; a write intent is not required for a correctly answered eligibility question.",
        "- state: all specified final projection fields match; interpret together with event count.",
        "- event_count_ok: one event for approved/eligible writes, zero otherwise.",
        "- pre_review_unsafe_write: writes before authorization among cases that must not auto-write.",
        "- HITL precision/recall: expected review cases / actual review cases; actual review / expected.",
        "- eligible_auto_success: task success among the ten cases explicitly permitted to auto-open a case.",
        "- execution_error: evaluator/graph exception; these rows remain in the denominator.",
        "",
        "Wilson 95% intervals and exact paired counts are in the JSON. The 50 orders span only five",
        "templated strata, so independence/generalization assumptions are weak. Treat p-values as",
        "descriptive, not evidence of a population-wide or statistically established advantage.",
        "",
        "## Breakdown by scenario",
        "",
        "| Stratum | N per arm | Initial LLM / rules+Verifier | Regression LLM / rules+Verifier |",
        "|---|---:|---|---|",
    ]
    for stratum in sorted({r["stratum"] for r in after["rows"]}):
        cells = []
        for run in (before, after):
            cells.append(
                " / ".join(
                    fraction(
                        summarize([r for r in run["rows"] if r["stratum"] == stratum and r["arm"] == a])[
                            "task_success"
                        ]
                    )
                    for a in ("llm", "rules_verifier")
                )
            )
        n = sum(r["stratum"] == stratum and r["arm"] == "llm" for r in after["rows"])
        report.append(f"| {stratum} | {n} | {cells[0]} | {cells[1]} |")
    report += [
        "",
        "## Controlled fault injection",
        "",
        "These are adversarial fixtures with a simulated write sink, not naturally occurring LLM errors",
        "or estimated production failure rates. Healthy controls guard against blocking everything.",
        "",
        "| Component | Disabled: handled faults | Enabled: handled faults | Enabled: healthy controls |",
        "|---|---:|---:|---:|",
    ]
    for component, metrics in fault_data["summary"].items():
        report.append(
            f"| {component} | {fraction(metrics['False']['fault_handling'])} | "
            f"{fraction(metrics['True']['fault_handling'])} | "
            f"{fraction(metrics['True']['healthy_controls'])} |"
        )
    report += [
        "",
        "Faults: contradictory decision fields; changed approval parameters/principal/order;",
        "timeout after simulated provider commit; restart with a submitted quarantine record.",
        "",
        "## Cost and timing",
        "",
        f"- Unique billed requests across initial, probes and regression: {len(records)}.",
        f"- Recorded usage: input {usage['prompt_tokens']:,}; output {usage['completion_tokens']:,} tokens.",
        f"- Conservative peak-list estimate (no cache discount): USD {cost_upper:.6f}; not an invoice.",
        "- Price source: https://api-docs.deepseek.com/quick_start/pricing/ (2026-09-25).",
        f"- Model-call p50/p95: {durations[math.ceil(len(durations) * 0.5) - 1]:.2f} / "
        f"{durations[math.ceil(len(durations) * 0.95) - 1]:.2f} ms. Includes classification and judge calls.",
        "- Graph replay times mix cached/local work and initial live calls; they are NOT user-response",
        "  latency, TTFT, QPS, or deployment capacity and must not be advertised as such.",
        "",
        "## Evidence and reproduction",
        "",
        "The model_calls.jsonl contains public Olist facts, authored messages, exact prompts, usage and",
        "responses; no API keys or private customer logs. Manifest files include code, policy and data",
        "hashes. Results include actual graph events, SQLite projections, plan hashes and tool hashes.",
        "Reviewer actions are scripted and human time is excluded. Answers use shared local generation;",
        "only alternate rejection answers are checked by a live same-model judge with exact quotes.",
        "That narrow judge is not a broad friendliness/hallucination evaluation.",
        "",
        "```powershell",
        "python -m evaluation.attribution --cache experiments/attribution/2026-09-25/model_calls.jsonl `",
        "  --output artifacts/attribution_replay",
        "python -m evaluation.attribution_faults",
        "```",
        "",
        "Cache-only is the default. Missing model responses fail closed as execution errors rather",
        "than spending tokens. A live run requires --live and explicit request/token ceilings.",
        "The archived initial run predates the policy repair; its manifest hashes identify that snapshot.",
        "All initial failures remain in initial_results.json. Do not rewrite old scores after fixes.",
    ]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    evidence = {
        p.name: digest(p.read_text(encoding="utf-8"))
        for p in output.iterdir()
        if p.is_file() and p.name != "evidence_hashes.json"
    }
    (output / "evidence_hashes.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(output), "requests": len(records), "cost_upper_usd": cost_upper}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("initial", "final", "calls", "faults", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    publish(args.initial, args.final, args.calls, args.faults, args.output)
