# AIHubMix LLM-as-Judge Partial Run Summary

运行时间：2026-09-07

Provider：AIHubMix OpenAI-compatible API

Model：`coding-glm-5-free`

状态：本轮完成 8 条后触发 provider 429 免费额度限制；后续 2 条未完成。

说明：早期脚本在 offset 补跑失败时覆盖了完整 Markdown/JSONL 报告。本文件根据本轮终端输出整理，用作 partial evidence summary。脚本已修复，后续重新运行会生成完整 `evaluation/llm_judge_eval_report.md` 和 `evaluation/llm_judge_eval_results.jsonl`。

## Summary

| 指标 | 结果 |
|---|---:|
| completed cases | 8 |
| pass_overall | 8/8 |
| pass_rate | 100.00% |
| answer_relevance_avg | 4.75/5 |
| faithfulness_avg | 4.88/5 |
| tool_correctness_avg | 5.00/5 |
| hitl_correctness_avg | 5.00/5 |

## Completed Cases

| case | user input | route/tasks | main tool/action | judge scores | finding |
|---|---|---|---|---|---|
| category_risk | `health beauty 类目有什么运营风险？` | `qa` | `search_category_risk` | 5/5/5/5 | 命中 `health_beauty`，回答延迟率、低评分率、取消率和运营建议。 |
| policy_boundary | `退款补偿能不能直接承诺？` | `policy` | `search_policy_knowledge` | 5/5/5/5 | 正确说明退款/补偿不能直接承诺，需要政策/工具/人工确认。 |
| multi_intent_hitl | `查订单...状态，并且说明退款政策，然后生成售后升级话术` | `order_status -> policy -> escalation` | `get_order_status`, `search_policy_knowledge`, `prepare_side_effect` | 5/5/5/5 | 只读任务先执行，升级动作停在 HITL。 |
| order_status_grounding | `帮我查一下订单...的状态和是否延迟` | `order_status` | `get_order_status` | 5/5/5/5 | 返回 delivered、预计/实际送达、延迟 11 天、金额、类目和评价分。 |
| invoice_policy_boundary | `客户要开发票，客服可以直接说发票已经开好了吗？` | `policy` | `search_policy_knowledge` | 5/5/5/5 | 命中 Invoice Request Policy，说明发票工具确认前不能声称已开具。 |
| address_change_hitl | `帮订单...改一下收货地址` | `escalation` | `prepare_side_effect`, `action_type=change_address` | 4/4/5/5 | 功能正确：已送达订单拒绝改址且不执行写工具；回答混入延迟/评价等弱相关事实，话术可优化。 |
| cancel_delivered_order | `帮我取消订单...` | `escalation` | `prepare_side_effect`, `action_type=cancel_order` | 4/5/5/5 | 功能正确：已送达订单拒绝取消且不执行写工具；回答可减少弱相关事实。 |
| ops_priority_queue | `生成一份售后运营日报，列出最该优先跟进的类目和订单` | `ops_decision` | `generate_after_sales_priority_report` | 5/5/5/5 | 返回 5 个高风险类目和 8 个优先跟进订单，并声明建议为只读。 |

## Observed Bad Cases

1. `coding-glm-5-free` 经常把结构化 JSON 包在 markdown fence 或返回顶层 list，导致 guard/planner/extractor fallback 增多。
2. 第 9-10 条补跑触发 AIHubMix 免费模型 429 quota，LLM-as-Judge 未完成全量 10 条。
3. `address_change_hitl` 和 `cancel_delivered_order` 的业务判断正确，但客户回复草稿包含延迟/评价分等弱相关事实，影响 answer relevance 和 polish。

## Follow-up Fixes Already Made

1. `app/llm/json_fallback.py` 支持顶层 JSON list、markdown fenced JSON、`InputGuardResult.reason` 缺失修复、`JudgeResult.explanation -> rationale` 修复。
2. `evaluation/llm_judge_eval.py` 改为边跑边落盘，offset 补跑输出到带 offset 的文件。
3. 当 provider 429 且本轮没有完成任何 case 时，只写 `evaluation/llm_judge_eval_last_error.md`，不再覆盖已有主报告。
