# E-Commerce Support Agent 使用手册

这是一套面向电商客服与售后 case resolution 的 Agent。你可以把自己当成客服、售后主管或消费者，通过网页输入一句自然语言，让 Agent 完成订单查询、政策问答、类目风险分析、退款/取消/改地址等售后请求判断，并在需要改动业务状态时进入 HITL 确认。

## 1. 启动方式

在项目目录执行：

```bash
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000/
```

如果只想看 API 文档，打开：

```text
http://127.0.0.1:8000/docs
```

默认不配置 API key 也能测试，系统会进入离线模式：任务规划、抽取、风控和回答生成使用本地 fallback。配置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 后会启用真实 OpenAI-compatible 模型，例如 DeepSeek。

## 2. 网页上怎么操作

打开首页后，你会看到三栏：

- 左侧：示例任务按钮，点击后会把测试句子填入输入框。
- 中间：对话测试区，填写 `Session ID` 和用户输入，点击发送。
- 右侧：Trace 与可观测性，可以查看整体 summary 或当前 session 的执行轨迹。
- 右侧 `Case Metrics`：按售后 case 聚合自动解决率、HITL 占比、错误写动作拦截、政策命中率、工具错误率、p95 延迟和单 case 成本。

`Session ID` 很重要。售后升级、退款、取消订单这类 HITL 流程需要同一个 session 才能继续确认。

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

### 3.3 类目运营风险

输入：

```text
health beauty 类目有什么运营风险？
```

预期结果：

- 返回 `health_beauty` 类目的订单量、延迟率、低分率、取消率等运营风险摘要。
- 这条链路会测试 query rewriting：用户写 `health beauty`，系统能命中真实类目 `health_beauty`。

### 3.4 多意图 + HITL

先输入：

```text
查订单 203096f03d82e0dffbc41ebc2e2bcfb7 状态，并且说明退款政策，然后生成售后升级话术
```

预期结果：

- Agent 会先查订单。
- 再检索退款政策。
- 最后生成售后升级草稿。
- 因为最后一步是副作用动作，会停在 HITL，询问是否确认执行。

然后保持同一个 `Session ID`，输入：

```text
yes
```

预期结果：

- 系统恢复上一次中断的 LangGraph 状态。
- 执行幂等副作用工具。
- 返回类似 `CASE-...` 的工单结果。

### 3.5 退款申请

输入：

```text
给订单 203096f03d82e0dffbc41ebc2e2bcfb7 申请退款
```

预期结果：

- Agent 生成退款/补偿申请草稿。
- 停在 HITL。
- 同 session 回复 `确认` 后，返回 `REFUND-...`。

### 3.6 投诉升级

输入：

```text
我要投诉升级订单 203096f03d82e0dffbc41ebc2e2bcfb7 的延迟问题
```

预期结果：

- Agent 会构造 `complaint_escalation` 类型的 `AfterSalesCase`。
- 决策层会给出证据、政策依据、Verifier 结果和客户回复草稿。
- 因为这是写入 CRM/投诉队列的副作用动作，会进入 HITL；确认后返回 `COMP-...`。

### 3.7 参数澄清

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
