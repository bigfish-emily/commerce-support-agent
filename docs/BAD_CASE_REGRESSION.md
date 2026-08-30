# Bad-Case Regression Notes

This project treats benchmark failures as regression assets. A failed case should
leave three artifacts:

1. a failure explanation,
2. a code or prompt/process change,
3. a deterministic test or benchmark rerun record.

## tau2 Retail Task 6: Multi-Item Confirmation Scope Drift

### Symptom

In the 30-task tau2 retail subset, task `6` failed while all other tasks passed.
The customer initially wanted to exchange two items in the same order: a water
bottle and a desk lamp. The scenario also says that if the agent asks for
confirmation, the customer should only exchange the desk lamp.

The failed run executed `exchange_delivered_order_items` with both item ids. The
golden action expected only the desk lamp item id.

### Root Cause

The agent treated the confirmation boundary as a conversation formality instead
of a scope contract. It asked a broad confirmation question for multiple item
mutations. The user simulator then answered broadly, and the agent executed a
multi-item write tool call.

This is a production-relevant failure type:

- customer-service users often change their mind during confirmation;
- write tools are irreversible or costly to reverse;
- a broad "yes" is not enough for multi-item mutation scope;
- prompt-only instructions are not a reliable safety boundary.

### Fix

The tau2 adapter now has a deterministic confirmation-scope guard:

- If the previous assistant turn is a multi-item write confirmation and the user
  replies with a broad confirmation such as "yes", "both", or "all", the adapter
  does not allow the next write step to proceed.
- It asks the user to list the exact item names to process now.
- Scoped confirmations such as "desk lamp only" are allowed through.

Code:

- `benchmark_adapters/tau2_confirmation_guard.py`
- `benchmark_adapters/tau2_retail_agent.py`
- `tests/test_tau2_bad_cases.py`

### Regression Standard

Minimum gate:

- local unit tests must pass;
- tau2 task `6` should be rerun into an isolated result directory;
- only after the single-task bad case passes should the 30-task summary be
  refreshed.

Baseline before this fix:

- tau2 retail 30-task subset: `pass^1 = 96.67%`, `29/30`;
- failed task id: `6`;
- failure category: write-action scope drift.

## tau2 Retail Task 20: Footwear Variant Size Drift

### Symptom

In a later full regression, task `20` selected a cheaper running-shoe variant
with the wrong size. The write tool call was otherwise valid, but the benchmark
expected the exchange to preserve the customer's current shoe size unless the
user explicitly asked for a size change.

### Root Cause

The agent optimized for price and availability but did not treat footwear size
as an implicit hard constraint. A first repair attempt scanned the whole history
for phrases like "size 8"; because assistant/tool messages also describe
candidate variants, it incorrectly concluded that the user had requested a size
change.

### Fix

The tau2 adapter now applies a deterministic variant preflight before returning
write tool calls:

- Build an item-to-product and product-to-variants view from prior tool outputs.
- For footwear writes, keep the original size by default.
- Only allow a different shoe size when a user message explicitly requests it.
- Prefer the highest-priced available same-size variant when several candidates
  are valid.

Code:

- `benchmark_adapters/tau2_variant_guard.py`
- `benchmark_adapters/tau2_retail_agent.py`
- `tests/test_tau2_bad_cases.py`

Regression evidence:

- `benchmark_runs/tau2_retail/task20_user_only_size_guard_summary.md`
- isolated task `20`: reward `1.0`, DB match `1/1`, write action match `1/1`.

## tau2 Retail Policy Cluster: Tasks 19, 22, and 29

### Symptom

A full 30-task regression after the first guard exposed three policy-boundary
failures:

- task `19`: the database action was correct, but the final answer omitted the
  explicit combined refund/exchange total;
- task `22`: after an address-change regret, the agent blurred the boundary
  between default-address rollback and order-address mutation;
- task `29`: the user referenced an item in another pending order as a desired
  exchange variant, and the agent incorrectly modified that pending order too.

### Fix

These were fixed as retail-policy constraints in the tau2 prompt and then
validated as a targeted regression set:

- when comparing refund/exchange/cancel options, state individual amounts and
  the combined total with exact arithmetic;
- for address changes, update default address and eligible pending orders first;
  if the user later regrets changing the default address, revert only the
  default address unless the user explicitly asks to revert order addresses;
- referencing an item in another pending order is a lookup signal, not
  authorization to mutate that order.

Regression evidence:

- `benchmark_runs/tau2_retail/failed4_policy_regression_summary.md`
- targeted failed set `19/20/22/29`: `4/4` passed.

## Current Regression Result

After the targeted fixes, the 30-task tau2 retail subset was rerun from scratch:

- `pass^1 = 100.00%`, `30/30`;
- `avg_reward = 100.00%`;
- DB match `30/30`;
- read action match `165/170`;
- write action match `38/38`;
- NL assertions `10/10`;
- p95 duration `31.17s`;
- average total cost `$0.004248` per conversation;
- failed task ids: `None`.

Evidence:

- `benchmark_runs/tau2_retail/last_summary.md`
- `benchmark_runs/tau2_retail/last_summary.json`

Local validation:

- `ruff check .`
- `python -m pytest`: `71 passed`
