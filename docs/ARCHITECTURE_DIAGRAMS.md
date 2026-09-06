# Architecture Diagrams

这份文档把项目中最适合面试讲解的框架图集中导出。每张图都有独立 Mermaid 源文件，便于放进 README、PPT、GitHub 或 Mermaid Live Editor。

## 1. ToolCallManager 治理链路

源文件：`docs/diagrams/tool_call_manager.mmd`

```mermaid
flowchart TD
    Start[Agent calls Tool] --> Validate[1. Pydantic schema validation]
    Validate -- failed --> Err1[Return schema_validation_failed]
    Validate -- passed --> Auth[2. Role and auth_scope permission check]
    Auth -- denied --> Err2[Return permission_denied]
    Auth -- passed --> CheckCache{3. Cacheable and not side_effect?}
    CheckCache -- cache hit --> ReturnCached[Return cached ToolCallResult]
    CheckCache -- miss or side_effect --> SideEffectCheck{4. side_effect=True?}
    SideEffectCheck -- yes --> AcquireLock[RuntimeStore distributed lock: SET NX]
    AcquireLock -- lock failed --> Err3[Return side_effect_in_progress]
    AcquireLock -- lock acquired --> Exec[5. Async/thread execution with timeout retry backoff]
    SideEffectCheck -- no --> Exec
    Exec -- success --> MaybeCache{Read-only cache enabled?}
    MaybeCache -- yes --> UpdateCache[Write TTL cache]
    MaybeCache -- no --> Audit
    UpdateCache --> Audit[6. Redact sensitive args and write audit log]
    Exec -- failure or timeout --> HasFallback{7. Fallback configured?}
    HasFallback -- yes --> RunFallback[Run fallback handler] --> Audit
    HasFallback -- no --> Err4[Return error_code and error_message] --> Audit
    ReturnCached --> Audit
    Err1 --> Audit
    Err2 --> Audit
    Err3 --> Audit
    Audit --> Done[Return normalized ToolCallResult]
```

这张图回答的是“Agent 调工具怎么生产化”。核心不是让模型直接调函数，而是在工具网关层集中处理 schema 校验、权限、缓存、锁、超时、重试、fallback、标准输出和审计。只读工具可以走 TTL cache；副作用工具不缓存，先抢分布式锁，再执行持久化幂等动作。面试里可以强调：LLM planner 不是最后一道安全边界，工具层才是能兜住错误参数、越权调用和重复写入的地方。

## 2. 售后 Case 决策链路

源文件：`docs/diagrams/after_sales_decision.mmd`

```mermaid
flowchart TD
    Start[Receive after-sales action_type] --> Split{Match action_type}
    Split -- cancel_order --> CancelCheck{order.status}
    CancelCheck -- created / approved / invoiced --> CancelApprove[Allow cancellation draft; risk depends on amount]
    CancelCheck -- canceled --> CancelReject1[Reject: already canceled]
    CancelCheck -- shipped / delivered --> CancelReject2[Reject: shipped or delivered]
    Split -- change_address --> AddrCheck{order.status}
    AddrCheck -- created / approved / invoiced --> AddrApprove[Allow address-change draft; risk medium]
    AddrCheck -- shipped / delivered / canceled --> AddrReject[Reject: logistics locked or order canceled]
    Split -- refund_request --> RefundCheck{Order facts and risk features}
    RefundCheck -- status is canceled --> RefundApprove1[Allow refund review case]
    RefundCheck -- delay_days >= 7 --> RefundApprove2[Allow refund review: delivery delay]
    RefundCheck -- review_score <= 2 --> RefundTicket[Allow support case first: investigate low review]
    RefundCheck -- otherwise --> RefundClarify[Clarify: insufficient evidence]
    Split -- invoice_request --> InvoiceApprove[Allow invoice request; ask for tax profile and email]
    Split -- complaint_escalation --> ComplaintApprove[Allow complaint escalation; high amount raises risk]
    Split -- other --> SupportApprove[Allow regular support follow-up case]
    CancelApprove --> Verify
    CancelReject1 --> Verify
    CancelReject2 --> Verify
    AddrApprove --> Verify
    AddrReject --> Verify
    RefundApprove1 --> Verify
    RefundApprove2 --> Verify
    RefundTicket --> Verify
    RefundClarify --> Verify
    InvoiceApprove --> Verify
    ComplaintApprove --> Verify
    SupportApprove --> Verify
    Verify[Verifier] --> Next{required_next_step}
    Next -- hitl --> HITL[Pause before write action]
    Next -- stop --> Stop[Return safe rejection]
    Next -- clarify --> Clarify[Ask for missing evidence]
    Next -- execute --> Execute[Execute low-risk action]
```

这张图说明为什么项目不是普通客服问答，而是 case resolution。LLM 可以理解用户说“我要退款”“我要投诉”，但是否能取消、是否能退款、是否要人工复核，不能由模型拍脑袋决定。决策层把 action_type、订单状态、延迟、评价、金额和政策命中转成结构化 outcome，再由 Verifier 决定下一步是 hitl、stop、clarify 还是 execute。

## 3. HITL 中断与恢复状态机

源文件：`docs/diagrams/hitl_resume_state.mmd`

```mermaid
stateDiagram-v2
    [*] --> NewRequest: receive user request
    NewRequest --> CheckPending: check session checkpoint
    CheckPending --> StartGraph: no pending interrupt
    CheckPending --> PendingHandling: pending interrupt exists
    PendingHandling --> CheckTimeout: check TTL
    CheckTimeout --> TimeoutPath: expires_at < now
    CheckTimeout --> CheckReply: still valid
    CheckReply --> ResumeConfirm: yes / confirm / 确认
    CheckReply --> ResumeCancel: no / cancel / 取消
    CheckReply --> RemindUser: normal new text
    StartGraph --> ExecuteNodes: plan_tasks -> execute_task_plan
    ExecuteNodes --> AwaitConfirmationNode: side-effect task requires HITL
    AwaitConfirmationNode --> Interrupted: LangGraph interrupt()
    Interrupted --> [*]: return decision draft to user
    TimeoutPath --> ResumeCommand: Command(resume="__hitl_timeout__")
    ResumeConfirm --> ResumeCommand: Command(resume=user_message)
    ResumeCancel --> ResumeCommand: Command(resume=user_message)
    ResumeCommand --> FinalizeNode: finalize_escalation
    RemindUser --> [*]: ask user to confirm or cancel first
    FinalizeNode --> SideEffectExec: confirmed == true
    FinalizeNode --> CanceledExec: confirmed == false or timeout
    SideEffectExec --> ClearPending: execute_side_effect with idempotency key
    CanceledExec --> ClearPending: discard draft
    ClearPending --> [*]: completed / canceled / timeout_canceled
```

这张图回答“为什么 HITL 不是简单弹窗”。真正的难点是 HTTP 请求结束后状态还要能恢复：LangGraph interrupt 挂起，checkpoint 保存上下文，Redis runtime store 保存 pending TTL。用户下一轮回复 yes/no 时，系统不是重新规划，而是 resume 到 finalize 节点；超时则用特殊 resume command 清理状态，避免悬挂副作用。

## 4. 项目整体业务架构

源文件：`docs/diagrams/overall_architecture.mmd`

```mermaid
flowchart LR
    User[Support agent or ops manager] --> UI[Web console / HTTP API]
    UI --> Guard[Input guard]
    Guard --> Planner[LLM task planner]
    Planner --> Graph[LangGraph plan-and-execute graph]
    Graph --> ReadTools[Read-only deterministic tools]
    Graph --> RAG[Workflow-constrained RAG]
    Graph --> Case[AfterSalesCase builder]
    ReadTools --> Olist[Olist order facts and category profiles]
    RAG --> Policy[Policy / FAQ / merchant rules KB]
    RAG --> SupportCorpus[ResCommons support corpus]
    Case --> Decision[AfterSalesDecisionEngine]
    Decision --> Verifier[Verifier]
    Verifier --> SafeExit[Reject or clarify]
    Verifier --> HITL[HITL confirmation]
    HITL --> ToolManager[ToolCallManager]
    ToolManager --> SideEffect[Refund / cancel / address / invoice / complaint tools]
    ToolManager --> Redis[Redis runtime store: cache, lock, TTL, rate limit]
    ToolManager --> MCP[MCP server/client boundary]
    Graph --> Trace[SQLite trace and case metrics]
    Trace --> Eval[Offline evals and bad-case regression]
    Eval --> CI[GitHub Actions CI]
```

这张图适合开场讲项目定位：它不是“LLM + 一个查询接口”，而是围绕售后 case 的受控执行链路。LLM 只处理不确定语言，订单事实、政策约束、风险判断、工具执行和副作用治理都由确定性模块接管。面试官问“业务价值在哪里”时，可以说：这个系统帮助客服坐席和售后运营主管把高风险售后处理流程标准化、可审计化、可回归评测。

## 5. MCP 企业工具边界

源文件：`docs/diagrams/mcp_enterprise_boundary.mmd`

```mermaid
flowchart TD
    Agent[Agent executor] --> Registry[Tool registry / ToolCallManager]
    Registry --> Metadata[list_enterprise_tool_boundaries]
    Metadata --> Schema[input schema]
    Metadata --> Auth[auth_scope and role allowlist]
    Metadata --> Risk[risk_level and side_effect flag]
    Metadata --> Idem[idempotency_required]
    Metadata --> Audit[audit contract]
    Registry --> LocalMCP[Local MCP server]
    Registry --> RemoteMCP[Remote MCP client]
    LocalMCP --> OrderTool[get_order_status]
    LocalMCP --> RiskTool[search_category_risk]
    LocalMCP --> CaseTool[assess_after_sales_case]
    LocalMCP --> ReportTool[generate_after_sales_priority_report]
    RemoteMCP --> Stripe[Stripe sandbox MCP]
    RemoteMCP --> OMS[Enterprise OMS MCP]
    RemoteMCP --> CRM[CRM / ticket MCP]
    RemoteMCP --> KB[Policy KB MCP]
    Stripe --> AuditStream[Audit / trace event]
    OMS --> AuditStream
    CRM --> AuditStream
    KB --> AuditStream
```

这张图用来回答“MCP 写在技术栈里到底做了什么”。MCP 在这里不是多 Agent 通信，也不是 function calling 的替代品，而是企业工具边界：工具要能被发现、描述、授权、审计和替换。本项目的本地 MCP server 模拟企业 OMS/CRM/知识库工具，remote MCP client/Stripe adapter 演示外部 SaaS 接入形态。

## 6. 评测飞轮

源文件：`docs/diagrams/evaluation_flywheel.mmd`

```mermaid
flowchart TD
    Cases[Eval cases and production bad cases] --> Offline[Offline deterministic evals]
    Offline --> Intent[Intent and multi-intent checks]
    Offline --> Tool[Tool schema / repair / HITL checks]
    Offline --> RAG[RAG retrieval metrics]
    Offline --> Trace[Trajectory consistency]
    Cases --> Live[Live LLM agent eval]
    Live --> Judge[LLM-as-judge answer quality smoke]
    Live --> Drift[Route drift report]
    Cases --> Bench[tau2-bench retail local run]
    Bench --> BenchFailures[Failed task clusters]
    Intent --> Report[agent_metrics_report.md]
    Tool --> Report
    RAG --> Report
    Trace --> Report
    Judge --> Report
    Drift --> Report
    BenchFailures --> Regression[Bad-case regression set]
    Regression --> CI[CI: pytest + ruff + offline evals]
    CI --> Cases
```

这张图回答“怎么证明 Agent 有效”。项目不是只看最终回答，而是拆成意图、多意图、工具参数、RAG、轨迹、真实 LLM、LLM judge、tau2 benchmark 和 bad-case regression。尤其要强调：离线 100% 不能等价于模型能力；真实 LLM eval 和 tau2-bench local run 才是更接近 Agent 能力的证据。

## 7. 主执行链路

源文件：`docs/diagrams/agent_execution_chain.mmd`

```mermaid
flowchart TD
    Request[User message] --> Pending{Pending HITL in session?}
    Pending -- yes --> PendingPolicy{Confirm / cancel / timeout?}
    PendingPolicy -- confirm --> Resume[Resume LangGraph checkpoint]
    PendingPolicy -- cancel or timeout --> Cleanup[Clear pending state]
    PendingPolicy -- unrelated text --> Reminder[Ask user to confirm or cancel first]
    Pending -- no --> InputGuard[Input guard]
    InputGuard -- rejected --> Reject[Off-topic or unsafe rejection]
    InputGuard -- accepted --> Plan[LLM structured task plan]
    Plan --> Sort[Read-only tasks first, side-effect tasks later]
    Sort --> Execute[Execute task plan]
    Execute --> Order[Order status tool]
    Execute --> Policy[Policy / FAQ / merchant RAG]
    Execute --> Ops[After-sales priority report]
    Execute --> Side[After-sales side-effect task]
    Side --> Case[Build AfterSalesCase]
    Case --> Decision[Decision + Verifier]
    Decision -- reject / clarify --> SafeAnswer[Safe answer without write]
    Decision -- requires HITL --> Interrupt[interrupt and persist checkpoint]
    Interrupt --> Draft[Return draft and confirmation question]
    Resume --> Finalize[finalize_escalation]
    Finalize --> ToolCall[execute_side_effect through ToolCallManager]
    ToolCall --> FinalAnswer[Final answer]
    Order --> FinalAnswer
    Policy --> FinalAnswer
    Ops --> FinalAnswer
    SafeAnswer --> FinalAnswer
    Cleanup --> FinalAnswer
    Reminder --> FinalAnswer
```

这张图适合回答“用户一句话进来后到底发生了什么”。先检查 session 是否有 pending HITL，避免用户绕过确认状态；没有 pending 才进入 input guard、LLM planner、任务排序和执行。只读任务优先，副作用任务最后进入 case 决策和 HITL。这个设计的业务理由是：先拿事实和政策，再做动作，避免模型在事实不完整时先执行退款、取消或改地址。

## 总结口径

面试讲这组图时，可以按这个顺序：

1. 先讲整体架构：项目是售后 case resolution Agent，不是泛聊天。
2. 再讲主执行链路：LLM 做规划，工作流控制顺序和副作用边界。
3. 然后讲售后决策：退款/取消/改地址/发票/投诉升级都进入 `AfterSalesCase`。
4. 接着讲 HITL：中断、恢复、超时、幂等，不是一个普通确认弹窗。
5. 再讲 ToolCallManager：schema、权限、缓存、锁、重试、fallback、审计。
6. 最后讲 MCP 和评测飞轮：MCP 是企业工具边界，评测飞轮证明改动不会只靠主观感觉。

最重要的一句话是：**这个项目把 Agent 的“不确定语言理解”和企业系统的“确定性执行边界”拆开了，LLM 不直接决定退款/取消这类高风险动作，而是生成结构化计划，后续由业务规则、工具治理、HITL、幂等和审计共同约束。**
