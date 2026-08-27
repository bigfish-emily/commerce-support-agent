# Route Drift Report

This report compares the live LLM planner output against the pinned eval expectation.
model: `deepseek-v4-flash`; prompt_version: `unknown`; cases: `30`.

## First-Intent Distribution

| intent | expected | actual | delta |
|---|---:|---:|---:|
| ops_decision | 7 | 7 | +0 |
| order_status | 9 | 9 | +0 |
| policy | 7 | 7 | +0 |
| qa | 7 | 7 | +0 |

## Task-Sequence Distribution

| task_sequence | expected | actual | delta |
|---|---:|---:|---:|
| ops_decision | 6 | 6 | +0 |
| ops_decision,policy | 1 | 1 | +0 |
| order_status | 6 | 6 | +0 |
| order_status,escalation | 1 | 1 | +0 |
| order_status,policy,escalation | 2 | 2 | +0 |
| policy | 6 | 6 | +0 |
| policy,escalation | 1 | 1 | +0 |
| qa | 6 | 6 | +0 |
| qa,escalation | 1 | 1 | +0 |

## Mismatches

task_exact_mismatch_rate: `0.00%`.