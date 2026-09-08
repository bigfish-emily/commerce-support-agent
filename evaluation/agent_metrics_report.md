# Agent Evaluation Metrics Report

本报告由 `python -m evaluation.agent_metrics_report` 生成。默认不需要 API key；LLM judge 属于可选联网评测，不能和本地确定性指标混为一谈。
Live LLM 与 LLM-as-Judge 行读取已落盘结果；修改 LangGraph 节点、prompt 或模型后应重新运行对应 live eval。

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
| 工具/参数 | 副作用任务门禁覆盖率 | 100.00% | 120 | 售后/退款/取消等副作用任务是否全部被标记为受控写动作。 | expected_intent=escalation 的 case 是否进入售后门禁，后续由决策器选择 HITL、自动执行、拒绝或澄清。 | 否 |
| 工具/参数 | 副作用 action_type 分发覆盖率 | 100.00% | 6 | 工单、退款、取消、改地址、发票、投诉升级六类动作是否都有工具落点。 | 六类 action_type 是否都有可执行的幂等工具模拟。 | 否 |
| 工具/参数 | ToolCallManager 治理项覆盖率 | 100.00% | 8 | 验证 schema、角色权限、只读缓存、租户隔离、Redis backend、timeout fallback、副作用幂等和审计脱敏是否可用。 | 运行一个无 LLM mini harness，逐项检查 ToolCallManager 的治理能力。 | 否 |
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
| RAG/检索 | 政策 KB Top1 | 100.00% | 19 | 政策问题首位是否命中正确章节。 | ranked[0] == expected_section。 | 否 |
| RAG/检索 | 政策 KB Recall@3 | 100.00% | 19 | Top3 是否包含正确政策章节。 | expected_section in ranked[:3]。 | 否 |
| RAG/检索 | 政策 KB MRR@3 | 100.00% | 19 | 正确政策章节越靠前分数越高。 | 命中时累加 1/rank。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 intent@1 | 64.00% | 100 | 首位召回文档的客服 intent 是否与 query intent 一致。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 intent@5 | 81.00% | 100 | Top5 是否出现同 intent 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 intent_mrr@5 | 70.73% | 100 | 同 intent 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 capability@1 | 64.00% | 100 | 首位召回文档的能力标签是否匹配。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 capability@5 | 81.00% | 100 | Top5 是否出现同 capability 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid bm25 capability_mrr@5 | 70.73% | 100 | 同 capability 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid local_vector_store intent@1 | 77.00% | 100 | 首位召回文档的客服 intent 是否与 query intent 一致。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid local_vector_store intent@5 | 91.00% | 100 | Top5 是否出现同 intent 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid local_vector_store intent_mrr@5 | 83.12% | 100 | 同 intent 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid local_vector_store capability@1 | 76.00% | 100 | 首位召回文档的能力标签是否匹配。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid local_vector_store capability@5 | 90.00% | 100 | Top5 是否出现同 capability 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid local_vector_store capability_mrr@5 | 82.12% | 100 | 同 capability 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank intent@1 | 78.00% | 100 | 首位召回文档的客服 intent 是否与 query intent 一致。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank intent@5 | 91.00% | 100 | Top5 是否出现同 intent 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank intent_mrr@5 | 83.57% | 100 | 同 intent 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank capability@1 | 77.00% | 100 | 首位召回文档的能力标签是否匹配。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank capability@5 | 90.00% | 100 | Top5 是否出现同 capability 文档。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| RAG/检索 | 客服对话 hybrid hybrid_rerank capability_mrr@5 | 82.57% | 100 | 同 capability 文档越靠前分数越高。 | ResCommons test query 检索 train corpus，比较召回文档 metadata。 | 否 |
| 审核台辅助 | 高风险类目覆盖率 | 100.00% | Top5 | 审核台优先处理队列是否能输出可跟进的高风险类目列表。 | after_sales_priority_report 返回 high_risk_categories 的数量 / 5。 | 否 |
| 审核台辅助 | 高风险类目排序正确率 | 100.00% | 5 | 类目是否按风险分从高到低排序，便于审核台优先处理。 | risk_score 序列是否单调递减。 | 否 |
| 审核台辅助 | 类目行动建议覆盖率 | 100.00% | 5 | 每个高风险类目是否都有可执行的审核建议。 | recommended_action 非空的比例。 | 否 |
| 审核台辅助 | 优先跟进订单覆盖率 | 100.00% | Top8 | 是否能从订单事实中挑出售后优先跟进队列。 | after_sales_priority_report 返回 priority_orders 的数量 / 8。 | 否 |
| 审核台辅助 | 优先跟进订单排序正确率 | 100.00% | 8 | 订单队列是否按售后优先级从高到低排序。 | priority_score 序列是否单调递减。 | 否 |
| 审核台辅助 | 订单行动建议覆盖率 | 100.00% | 8 | 每个优先订单是否给出原因和建议动作。 | recommended_action 非空且 reasons 非空的比例。 | 否 |
| 审核台辅助 | 审核建议只读/HITL 边界命中率 | 100.00% | 1 | 审核台建议是否明确把排序建议和退款/取消等副作用执行分开。 | decision_rules 中是否声明副作用仍需 HITL。 | 否 |
| 端到端/轨迹 | 轨迹包含 plan 节点 | 100.00% | 245 | 每次任务是否先产生可审计 task plan。 | trajectory_events 中是否包含 node=plan_tasks。 | 否 |
| 端到端/轨迹 | 轨迹 intent 覆盖率 | 100.00% | 245 | 执行轨迹是否覆盖 gold route intent。 | expected_intent 是否出现在 trajectory event intent 列表。 | 否 |
| 端到端/轨迹 | 轨迹工具正确率 | 100.00% | 245 | 轨迹中是否调用了 intent 对应工具。 | event.details.tool == expected_tool(expected_intent)。 | 否 |
| 端到端/轨迹 | 副作用受控终态覆盖率 | 100.00% | 245 | 副作用任务是否进入 HITL、低风险执行、拒绝或澄清等受控终态。 | escalation case 是否出现 awaiting_confirmation/completed/reject/ask_clarification 等合法状态。 | 否 |
| 端到端/轨迹 | 轨迹无失败率 | 100.00% | 245 | 离线 gold 轨迹是否没有 failed/blocked 事件。 | trajectory statuses 中不含 failed/blocked。 | 否 |
| 产品主链路 | customer flow pass rate | 100.00% | 9 | 消费者自助售后、人工审核台、越权拦截和审批执行是否按产品边界完成。 | 每条 customer flow case 的 HTTP、answer、task_plan、tool_calls、HITL/auth checks 全部为 true 才算 pass。 | 否 |
| 产品主链路 | customer flow answer_terms | 100.00% | 9 | 回答是否包含该产品场景必须出现的业务关键词。 | customer_flow_eval_results.jsonl 中 checks.answer_terms=true 的比例。 | 否 |
| 产品主链路 | customer flow customer_cannot_confirm | 100.00% | 5 | 消费者是否无法通过 yes/no 自己批准高风险写动作。 | customer_flow_eval_results.jsonl 中 checks.customer_cannot_confirm=true 的比例。 | 否 |
| 产品主链路 | customer flow handoff_reason_present | 100.00% | 5 | 进入审核台的 case 是否携带明确的人机切换原因。 | customer_flow_eval_results.jsonl 中 checks.handoff_reason_present=true 的比例。 | 否 |
| 产品主链路 | customer flow http_200 | 100.00% | 9 | 消费者入口或审核台接口是否返回成功响应。 | customer_flow_eval_results.jsonl 中 checks.http_200=true 的比例。 | 否 |
| 产品主链路 | customer flow pending_review | 100.00% | 5 | 高风险售后动作是否暂停并进入审核台。 | customer_flow_eval_results.jsonl 中 checks.pending_review=true 的比例。 | 否 |
| 产品主链路 | customer flow review_approve_executes | 100.00% | 1 | 售后审核员 approve 后是否恢复 checkpoint 并执行幂等写工具。 | customer_flow_eval_results.jsonl 中 checks.review_approve_executes=true 的比例。 | 否 |
| 产品主链路 | customer flow review_requires_token | 100.00% | 5 | 审核台读取 case 是否必须携带 review token。 | customer_flow_eval_results.jsonl 中 checks.review_requires_token=true 的比例。 | 否 |
| 产品主链路 | customer flow sources | 100.00% | 9 | 政策问答是否返回可追溯的 policy/FAQ 来源。 | customer_flow_eval_results.jsonl 中 checks.sources=true 的比例。 | 否 |
| 产品主链路 | customer flow task_plan | 100.00% | 7 | trace replay 中的真实 task_plan 是否覆盖预期任务顺序。 | customer_flow_eval_results.jsonl 中 checks.task_plan=true 的比例。 | 否 |
| 产品主链路 | customer flow tool_calls | 100.00% | 7 | trace replay 中是否出现预期确定性工具调用。 | customer_flow_eval_results.jsonl 中 checks.tool_calls=true 的比例。 | 否 |
| 产品主链路 | customer flow unauthorized_blocked | 100.00% | 1 | 消费者查询非本人订单是否在进入 Agent 图前被拦截。 | customer_flow_eval_results.jsonl 中 checks.unauthorized_blocked=true 的比例。 | 否 |
| 产品主链路 | customer flow p95 latency | 54.97 ms | 9 | 消费者入口首轮响应的 p95，本地离线模式不包含真实模型网络时间。 | 按 customer_flow_eval 每条 case latency_ms 取 p95。 | 否 |
| 产品主链路 | auto resolution rate | 44.44% | 9 | 消费者请求无需人工审核即可完成答复、越权拦截或越界拒绝的比例。 | customer_flow_eval 中 resolution_type in auto_answer/auth_block/guard_reject 的比例。 | 否 |
| 产品主链路 | handoff rate | 55.56% | 9 | 需要进入售后审核台的请求比例。 | customer_flow_eval 中 resolution_type=handoff_review 的比例。 | 否 |
| 产品主链路 | handoff precision | 100.00% | 5 | 进入审核台的请求是否都是评测集中预期需要人工处理的高风险请求。 | actual handoff 且 handoff_expected=true 的数量 / actual handoff 数量。 | 否 |
| 产品主链路 | expected handoff recall | 100.00% | 5 | 评测集中应转人工的请求是否全部进入审核台。 | handoff_expected=true 且 resolution_type=handoff_review 的数量 / expected handoff 数量。 | 否 |
| 产品主链路 | policy grounding rate | 100.00% | 9 | 需要政策或售后判断的回答是否带有可追溯政策依据。 | customer_flow_eval 中 policy_grounded=true 的比例。 | 否 |
| 产品主链路 | avg customer turns | 1.56 | 9 | 从用户发起到自动答复或进入审核台的平均用户轮次。 | customer_flow_eval 每条 case 的 customer_turns 平均值。 | 否 |
| 产品主链路 | handoff reason coverage | 100.00% | 5 | 进入审核台的 case 是否带有明确的人机切换原因。 | actual handoff 中 handoff_reasons 非空的比例。 | 否 |
| 产品主链路 | resolution type counts | {'auto_answer': 2, 'handoff_review': 5, 'auth_block': 1, 'guard_reject': 1} | 9 | 产品链路输出类型分布，用于观察自动答复、转人工、越权拦截和越界拒绝。 | 按 customer_flow_eval 的 resolution_type 聚合计数。 | 否 |
| 真实 LLM Agent | live eval status | not_run | 0 | 真实 LLM 进入 planner/抽槽/生成/guard 主链路后的端到端评估状态。 | `OPENAI_API_KEY=...` 或 `AIHUBMIX_API_KEY=...` 后运行 `python -m evaluation.live_agent_eval` 会生成结果。 | 是 |
| 外部Benchmark | tau2-bench retail pass^1 | 91.23% | 114 tasks | 官方 retail 客服任务中至少一次完成任务并通过 reward 的比例。 | tau2-bench 对每个 task 的 reward>=1 计算 pass^1；当前是 DeepSeek retail base split 114-task local run。 | 是 |
| 外部Benchmark | tau2-bench retail avg reward | 91.23% | 114 | 官方 reward 均值，综合 DB/env/NL assertion 等检查。 | 读取 tau2 result reward_info.reward 后求平均。 | 是 |
| 外部Benchmark | tau2-bench retail DB match | 105/114 (92.11%) | 114 | 副作用工具执行后，最终数据库状态是否与官方 gold state 匹配。 | reward_info.db_check.db_match=true 的数量 / 有 DB check 的 simulation 数。 | 是 |
| 外部Benchmark | tau2-bench retail read action match | 346/357 (96.92%) | 357 | 只读工具调用序列和参数是否匹配官方期望。 | reward_info.action_checks 中 tool_type=read 且 action_reward=1 的数量 / read action 数。 | 是 |
| 外部Benchmark | tau2-bench retail write action match | 162/176 (92.05%) | 176 | 退款、退货、换货、改订单等写工具是否按官方期望执行。 | reward_info.action_checks 中 tool_type=write 且 action_reward=1 的数量 / write action 数。 | 是 |
| 外部Benchmark | tau2-bench retail NL assertions | 58/61 (95.08%) | 61 | 自然语言回答是否满足官方任务断言。 | reward_info.nl_assertions 中 met=true 的数量 / NL assertion 数。 | 是 |
| 外部Benchmark | tau2-bench retail p95 latency | 32.77s | 114 | 官方用户模拟器 + Agent 多轮会话的端到端 p95 时长。 | 按 tau2 simulation duration 取 p95。 | 是 |
| 外部Benchmark | tau2-bench retail avg total cost | $0.006036 | 114 | 官方用户模拟器 + Agent + judge 的平均单会话模型成本。 | summary 中 agent_cost 与 user_cost 汇总后按 evaluated_simulations 求平均。 | 是 |
| 答案质量 | deterministic groundedness proxy | 100.00% | 2 | 无 API key 情况下，验证回答是否只引用检索到的类目/政策来源。 | 生成的 fallback/template answer 是否包含 retrieved context 中的实体或章节。 | 否 |
| 答案质量 | answer relevance proxy | 100.00% | 2 | 无模型裁判时，用关键词覆盖近似评估回答是否贴合问题。 | answer 是否包含 query 期望的业务关键词。 | 否 |
| 答案质量 | LLM judge status | not_run | 0 | 真实模型裁判评估状态；需要 API key 才能运行。 | `OPENAI_API_KEY=...` 或 `AIHUBMIX_API_KEY=...` 后运行 `python -m evaluation.llm_judge_eval` 会生成 llm_judge_eval_results.jsonl。 | 是 |
| 安全/风控 | 启发式输入拒绝准确率 | 100.00% | 5 | LLM guard 不可用时，明显越界/注入请求是否被拒绝。 | unsafe fixture 中 on_topic=false 的比例。 | 否 |
| 安全/风控 | 启发式输入放行准确率 | 100.00% | 5 | 正常客服问题和 HITL 短回复是否不会被误杀。 | safe fixture 中 on_topic=true 的比例。 | 否 |
| 安全/风控 | 输出坏结果拦截准确率 | 100.00% | 4 | 空输出、TODO、traceback 是否被拦截，正常回答是否放行。 | deterministic output guard 与 expected label 是否一致。 | 否 |
| 性能/成本 | order_status_lookup p95 延迟 | 0.001 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | category_risk_retrieval p95 延迟 | 0.190 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | policy_kb_retrieval p95 延迟 | 0.996 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | escalation_draft p95 延迟 | 0.002 ms | 200 | 不含 LLM 网络时间的确定性工具层 p95 延迟。 | warmup 20 次后运行 200 次，取 p95。 | 否 |
| 性能/成本 | route eval prompt 估算 token | 18378 | 245 cases | 评估集整体输入体量，用于估算跑 LLM eval 的成本。 | ASCII/4 + 非 ASCII*1.5 的粗略估算。 | 否 |
| 性能/成本 | policy KB 估算 token | 2692 | 3 files | 当前 policy/FAQ/merchant rules 知识库规模，用于上下文预算。 | ASCII/4 + 非 ASCII*1.5 的粗略估算。 | 否 |
| 可观测性 | trace 写入与回放可用率 | 100.00% | 1 | 请求 trace 是否可按 session 查询回放。 | 写入一条 trace 后 list_session_traces 是否返回记录。 | 否 |
| 可观测性 | trace summary 可用率 | 100.00% | 2 | 是否能统计状态分布、路由分布和延迟。 | trace_summary().total 是否等于写入条数。 | 否 |
| 可观测性 | case metrics 聚合可用率 | 100.00% | 1 | 是否能从 trace 中按售后 case 聚合业务指标。 | 写入 after_sales_cases 后 case_metrics().total_cases 是否为 1。 | 否 |
| 可观测性 | wrong_write_blocked | 1 | 1 | 不符合政策或订单状态的写动作被 Verifier/决策层拦截的次数。 | 统计 outcome=reject/ask_clarification 的写动作 case 数。 | 否 |
| 可观测性 | policy_hit_rate | 100.00% | 1 | 售后 case 是否带有可追溯政策/FAQ/商家规则依据。 | policy_refs 非空的售后 case / total_cases。 | 否 |