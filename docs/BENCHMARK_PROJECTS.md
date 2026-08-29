# Benchmark Work Plan

This project already uses public data for business evaluation. The next step is
to add horizontal, externally comparable benchmarks without weakening the main
business project.

## Correct Benchmark Direction

The right move is not to invent more synthetic project metrics. The right move
is to connect the existing Agent to official, externally comparable benchmarks:

1. tau2/tau3-bench retail first.
2. BFCL second, focused on function/tool-calling reliability.
3. GAIA/AgentBench only if there is extra time, because the ROI is lower for an
   e-commerce support resume project.

The old `sierra-research/tau-bench` repository now warns that its airline and
retail tasks are outdated. Use the actively updated `sierra-research/tau2-bench`
repository, whose package name is still `tau2` and whose current release is
described as tau3/τ³-bench.

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
Results are written by tau2 under `tau2-root/data/simulations/`. The launcher
accepts `--judge-llm` so tau2 natural-language assertions do not silently fall
back to the default OpenAI model when using DeepSeek or another compatible
provider.

After a real run, summarize the official result file or result directory:

```bash
python scripts/summarize_tau2_results.py \
  --results D:/benchmarks/tau2-bench/data/simulations/olist_agent_tau2_retail_subset
```

The summary reports `avg_reward`, `pass^k`, termination counts, duration/cost
statistics, and failed task IDs. This is the number that can be quoted in a
resume after the benchmark has actually run.

Current local tau2 result:

- Environment: external `benchmark-tmp` tau2 checkout, Python 3.12.12,
  DeepSeek `deepseek/deepseek-chat` for agent, user simulator, and NL assertion
  judge.
- Run: `olist_agent_tau2_retail_30_deepseek_final`, 30 retail tasks, 1 trial
  each, serial concurrency, timeout 300s.
- Result: `avg_reward=96.67%`, `pass^1=96.67%`, `db_match=29/30`,
  `read_action_match=163/170`, `write_action_match=37/38`,
  `nl_assertions=10/10`, `p95_duration=28.78s`,
  `avg_total_cost=$0.001573`.
- Failed task IDs: `6`.
- Evidence: `benchmark_runs/tau2_retail/last_summary.md` and
  `benchmark_runs/tau2_retail/last_summary.json`.

This is an official benchmark subset score, not a full leaderboard
submission. It is safe to cite only with the subset size and model/provider
clearly stated.

## Why Not Put tau2 in pyproject

The main app uses Python 3.11. tau2/tau3-bench requires Python `>=3.12,<3.14`.
Keeping the benchmark in an external checkout avoids dependency drift and lets
GitHub CI remain fast and deterministic.

## BFCL Scope

BFCL is valuable, but it answers a different question.

tau2/tau3 retail asks: can the full agent converse with a simulated customer,
follow retail policy, call tools, and finish the business task?

BFCL asks: can the model or adapter produce correct function calls across
schemas, parallel calls, multi-turn calls, executable calls, relevance detection,
and newer agentic categories?

So BFCL should support the resume claim "tool-calling governance and schema
robustness", not replace tau2 retail as the main e-commerce Agent benchmark.

Implemented launcher:

```bash
python scripts/run_bfcl_subset.py \
  --bfcl-root D:/benchmarks/bfcl \
  --model gpt-4.1-mini-FC \
  --test-category simple_python \
  --test-category multiple_python \
  --test-category parallel_python \
  --dry-run
```

For low-cost smoke tests, use BFCL `--run-ids` in the external BFCL project and
evaluate with `--partial-eval`. Do not compare partial scores against the
leaderboard.

After evaluation, summarize BFCL score files:

```bash
python scripts/summarize_bfcl_results.py \
  --score-root D:/benchmarks/bfcl/score \
  --model gpt-4.1-mini-FC
```

The summary reads BFCL category score JSON files and `data_*.csv` leaderboard
exports. It is schema-tolerant across BFCL versions and is covered by unit tests.

## Benchmark ROI

| Candidate | Value | Difficulty | Workload | ROI | Recommendation |
|---|---|---:|---:|---:|---|
| tau2/tau3-bench retail subset | Directly comparable customer-service agent score; closest to this project | Medium | 1-2 days for subset, 3-5 days for stronger agent | High | Done for 30-task DeepSeek official subset; next fix failed task `6`, then expand to 50 tasks or full split |
| tau2 banking_knowledge | Tests RAG over unstructured knowledge with configurable retrieval | Medium-High | 2-4 days | High for RAG roles | Do after retail |
| Berkeley Function Calling Leaderboard subset | Measures schema/tool-call correctness across many APIs | Medium | 1-2 days for AST subset, 3-5 days for multi-turn/agentic | Medium-High | Do second; supports tool-governance claims |
| ToolBench-style tool-use agent | Broad tool-use research benchmark | High | 1-2 weeks | Medium | Less aligned with e-commerce resume |
| SWE-bench Lite | Software engineering agent benchmark | High | 1-2 weeks | High for coding-agent roles, lower for e-commerce agent | Separate project only |
| RAGAS/DeepEval retrieval QA benchmark | Easy quality metrics for RAG answers | Low-Medium | 1 day | Medium | Add as supporting eval, not core project |

## Interview Positioning

Best answer:

"I separated business eval from public benchmark eval. The Olist/Bitext/ResCommons
suite proves my own product chain works on public data. tau2 retail gives an
external comparable score because it owns the policy, tools, user simulator, and
reward function. BFCL gives a separate tool-calling score for function schema and
multi-turn tool-use reliability. I did not merge benchmark dependencies into the
app dependency graph because their runtime requirements differ from the service.
Instead I keep thin adapters and reproducible launch manifests. The current
tau2 retail number is a 30-task DeepSeek official subset score, so I report it
with task count, model, latency, cost, and failed task id, not as a full
leaderboard claim."

Weak answer to avoid:

"I used Olist, so I ran a benchmark." Olist is a public dataset, not an Agent
benchmark.
