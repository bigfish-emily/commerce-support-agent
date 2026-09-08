# E-Commerce Support Agent 使用手册

这是一套面向消费者自助售后和人工审核的 Agent。消费者在网页里直接输入订单查询、退款、取消、改地址、发票或投诉诉求；Agent 会先回答可直接处理的问题，遇到退款/取消/改地址/投诉升级这类高风险写动作时生成售后 case，并交给右侧审核台确认后再执行。

## 1. 启动方式

在项目目录执行：

```bash
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/customer
http://127.0.0.1:8000/review
```

如果只想看 API 文档，打开：

```text
http://127.0.0.1:8000/docs
```

默认不配置 API key 也能测试，系统会进入离线模式：任务规划、抽取、风控和回答生成使用本地 fallback。配置 `OPENAI_API_KEY` 或 `AIHUBMIX_API_KEY` 后会启用真实 OpenAI-compatible 模型；模型名用 `OPENAI_MODEL` 指定。

消费者入口会做订单归属检查。默认 demo 账号只允许查询样例订单 `203096f03d82e0dffbc41ebc2e2bcfb7`，真实部署时这里应接企业 IAM/OMS 的订单归属接口。

## 2. 网页上怎么操作

打开首页后，你会看到两个入口：

- `消费者入口`：面向真实用户的自助售后对话。
- `审核台`：面向售后人员的 HITL case 审核。

消费者入口包含两栏：

- 左侧：消费者诉求模板，点击后会把测试句子填入输入框。
- 右侧：消费者自助对话区，填写 `Session ID` 和用户输入，点击发送。

审核台包含两栏：

- 左侧：读取待审核 case，并执行 approve/reject。
- 右侧：查看审核包、case metrics、trace summary 和当前 session 轨迹。
- `Case Metrics`：按售后 case 聚合自动解决率、HITL 占比、错误写动作拦截、政策命中率、工具错误率、p95 延迟和单 case 成本。

审核台和单 session trace replay 默认携带本地演示 token：`X-Review-Token: local-review-demo`。可以用环境变量 `REVIEW_API_TOKEN` 改成本机自己的值。

`Session ID` 很重要。消费者提交高风险售后申请后，审核台会按同一个 session 读取 case 草稿、证据、政策依据和 Verifier 结果。

## 3. 推荐测试路径

### 3.1 订单查询

输入：

```text
帮我查一下订单 203096f03d82e0dffbc41ebc2e2bcfb7 的状态
```

预期结果：

- 返回订单状态、客户州、类目、下单时间、预计送达、实际送达、延迟天数、支付金额和评价分。
- sources 通常为空，因为这是结构化订单事实查询，不是文档 RAG。
- trace 中会看到 `plan_tasks` 和 `get_order_status`。

### 3.2 政策 RAG

输入：

```text
退款补偿能不能直接承诺？
```

预期结果：

- 返回基于政策知识库的回答。
- sources 会出现相关政策章节，例如补偿边界或人工审核类章节。
- trace 中会看到 `search_policy_knowledge`。

### 3.3 订单归属保护

输入：

```text
帮我查一下订单 00000000000000000000000000000000 的状态
```

预期结果：

- 消费者入口会拒绝查询不属于当前账号的订单。
- trace 中记录 `unauthorized_order_access`，但会对用户消息和订单号做脱敏。
- 内部审核台或企业后台可以在具备权限的情况下查看完整 case 证据包。

### 3.4 内部风险画像

输入：

```text
health beauty 类目有什么运营风险？
```

预期结果：

- 内部 `/chat` 可返回 `health_beauty` 类目的订单量、延迟率、低分率、取消率等风险摘要。
- 消费者入口 `/customer/chat` 默认没有运营分析权限，真实部署中这类问题属于商家/运营后台。
- 这类画像主要用于审核台排序、case 风险解释和运营复盘，不是消费者自助主链路。

### 3.5 多意图 + 人工审核

先输入：

```text
查订单 203096f03d82e0dffbc41ebc2e2bcfb7 状态，并且说明退款政策，然后申请退款
```

预期结果：

- Agent 会先查订单。
- 再检索退款政策。
- 最后生成售后申请。
- 因为最后一步是副作用动作，会停在 HITL，消费者侧显示“已提交审核”。

然后保持同一个 `Session ID`，在右侧点击：

```text
审核包 -> 审核通过
```

预期结果：

- 系统恢复上一次中断的 LangGraph 状态。
- 以售后人员权限执行幂等副作用工具。
- 返回类似 `CASE-...` 的工单结果。

### 3.6 退款申请

输入：

```text
给订单 203096f03d82e0dffbc41ebc2e2bcfb7 申请退款
```

预期结果：

- Agent 生成退款/补偿申请草稿。
- 消费者侧提示已进入审核。
- 右侧审核台点击 `审核通过` 后，返回 `REFUND-...`。

### 3.7 投诉升级

输入：

```text
我要投诉升级订单 203096f03d82e0dffbc41ebc2e2bcfb7 的延迟问题
```

预期结果：

- Agent 会构造 `complaint_escalation` 类型的 `AfterSalesCase`。
- 消费者侧只显示安全回复。
- 审核台可以看到证据、政策依据、Verifier 结果和客户回复草稿；通过后返回 `COMP-...`。

### 3.8 参数澄清

输入：

```text
帮我查一下订单状态
```

预期结果：

- 系统不会反复调用工具。
- 会提示你提供完整 32 位订单号。

输入多个订单号时，系统会提示你明确要处理哪一个。

### 3.8 越界请求

输入：

```text
帮我写一个操作系统内核
```

预期结果：

- 输入风控拒绝。
- 返回只支持电商客服、售后 case 和订单履约任务的提示。

## 4. Trace 怎么看

在网页右侧点击：

```text
刷新 summary
```

你会看到：

- total：总请求数
- status_counts：成功、输入拒绝、输出拒绝等状态分布
- route_intent_counts：不同业务入口的分布
- avg_latency_ms / p95_latency_ms：平均和 p95 延迟

点击：

```text
Case Metrics
```

你会看到：

- `auto_resolution_rate`：自动拒绝、澄清或低风险处理的 case 占比
- `hitl_rate`：需要人工确认/人工复核的 case 占比
- `wrong_write_blocked`：错误写动作被拦截次数
- `policy_hit_rate`：case 是否命中政策/FAQ/商家规则
- `tool_error_rate`：工具调用失败占比
- `p95_latency_ms`：售后 case 链路 p95 延迟
- `cost_per_case`：已有成本样本的平均单 case 成本

点击：

```text
查看当前 session trace
```

你会看到当前 session 的请求记录，其中 `trajectory_json` 包含：

- `plan_tasks`
- `select_next_task`
- `extract_slots`
- `retrieve_context`
- `execute_read_task` 或 `build_after_sales_case`
- 检索或工具名称
- HITL 状态
- 最终确认结果

这就是 trace replay：不只看最终答案，还能复盘 Agent 每一步为什么这么做。

## 5. 离线模式和真实模型模式

### 离线模式

不配置 key 时直接启动即可。优点是零成本、稳定、适合本地 demo 和 CI 回归。缺点是回答语言质量只是模板/fallback 水平。

### DeepSeek 示例

创建 `.env`：

```text
OPENAI_API_KEY=你的 deepseek key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
```

也可以使用 AIHubMix：

```powershell
$env:AIHUBMIX_API_KEY="你的 key"
$env:OPENAI_MODEL="平台上的低价 chat 模型"
```

重新启动服务后，规划、抽槽、政策回答、类目分析回答会走真实模型。

## 6. 评测命令

完整分层指标报告：

```bash
.\.venv\Scripts\python.exe -m evaluation.agent_metrics_report
```

输出文件：

```text
evaluation/agent_metrics_report.md
```

常用验证：

```bash
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app evaluation scripts tests
```
