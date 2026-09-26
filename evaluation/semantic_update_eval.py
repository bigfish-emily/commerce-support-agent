"""Paired classifier inputs; no fallback can conceal a provider failure."""

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path

from app.llm.semantic_decision import CRITERIA, classify_update


def summarize(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("No evaluation rows")
    f1 = []
    for label in CRITERIA:
        tp = sum(not r["error"] and r["choice"] == r["expected"] == label for r in rows)
        fp = sum(not r["error"] and r["choice"] == label and r["expected"] != label for r in rows)
        fn = sum(r["expected"] == label and (r["error"] or r["choice"] != label) for r in rows)
        f1.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    latencies = sorted(r["latency_ms"] for r in rows)
    probability_rows = [r for r in rows if not r["error"] and r["probabilities"]]
    brier = [
        sum((r["probabilities"][label] - float(r["expected"] == label)) ** 2 for label in CRITERIA)
        for r in probability_rows
    ]
    return {
        "n": len(rows),
        "provider_errors": sum(bool(r["error"]) for r in rows),
        "accuracy": sum(not r["error"] and r["choice"] == r["expected"] for r in rows) / len(rows),
        "macro_f1_fixed_5_labels": sum(f1) / len(f1),
        "p50_ms": latencies[math.ceil(len(rows) * 0.5) - 1],
        "p95_ms": latencies[math.ceil(len(rows) * 0.95) - 1],
        "multiclass_brier": sum(brier) / len(brier) if brier else None,
        "brier_n": len(brier),
        "usage_observed_n": sum(r["input_tokens"] is not None for r in rows),
        "input_tokens": sum(r["input_tokens"] or 0 for r in rows),
        "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
    }


async def run(args):
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        cases = cases[: args.limit]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    if any(c["expected"] not in CRITERIA for c in cases):
        raise ValueError("Unknown gold label")
    llm = None
    if args.backend == "llm":
        from langchain_openai import ChatOpenAI

        from app.config.llm_settings import resolve_llm_settings

        settings = resolve_llm_settings()
        if settings.api_key == "offline":
            raise ValueError("A real model credential is required")
        llm = ChatOpenAI(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0,
            max_tokens=256,
            timeout=15,
            max_retries=0,
        )
    rows = []
    for case in cases:
        context = case["context"]
        result = await classify_update(context, backend=args.backend, llm=llm)
        rows.append(
            {
                "id": case["id"],
                "family": case["family"],
                "expected": case["expected"],
                "context_sha256": hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest(),
                **result.model_dump(),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    summary = {
        "backend": args.backend,
        "dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "limitations": "Component evaluation; smoke fixtures are not an independent benchmark.",
        **summarize(rows),
    }
    args.output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rules", "llm", "jev"], required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    asyncio.run(run(parser.parse_args()))
