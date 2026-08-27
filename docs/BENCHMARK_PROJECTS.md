# Benchmark Work Plan

This project already uses public data for business evaluation. The next step is
to add horizontal, externally comparable benchmarks without weakening the main
business project.

## Current Position

- Olist facts: 98,666 public marketplace orders and 73 category risk entries.
- Bitext intent eval: 1,080 customer-support utterances mapped to business route intents.
- ResCommons hybrid retrieval: 35k train corpus plus 100 test queries.
- V1rtucious profile: 2,000 e-commerce chatbot cases used for eval taxonomy coverage.
- Live LLM eval: 30 true Agent cases using the full LangGraph `/chat` path.

These are useful project-level evaluations, but they are not leaderboard-style
benchmarks. In interviews, describe them as "public-data-driven business evals",
not as a standard benchmark.

## Why tau2/tau3-bench Retail

tau2/tau3-bench is the closest external benchmark to this project because it
evaluates customer-service agents in simulated real-world domains. A domain
contains a policy, tools, tasks, and a user simulator. The retail domain covers
order lookup, product/user lookup, cancellation, return, exchange, address
change, payment changes, and human transfer. This overlaps strongly with the
Olist support and after-sales workflow.

Important engineering implication: tau2 is a separate benchmark environment,
not a dataset to import into the Olist app. The benchmark owns its own retail
database, tools, policy, user simulator, and scoring. Our integration should
therefore be an adapter implementing tau2's `HalfDuplexAgent` interface.

## Implemented Adapter

`benchmark_adapters/tau2_retail_agent.py` implements a tau2 custom agent:

- Implements `HalfDuplexAgent.generate_next_message()`.
- Receives tau2 retail tools and domain policy at construction time.
- Uses tau2's `generate()` utility so tool calls are evaluated by tau2.
- Adds a production-style system prompt: read tools before write tools, ask for
  clarification on missing slots, repair recoverable parameters once, and keep a
  confirmation boundary before write tools.

`scripts/run_tau2_retail_subset.py` is the local launcher:

```bash
python scripts/run_tau2_retail_subset.py \
  --tau2-root D:/benchmarks/tau2-bench \
  --agent-llm openai/gpt-4.1-mini \
  --user-llm openai/gpt-4.1-mini \
  --num-tasks 5 \
  --dry-run
```

Run without `--dry-run` after configuring an API key in the tau2 environment.
Results are written by tau2 under `tau2-root/data/simulations/`.

## Why Not Put tau2 in pyproject

The main app uses Python 3.11. tau2/tau3-bench requires Python `>=3.12,<3.14`.
Keeping the benchmark in an external checkout avoids dependency drift and lets
GitHub CI remain fast and deterministic.

## Benchmark ROI

| Candidate | Value | Difficulty | Workload | ROI | Recommendation |
|---|---|---:|---:|---:|---|
| tau2/tau3-bench retail subset | Directly comparable customer-service agent score; closest to this project | Medium | 1-2 days for subset, 3-5 days for stronger agent | High | Do first |
| tau2 banking_knowledge | Tests RAG over unstructured knowledge with configurable retrieval | Medium-High | 2-4 days | High for RAG roles | Do after retail |
| Berkeley Function Calling Leaderboard subset | Measures schema/tool-call correctness across many APIs | Medium | 2-3 days | Medium-High | Good separate mini-project |
| ToolBench-style tool-use agent | Broad tool-use research benchmark | High | 1-2 weeks | Medium | Less aligned with e-commerce resume |
| SWE-bench Lite | Software engineering agent benchmark | High | 1-2 weeks | High for coding-agent roles, lower for e-commerce agent | Separate project only |
| RAGAS/DeepEval retrieval QA benchmark | Easy quality metrics for RAG answers | Low-Medium | 1 day | Medium | Add as supporting eval, not core project |

## Interview Positioning

Best answer:

"I separated business eval from public benchmark eval. The Olist/Bitext/ResCommons
suite proves my own product chain works on public data. tau2 retail gives an
external comparable score because it owns the policy, tools, user simulator, and
reward function. I did not merge tau2 into the app dependency graph because its
Python/runtime requirements differ from the service. Instead I wrote a tau2
adapter that implements the benchmark's agent interface."

Weak answer to avoid:

"I used Olist, so I ran a benchmark." Olist is a public dataset, not an Agent
benchmark.
