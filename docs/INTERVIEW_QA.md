# E-Commerce Agent Interview Q&A

这份文档按“面试官深挖”的方式准备，回答时核心原则是：先讲业务约束，再讲技术取舍，最后讲代码落点和评测证据。

## 项目定位

### 1. 这个项目到底给谁用？

给电商客服主管、售后运营、商家运营和一线客服坐席使用。它不是面向普通消费者的泛聊天机器人，而是一个内部客服 + 售后运营决策 Agent：能查订单事实、解释售后政策、分析类目风险、生成优先跟进队列，并在退款、取消、改地址、发票、创建工单等副作用动作前进入人工确认。

代码落点：`app/main.py` 暴露 `/chat`；`app/agent/actions.py` 执行业务计划；`app/olist/service.py` 提供订单事实、类目风险和售后运营报告。

### 2. 为什么说它是 Agent，而不是模板问答？

它具备四个 Agent 必要特征：

- 能理解自然语言并拆成任务计划：`IntentPlanner.plan()` 输出结构化 `TaskPlanResult`。
- 能选择和调用工具：订单查询、政策检索、类目风险检索、售后运营报告、售后升级工具。
- 能处理多步和副作用：只读任务先执行，遇到售后升级进入 HITL。
- 能留下可评测轨迹：`trajectory_events` 记录 plan、tool、status，SQLite trace 可回放。

但它不是完全自主 Agent，而是 workflow-constrained Agent。客服/售后场景比开放式研究更重视权限、确定性和审计。

### 3. 业务面是不是太窄？

V1 不是完整电商运营平台，但覆盖了客服和售后运营决策闭环：订单事实、政策解释、风险分析、优先队列、副作用治理和 trace。真正的电商 Agent 可以扩到选品、广告、库存、定价、营销，但如果简历目标是高级 Agent 工程，第一版更应该把一个高风险业务链路做深，而不是堆很多浅功能。

新增的 `ops_decision` 让项目从“单用户客服回复”扩展到“客服主管/售后运营看哪些类目和订单优先处理”，业务完整度更强。

## 架构选择

### 4. 为什么不用多 Agent？

当前没有用多 Agent，是因为任务复杂度还没有超过单图多 capability 的临界点。订单查询、政策问答、类目风险、售后升级之间共享同一份订单上下文和 HITL 状态，如果过早拆成多个 Agent，会引入额外通信、状态一致性、冲突仲裁和成本问题。

更合理的演进是：

- V1：单 LangGraph + 多 capability，保证链路可测、可审计。
- V2：当接入广告、库存、选品、客服质检等独立域后，拆成 CustomerSupportAgent、OpsDecisionAgent、InventoryAgent、MarketingAgent，由 supervisor 统一分发。
- V3：跨团队或跨系统时使用 A2A/Agent Card 做能力发现和任务委托。

### 5. 如果改成多 Agent，会怎么分工？

推荐 supervisor 架构：

- Supervisor：理解用户目标、拆任务、做权限和预算控制。
- Support Agent：订单查询、政策解释、客服话术。
- After-sales Ops Agent：风险日报、优先队列、SLA 分析。
- Tool Agent：封装 OMS/CRM/Refund/Coupon MCP tools，统一幂等和审计。
- Evaluator Agent：对结果做事实一致性和策略合规检查。

不采用去中心化 peer-to-peer 的原因是客服售后动作有强流程和副作用，必须有一个全局控制点做状态机和审批。

### 6. 为什么不是 ReAct？

ReAct 适合探索式问题，例如资料研究、代码定位、开放工具搜索。这个项目更适合 plan-and-execute：用户意图通常能提前拆成订单查询、政策问答、售后升级等业务步骤，且副作用动作必须排序和审批。

如果用 ReAct，模型可能在中途反复检索、重复调用工具，成本和审计难度更高。当前设计让 LLM 负责 planner、slot extraction、answer generation，确定性 executor 负责顺序、副作用、幂等和 trace。

### 7. 用户一句话里先申请退款再问政策怎么办？

执行器不会按用户原始顺序盲目执行。`_execution_order()` 会把 read-only 任务放在副作用任务前：先查订单、查政策、说明约束，再准备退款/补偿草稿并停在 HITL。这样用户即使说“先退款再告诉我政策”，系统也不会在用户理解政策前执行副作用。

### 8. 如果 LLM planner 拆错，怎么回滚？

只读任务不需要业务回滚，只需要 trace 记录错误并允许用户重问。副作用任务不会直接执行，先进入 HITL，所以 planner 拆错通常会在确认前被用户或人工发现。当前已经落地的保护包括：

- planner 输出 schema 校验和业务规则校验；
- provider 不支持 native structured output 时走 JSON-text fallback，再用 Pydantic 校验；
- 只读任务优先执行，副作用任务延后并停在 HITL；
- HITL pending action 带 `created_at/expires_at`，过期后不能再确认执行；
- risky action 二次分类；
- confirmed action 用幂等 key 返回稳定业务编号。

## RAG 与知识库

### 9. 怎么做 Agentic RAG？

项目有三条检索链路：

- 类目风险检索：query rewriting + exact/token/adaptive retrieval，解决 `health beauty`、`health-beauty`、`healthbeauty` 到 `health_beauty` 的归一。
- Policy KB：按 markdown section 切块，检索退款、补偿、取消、发票、升级边界。
- 客服对话 hybrid retrieval：ResCommons train corpus 做 BM25 + char-ngram vector + rerank，给 QA/policy 生成补充上下文。

Agentic 的部分在于 planner 决定当前任务是否需要检索、检索哪类对象、是否需要结合已完成任务上下文生成答案。不是每轮都检索，订单精确查询就直接走事实工具。

### 10. RAG 为什么不用 embedding / reranker？

准确说，不是完全不用。当前主链路已经接入了本地 hybrid retriever：BM25 召回、字符 ngram 向量分数、hybrid rerank。只是没有引入在线 embedding API，因为：

- 订单 ID 必须精确匹配，embedding 不合适；
- 类目名是短实体，规则归一化更可解释；
- policy KB 规模小，关键词/section 检索足够作为 baseline；
- 个人项目要控制成本和可复现性，不能把基础 CI 依赖外部 embedding 服务。

生产化会替换为 Elasticsearch/BM25 + vector DB + learned reranker，并评估 Recall@K、MRR、nDCG、faithfulness 和 answer relevance。

### 11. policy KB 是不是手写的？

`support_policy.md` 是项目内构造的政策知识库，不应伪装成企业真实文档。它的作用是模拟电商售后政策文档的结构和边界：退款、补偿、取消、发票、改地址、升级审批。真实上线时会从企业 SOP、FAQ、客服质检规则、商家规则中心增量同步。

面试回答要坦诚：公开订单数据有，但真实企业售后政策通常不公开，因此用合成政策文档验证 RAG 和 HITL 边界，用公开客服数据补语言多样性。

### 12. 检索失败 fallback 怎么做？

类目检索：exact miss 后走 token overlap，再走 adaptive rewrite；仍失败就返回全局高订单量/高风险类目的安全摘要，不编造具体类目。

Policy 检索：没有足够匹配章节时，回答“未命中明确政策，需要人工确认”，而不是承诺退款或补偿。

客服对话检索：hybrid TopK 低相关时只作为弱上下文，不作为事实依据。

## MCP 与工具治理

### 13. 为什么 MCP 没接进主执行路径？

当前主路径直接调用本地 deterministic service，是为了让评测和 demo 在无外部凭证时稳定可跑。MCP 已经作为 server 暴露本项目工具，也有 client adapter，可接 Stripe/数据库/CRM 等外部 MCP。

生产中应该把 `OlistService` 后面的实现替换成外部 MCP/内部 RPC：Graph 和工具 schema 不变，只替换 tool adapter。也就是说 MCP 是工具接入层，不应该侵入业务 planner 的核心语义。

### 14. 为什么别人要调你的 MCP，不直接查库？

不是“别人必须调我的 MCP”，而是 MCP server 提供了标准化工具边界。直接查库只适合内部工程服务；Agent 或外部系统需要的是带 schema、权限、审计、幂等和业务语义的工具，例如 `draft_escalation(order_id)`，而不是裸 SQL。

MCP 的价值是把数据库/RPC 封装成 Agent 可安全调用的业务工具，让调用方不需要知道表结构、连接池、权限细节和副作用规则。

### 15. MCP 和 function calling 有什么区别？

Function calling 是模型输出工具调用 JSON 的能力，发生在 LLM provider 协议内。MCP 是 Agent 与外部工具服务器之间的上下文/工具协议，通常基于 JSON-RPC，支持 stdio、Streamable HTTP 等传输。

本项目里 function calling/structured output 负责让 planner 输出结构化任务；MCP server 负责把订单查询、风险检索、售后草稿暴露给外部 Agent 客户端。两者可以组合：LLM 先决定调用哪个 tool，再由 MCP client 真正发起工具调用。

### 16. 真实退款/取消订单如何保证幂等？

不能用“订单号 + 文本哈希”当生产级幂等。真实方案应该用业务粒度 key：

- refund：`tenant_id + order_id + refund_type + amount + reason_code`
- cancel：`tenant_id + order_id + cancel_reason + requested_by`
- coupon：`tenant_id + customer_id + campaign_id + issue_reason`

工具端必须持久化 request_id、状态、结果和外部系统返回码。重复请求返回已有结果，而不是重复执行。

## HITL 与副作用

### 17. 为什么 escalation 要停在 HITL？

因为 escalation 下游可能是创建工单、提交退款/补偿、取消订单、改地址、发票申请。它们会改变业务状态、产生财务或履约影响。HITL 的作用不是自动提权，而是把模型生成的草稿和理由交给用户/坐席确认。

用户确认后，系统调用对应工具；用户取消后清理 pending state；用户发起无关新任务时，当前实现会要求先确认/取消或换 session，避免一个 session 里悬挂副作用被误触发。

### 18. HITL 超时怎么做？

当前已经落地在主 `/chat` 入口和 LangGraph checkpoint 里。副作用草稿生成时，`pending_side_effect` 会写入 `created_at`、`expires_at` 和 `timeout_seconds`，默认 `HITL_TIMEOUT_SECONDS=900`。用户在过期后再回复 yes，服务端不会执行旧工具调用，而是 resume graph 并取消旧 interrupt。

代码落点：`app/agent/actions.py` 写入超时元数据；`app/main.py` 的 `_pending_confirmation_expired()` 在恢复 interrupt 前拦截过期确认；`tests/test_main.py` 覆盖“超时后 yes 不会创建 CASE”的行为。

### 19. 用户回复“不要/算了”怎么处理？

确认词表和取消词表都要显式建模。不能只把非 yes 当 cancel，也不能把任何中文短句都拦住。生产里应该使用一个小型 confirmation classifier，输入包括 pending action 摘要和用户回复，输出 confirm/cancel/unclear/new_task，并对 unclear 返回澄清。

## 评测与指标

### 20. LLM 是怎么进入主链路的？不是只有 LLM-as-Judge 吧？

不是。LLM-as-Judge 只是事后答案质量评估。真正的主链路是：input guard -> LLM task planner -> LLM slot extractor/answer generator -> deterministic tools -> output guard。`IntentPlanner.plan()` 是有 key 时的第一步，规则 decomposer 只在 LLM 调用失败或输出非法时兜底。

现在已经新增 `evaluation/live_agent_eval.py`，用 DeepSeek `deepseek-v4-flash` 跑真实 Agent 主链路：30 条 case 覆盖订单查询、类目风险、政策边界、发票/改地址、售后运营决策、多意图 HITL，`case_pass_rate=100%`，`task_exact/tools_used/hitl_correct/output_valid/answer_keywords` 均为 `100%`。

LLM-as-Judge 仍保留 3 条 smoke，用来验证 answer relevance、faithfulness、tool correctness、HITL correctness，但它不是核心能力证明。

### 21. 之前“trajectory 100%”为什么有风险？

如果轨迹是按 expected intent 合成出来再评分，就是自证循环。现在应该跑真实 LangGraph offline graph，从 planner 到 executor 产生真实 `trajectory_events`，再评分。真实 90% 加失败分析比合成 100% 更可信。

### 22. 评价 Agent/RAG 应该看哪些指标？

规划层：intent accuracy、multi-intent exact match、contains-all、side-effect detection、task order correctness。

检索层：Recall@K、MRR、nDCG、context precision、context recall。

工具层：tool selection accuracy、参数修复率、非法参数澄清率、幂等重复命中率。

答案层：answer relevance、faithfulness、groundedness、policy compliance。

执行层：trajectory completeness、expected tool used、HITL coverage、no-failed-event、恢复成功率。

系统层：p50/p95/p99 latency、token cost、错误率、超时率、trace coverage。

安全层：prompt injection rejection、PII redaction、权限越权拦截、多租户隔离。

### 23. 现在的指标能证明真实 Agent 能力吗？

能分层证明，而不是只靠一个分数。离线指标证明数据、工具、RAG、状态机、HITL 和 trace 不会因为模型随机性而漂；真实 LLM Agent eval 证明模型已经进入 planner、抽槽、生成和 guard 主链路；LLM-as-Judge 证明小样本答案质量。当前总表有 75 项指标，其中“真实 LLM Agent”分组 5 条 live case 全过。

如果面试官追问“30 条够不够”，回答要明确：30 条已经不是单点 smoke，覆盖了五类主路径和多意图 HITL；但它仍不是线上统计意义上的大样本。可信度来自可扩展的 eval harness，下一步可以按同一格式扩到 100+，并把失败样本进入回归集。

## 安全、隐私、多租户

### 24. trace 里会不会泄露用户隐私？

会有风险。当前 trace 用于本地 demo，会记录用户消息和回答。生产必须做：

- order_id/customer_id 脱敏；
- 手机、地址、邮箱等 PII redaction；
- trace 按 tenant/session 加 ACL；
- 敏感工具参数加密或只存 hash；
- 可观测性接口鉴权；
- 审计流和业务库分级保留。

### 25. 多租户商家数据怎么隔离？

真实多租户至少三层隔离：

- 数据层：所有 order、policy、tool credential 都带 `tenant_id`，查询强制加 tenant filter。
- 上下文层：session/checkpoint/memory/retrieval namespace 按 tenant 隔离。
- 工具层：MCP tool registry 按 tenant 暴露不同工具和 scopes，退款/优惠券等高风险工具需要角色权限。

当前个人项目没有真实多租户，因为 Olist 是公开单租户数据。可以在下一版加入 tenant_id 字段和 ACL middleware 做模拟。

## 生产化追问

### 26. LLM 挂了 fallback 还有意义吗？

有意义，但只限高确定性任务。LLM 挂了时，订单查询、类目风险、政策检索、运营报告仍可用，因为它们依赖确定性工具和检索。需要开放生成能力的任务会退化成模板回答或要求人工介入。fallback 的目标不是保持“智能感”，而是保持业务可用和不出错。

### 27. 为什么最后回复没有再用 LLM rephrase？

有 LLM 时 QA/policy 会用模型生成；离线模式和副作用确认场景故意使用模板。原因是副作用确认文本必须稳定、可审计、不能被模型改写成“已经退款”这种误导表达。生产可以加一个 constrained rewriter，但必须保留动作、金额、订单号、审批状态等字段不被改写。

### 28. OlistService 为什么要单独建？

它是业务事实服务边界。V1 用 Olist public dataset 实现，生产可以替换成 OMS/CRM/数据仓库/MCP adapter。把它隔离出来，是为了让 Agent graph 不直接依赖 CSV、SQL 或外部 API 细节。

### 29. 如果接入真实 OMS/CRM，代码怎么改？

保留 `AgentActions`、state、HITL、trace、eval case 格式，把 `OlistService` 替换为接口实现，例如 `OrderServiceProtocol`、`RefundServiceProtocol`、`PolicyServiceProtocol`。工具调用可以走内部 RPC，也可以通过 MCP client。关键是 schema 和 side-effect contract 不变。

### 30. 当前项目哪里还不完美？

主要缺口：

- 没有真实企业 OMS/CRM，只能模拟副作用；
- LLM judge 样本量小；
- 多租户 ACL 还未落地；
- policy KB 不是企业真实 SOP；
- hybrid retrieval 是本地 baseline，还不是 ES + vector DB + reranker；
- 可观测性是 SQLite 和简单接口，不是 OpenTelemetry + dashboard；
- live eval 样本目前是 30 条，还需要扩成更大的模型回归集。

回答时不要否认缺口，要强调这些是个人项目和生产系统之间的边界，并说明可落地的演进路径。

### 31. 如果算法同学坚持全自主 Agent，你怎么推动工作流约束？

用数据和风险说服：先定义副作用错误成本、token 成本、失败回放成本和 SLA。对低风险只读任务可以给模型更多自主权；对退款、取消、发券、改地址必须 workflow/HITL。折中方案是“planner 自主，executor 受限”：模型决定做什么，系统决定能不能做、按什么顺序做、是否需要审批。

### 32. 面试官问“你这个项目我为什么不觉得高级”，怎么答？

高级不在于用了多少框架，而在于把 Agent 落到生产问题：多意图拆解、工具参数修复、RAG 召回评测、副作用 HITL、真实轨迹 eval、trace 回放、MCP 边界和成本控制。这个项目不是一个大模型聊天 UI，而是一个可测试、可审计、可替换工具后端的业务 Agent skeleton。

更坦诚的说法：目前它是“高级 Agent 工程样板项目”，不是完整商用 SaaS。它足够支撑秋招面试讨论架构和工程取舍；如果继续冲更强竞争力，优先补 tenant ACL、真实 ES/vector DB、dashboard 和更大规模 live LLM eval。

## 项目成熟度评分

| 维度 | 当前评分 | 证据 | 冲 9 分补强 |
|---|---:|---|---|
| 业务完整度 | 8/10 | 覆盖客服查询、政策解释、售后升级、退款/取消/改地址/发票申请、售后运营决策 | 接入真实商家规则、库存/优惠券/CRM sandbox |
| Agent 架构 | 8.5/10 | LangGraph plan-and-execute、LLM planner、结构化 fallback、HITL、HITL timeout、trace | 增加 checkpoint 持久化恢复 demo 和更完整状态回放 |
| RAG 能力 | 7.5/10 | 类目 adaptive retrieval、policy KB、ResCommons hybrid retrieval 已接主链路 | ES/BM25 + vector DB + reranker，补 nDCG/context precision |
| 工具治理 | 8/10 | 参数修复、副作用 action_type、MCP server/client adapter、幂等模拟 | 持久化幂等表、真实外部 MCP sandbox |
| 工程规范 | 8/10 | GitHub Actions CI 已配置 push/PR 自动跑 ruff、pytest 和离线 eval | 增加覆盖率报告、pre-commit、依赖安全扫描 |
| 评测体系 | 8.8/10 | 53 tests、245 真实轨迹 eval、1080 intent eval、60 multi-intent、30 条 live LLM eval、75 项总指标 | 扩大 live LLM eval 到 100+ 条，加入失败样本回归池 |
| 生产化 | 6.5/10 | SQLite trace、runtime status、guard fallback、成本估算 | 多租户 ACL、PII 脱敏、限流、OpenTelemetry/Grafana |
| 面试可讲性 | 9/10 | 数据来源、架构边界、MCP/RAG/HITL/评测都能被追问 | 做一段 3 分钟 demo script 和失败案例复盘 |
