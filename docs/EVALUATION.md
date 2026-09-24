# Evaluation

## What Is Being Measured

The primary product metric is **Case Resolution Success (CRS)**. A case passes
only when the workflow:

1. covers the customer-requested tasks in their requested order;
2. selects the correct disposition: answer, clarify, reject, auto-execute, or
   handoff;
3. uses the correct action type and produces the expected durable SQLite order
   projection when a write is expected;
4. produces exactly one durable write after an approved handoff, and zero
   writes for clarify, reject, or blocked-input cases;
5. blocks the expected prompt-injection inputs and gives the customer a reply
   consistent with the business outcome.

This is intentionally stricter than planner JSON accuracy or tool-call syntax:
it checks the customer-visible result and the persisted business state together.

CRS is a **composite end-to-end outcome metric**, not an industry-standard
leaderboard metric and not an explanation of which component caused a change.
Use the component Quality Gate for decision routing, Verifier detection, tool
governance, lifecycle recovery, input guard and retrieval evidence metrics.

## Matched Comparison

Both variants use the same `deepseek-chat` model, customer message, Olist facts,
policy documents, and clean mutable SQLite state for every row. The baseline
uses three deliberately bare native tools; the workflow uses the governed tool
layer. This is therefore a workflow-and-governance ablation, not an identical
tool-schema comparison.

| Variant | What it contains |
|---|---|
| `bare_function_calling` | A five-turn native tool-calling loop. The model chooses tool order, action, disposition, and whether to write. |
| `langgraph_workflow` | The product graph: task state, typed slot extraction, deterministic Decision Engine and Verifier, checkpointed staff review, idempotent write projection, and customer presenter. |

The baseline is deliberately thin. It is a control condition for the value of
workflow constraints, not a vendor model leaderboard or a production baseline.

## Challenge Split

`evaluation/e2e/cases_challenge.jsonl` contains 24 manually authored customer
requests grounded in a public Olist snapshot. Its order ids do not overlap with
the 12-case development split or 36-case regression split. The challenge covers
small talk, ambiguous requests, policy questions, order reads, invalid writes,
refund review, staff approval/rejection, multi-intent requests, English input,
and prompt injection.

The policy oracle in `evaluation/e2e/after_sales_policy_v2.json` is evaluated
without importing the production `AfterSalesDecisionEngine`. This prevents a
production rule change from silently redefining the gold outcome.

## First Challenge Run

Run date: 2026-09-17. Model: `deepseek-chat` through the configured
OpenAI-compatible endpoint. One temporary SQLite database was created for the
run and its mutable case/event/projection tables were reset before each case and
each arm. Olist historical facts and policy documents stay fixed.
Latency is wall-clock time from API request to the pre-review customer response;
it excludes the duration a human reviewer may take to act.

| Metric | Bare function calling | LangGraph workflow | Difference |
|---|---:|---:|---:|
| CRS | 7/24 (29.17%) | 22/24 (91.67%) | +62.50 pp |
| Task coverage | 23/24 (95.83%) | 23/24 (95.83%) | 0.00 pp |
| Disposition accuracy | 19/24 (79.17%) | 22/24 (91.67%) | +12.50 pp |
| Action-type accuracy | 20/24 (83.33%) | 24/24 (100.00%) | +16.67 pp |
| State-projection accuracy | 22/24 (91.67%) | 24/24 (100.00%) | +8.33 pp |
| Unsafe-write rate | 1/15 (6.67%) | 0/15 (0.00%) | -6.67 pp |
| Customer outcome explained | 9/24 (37.50%) | 24/24 (100.00%) | +62.50 pp |
| p50 / p95 pre-review latency | 3.17 / 4.38 s | 0.77 / 1.86 s | -2.40 / -2.52 s |

The baseline's task coverage is derived from its observed tool calls, not its
self-reported final JSON. It therefore receives credit for a read or write it
actually performed. The workflow's advantage comes from the decision and
verification gates, durable review boundary, and deterministic customer
receipts, rather than from a scoring convention.

## Failure Analysis and Regression

The first challenge run exposed two workflow failures.

| Case | Root cause | Correction | Regression evidence |
|---|---|---|---|
| “现在是不是已经取消” | The status-confirmation fast path covered only a narrower wording set and treated this as a cancellation request. | Added status-confirmation variants before action routing. | The repaired case returned `order_status` and no write. |
| Delivered order address change | Address fields were validated before the order-state prohibition, so the customer was asked for data before receiving the rejection. | Moved state-policy rejection ahead of address slot completion. | The repaired case returned `reject` and no write. |

The six-row targeted live regression batch containing both failures and nearby
read/reject variants passed 6/6 after the correction. The original challenge
score remains reported above; it is not overwritten by the regression result.

## Retrieval and Reliability Components

- The ResCommons retrieval experiment is a component metric, not CRS: the local
  hybrid retriever improved intent@1/intent@5 from 64%/81% for BM25 to 77%/92%
  on the derived labeled support corpus.
- Policy/FAQ/merchant-rule markdown changes use a separate deterministic release
  gate. It checks frozen gold-question Top1, Recall@3, MRR@3, source-type@1,
  returned-evidence integrity, document structure, and instruction-like text
  before the pack is released. The gate is deliberately independent of an LLM
  so a document edit has a fast, reproducible pre-release signal.
- Idempotency, persistent replay, and concurrent side-effect locking are
  deterministic reliability contracts covered by unit tests over the SQLite
  case store and Redis-compatible runtime store. They are reported separately
  from LLM-driven CRS because model sampling is not the source of that risk.
- External tool-use benchmarks such as tau-bench and BFCL remain separate
  experiments. They do not share the Olist policy, tools, or case lifecycle,
  and are not presented as this product's end-to-end result.

## Reproduce

Set an OpenAI-compatible API key and model in `.env`.

```powershell
$env:PYTHONPATH='.'
& .\.venv\Scripts\python.exe -m evaluation.e2e.after_sales_e2e_bench validate
& .\.venv\Scripts\python.exe -m evaluation.e2e.after_sales_e2e_bench run `
  --split challenge --output artifacts\e2e_challenge.json
```

Artifacts contain row-level messages and are intentionally ignored by Git.
They should be reviewed before reporting a new run because provider-side seed
control and token accounting are not available in this setup.

## Policy-RAG Release Gate

Run this whenever `data/knowledge_base/*.md` changes:

```powershell
$env:PYTHONPATH='.'
& .\.venv\Scripts\python.exe -m evaluation.rag_release_gate --gate `
  --output artifacts\rag_release.json `
  --markdown-output artifacts\rag_release.md
```

The frozen policy set contains 19 hand-reviewed questions spanning policy,
FAQ, and merchant-rule evidence. `Top1` requires the exact expected policy
section to rank first; `Recall@3` requires it to occur in the first three;
`MRR@3` remains sensitive to ranking position. `source_type@1` protects the
policy/FAQ/merchant-rule boundary, while evidence integrity requires each
returned item to retain a source, source type, section title, and text.

For retrieval implementation or support-corpus changes, include the optional
ResCommons component regression as well:

```powershell
& .\.venv\Scripts\python.exe -m evaluation.rag_release_gate --gate --include-support-corpus
```

## Layered After-Sales Quality Gate

Run this deterministic gate after changing Decision Engine rules, Verifier,
ToolCallManager, case lifecycle code, input guard patterns, or policy documents:

```powershell
& .\.venv\Scripts\python.exe -m evaluation.after_sales_quality_gate generate
& .\.venv\Scripts\python.exe -m evaluation.after_sales_quality_gate run `
  --output artifacts\after_sales_quality_gate.json `
  --markdown-output artifacts\after_sales_quality_gate.md
```

The frozen `after_sales_quality_v1` fixture contains 250 cases anchored to 250
distinct Olist orders, with human-authored wording templates across safe
automatic execution, rejection, clarification, HITL routing, multi-intent
requests, and known adversarial inputs. Its labels are an independent evaluation
policy contract, not generated by the production Decision Engine.

The report intentionally separates:

| Layer | Metrics | Claim it supports |
|---|---|---|
| Decision | disposition accuracy; next-step accuracy; safe-auto and HITL precision/recall | Rule and routing regressions over frozen order facts |
| Verifier | injected-fault detection | Whether an internally inconsistent decision is stopped |
| Tool governance | schema rejection; permission denial; cache hit; lock contention; idempotent write | Execution-control contracts |
| Case lifecycle | duplicate submission; appeal; timeout transition | Persisted HITL recovery contracts |
| Safety | known-pattern injection blocking | Regression coverage for explicit unsafe inputs |
| Retrieval | policy evidence retrieval and optional ResCommons hybrid retrieval | Evidence availability and ranking |

The Verifier gate injects 20 malformed decisions: five high-risk action types
times four fault families (missing policy evidence, reject/write contradiction,
low confidence without a safe exit, and review outcome without HITL). It is a
contract test for those explicit invariants, not a distributional estimate of
real-world model error.

These deterministic metrics do not quantify open-ended model quality, customer
satisfaction, or real payment reliability. Run the live E2E comparison on a
versioned held-out sample for those claims; record model ID, prompt revision,
run date, artifacts, and provider constraints with the result.

## Versioned 50-Case Live Holdout

The quality fixture also produces `evaluation/e2e/cases_quality_holdout.jsonl`:
50 Olist-grounded cases that are order-disjoint from the existing dev, test,
and challenge splits. It contains 10 cases each for safe automatic service-case
creation, prohibited cancellation, refund handoff, invoice handoff, and
multi-intent read/policy/refund handoff.

```powershell
& .\.venv\Scripts\python.exe -m evaluation.after_sales_quality_gate generate-live-holdout
& .\.venv\Scripts\python.exe -m evaluation.e2e.after_sales_e2e_bench run `
  --cases-file evaluation\e2e\cases_quality_holdout.jsonl `
  --output artifacts\e2e_quality_holdout.json
```

The live artifact reports each arm's `execution_error_rate`, completed-case
count, planner provenance (`llm` versus `deterministic_fallback`), stable
fallback-reason counts, and CRS. Only rows planned by the live model belong in
a live-model quality claim. Fallback rows remain availability evidence and are
reported separately rather than inflating model quality or hiding a gateway
outage behind a successful workflow result.

The local observability summary exposes the same provenance as
`llm_planner_attempts`, `llm_planner_successes`,
`deterministic_planner_fallbacks`, and `planner_fallback_reason_counts`. These
are runtime availability metrics; they should never be substituted for task
quality metrics such as CRS or action correctness.
