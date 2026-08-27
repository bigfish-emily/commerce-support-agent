"""Prepare and run a tau2/tau3-bench retail subset in an external checkout.

The main project targets Python 3.11. tau2-bench currently requires Python
3.12+, so this script intentionally executes the benchmark in a separate
checkout instead of adding tau2 as a project dependency.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "benchmark_adapters" / "tau2_retail_agent.py"
RUN_DIR = ROOT / "benchmark_runs" / "tau2_retail"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tau2 retail benchmark subset.")
    parser.add_argument("--tau2-root", required=True, help="Path to a local tau2-bench checkout.")
    parser.add_argument("--agent-llm", default="openai/gpt-4.1-mini")
    parser.add_argument("--user-llm", default="openai/gpt-4.1-mini")
    parser.add_argument("--num-tasks", type=int, default=5)
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--save-to", default="olist_agent_tau2_retail_subset")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tau2_root = Path(args.tau2_root).expanduser().resolve()
    pyproject = tau2_root / "pyproject.toml"
    if not pyproject.exists():
        raise SystemExit(f"Not a tau2-bench checkout: {tau2_root}")
    if not ADAPTER.exists():
        raise SystemExit(f"Missing adapter: {ADAPTER}")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        "uv",
        "run",
        "python",
        str(ADAPTER),
        "--agent-llm",
        args.agent_llm,
        "--user-llm",
        args.user_llm,
        "--num-tasks",
        str(args.num_tasks),
        "--num-trials",
        str(args.num_trials),
        "--seed",
        str(args.seed),
        "--save-to",
        args.save_to,
    ]
    manifest = {
        "benchmark": "tau2/tau3-bench retail",
        "tau2_root": str(tau2_root),
        "adapter": str(ADAPTER),
        "command": command,
        "notes": [
            "Runs in the tau2 Python 3.12+ environment.",
            "Results are saved by tau2 under tau2_root/data/simulations/.",
            "Use a cheap OpenAI-compatible model for subset smoke tests.",
        ],
    }
    (RUN_DIR / "last_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Command:")
    print(" ".join(command))
    print(f"Manifest: {RUN_DIR / 'last_manifest.json'}")
    if args.dry_run:
        return 0

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))
    completed = subprocess.run(command, cwd=tau2_root, env=env, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    sys.exit(main())
