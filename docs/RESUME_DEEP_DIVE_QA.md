# Resume Deep-Dive Interview Q&A

这份文档专门对应简历里的“电商客服/售后运营 Agent 系统”项目，用大厂高级 Agent 研发面试官的视角拆解追问。回答时不要只背结论，顺序尽量是：

1. 先讲业务约束；
2. 再讲技术选择；
3. 再讲代码落点；
4. 最后讲评测证据和边界。

## 0. 30 秒项目开场

**问题：你先用一句话介绍这个项目。**

回答：

这是一个面向电商客服坐席和售后运营的 workflow-constrained Agent。它不是泛聊天机器人，而是把订单查询、政策问答、类目风险分析、售后优先队列，以及退款、取消、改地址等高风险动作纳入一个可审计、可评测、可恢复的业务执行链路。LLM 负责意图规划、抽槽、query rewriting 和回答生成；订单事实、副作用动作、权限、幂等和审计由确定性系统控制。

**继续追问：你说它是 Agent，不是工作流，边界在哪里？**

回答：

它不是纯固定工作流，因为用户输入是自然语言，可能多意图、缺槽、乱序、夹杂政策问题和副作用请求。系统需要先用 LLM planner 生成任务计划，再根据任务类型选择工具、检索知识、组织回答，并在副作用前中断等待确认。但它也不是全自主 Agent，因为客服售后场景有强业务边界，不能让模型自由探索或自由执行退款。因此我把它定义为 workflow-constrained Agent：模型处理不确定语言，工作流约束执行边界。

代码落点：

- `app/main.py`: `/chat` 入口、HITL resume、trace 记录；
- `app/agent/graph.py`: LangGraph 状态图；
- `app/agent/actions.py`: 任务执行、RAG、HITL、副作用执行；
- `app/llm/intent_planner.py`: LLM 任务规划；
- `app/tool_call/framework.py`: ToolCallManager。

## 1. 对应简历第 1 条：业务场景与高风险动作

简历原句：

> 构建面向客服坐席与售后运营的业务 Agent，将订单查询、政策问答、类目风险分析、售后优先队列，以及退款/取消/改地址等高风险动作纳入可审计流程。

### 1.1 这个系统到底给谁用？

回答：

目标用户有两类。第一类是一线客服坐席，用来快速查订单、解释政策、生成售后话术，并在退款、取消、改地址、发票等动作前拿到可审核草稿。第二类是客服主管或售后运营，用来看类目风险、延迟率、低分率、取消率和优先跟进订单，辅助决定先处理哪些类目和订单。

这个定位不是为了逃避“面向消费者”的难度，而是因为我想展示的是企业内部 Agent 的工程能力：权限、审计、幂等、HITL、工具治理、评测和成本控制。消费者侧更强调体验、推荐、转化和售前导购；客服/运营侧更强调事实正确、动作可控、错误可追责。高级 Agent 工程面试更容易围绕后者深挖系统设计。

### 1.1.1 为什么不直接做面向消费者的电商 Agent？

回答：

面向消费者当然可以做，而且长期看消费者会直接和 Agent 交互。但我这个项目选择“客服坐席/售后运营”作为第一版，是产品和工程上的主动取舍，不是因为消费者 Agent 做不了。

原因有四个：

1. **权限边界不同**：消费者 Agent 能展示订单和政策，但不能随便直接退款、取消订单、改地址；这些动作最终仍要落到企业 OMS、CRM、支付、物流系统。内部客服 Agent 更适合展示权限校验、HITL、幂等和审计这些企业级能力。
2. **错误成本不同**：消费者侧一句错误承诺会直接变成客诉，例如“我保证给你退款”。所以真正上线时，消费者 Agent 往往只能做低风险动作，复杂售后仍要转人工或内部系统确认。我的项目把高风险动作放在内部客服工作台里，更符合生产落地路径。
3. **数据可见性不同**：消费者只能看到自己的订单；客服/运营需要跨订单、跨类目、跨风险指标做分析，例如售后优先队列、低分率和延迟率。这部分更能体现 Agent + tools + RAG + analytics 的组合能力。
4. **评测更明确**：客服售后可以用 tool-use、DB state、policy compliance、NL assertion、HITL correctness 来评测；消费者导购更依赖转化率、满意度和在线 A/B，个人项目很难拿到真实闭环数据。

面试时可以补一句：

> 如果要做消费者版，我会把当前系统作为 back-office agent/tool gateway，前面再加一个 customer-facing agent。消费者 Agent 负责售前导购、订单自助查询、政策解释和低风险申请入口；真正的退款、取消、改地址仍然调用内部工具，并受权限、HITL、幂等和审计约束。也就是说不是二选一，而是前台体验层和后台执行层的分工。

不要说：

- “给消费者用的智能客服。”这会导致面试官问为什么能直接操作订单、为什么暴露 trace。
- “全链路电商运营 Agent。”目前没有选品、广告、库存补货、价格调整，不能吹过界。

### 1.2 为什么把退款、取消、改地址叫高风险动作？

回答：

因为它们会改变业务状态或触发外部副作用。退款会影响资金，取消订单会影响履约和库存，改地址会影响物流，发票会影响财务合规。LLM 可以生成建议，但不能直接承诺或执行。系统必须在副作用前做权限校验、HITL 确认、幂等控制和审计记录。

追问：只弹一个确认框够吗？

回答：

不够。HITL 只是执行前的确认门，不能替代工具层治理。本项目有三层：

- Planner 层识别副作用意图；
- Graph 层进入 LangGraph interrupt，等待用户确认；
- Tool 层用 schema、权限、Redis 副作用锁、SQLite 幂等记录和审计日志兜底。

### 1.3 类目风险分析和售后优先队列有什么业务价值？

回答：

类目风险画像把全量订单聚合成运营指标，例如延迟率、低分率、取消率、平均延迟天数、平均支付金额和样例订单。售后优先队列则把这些指标落到具体订单上，优先挑出延迟、低评分、取消风险或高金额订单，帮助主管决定先处理哪里，而不是只对单个用户被动回复。

追问：这是不是普通 BI，不是 Agent？

回答：

单独看类目风险确实像 BI，但 Agent 的价值在于把它和自然语言任务、政策 RAG、订单事实工具、售后动作草稿和 HITL 串起来。用户可以问“health beauty 类目有什么售后风险，给我优先处理建议”，系统会检索类目画像、生成解释，并在需要对具体订单处理时进入副作用流程。

## 2. 对应简历第 2 条：LangGraph、Plan-and-Execute 与执行链路

简历原句：

> 基于 LangGraph 构建 plan-and-execute 流程，并将 workflow-constrained RAG、确定性工具执行、副作用隔离、HITL 审批、审计日志和 τ-bench 评测纳入同一执行链路。

### 2.1 为什么用 LangGraph，而不是裸 function calling？

回答：

裸 function calling 只能解决“模型生成 tool call JSON”，不能天然解决多步状态、条件分支、中断恢复、HITL 和 checkpoint。这个项目需要处理多意图任务，例如“查订单状态、解释退款政策、生成售后升级话术”，只读任务要先执行，副作用任务要暂停等待确认，用户确认后还要从同一个 session 恢复执行。LangGraph 的状态图、条件边、interrupt 和 checkpoint 正好覆盖这些问题。

追问：LangGraph 在你项目里到底用了哪些能力？

回答：

- StateGraph 定义 `AgentState`；
- 节点包括 input planning、task execution、await confirmation、finalize side effect；
- 条件边用于判断是否进入 HITL；
- `interrupt()` 用于副作用确认前暂停；
- AsyncSqliteSaver 用于 session checkpoint；
- `/chat` 里通过 `Command(resume=...)` 恢复中断执行。

### 2.2 为什么是 plan-and-execute，不是 ReAct？

回答：

客服售后任务通常有明确业务阶段：识别意图、查事实、查政策、生成草稿、确认副作用、执行工具。ReAct 适合开放探索，但这里反复 thought-action-observation 会增加成本、重复调用工具、难以审计，并可能在副作用场景里越界。plan-and-execute 更适合把任务先结构化，再让确定性 executor 按风险排序执行。

追问：那什么时候会用 ReAct？

回答：

如果任务是开放研究、未知工具搜索、复杂排障或多轮资料定位，我会考虑 ReAct。但退款、取消、改地址这种强约束业务，我会把 ReAct 限制在只读分析阶段，副作用动作仍然由工作流和工具层控制。

### 2.3 “同一执行链路”是什么意思？τ-bench 也在主链路里吗？

回答：

这句话容易引起误会，最好不要在简历里写“把 τ-bench 纳入同一执行链路”。更准确的说法是：**业务执行链路和 benchmark 评测链路是两个入口，但验证的是同一类 Agent harness 能力。**

主业务链路是 Olist Agent 的 `/chat`：

```text
用户请求 -> input guard -> LLM planner -> task executor
       -> facts/RAG/tools -> HITL interrupt -> side-effect tool
       -> output guard -> SQLite trace
```

这条链路服务真实业务 demo，使用 Olist 订单事实、policy KB、ResCommons 检索语料、ToolCallManager、Redis runtime 和 trace。

τ-bench retail 是外部评测链路：

```text
tau2 user simulator -> tau2 retail policy/tools -> 我实现的 tau2 adapter
                    -> LLM tool-use policy -> tau2 scorer
```

这条链路不经过 Olist `/chat`，也不使用 Olist 数据。它的价值是用外部 benchmark 自带的 policy、tools、user simulator 和 reward function，评估我的客服域 harness 是否能读工具、写工具、遵守政策、完成多轮任务和产生正确最终状态。

所以面试时不要说“τ-bench 在生产链路里”。应该说：

> 我把生产链路和评测链路分开。生产链路跑 Olist 客服/售后 Agent；评测链路通过 τ-bench adapter 接入外部 retail benchmark。两者不共享数据和工具实现，但共享同一套工程原则：plan before write、read before mutate、schema-constrained tool call、confirmation boundary、side-effect guard 和 traceable result。这样既不污染业务代码，又能获得外部可比指标。

这样讲体现的是工程判断：benchmark 不应该进入线上请求路径，但 benchmark 暴露出的失败要进入 bad-case regression。

如果要更稳，简历可改成：

> 基于 LangGraph 构建 plan-and-execute 主链路，串联 workflow-constrained RAG、确定性工具执行、副作用隔离、HITL 审批和审计日志；另接入 τ-bench retail adapter，用外部客服域 benchmark 验证 tool-use 和 policy compliance。

### 2.4 用户一句话多个意图怎么保证不漏不重？

回答：

主路径用 LLM planner 输出结构化任务计划，每个 task 有 intent、slots、risk、requires_confirmation 等字段。执行器不会按原句顺序盲目执行，而是做业务排序：只读任务优先，例如订单查询、政策检索、类目分析；副作用任务靠后，并进入 HITL。重复任务通过 order_id、action_type、reason_code 等业务字段归一，避免同一订单同一动作重复提交。

追问：如果用户说“先退款，再告诉我政策”呢？

回答：

系统不会按用户原始顺序先退款。退款属于高风险副作用，必须先查订单和政策，把限制告诉用户，再生成草稿并等待确认。这个设计牺牲一点“听话”，换取合规和可审计。

### 2.5 planner 拆错了怎么办？

回答：

首先 planner 输出受 Pydantic schema 约束，非法结构会 fallback。其次只读任务即使拆错也不会产生业务副作用，只会写 trace 并允许用户纠正。第三，副作用任务必须 HITL，用户或人工可以在确认前拦截。最后工具层还有 schema 校验、权限和幂等，planner 不是唯一安全边界。

高级追问：能不能自动回滚？

回答：

只读动作不需要回滚。副作用动作在真实系统里不能简单 `undo`，比如退款、取消订单可能已经通知支付和履约系统，所以更好的策略是执行前强约束、执行中幂等、执行后补偿流程。比如错误退款不能直接删除，而是创建反向工单或人工复核。

## 3. 对应简历第 3 条：ToolCallManager、权限、幂等、MCP

简历原句：

> 设计 ToolCallManager 统一治理工具 Schema 校验、权限、缓存、超时重试、fallback 与幂等控制，并实现 MCP server/client adapter，便于接入外部企业系统。

### 3.1 ToolCallManager 解决了什么问题？

回答：

它把工具调用从“节点里随手调函数”升级为受治理的工具执行层。每个工具都有 `ToolSpec`，声明 name、description、input_model、allowed_roles、side_effect、cache_ttl、timeout、retries、fallback 和 handler。执行时统一做 schema 校验、权限检查、缓存、异步执行、超时重试、结果标准化和审计。

代码落点：`app/tool_call/framework.py`。

### 3.2 你怎么做参数校验？

回答：

每个工具绑定 Pydantic input model。例如订单查询要求 `order_id` 满足长度限制；policy search 要求 query 非空且 k 在范围内；副作用工具要求 action_type、order_id 和 message_text 合法。非法参数不会直接进 handler，而是返回标准 `ToolCallResult`，error_code 是 `schema_validation_failed`，并写入 audit event。

追问：LLM 生成非法参数后怎么修复？

回答：

可修复参数先做 deterministic repair，比如 order_id 的大小写、空格、前缀、多 ID 提取。不完整或歧义参数不盲修，返回澄清问题。关键点是：修复发生在工具入口前，修不了就不执行副作用。

### 3.3 权限怎么做？是不是企业级？

回答：

当前是 demo 级 RBAC 白名单，`ToolCallContext` 里有 role、tenant_id、user_id、session_id；每个工具有 allowed_roles。例如 `generate_after_sales_priority_report` 只允许 ops_manager/admin，普通 support_agent 不能调用。真实企业里会把这里接入 IAM/OAuth scopes、组织权限、商家 ACL 和工具 registry。

不要说“已经完整企业级鉴权”。应该说“权限边界和扩展点已经落地，当前实现是角色白名单”。

### 3.4 Redis 在工具治理里做什么？

回答：

Redis 不替代数据库，它负责短生命周期运行时协调：

- 只读工具 TTL cache；
- `/chat` 入口限流；
- HITL pending confirmation TTL；
- 多 worker 下副作用工具分布式锁。

最终业务幂等记录和 trace 仍然落 SQLite。原因是 Redis 可能过期、淘汰或重启，不适合作为最终审计证据。

### 3.5 幂等 key 怎么设计？

回答：

副作用工具不使用缓存，而是用业务粒度幂等 key。核心字段是 tenant_id、action_type、order_id 和归一化 reason_code。这样用户把话术改几个字，不会绕过幂等；同一订单同一退款/取消/改地址动作重复确认，会返回已有 result_id 和 duplicate=True。

追问：为什么不用 message hash？

回答：

旧设计如果用完整 message hash，用户改一个字就会生成新 key，重复创建 case。业务幂等应该绑定业务语义，而不是绑定自然语言表述。

### 3.6 超时、重试和 fallback 怎么做？

回答：

每个工具在 `ToolSpec` 里配置 timeout_seconds、retries 和 backoff_seconds。执行时用 `asyncio.wait_for` 控制超时，失败后指数退避重试。只读工具可以配置 fallback，例如订单事实工具失败时返回“暂时不可用，请稍后重试或转人工核查”。副作用工具一般不做业务结果 fallback，最多返回失败原因，因为不能在不确定状态下假装执行成功。

### 3.7 MCP 在项目里到底做了什么？

回答：

项目同时做了 MCP server 和 MCP client adapter。server 侧把本地业务能力暴露成 tools，例如订单查询、类目风险检索、售后报告和 escalation 草稿。client 侧支持 stdio 和 remote Streamable HTTP，预留接企业 OMS/CRM/工单/退款系统，并接入 Stripe sandbox 验证外部支付/退款类副作用工具的 schema、鉴权和边界。

代码落点：

- `app/mcp_server.py`;
- `app/mcp_client.py`;
- `app/stripe_mcp.py`。

### 3.8 MCP 和 function calling 区别是什么？

回答：

function calling 是模型供应商提供的“让模型按 schema 生成函数调用参数”的机制，重点是 LLM 输出格式。MCP 是 Agent 和外部工具/上下文服务之间的协议，重点是工具发现、schema 暴露、资源访问和跨进程通信。简单说：function calling 是模型怎么表达要调工具；MCP 是工具服务怎么被 Agent 发现和调用。

追问：为什么不直接查数据库，还要 MCP？

回答：

在个人 demo 里直接查库最快；在企业里，Agent 通常不应该直接连生产库，因为权限、审计、限流、数据脱敏和业务一致性都很难控制。MCP server 或内部 tool gateway 可以把数据库、OMS、CRM、退款系统包装成受治理的工具，让 Agent 只看到授权 schema 和受控动作。

## 4. 对应简历第 4 条：Olist 数据、订单事实、类目画像

简历原句：

> 基于 Olist 公开数据构建 98,666 条订单事实与 73 个类目画像，聚合配送延迟、低分率、取消率、支付金额、评价分等特征，支持售后风险排序和处理建议生成。

### 4.1 数据从哪里来，为什么选 Olist？

回答：

Olist 是公开电商数据集，包含订单、订单明细、支付、评价、商品、商家、用户、类目翻译等表。它适合做客服和售后运营，因为有订单状态、交付时间、支付金额、评价分和商品类目，可以构建订单事实和类目风险画像。

边界：

Olist 不是客服对话数据，也不是标准 Agent benchmark，所以项目另外用了 ResCommons 做客服语料检索，用 τ-bench retail 做外部客服域评测。

### 4.2 98,666 条订单事实是怎么构建的？

回答：

离线 ETL 把订单表、订单明细、支付、评价、商品和类目翻译 join 成按 order_id 查询的事实索引。每条订单事实包含订单状态、下单时间、预计送达、实际送达、延迟天数、支付金额、评价分、商品类目等字段。订单查询不走 embedding，因为 order_id 是精确实体，必须返回事实，不适合语义近似。

### 4.3 73 个类目画像怎么算？

回答：

按商品类目 group by，聚合订单数、完整数据订单数、延迟率、低分率、取消率、平均延迟天数、平均评分、平均支付金额和样例订单。类目画像用于运营分析，不是说类目本身危险，而是衡量该类目产生售后风险的概率和优先级。

追问：类目画像是不是太简单？

回答：

当前是离线规则画像，足够支撑项目里的售后优先排序，但如果生产化会继续引入 SLA、商家、物流商、地区、客单价、退货率、投诉文本情绪和时间窗口，形成可更新的风险模型。

### 4.4 为什么订单事实不是 RAG？

回答：

订单查询是精确事实查询，用户给出 order_id 时应该走 deterministic lookup。把订单 ID 放进向量库做 RAG 会引入近似召回风险，可能查错订单。RAG 更适合政策文档、FAQ、客服对话和规则解释；结构化事实适合 SQL/索引/工具查询。

### 4.5 如果真实业务数据更复杂怎么办？

回答：

真实电商会有订单、履约、库存、支付、退款、优惠券、工单、客服会话、商家规则等多源数据。我的设计会把它们分层：

- 结构化强一致事实：走 OMS/CRM/SQL 工具；
- 非结构化政策、FAQ、客服话术：走 hybrid retrieval；
- 有副作用动作：走 HITL + 幂等工具；
- 运营分析：离线/近实时聚合后作为只读工具。

关键不是把所有数据塞进一个 RAG，而是按数据类型决定访问方式。

## 5. 对应简历第 5 条：RAG、Hybrid Retrieval、τ-bench 指标

简历原句：

> 实现 workflow-constrained RAG 与 hybrid retrieval，在 ResCommons 客服语料上将 BM25 intent@1/intent@5 从 64%/81% 提升至 77%/91%；接入 τ-bench retail 评测，在 114-task local run 中达到 pass^1 91.23%。

### 5.1 什么是 workflow-constrained RAG？

回答：

它不是让 Agent 自由决定无限检索，而是在业务工作流里约束何时检索、检索哪个源、如何使用结果。订单查询走事实工具；政策问答走 SOP 分段检索；类目风险走类目画像检索；客服表达参考走 ResCommons 相似对话检索。LLM 负责 query rewriting 和 grounded answer generation，但不能脱离检索证据编造政策或承诺退款。

### 5.2 为什么不用完整 Agentic RAG 自主循环？

回答：

自主循环适合开放问答或研究型任务，但客服售后更怕失控和成本膨胀。当前设计允许 LLM 改写 query 和生成回答，但检索轮次、数据源和副作用边界由工作流控制。未来如果接入大量 FAQ、工单和商家规则，可以在只读 RAG 阶段增加 adaptive retrieval：低置信度时追加检索、改写 query、扩大召回源，再由 reranker 重排。

### 5.3 hybrid retrieval 的基线和提升是什么？

回答：

ResCommons train 语料作为 corpus，test query 作为评测，不把测试样本放回检索库，避免虚高。baseline 是 BM25，指标是 intent@1 和 intent@5，即 top1/top5 检索结果的 intent 是否命中 query intent。BM25 是 64%/81%，加入 `VectorStore` 召回和 RRF 风格融合后达到 78%/91%。默认向量后端是本地 hashing embedding，Docker 可切到 Qdrant；生产版可以替换为 BGE/Jina/OpenAI/企业 embedding。

追问：这个指标说明什么，不说明什么？

回答：

说明 hybrid retrieval 比纯 BM25 更能处理客服口语化表达和词形差异；但它不直接等于最终回答质量。最终 Agent 还要看 answer faithfulness、tool correctness、HITL correctness、task success、latency 和 cost。

### 5.4 为什么类目别名 Top1 能从 5% 到 97.5%？

回答：

原始类目名是下划线形式，例如 `health_beauty`。真实用户可能写 `health beauty`、`health-beauty`、中文别名或业务俗称。直接 exact match 对别名几乎不行，Top1 很低。系统加入 query rewriting、别名词表和 token overlap 后，能把常见写法归一到真实类目。

被追问时要主动说边界：

这个指标不是证明大规模语义检索能力，而是证明类目实体归一化能力。对 noisy holdout 或新别名，仍需要 query log、embedding 和 reranker。

### 5.5 为什么没有用 embedding / reranker？

回答：

项目已经抽出了 `VectorStore` 边界，并在客服历史语料检索中接入 BM25 + VectorStore + RRF 融合。默认本地 hashing embedding 是为了无 API key、无模型下载、CI 可复现；Docker 模式可以通过 Qdrant 作为向量数据库后端。对订单 ID 和类目名这种高精度实体，确定性归一化更可靠；对大规模 FAQ 和客服对话，生产版本会把 embedder 替换为 BGE/Jina/OpenAI/企业 embedding，再加 cross-encoder reranker，并用 Recall@K、MRR、nDCG、latency 和 cost 比较收益。

### 5.6 τ-bench retail 结果怎么讲最稳？

回答：

我接的是 `sierra-research/tau2-bench` v1.0.1 的 retail base split。它包含 retail policy、用户模拟器、工具和 reward function。我用 DeepSeek V4-Flash 跑了 114-task local run，pass^1 是 91.23%（104/114），DB match 92.11%，write action match 92.05%，NL assertions 95.08%，p95 32.77s。它不是公开 leaderboard submission，也不是我自己造的业务 eval。

### 5.7 为什么模型写 DeepSeek V4-Flash？

回答：

调用侧在 LiteLLM 里用了 `deepseek/deepseek-chat` 兼容写法，但原始 run log 里 `raw_data.model` 返回的是 `deepseek-v4-flash`。所以简历写实际服务端模型 DeepSeek V4-Flash；面试可以解释兼容入口和服务端模型名的区别。

### 5.8 91.23% 是高还是低？

回答：

它是一个有竞争力但不完美的本地复现数字。价值不在于说自己超过榜单，而在于证明我能接标准客服域 benchmark，跑完整 split，拆出 DB/action/NL/latency/cost 多层指标，并把失败样本沉淀成 regression。比 30 条 100% 更可信。

追问：为什么不跑 leaderboard？

回答：

公开 leaderboard 需要固定提交流程、多 trial、成本预算和环境控制。作为个人项目，我先跑 base split local run，保证可复现证据和失败分析。如果公司需要，我可以继续补多 trial pass^k 方差和正式提交。

### 5.9 失败的 10 个 task 说明什么？

回答：

说明系统在长尾复杂客服场景仍有改进空间，主要集中在：

- 复杂退换货和取消的确认范围；
- pending order 的地址和 item mutation 顺序；
- 最终答复金额必须绑定工具返回值；
- 用户模拟器和 expected assertion 有少量边界冲突。

这不是坏事，反而是评测飞轮的输入。项目已经把 task 0、6、19、20、22、29 等早期失败沉淀成 bad-case regression。

## 6. 系统设计升级题

### 6.1 如果日请求量从 demo 到 10 万，会怎么改？

回答：

入口层用 FastAPI 多 worker，前面加 API Gateway 做鉴权、限流和租户识别。短生命周期状态如工具缓存、限流、HITL TTL、分布式锁放 Redis。长期状态如会话、trace、case idempotency 放 MySQL/PostgreSQL。异步长任务和外部副作用通过消息队列解耦，例如 Kafka/RocketMQ。RAG 层拆成 ES/BM25、vector DB 和 reranker 服务。可观测性接 OpenTelemetry、Prometheus/Grafana 和 trace sampling。

### 6.2 多租户怎么做？

回答：

需要三层隔离：

- 数据隔离：tenant_id 进入所有 query/cache/idempotency key，数据库可以行级 tenant_id 或物理库隔离；
- 工具隔离：ToolCallContext 带 tenant、user、role、scopes，工具 registry 按租户授权；
- 上下文隔离：session、checkpoint、memory、trace 都带 tenant_id，RAG index 按租户 namespace 或 collection 隔离。

当前项目实现了 tenant-aware cache key 和 role whitelist，但没有完整 IAM/ACL，这是个人项目边界。

### 6.3 trace 会不会泄露隐私？

回答：

会有风险，所以 trace 不能直接存全量明文。当前工具审计里会对 order_id 做部分脱敏，对长 message_text 存 hash 和长度。生产里还要做字段级脱敏、权限控制、保留周期、审计访问日志和敏感字段加密。LLM request/response 如果落盘，也要按 PII 策略处理。

### 6.4 如果 LLM 挂了，系统还能做什么？

回答：

LLM 挂了不能完成高质量自然语言规划和生成，但系统仍可做部分高确定性任务：订单 ID 查询、已知类目风险查询、政策关键词检索、拒绝越界请求、返回人工澄清。fallback 的意义不是让系统和 LLM 一样聪明，而是在故障时保住安全边界和基础客服能力。

### 6.5 如果接真实 OMS/CRM，哪些代码要改？

回答：

核心 graph 不需要大改。替换的是 tool handler 和 MCP adapter：

- `OlistService` 替换为 OMS/CRM client；
- `SQLiteCaseService` 替换为真实工单/退款/取消接口；
- ToolSpec 的 input/output schema 保持稳定；
- ToolCallManager 的权限、幂等、timeout、fallback、audit 保留；
- RAG corpus 替换为企业 SOP、FAQ、商家规则和客服对话。

这也是为什么要把业务工具和 graph 解耦。

## 7. 面试官会故意挖坑的问题

### 7.1 你是不是只是把很多开源库串起来？

回答：

我用开源库解决状态图、Web 服务和模型调用，但项目难点不在“调用 LLM”，而在业务边界。具体包括多意图排序、副作用 HITL、工具统一治理、业务幂等、Redis 运行时协调、公开数据构建、hybrid retrieval baseline、真实 LLM eval、τ-bench adapter 和 bad-case regression。这些是把 Agent 落到业务系统必须解决的问题。

### 7.2 为什么不用多 Agent，简历投高级 Agent 岗会不会显得不够？

回答：

高级不等于一定多 Agent。客服售后这个场景共享订单上下文、权限状态和副作用审批，单 LangGraph 多 capability 更稳定、更便宜、更好审计。多 Agent 会在接入广告、库存、选品、客服质检等独立域时再引入，推荐 supervisor 架构，而不是一开始去中心化。

### 7.3 你的 policy KB 是不是手写的，能证明 RAG 吗？

回答：

policy KB 是项目内的 SOP 文档，用来验证政策检索、grounded answer 和副作用边界，不伪装成真实企业私有文档。为了避免 RAG 只测手写小文档，我另外接了 ResCommons 公开客服语料做 hybrid retrieval，并区分 train corpus 和 test query。

### 7.4 你这个 benchmark 是不是为了分数写 adapter，和自己项目没关系？

回答：

τ-bench adapter 不复用 Olist 数据，因为 benchmark 本来就有自己的 policy、tools 和 user simulator。它验证的是客服域 Agent harness 能力：读工具、写工具、遵守 policy、处理多轮用户、完成 DB state 和 NL assertion。Olist 项目验证的是我自己的业务链路，τ-bench 提供横向可比的外部压力测试，两者目标不同。

### 7.5 你怎么证明不是 prompt 过拟合？

回答：

不能只靠 30 条自选 case，所以我跑了 114-task base split，并报告失败任务。另一个证据是指标拆分：DB match、read/write action、NL assertion、latency 和 cost 都分开看。如果只是过拟合 prompt，full split 和 action-level 指标会更容易暴露问题。

### 7.6 为什么 p95 32.77s 这么慢？

回答：

τ-bench 是多轮用户模拟器，不是单次 API 请求；一次任务包含 agent LLM、多轮 user simulator、工具调用和 NL judge，p95 32.77s 是 conversation-level latency。线上产品会把用户模拟器和 judge 去掉，并做模型分层、缓存、并发工具调用、流式输出和小模型路由，真实用户侧延迟会低于 benchmark conversation time。

### 7.7 你这个平均成本怎么算，可信吗？

回答：

我只把它作为本地成本参考，不和 leaderboard 比。tau2 结果里 user cost 是 114/114 都有，agent cost 只有 61/114 样本完整，因此 summary 里显式写了 cost coverage。简历写“约 $0.0060/conversation”，面试会说明是 cost-complete samples 的平均，不把缺失字段当 0。

### 7.8 如果模型把退款政策答错了怎么办？

回答：

政策回答必须基于检索到的 SOP 段落生成，不能直接承诺退款。输出 guard 会拦截明显坏输出；更关键的是副作用工具不会因为回答文字就执行，必须 HITL。生产里还会加 policy compliance judge 或规则引擎，对“承诺退款”“承诺优惠券”等敏感表达做拦截。

### 7.9 如果两个工具状态不一致怎么办？

回答：

先定义 source of truth。订单状态以 OMS 为准，支付以支付系统为准，工单以 CRM 为准。Agent 不直接合并矛盾事实，而是把冲突写入 trace，返回“需要人工核查”，或创建人工复核 case。对副作用动作，工具返回结果必须成为最终回答事实来源，不能靠 LLM 自己计算或猜。

### 7.10 如果用户连续发新请求，但 session 里还有 pending HITL 怎么办？

回答：

当前策略是阻塞同一 session 的新任务，要求用户先确认 yes 或取消 no，或者换 session。原因是 pending side effect 是高风险状态，如果允许新任务插队，容易把确认回复绑定到错误动作。更复杂版本可以支持 pending action id，让用户明确确认某个 action，但实现复杂度更高。

## 8. 建议背诵的 10 个核心答案

1. **项目定位**：内部客服 + 售后运营 Agent，不是消费者泛聊天。
2. **Agent 边界**：LLM 处理语言不确定性，业务事实和副作用由确定性系统控制。
3. **为什么 LangGraph**：状态、中断、恢复、条件边、checkpoint。
4. **为什么不是 ReAct**：客服售后强约束、高副作用，plan-and-execute 更可控。
5. **ToolCallManager**：schema、权限、缓存、timeout、retry、fallback、audit、idempotency。
6. **MCP**：工具协议和外部企业系统接入层，不是 function calling，也不是多 Agent 通信协议。
7. **RAG**：订单走事实工具，政策走 SOP，客服语料走 hybrid retrieval，类目走实体归一化。
8. **数据**：Olist 做订单事实和类目画像，ResCommons 做客服语料检索，τ-bench 做外部客服域评测。
9. **评测**：不要只说 pass^1，拆 DB match、write action、NL assertion、latency、cost。
10. **不足**：还不是 public leaderboard，真实企业 IAM/OMS/CRM 未接入，RAG 可升级 ES/vector/reranker，多租户可进一步生产化。
