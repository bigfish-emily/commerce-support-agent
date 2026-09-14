# 使用手册

## 产品入口

启动服务后打开 `http://127.0.0.1:8000/customer`。选择订单后即可询问物流、退款政策，或提交退款、取消订单、改地址、发票和投诉等售后请求。演示账号仅能访问样例订单 `203096f03d82e0dffbc41ebc2e2bcfb7`。

涉及业务写入的请求会生成申请：客户立即看到受理状态，后台审核完成后页面会自动刷新进度。应用没有真实支付或退款能力，所有写动作仅作用于本地业务服务。

打开 `http://127.0.0.1:8000/review` 进入审核台。默认本地凭证为 `local-review-demo`，可用 `REVIEW_API_TOKEN` 覆盖。审核人员可查看客户对话和订单事实、接手会话、使用可编辑的 AI 回复草稿，以及批准或驳回待审核申请。

`/technical` 保留给工程检查；客户页不展示 trace、工具名称、风控标签或内部政策字段。

## 启动与模型

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload
```

未配置 key 时，应用使用本地 fallback，适合测试流程。配置 OpenAI-compatible key 后会启用真实模型：

```text
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=your-chat-model
```

部分兼容网关不稳定支持原生 structured output；本项目会改用“JSON 文本 + Pydantic 校验”的兼容路径。

## 推荐演示

1. 在客户页输入“包裹晚到了，想申请退款”。系统查询订单与政策，随后显示“申请已受理，等待审核”。
2. 打开审核台，选择该会话，阅读订单事实和申请依据；点击“接手会话”后，客户新消息直接进入人工会话。
3. 点击“生成回复建议”，编辑后发送；或批准/驳回待审核申请。客户页会轮询并显示最新结果。

内部 trace、case 指标与 API 文档位于 `/observability/*` 和 `/docs`，审核访问需要 review token。
