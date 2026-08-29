"""Summarize tau2/tau3-bench results without importing tau2.

tau2 can save either a single JSON file or a directory containing results.json
and per-simulation JSON files. This script reads both formats and writes a small
markdown/json summary that can be quoted in a resume only after a real run.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "benchmark_runs" / "tau2_retail"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize tau2/tau3 result files.")
    parser.add_argument("--results", required=True, help="tau2 result JSON or result directory.")
    parser.add_argument("--out", default=str(OUT_DIR / "last_summary.md"))
    args = parser.parse_args()

    path = Path(args.results).expanduser().resolve()
    data = _load_results(path)
    simulations = data.get("simulations", [])
    summary = summarize(simulations)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(path, summary), encoding="utf-8")
    (out.with_suffix(".json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(render_markdown(path, summary))
    print(f"\nWrote {out}")
    return 0


def _load_results(path: Path) -> dict:
    if path.is_dir():
        meta = json.loads((path / "results.json").read_text(encoding="utf-8"))
        sims_dir = path / "simulations"
        simulations = []
        if sims_dir.exists():
            for sim_path in sorted(sims_dir.glob("*.json")):
                simulations.append(json.loads(sim_path.read_text(encoding="utf-8")))
        else:
            simulations = meta.get("simulations", [])
        meta["simulations"] = simulations
        return meta
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def summarize(simulations: list[dict]) -> dict:
    evaluated = [
        sim
        for sim in simulations
        if sim.get("termination_reason") != "infrastructure_error"
    ]
    rewards = [
        float(sim.get("reward_info", {}).get("reward", 0.0))
        for sim in evaluated
        if sim.get("reward_info") is not None
    ]
    successes_by_task: dict[str, list[bool]] = defaultdict(list)
    for sim in evaluated:
        task_id = str(sim.get("task_id"))
        reward = float(sim.get("reward_info", {}).get("reward", 0.0)) if sim.get("reward_info") else 0.0
        successes_by_task[task_id].append(reward >= 1 - 1e-6)

    max_k = min((len(values) for values in successes_by_task.values()), default=0)
    pass_hat_ks = {
        f"pass^{k}": _mean_pass_k(successes_by_task, k)
        for k in range(1, max_k + 1)
    }
    termination_counts = Counter(str(sim.get("termination_reason", "unknown")) for sim in simulations)
    durations = [float(sim.get("duration", 0.0)) for sim in evaluated if sim.get("duration") is not None]
    agent_costs = [
        float(sim.get("agent_cost", 0.0))
        for sim in evaluated
        if sim.get("agent_cost") is not None
    ]

    return {
        "total_simulations": len(simulations),
        "evaluated_simulations": len(evaluated),
        "total_tasks": len(successes_by_task),
        "avg_reward": sum(rewards) / len(rewards) if rewards else 0.0,
        "pass_hat_ks": pass_hat_ks,
        "termination_counts": dict(termination_counts),
        "avg_duration_seconds": sum(durations) / len(durations) if durations else None,
        "p95_duration_seconds": _percentile(durations, 95) if durations else None,
        "avg_agent_cost": sum(agent_costs) / len(agent_costs) if agent_costs else None,
        "failed_task_ids": [
            task_id
            for task_id, values in successes_by_task.items()
            if not any(values)
        ][:20],
    }


def _mean_pass_k(successes_by_task: dict[str, list[bool]], k: int) -> float:
    values = []
    for successes in successes_by_task.values():
        if len(successes) < k:
            continue
        success_count = sum(successes)
        values.append(_pass_k(len(successes), success_count, k))
    return sum(values) / len(values) if values else 0.0


def _pass_k(num_trials: int, success_count: int, k: int) -> float:
    if num_trials < k:
        return 0.0
    return math.comb(success_count, k) / math.comb(num_trials, k)


def _percentile(values: list[float], percentile: int) -> float:
    sorted_values = sorted(values)
    index = round((len(sorted_values) - 1) * percentile / 100)
    return sorted_values[index]


def render_markdown(path: Path, summary: dict) -> str:
    lines = [
        "# tau2/tau3 Retail Benchmark Summary",
        "",
        f"results: `{path}`",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| total_simulations | {summary['total_simulations']} |",
        f"| evaluated_simulations | {summary['evaluated_simulations']} |",
        f"| total_tasks | {summary['total_tasks']} |",
        f"| avg_reward | {summary['avg_reward']:.2%} |",
    ]
    for name, value in summary["pass_hat_ks"].items():
        lines.append(f"| {name} | {value:.2%} |")
    if summary["avg_duration_seconds"] is not None:
        lines.append(f"| avg_duration_seconds | {summary['avg_duration_seconds']:.2f} |")
    if summary["p95_duration_seconds"] is not None:
        lines.append(f"| p95_duration_seconds | {summary['p95_duration_seconds']:.2f} |")
    if summary["avg_agent_cost"] is not None:
        lines.append(f"| avg_agent_cost | {summary['avg_agent_cost']:.6f} |")
    lines.extend(["", "## Termination Counts", "", "| reason | count |", "|---|---:|"])
    for reason, count in sorted(summary["termination_counts"].items()):
        lines.append(f"| {reason} | {count} |")
    lines.extend(["", "## Failed Task IDs", ""])
    if summary["failed_task_ids"]:
        lines.extend(f"- `{task_id}`" for task_id in summary["failed_task_ids"])
    else:
        lines.append("- None")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
