# Architecture Diagrams

这份文档集中保存项目核心 Mermaid 架构图。所有图都和当前代码链路对齐，尤其是 LangGraph 已拆成显式节点：`plan_tasks`、`select_next_task`、`extract_slots`、`retrieve_context`、`execute_read_task`、`build_after_sales_case`、`await_confirmation`、`finalize_escalation`、`finalize_answer`。

## 1. 一张图讲完整主链路

源文件：`docs/diagrams/agent_execution_chain.mmd`

```mermaid
flowchart TD
    A[1 用户输入 /chat<br/>客服坐席或售后运营主管] --> B{2 当前 Session<br/>是否有待确认副作用?}
    B -- 有 --> C{3 用户回复类型}
    C -- yes/确认 --> D[恢复 LangGraph checkpoint<br/>进入 finalize_escalation]
    C -- no/取消/超时 --> E[清理 pending<br/>记录 canceled/timeout]
    C -- 普通新问题 --> F[提示先确认或取消<br/>阻止绕过 HITL]
    B -- 无 --> G[4 Input Guard<br/>业务域 + 注入风险检查]
    G -- 拒绝 --> H[安全拒绝<br/>不进入工具]
    G -- 通过 --> I[5 LLM Planner<br/>拆多意图 task_plan]
    I --> J[6 select_next_task<br/>只读优先, 副作用后置]
    J --> K[7 extract_slots<br/>抽槽 + 参数修复]
    K --> L[8 retrieve_context<br/>事实/政策/语料/类目证据]
    L --> M{9 是否副作用任务?}
    M -- 否 --> N[10 execute_read_task]
    N --> J
    M -- 是 --> O[11 build_after_sales_case]
    O --> P[12 Decision Engine]
    P --> Q[13 Verifier]
    Q --> R{14 下一步}
    R -- stop --> S[安全拒绝]
    R -- clarify --> T[要求补充信息]
    R -- execute --> U[低风险自动执行]
    R -- hitl --> V[interrupt 等确认]
    U --> W[15 ToolCallManager]
    D --> W
    W --> Y[16 execute_side_effect<br/>Redis lock + SQLite idempotency]
    S --> Z[17 finalize_answer]
    T --> Z
    E --> Z
    F --> Z
    Y --> Z
    J -- 无剩余任务 --> Z
    Z --> AA[18 trace + metrics]
    AA --> AB[返回 answer/sources/session_id]
```

每条线代表一次真实状态迁移：pending 检查防止绕过 HITL，planner 负责拆任务，select 节点保证只读先执行，retrieve 节点统一拉证据，售后副作用进入 `AfterSalesCase -> Decision -> Verifier`，写动作必须通过 ToolCallManager 和幂等存储。

## 2. ToolCallManager 治理链路

源文件：`docs/diagrams/tool_call_manager.mmd`

```mermaid
flowchart TD
    Start[Agent 调用工具] --> Validate[1 Pydantic Schema 校验]
    Validate -- 失败 --> Err1[返回 schema_validation_failed]
    Validate -- 成功 --> Auth[2 Role + auth_scope 权限检查]
    Auth -- 越权 --> Err2[返回 permission_denied]
    Auth -- 通过 --> CheckCache{3 是否可缓存且非 side_effect?}
    CheckCache -- 命中 --> ReturnCached[返回 cached ToolCallResult]
    CheckCache -- 未命中/副作用 --> SideEffectCheck{4 side_effect=True?}
    SideEffectCheck -- 是 --> AcquireLock[5 Redis/RuntimeStore SET NX 锁]
    AcquireLock -- 拿锁失败 --> Err3[返回 side_effect_in_progress]
    AcquireLock -- 拿锁成功 --> Exec[6 async/to_thread 执行 + timeout/retry/backoff]
    SideEffectCheck -- 否 --> Exec
    Exec -- 成功 --> MaybeCache{7 只读 TTL cache?}
    MaybeCache -- 是 --> UpdateCache[写 tenant-scoped cache]
    MaybeCache -- 否 --> Audit
    UpdateCache --> Audit[8 脱敏审计日志]
    Exec -- 失败/超时 --> HasFallback{9 有 fallback?}
    HasFallback -- 有 --> RunFallback[执行 fallback handler] --> Audit
    HasFallback -- 无 --> Err4[返回错误码] --> Audit
    ReturnCached --> Audit
    Err1 --> Audit
    Err2 --> Audit
    Err3 --> Audit
    Audit --> Done[标准 ToolCallResult]
```

当前工具调用框架覆盖 schema、role、scope、tenant cache、异步执行、超时重试、fallback、审计和 side-effect lock。副作用工具不缓存，最终幂等记录落 SQLite case store。

## 3. 售后 Case 决策链路

源文件：`docs/diagrams/after_sales_decision.mmd`

```mermaid
flowchart TD
    Start[接收售后 action_type + order facts + policy hits] --> Signals[提取 risk_signals]
    Signals --> Split{action_type}
    Split -- refund_request --> Refund[延迟/低分/取消订单进入退款核查 HITL, 事实不足则澄清]
    Split -- cancel_order --> Cancel[发货前可取消; 低额低风险自动, 高额 HITL, 发货后拒绝]
    Split -- change_address --> Address[发货前可改址; 低额低风险自动, 物流锁定后拒绝]
    Split -- invoice_request --> Invoice[发票申请需补齐抬头/税号/邮箱]
    Split -- complaint_escalation --> Complaint[投诉升级 HITL, 不提前承诺补偿]
    Split -- open_support_case --> Case[普通跟进低风险自动; 延迟/低分/补偿/投诉进入 HITL]
    Refund --> Verify[Verifier]
    Cancel --> Verify
    Address --> Verify
    Invoice --> Verify
    Complaint --> Verify
    Case --> Verify
    Verify --> Next{execute / hitl / clarify / stop}
```

当前 Olist 数据能直接支撑订单状态、金额、延迟、低分和政策命中，因此这些字段进入真实判断；`risk_signals` 预留了已退款、部分退款、优惠券/积分、疑似滥用等企业常见字段，当前公开数据没有这些事实来源时按安全默认值处理。低风险自动执行门禁已经落地，完整售后风控需要接入企业真实退款、支付、会员和风控事实。

## 4. HITL 中断与恢复

源文件：`docs/diagrams/hitl_resume_state.mmd`

```mermaid
stateDiagram-v2
    [*] --> NewRequest: 用户请求进入 /chat
    NewRequest --> CheckPending: 读取 checkpoint + Redis TTL
    CheckPending --> InputGuard: 没有 pending
    CheckPending --> PendingHandling: 有 pending
    PendingHandling --> TimeoutPath: 过期
    PendingHandling --> ResumeConfirm: yes/确认
    PendingHandling --> ResumeCancel: no/取消
    PendingHandling --> RemindUser: 普通新问题
    InputGuard --> PlanTasks
    PlanTasks --> SelectNextTask
    SelectNextTask --> ExtractSlots
    ExtractSlots --> RetrieveContext
    RetrieveContext --> ExecuteReadTask: 只读任务
    ExecuteReadTask --> SelectNextTask
    RetrieveContext --> BuildAfterSalesCase: 副作用任务
    BuildAfterSalesCase --> AwaitConfirmation: 需要 HITL
    AwaitConfirmation --> Interrupted: interrupt()
    Interrupted --> [*]: 返回草稿
    TimeoutPath --> FinalizeEscalation
    ResumeConfirm --> FinalizeEscalation
    ResumeCancel --> FinalizeEscalation
    FinalizeEscalation --> SelectNextTask
    SelectNextTask --> FinalizeAnswer: 无剩余任务
    FinalizeAnswer --> [*]
```

用户确认后恢复到 `finalize_escalation`，执行已保存的草稿动作；取消或超时则清理 pending；执行后继续跑剩余任务。

## 5. MCP 企业工具边界

源文件：`docs/diagrams/mcp_enterprise_boundary.mmd`

```mermaid
flowchart TD
    Agent[Agent executor] --> Registry[ToolCallManager / tool registry]
    Registry --> Metadata[list_enterprise_tool_boundaries]
    Metadata --> Schema[input schema]
    Metadata --> Auth[role + auth_scope]
    Metadata --> Risk[risk_level + side_effect]
    Metadata --> Idem[idempotency_required]
    Registry --> LocalMCP[Local MCP server]
    Registry --> RemoteMCP[Remote MCP client]
    LocalMCP --> OrderTool[get_order_status]
    LocalMCP --> RiskTool[search_category_risk]
    LocalMCP --> CaseTool[assess_after_sales_case]
    RemoteMCP --> Stripe[Stripe sandbox MCP]
    RemoteMCP --> OMS[Enterprise OMS/CRM/Refund MCP]
```

MCP 在项目里承担企业工具接入边界。它暴露工具 schema、权限、风险等级、幂等要求和审计语义；生产环境可以把本地 Olist/SQLite 工具替换成企业 OMS、CRM、退款、发票、优惠券 MCP server。

## 6. 评测飞轮

源文件：`docs/diagrams/evaluation_flywheel.mmd`

```mermaid
flowchart TD
    Code[代码/Prompt/规则改动] --> CI[CI: ruff + pytest]
    CI --> Offline[离线 eval: intent/multi-intent/tool/RAG/trajectory/performance]
    Offline --> Live[真实 LLM 回归: planner/tool/HITL/answer]
    Live --> Judge[LLM-as-Judge: relevance/faithfulness/tool/HITL]
    Judge --> Tau[tau2-bench retail local run]
    Tau --> BadCase[失败样本归因]
    BadCase --> Regression[bad-case regression]
    Regression --> CI
    Trace[SQLite trace/case metrics] -.抽样回放.-> Offline
```

评测飞轮分层证明项目能力：单测和离线 eval 证明确定性模块稳定；真实 LLM 回归证明模型进入主链路后可用；LLM-as-Judge 看回答质量；tau2-bench retail 给出第三方客服环境的横向指标。

## 总结

项目把自然语言理解和企业执行边界拆开：LLM 生成结构化计划，业务规则、RAG、工具治理、HITL、Redis 锁、SQLite 幂等和 trace 共同保证售后动作可控、可审计、可评测。
