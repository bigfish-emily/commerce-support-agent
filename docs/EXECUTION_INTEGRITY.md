# Execution integrity and semantic decisions

## Implemented boundaries

- Missing Olist refund/fraud fields are unknown (`null`), not evidence of no risk.
  Automatic cancellation now requires explicit non-refunded/non-partial evidence.
- Legacy rule scores carry `confidence_source=legacy_heuristic`. They are not
  probabilities and no longer trigger confidence-based escalation. New rule
  outputs identify the rule and rule-policy version. This version identifies
  executable rules, not the version of every retrieved document.
- Pending actions bind the principal/session, action, message parameters,
  decision and order facts. Changed or missing bindings fail closed on resume.
  Old checkpoints without bindings require a new request.
- Failed writes no longer become completed tasks/executed reviews. Ambiguous
  writes return `outcome_unknown`, without automatic retries or success fallback.
- SQLite-backed business tools persist a write quarantine before dispatch.
  Restart or rephrasing cannot silently retry a quarantined tenant/order.
  A crashed `submitted` record stays quarantined. Internal reconciliation requires
  an evidence reference; there is no public unlock endpoint.
- Review decisions are durable commands in the case database. A lifespan worker
  scans deadlines, leases commands, resumes checkpoints and retries recoverable
  failures. Redis eviction does not cancel a case. Committed local order events
  provide replay receipts when a crash happens before checkpoint persistence.
- Redis fixed-window counters use an atomic Lua script. Order-scoped write locks
  renew with token checks; the non-expiring SQLite write reservation remains the
  final local concurrent-write boundary when Redis leases are lost.
- Independent read tasks execute with bounded concurrency and private state;
  writes remain sequential. Configured policy MCP is called through ToolCallManager.

## Limits still requiring integration work

The order fingerprint is a preflight check, not an atomic compare-and-swap at a
remote OMS. Redis TTL does not stop a remote operation. External providers still
need stable idempotency keys and receipt/status reconciliation. The current
business write is a local SQLite simulation, not money movement. The journal
provides quarantine, not automatic provider reconciliation. Its successful
records are removed because durable success deduplication belongs to the case
store. In-memory demos have no restart durability. Cases and order projections
retain their existing tenant limitations; this patch does not claim complete
multi-tenant storage isolation.

## Semantic comparison

Availability as of 2026-09-25: Jev access cannot currently be obtained for this
project. Keep the backend off. The adapter is unverified against a live account;
there are no Jev latency, accuracy, or end-to-end improvement measurements.
Its presence is not a dependency of the customer workflow.

`SEMANTIC_DECISION_SHADOW=rules|llm|jev` enables a prediction trace in the planner.
Default is `off`. Shadow predictions never approve/cancel requests or bypass
the planner. Shadow mode adds latency; it is not a performance optimization yet.
Existing requests intercepted before the planner do not get a shadow prediction.

Jev uses the official `https://api.typesafe.ai/openapi.json` contract, checked
2026-09-25: POST `/v1/systemone`, ChoiceQuestion `criteria`, typed answers and
token usage. Configure `TYPESAFE_API_KEY` and an account-supported pinned
`JEV_MODEL`. No credential is bundled. The response's resolved model is recorded.
Customer context is sent only when the shadow backend is explicitly enabled.

Run the same fixtures with each backend (export credentials beforehand):

```powershell
python -m evaluation.semantic_update_eval --backend rules --cases evaluation/semantic_update_smoke.jsonl --output evaluation/semantic_rules_smoke.jsonl
python -m evaluation.semantic_update_eval --backend jev --cases evaluation/semantic_update_smoke.jsonl --output evaluation/semantic_jev_smoke.jsonl
python -m evaluation.semantic_update_eval --backend llm --cases evaluation/semantic_update_smoke.jsonl --output evaluation/semantic_llm_smoke.jsonl
```

Outputs include row-level predictions, context/dataset hashes, backend errors,
resolved models, token counts, accuracy, fixed-five-label macro-F1, nearest-rank
latencies, and multiclass Brier score where a valid distribution was returned.
Missing probabilities or usage remain unavailable, not zero-quality evidence.
The ten authored fixtures are smoke cases, not held-out research evidence.
Before making a speed/quality claim: freeze independent scenario families,
calibrate thresholds on development data, and compare end-to-end task outcomes.

## Evaluation audit

The old CRS scorer enforces ordered coverage and phrase-based response checks;
its baseline has a different tool surface. Historical gains are exploratory.
The new attribution runner fixes the plan, tools and initial state while varying
the assessment provider and Verifier. Budget-exhausted or cache-incomplete runs
cannot be published as valid comparisons. It measures conditional component
effects, not the entire customer HTTP experience. Final post-repair live evidence
is still pending; do not reuse historical scores for the changed runtime.
