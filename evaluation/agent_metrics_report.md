# Agent Evaluation Metrics Report

本报告由 `python -m evaluation.agent_metrics_report` 生成。默认不需要 API key；LLM judge 属于可选联网评测，不能和本地确定性指标混为一谈。

| 分类 | 指标 | 当前结果 | 样本量 | 含义 | 计算方式 | API key |
|---|---|---:|---:|---|---|---|
| 规划/意图 | 细粒度客服 intent 到业务 route intent 准确率 | 100.00% | 1080 | 验证 27 类客服原始意图能否映射到 order_status/policy/escalation 等业务入口。 | map_intent(intent).route_intent == expected_intent 的比例。 | 否 |
| 规划/意图 | 多意图拆解 exact match | 100.00% | 60 | 用户一句话含多个任务时，预测任务列表必须和 gold 完全一致。 | predicted_intents == expected_intents 的比例。 | 否 |
| 规划/意图 | 多意图 contains-all | 100.00% | 60 | 允许多预测，但不能漏掉用户要求的业务任务。 | expected_intents 是否为 predicted_intents 子集。 | 否 |
| 规划/意图 | 多意图顺序准确率 | 100.00% | 60 | 验证 read-only 查询是否在副作用动作之前，避免先执行退款/取消。 | expected_intents 在 predicted_intents 中的相对顺序是否保持。 | 否 |
| 规划/意图 | 副作用识别准确率 | 100.00% | 60 | 验证 planner/decomposer 能否识别需要 HITL 的任务。 | any(task.side_effect) == has_side_effect。 | 否 |
| 工具/参数 | 业务工具任务成功率 | 100.00% | 245 | 验证订单查询、类目分析、售后草稿是否都能被事实工具支撑。 | Olist gold case 上，工具输出与 expected order/status/category/draft 是否匹配。 | 否 |
| 工具/参数 | 工具选择覆盖率 | 100.00% | 245 | 每个 gold route intent 是否都有确定性工具承接。 | expected_intent 是否能映射到预期 tool name。 | 否 |
| 工具/参数 | order_id 参数修复准确率 | 100.00% | 6 | 工具参数含空格、大小写、前缀、缺失、多 ID 时是否能修复或拒绝。 | repair_order_id 输出 ok/value/error_code 与 gold 是否一致。 | 否 |
| 工具/参数 | 澄清返回正确率 | 100.00% | 3 | 缺失/不完整/多订单号时，系统是否返回可执行澄清而不是盲目重试。 | 非法参数 case 中 result.ok=false 且 message 非空。 | 否 |
| 工具/参数 | 副作用任务 HITL 覆盖率 | 100.00% | 120 | 售后/退款/取消等副作用任务是否全部进入人工确认门。 | expected_intent=escalation 的 case 是否都要求确认。 | 否 |
| 工具/参数 | 副作用 action_type 分发覆盖率 | 100.00% | 5 | 退款、取消、改地址、发票、工单五类动作是否都有工具落点。 | 五类 action_type 是否都有可执行的幂等工具模拟。 | 否 |
| RAG/检索 | 类目 RAG exact_underscore Top1 | 32.08% | 240 | 首位召回是否命中正确类目。 | ranked[0] == expected_category。 | 否 |
| RAG/检索 | 类目 RAG exact_underscore Recall@3 | 32.08% | 240 | Top3 是否包含正确类目，衡量召回能力。 | expected_category in ranked[:3]。 | 否 |
| RAG/检索 | 类目 RAG exact_underscore MRR@3 | 32.08% | 240 | 正确类目越靠前分数越高。 | 命中时累加 1/rank，未命中为 0。 | 否 |
| RAG/检索 | 类目 RAG exact_underscore realistic_alias Top1 | 5.00% | 40 | 中文别名、行业俗称、英文近义表达等更接近真实用户说法的首位命中率。 | 只在 realistic_alias 子集上计算 ranked[0] == expected_category。 | 否 |
| RAG/检索 | 类目 RAG token_overlap Top1 | 62.92% | 240 | 首位召回是否命中正确类目。 | ranked[0] == expected_category。 | 否 |
| RAG/检索 | 类目 RAG token_overlap Recall@3 | 66.67% | 240 | Top3 是否包含正确类目，衡量召回能力。 | expected_category in ranked[:3]。 | 否 |
| RAG/检索 | 类目 RAG token_overlap MRR@3 | 64.65% | 240 | 正确类目越靠前分数越高。 | 命中时累加 1/rank，未命中为 0。 | 否 |
| RAG/检索 | 类目 RAG token_overlap realistic_alias Top1 | 25.00% | 40 | 中文别名、行业俗称、英文近义表达等更接近真实用户说法的首位命中率。 | 只在 realistic_alias 子集上计算 ranked[0] == expected_category。 | 否 |
| RAG/检索 | 类目 RAG adaptive_rewrite Top1 | 92.08% | 240 | 首位召回是否命中正确类目。 | ranked[0] == expected_category。 | 否 |
| RAG/检索 | 类目 RAG adaptive_rewrite Recall@3 | 92.50% | 240 | Top3 是否包含正确类目，衡量召回能力。 | expected_category in ranked[:3]。 | 否 |
| RAG/检索 | 类目 RAG adaptive_rewrite MRR@3 | 92.22% | 240 | 正确类目越靠前分数越高。 | 命中时累加 1/rank，未命中为 0。 | 否 |
| RAG/检索 | 类目 RAG adaptive_rewrite realistic_alias Top1 | 97.50% | 40 | 中文别名、行业俗称、英文近义表达等更接近真实用户说法的首位命中率。 | 只在 realistic_alias 子集上计算 ranked[0] == expected_category。 | 否 |
| RAG/检索 | 政策 KB Top1 | 100.00% | 12 | 政策问题首位是否命中正确章节。 | ranked[0] == expected_section。 | 否 |
| RAG/检索 | 政策 KB Recall@3 | 100.00% | 12 | Top3 是否包含正确政策章节。 | expected_section in ranked[:3]。 | 否 |
| RAG/检索 | 政策 KB MRR@3 | 100.00% | 12 | 正确政策章节越靠前分数越高。 | 命中时累加 1/rank。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 intent@1 | 64.00% | 100 | 首位召回文档的客服 intent 是否与 query intent 一致。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 intent@5 | 81.00% | 100 | Top5 是否出现同 intent 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 intent_mrr@5 | 70.73% | 100 | 同 intent 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 capability@1 | 64.00% | 100 | 首位召回文档的能力标签是否匹配。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 capability@5 | 81.00% | 100 | Top5 是否出现同 capability 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 capability_mrr@5 | 70.73% | 100 | 同 capability 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid char_ngram_vector intent@1 | 76.00% | 100 | 首位召回文档的客服 intent 是否与 query intent 一致。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid char_ngram_vector intent@5 | 91.00% | 100 | Top5 是否出现同 intent 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid char_ngram_vector intent_mrr@5 | 82.62% | 100 | 同 intent 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid char_ngram_vector capability@1 | 75.00% | 100 | 首位召回文档的能力标签是否匹配。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid char_ngram_vector capability@5 | 90.00% | 100 | Top5 是否出现同 capability 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid char_ngram_vector capability_mrr@5 | 81.62% | 100 | 同 capability 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank intent@1 | 77.00% | 100 | 首位召回文档的客服 intent 是否与 query intent 一致。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank intent@5 | 91.00% | 100 | Top5 是否出现同 intent 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank intent_mrr@5 | 83.07% | 100 | 同 intent 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank capability@1 | 76.00% | 100 | 首位召回文档的能力标签是否匹配。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank capability@5 | 90.00% | 100 | Top5 是否出现同 capability 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank capability_mrr@5 | 82.07% | 100 | 同 capability 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| 运营决策 | 高风险类目覆盖率 | 100.00% | Top5 | 售后运营日报是否能输出可跟进的高风险类目列表。 | after_sales_priority_report 返回 high_risk_categories 的数量 / 5。 | 否 |
| 运营决策 | 高风险类目排序正确率 | 100.00% | 5 | 类目是否按风险分从高到低排序，便于运营优先处理。 | risk_score 序列是否单调递减。 | 否 |
| 运营决策 | 类目行动建议覆盖率 | 100.00% | 5 | 每个高风险类目是否都有可执行的运营建议。 | recommended_action 非空的比例。 | 否 |
| 运营决策 | 优先跟进订单覆盖率 | 100.00% | Top8 | 是否能从订单事实中挑出售后优先跟进队列。 | after_sales_priority_report 返回 priority_orders 的数量 / 8。 | 否 |
| 运营决策 | 优先跟进订单排序正确率 | 100.00% | 8 | 订单队列是否按售后优先级从高到低排序。 | priority_score 序列是否单调递减。 | 否 |
| 运营决策 | 订单行动建议覆盖率 | 100.00% | 8 | 每个优先订单是否给出原因和建议动作。 | recommended_action 非空且 reasons 非空的比例。 | 否 |
| 运营决策 | 运营建议只读/HITL 边界命中率 | 100.00% | 1 | 运营决策报告是否明确把建议和退款/取消等副作用执行分开。 | decision_rules 中是否声明副作用仍需 HITL。 | 否 |
| 端到端/轨迹 | 轨迹包含 plan 节点 | 100.00% | 245 | 每次任务是否先产生可审计 task plan。 | trajectory_events 中是否包含 node=plan_tasks。 | 否 |
| 端到端/轨迹 | 轨迹 intent 覆盖率 | 100.00% | 245 | 执行轨迹是否覆盖 gold route intent。 | expected_intent 是否出现在 trajectory event intent 列表。 | 否 |
| 端到端/轨迹 | 轨迹工具正确率 | 100.00% | 245 | 轨迹中是否调用了 intent 对应工具。 | event.details.tool == expected_tool(expected_intent)。 | 否 |
| 端到端/轨迹 | 副作用 HITL 轨迹覆盖率 | 100.00% | 245 | 副作用任务轨迹是否进入 awaiting_confirmation。 | escalation case 是否有 status=awaiting_confirmation。 | 否 |
| 端到端/轨迹 | 轨迹无失败率 | 100.00% | 245 | 离线 gold 轨迹是否没有 failed/blocked 事件。 | trajectory statuses 中不含 failed/blocked。 | 否 |
| 真实 LLM Agent | live case pass rate | 100.00% | 30 | 真实 LLM 作为 planner/抽槽/生成器进入 Agent 主链路后，端到端 case 是否全部通过。 | 每条 case 的 task/tool/HITL/trace/output/answer checks 全部为 true 才算 pass。 | 是 |
| 真实 LLM Agent | live task_exact | 100.00% | 30 | LLM planner 生成的任务列表是否与 gold 完全一致。 | live_agent_eval_results.jsonl 中 checks.task_exact=true 的比例。 | 是 |
| 真实 LLM Agent | live tools_used | 100.00% | 30 | 真实轨迹是否调用了该任务需要的确定性工具。 | live_agent_eval_results.jsonl 中 checks.tools_used=true 的比例。 | 是 |
| 真实 LLM Agent | live hitl_correct | 100.00% | 30 | 副作用任务是否进入 HITL，只读任务是否不误触发 HITL。 | live_agent_eval_results.jsonl 中 checks.hitl_correct=true 的比例。 | 是 |
| 真实 LLM Agent | live no_failed_event | 100.00% | 30 | 真实轨迹里是否没有 failed/blocked 事件。 | live_agent_eval_results.jsonl 中 checks.no_failed_event=true 的比例。 | 是 |
| 真实 LLM Agent | live output_valid | 100.00% | 30 | 输出 guard 是否放行真实 Agent 回答。 | live_agent_eval_results.jsonl 中 checks.output_valid=true 的比例。 | 是 |
| 真实 LLM Agent | live answer_keywords | 100.00% | 30 | 回答是否包含该业务问题必须出现的实体/政策/动作关键词或同义表达。 | live_agent_eval_results.jsonl 中 checks.answer_keywords=true 的比例。 | 是 |
| 真实 LLM Agent | live p50 latency | 10830.09 ms | 30 | 包含真实 LLM 网络调用、JSON fallback、工具执行和 output guard 的端到端 p50。 | 按 live_agent_eval 每条 case latency_ms 取 p50。 | 是 |
| 真实 LLM Agent | live p95 latency | 26052.12 ms | 30 | 包含真实 LLM 网络调用、JSON fallback、工具执行和 output guard 的端到端 p95。 | 按 live_agent_eval 每条 case latency_ms 取 p95。 | 是 |
| 性能/成本 | live approx p50 turn tokens | not_recorded | 30 | 旧版 live eval 结果未记录 token 估算字段；下一次 live eval 会自动写入。 | 重新运行 evaluation.live_agent_eval 后按 approx_turn_tokens 取 p50。 | 是 |
| 真实 LLM Agent | route drift first-intent match | 100.00% | 30 | 同一 live 回归集上，LLM planner 首个业务意图是否偏离 pinned expectation。 | first(actual_tasks) == first(expected_tasks)。 | 是 |
| 真实 LLM Agent | route drift task-sequence match | 100.00% | 30 | 同一 live 回归集上，多任务序列是否偏离 pinned expectation，用于检测 prompt/model 版本漂移。 | actual_tasks == expected_tasks。 | 是 |
| 外部Benchmark | tau2/tau3 retail pass^1 | 100.00% | 10 tasks | 官方 retail 客服任务中至少一次完成任务并通过 reward 的比例。 | tau2 对每个 task 的 reward>=1 计算 pass^1；当前是 DeepSeek 10-task subset smoke。 | 是 |
| 外部Benchmark | tau2/tau3 retail avg reward | 100.00% | 10 | 官方 reward 均值，综合 DB/env/NL assertion 等检查。 | 读取 tau2 result reward_info.reward 后求平均。 | 是 |
| 外部Benchmark | tau2/tau3 retail DB match | 10/10 (100.00%) | 10 | 副作用工具执行后，最终数据库状态是否与官方 gold state 匹配。 | reward_info.db_check.db_match=true 的数量 / 有 DB check 的 simulation 数。 | 是 |
| 外部Benchmark | tau2/tau3 retail read action match | 61/64 (95.31%) | 64 | 只读工具调用序列和参数是否匹配官方期望。 | reward_info.action_checks 中 tool_type=read 且 action_reward=1 的数量 / read action 数。 | 是 |
| 外部Benchmark | tau2/tau3 retail write action match | 11/11 (100.00%) | 11 | 退款、退货、换货、改订单等写工具是否按官方期望执行。 | reward_info.action_checks 中 tool_type=write 且 action_reward=1 的数量 / write action 数。 | 是 |
| 外部Benchmark | tau2/tau3 retail NL assertions | 3/3 (100.00%) | 3 | 自然语言回答是否满足官方任务断言。 | reward_info.nl_assertions 中 met=true 的数量 / NL assertion 数。 | 是 |
| 外部Benchmark | tau2/tau3 retail p95 latency | 28.29s | 10 | 官方用户模拟器 + Agent 多轮会话的端到端 p95 时长。 | 按 tau2 simulation duration 取 p95。 | 是 |
| 外部Benchmark | tau2/tau3 retail avg total cost | $0.006845 | 10 | 官方用户模拟器 + Agent + judge 的平均单会话模型成本。 | summary 中 agent_cost 与 user_cost 汇总后按 evaluated_simulations 求平均。 | 是 |
| 答案质量 | deterministic groundedness proxy | 100.00% | 2 | 无 API key 情况下，验证回答是否只引用检索到的类目/政策来源。 | 生成的 fallback/template answer 是否包含 retrieved context 中的实体或章节。 | 否 |
| 答案质量 | answer relevance proxy | 100.00% | 2 | 无模型裁判时，用关键词覆盖近似评估回答是否贴合问题。 | answer 是否包含 query 期望的业务关键词。 | 否 |
| 答案质量 | LLM judge 小样本 | 4 项均分 5.00/5，pass_rate 100% | 3 | 用裁判模型评估 answer relevance、faithfulness、tool correctness、HITL correctness。 | `python -m evaluation.llm_judge_eval` 真实运行 Agent 后把 answer/task_plan/trace/context 交给 DeepSeek judge；当前覆盖 category_risk、policy_boundary、multi_intent_hitl。 | 是 |
| 安全/风控 | 启发式输入拒绝准确率 | 100.00% | 5 | LLM guard 不可用时，明显越界/注入请求是否被拒绝。 | unsafe fixture 中 on_topic=false 的比例。 | 否 |
| 安全/风控 | 启发式输入放行准确率 | 100.00% | 5 | 正常客服问题和 HITL 短回复是否不会被误杀。 | safe fixture 中 on_topic=true 的比例。 | 否 |
| 安全/风控 | 输出坏结果拦截准确率 | 100.00% | 4 | 空输出、TODO、traceback 是否被拦截，正常回答是否放行。 | deterministic output guard 与 expected label 是否一致。 | 否 |
| 性能/成本 | order_status_lookup p95 延迟 | 0.001 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | category_risk_retrieval p95 延迟 | 0.227 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | policy_kb_retrieval p95 延迟 | 0.710 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | escalation_draft p95 延迟 | 0.002 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | route eval prompt 估算 token | 18378 | 245 cases | 评估集整体输入体量，用于估算跑 LLM eval 的成本。 | ASCII/4 + 非 ASCII*1.5 的粗略估算。 | 否 |
| 性能/成本 | policy KB 估算 token | 1951 | 1 file | 当前政策知识库规模，用于上下文预算。 | ASCII/4 + 非 ASCII*1.5 的粗略估算。 | 否 |
| 可观测性 | trace 写入与回放可用率 | 100.00% | 1 | 请求 trace 是否可按 session 查询回放。 | 写入一条 trace 后 list_session_traces 是否返回记录。 | 否 |
| 可观测性 | trace summary 可用率 | 100.00% | 1 | 是否能统计状态分布、路由分布和延迟。 | trace_summary().total 是否等于写入条数。 | 否 |