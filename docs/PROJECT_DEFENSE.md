# Project Defense Notes

这份文档用于面试答辩：每个说法都尽量绑定代码、数据、指标和边界，避免“简历写得像做了，但追问时落不到实现”。

## Resume Bullet Version

```tex
\entryhead{电商客服/售后运营 Agent 系统}{Python · FastAPI · LangGraph · MCP · RAG · Pydantic · Docker}{2026.7 -- 至今}

- 设计并实现面向客服坐席与售后运营主管的业务 Agent，覆盖订单查询、政策问答、类目风险分析、售后优先队列与退款/取消/改地址/发票等副作用申请；使用 LangGraph 固化 plan-and-execute 流程，LLM 负责意图规划、抽槽、query rewriting 和回答生成，业务事实与副作用动作由确定性工具执行。
- 基于 Olist 公开电商数据构建 98,666 条订单事实与 73 个类目运营画像，离线加工配送延迟、低分率、取消率、支付金额、评价分等特征；结合 Bitext、ResCommons、V1rtucious 构建客服意图、多意图、检索和 tool-use 评测集，区分公开数据业务 eval 与外部 benchmark。
- 实现 workflow-constrained RAG 与客服语料 hybrid retrieval：ResCommons 35k train 语料检索 100 条 test query，BM25 intent@1/intent@5 为 64%/81%，char-ngram rerank 后提升到 76%/91%，hybrid RRF 为 77%/91%；类目检索在 240 条别名/扰动评测中 adaptive rewrite Top1 为 92.08%，其中 realistic alias Top1 为 97.50%、noisy holdout Top1 为 10.00%。
- 实现 MCP 企业工具接入边界：本地 MCP server 暴露 get_order_status、search_category_risk、generate_after_sales_priority_report、draft_escalation；client 侧支持 stdio 与 Streamable HTTP，预留 Stripe sandbox/企业 OMS/CRM/工单系统 adapter，保证工具 schema、鉴权、幂等和审计逻辑与 Agent graph 解耦。
- 建立 CI 与多层评测：pytest 55 passed，真实 LangGraph 轨迹 eval 245/245，Bitext intent mapping 1,080/1,080，多意图拆解 60/60；DeepSeek live Agent eval 30/30，覆盖真实 LLM planner/抽槽/生成/guard 主链路，记录 p50 10.83s、p95 26.05s、route drift 和失败样本入口。
```

这版故意不写“生产级闭环全完成”，也不把所有 100% 当模型能力。最值得强调的是基线提升、数据规模、MCP 代码落点、真实 LLM 主链路和延迟成本。

## Evidence Map

| Claim | Evidence | Boundary |
|---|---|---|
| LangGraph plan-and-execute | `app/agent/graph.py`, `app/agent/actions.py` | 当前是单图多 capability，不是多 Agent |
| LLM first step | `app/llm/intent_planner.py` | LLM 失败时才进入 deterministic fallback |
| HITL + resume | `interrupt()` in `AgentActions.await_confirmation`, checkpoint in `/chat` | HITL 是确认门，不是自动提权 |
| MCP support | `app/mcp_server.py`, `app/mcp_client.py`, `app/stripe_mcp.py` | 主 demo 默认用本地 service 保证无凭证可跑 |
| Persistent idempotency | `SQLiteCaseService` in `app/olist/service.py` | 个人项目模拟企业工具，不产生真实退款 |
| Hybrid retrieval formula | `app/retrieval/hybrid.py` | 本地 char-ngram vector baseline，不是线上 embedding/reranker |
| Real LLM eval | `evaluation/live_agent_eval.py`, `evaluation/live_agent_eval_results.jsonl` | 30 条回归集，不是 leaderboard benchmark |
| Route drift eval | `evaluation/route_drift_eval.py` | 比较 pinned eval expectation，不等于线上流量漂移 |
| tau2 adapter | `benchmark_adapters/tau2_retail_agent.py`, `scripts/run_tau2_retail_subset.py` | tau2 需独立 Python 3.12+ 环境和 API key |

## Seven Hard Questions

### 1. 为什么 LangGraph，而不是裸 function calling 或自研？

裸 function calling 只能让模型产出 tool-call JSON，不能天然解决任务状态、恢复、中断、条件边和审计。这个项目需要多意图任务、只读任务优先、副作用 HITL、超时取消、确认后恢复执行，因此选择 LangGraph。

代码落点：

- `StateGraph(AgentState)` 定义状态图。
- `add_conditional_edges()` 区分是否进入 `await_confirmation`。
- `interrupt()` 暂停副作用任务。
- checkpointer 让 session 可以恢复。

自研也能做，但面试里要讲清楚：我没有把精力浪费在重新造状态机，而是把业务风险、工具治理和评测体系做深。

### 2. 幂等 key 具体字段是什么？超时后重试怎么办？

当前 key 是：

```text
olist-demo:{action_type}:{order_id}:{reason_code}
```

`reason_code` 由业务原因归一得到，例如 `delivery_delay`、`low_review`、`canceled`、`generic`，不再使用完整 message hash，所以用户把话术改几个字不会绕过幂等。

执行结果持久化到 SQLite case table，并以 idempotency key 做唯一约束。重复确认同一业务动作返回已有 `result_id` 和 `duplicate=True`。HITL 超时前没有执行工具，因此不会产生业务 case；用户重新发起并确认后，如果业务语义相同，会命中同一个 key 或创建唯一记录。

这个 bug 是通过“同一订单同一动作改写文本后重复执行”的测试暴露的：旧 key 绑定全文 hash，改一个字就变成新 case；新实现改成业务粒度 key。

### 3. trace 审计回放怎么处理 LLM 非确定性？

当前 trace 回放是状态轨迹回放，不是完全 deterministic replay。它记录 session、user_message、route_intent、final_answer、sources、trajectory_events、latency 和 status，可以复盘“模型规划了什么、工具调用了什么、在哪里进入 HITL”。

如果要做到严格重放，需要再存：

- prompt_version、model、temperature、base_url；
- 每次 LLM request/response snapshot 或 response stub；
- 每次 tool input/output；
- checkpoint state hash；
- 外部工具返回码和幂等结果。

本项目已经在 live eval 结果里记录 `model`、`prompt_version`、`evaluated_at`、`latency_ms`，下一步可以把同样字段同步写入 trace。

### 4. hybrid 融合公式是什么，权重怎么定？为什么不用 cross-encoder rerank？

代码在 `app/retrieval/hybrid.py`：

1. 用 BM25 在 train corpus 上召回最多 300 个候选。
2. 对候选计算 char 4-gram overlap cosine-like score。
3. 取 BM25 Top40 和 vector Top40 做 RRF 风格融合：

```text
score(doc) = 0.2 / (bm25_rank + 20) + 2.0 / (vector_rank + 20)
```

权重来自当前 ResCommons 回归集的经验：客服 query 很多是短句、拼写和表达变体，char-ngram 对同 intent 的近似表达更敏感，所以 vector rank 权重大于 BM25。

没有上 cross-encoder 的原因是个人项目需要低成本、可复现、CI 可跑；cross-encoder 会引入模型下载、显存/CPU 开销和更慢延迟。生产版会把这条 baseline 升级为 BM25/vector 召回 + cross-encoder rerank，并用 Recall@K、MRR、nDCG、latency/cost 做取舍。

### 5. 路由漂移怎么定义、怎么检出？

路由漂移不是“模型偶尔答错”这么笼统，而是同一评测集、同一任务定义下，换 prompt/model/version 后 planner 输出分布或任务序列偏离基线。

本项目用两层检测：

- first-intent match：首个业务意图是否偏离。
- task-sequence match：多任务序列是否完全一致。

脚本：`python -m evaluation.route_drift_eval`。它读取 `evaluation/live_agent_eval_results.jsonl`，输出 `evaluation/route_drift_report.md`，用于比较 expected/actual intent 分布和 mismatch cases。

线上可以进一步按天统计 route distribution、side-effect rate、fallback rate，用 PSI/KL divergence 或简单阈值报警。

### 6. 评测集怎么构造？多少人工标注？

这个项目要分清“公开数据派生标签”和“人工/规则构造标签”：

- Olist：公开订单事实，expected status/category/order_id 从 CSV join 后确定性生成，不需要人工标注。
- Bitext：公开客服 utterance 自带 intent，人工写了 intent-to-route 映射表，用 1,080 条样本验证映射覆盖。
- ResCommons：公开客服对话语料，train 做 corpus，test query 用原始 intent/capability metadata 做检索评测。
- V1rtucious：公开 2,000 条 Agent test set，用于 profile tool/RAG/escalation 覆盖，不吹成经典 benchmark。
- Multi-intent/live eval：人工设计回归集，目标是覆盖关键业务路径和历史 bug，不是统计意义 benchmark。
- Policy KB：政策文档是项目内模拟 SOP，评测问题人工写，用来验证 RAG 边界。

回答重点：我不会把“自己构造的 30 条 live eval”包装成行业 benchmark；它是回归测试。横向可比需要 tau2 retail。

### 7. 成本与延迟怎么讲？

最新 live eval 记录 p50/p95 latency：p50 约 10.83s，p95 约 26.05s。这个数字包含真实 LLM guard、planner、抽槽/生成、工具执行和 output guard。它暴露的问题也很明确：当前便宜模型和多次 LLM 调用导致端到端延迟偏高。

优化方向：

- guard 和 slot extraction 用轻量模型或规则前置；
- planner 与安全分类合并成一次结构化输出；
- policy/QA 的生成阶段按置信度跳过；
- support_docs 检索并行化；
- 缓存稳定 policy answer/category summary；
- 高风险任务保留 HITL，低风险只读任务走 fast path。

成本口径上，本项目不在代码里写死价格，因为模型定价会变；`live_agent_eval.py` 已记录 `input_chars`、`answer_chars`、`approx_turn_tokens`，真实接入时应优先读取 provider usage tokens，并按当日价格计算。

## Benchmark Plan

Olist/Bitext/ResCommons 是业务 eval，不是 leaderboard benchmark。外部横向可比优先级：

| Benchmark | 价值 | 当前状态 | 下一步 |
|---|---|---|---|
| tau2/tau3 retail | 最贴客服/售后、tool-use、policy compliance、副作用动作 | adapter 和 runner 已实现，dry-run 通过；Windows uv 在依赖 copy/sync 阶段长时间不退出，尚无正式分数 | 独立 Python 3.12 环境跑 5-10 条 subset，落盘 pass@1/tool error/latency |
| tau2 banking_knowledge | 补非结构化政策 RAG 与多轮问答 | 未实现 adapter | retail 跑通后复用 HalfDuplexAgent prompt，加 knowledge retrieval 策略 |
| BFCL subset | 横向验证 function/tool calling schema | 未实现 | 适合作为单独小项目，不混入电商业务简历 |

如果 tau2 在 Windows/uv 上继续卡住，建议使用 WSL2 或 Linux runner。原因不是 Agent 代码问题，而是 tau2 当前 Python 3.12+ 依赖环境与本项目 Python 3.11 服务环境不同。
