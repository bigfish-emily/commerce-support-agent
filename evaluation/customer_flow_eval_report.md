# Customer Flow Eval Report

customer_flow_eval pass=8/8 p95=19.80ms mode=offline_workflow model=gpt-4o-mini

| case | passed | latency_ms | key checks | answer preview |
|---|---:|---:|---|---|
| customer_order_status | True | 16.69 | ok | [订单查询] 订单 203096f03d82e0dffbc41ebc2e2bcfb7 当前状态：delivered。 客户州：SP；类目：health_beauty。 下单时间：2017-09-18 14:31:30；预计送达：2017-09-28 00:00:00；实际送达：2017-10-09 22:23:46；延迟 11 天。 支付金额：118.86；评价分：2。 |
| customer_policy_boundary | True | 13.1 | ok | [政策问答] 根据已命中的政策章节：Compensation Boundary Policy、Refund Status FAQ、Delivery Delay Policy。 政策要点：Compensation Boundary Policy Compensation, coupons, refunds, replacements, manua；Refund Status FAQ Customers often ask whether  |
| customer_refund_to_review | True | 13.65 | ok | [售后升级] 已为订单 203096f0 生成售后处理单：提交退款/补偿申请，并提交给售后人员审核。 您好，订单 203096f0 当前状态为 delivered，支付金额 118.86，类目 health_beauty。系统记录显示配送延迟 11 天。评价分为 2。我可以先为您提交人工审核/售后处理申请，在确认前不会承诺退款或补偿结果。处理依据：订单延迟 11 天，命中延迟售后核查条件。 该类请求涉及退款、取消、改地址或投诉升级，系统 |
| reviewer_approves_refund | True | 12.31 | ok | [售后升级] 已为订单 203096f0 生成售后处理单：提交退款/补偿申请，并提交给售后人员审核。 您好，订单 203096f0 当前状态为 delivered，支付金额 118.86，类目 health_beauty。系统记录显示配送延迟 11 天。评价分为 2。我可以先为您提交人工审核/售后处理申请，在确认前不会承诺退款或补偿结果。处理依据：订单延迟 11 天，命中延迟售后核查条件。 该类请求涉及退款、取消、改地址或投诉升级，系统 |
| customer_cannot_self_confirm | True | 12.05 | ok | [售后升级] 已为订单 203096f0 生成售后处理单：提交退款/补偿申请，并提交给售后人员审核。 您好，订单 203096f0 当前状态为 delivered，支付金额 118.86，类目 health_beauty。系统记录显示配送延迟 11 天。评价分为 2。我可以先为您提交人工审核/售后处理申请，在确认前不会承诺退款或补偿结果。处理依据：订单延迟 11 天，命中延迟售后核查条件。 该类请求涉及退款、取消、改地址或投诉升级，系统 |
| unauthorized_order_access | True | 5.29 | ok | 为了保护订单隐私，我只能处理当前账号名下的订单。请确认登录账号或订单号后再试。 |
| customer_multi_intent_read_then_refund | True | 19.8 | ok | [订单查询] 订单 203096f03d82e0dffbc41ebc2e2bcfb7 当前状态：delivered。 客户州：SP；类目：health_beauty。 下单时间：2017-09-18 14:31:30；预计送达：2017-09-28 00:00:00；实际送达：2017-10-09 22:23:46；延迟 11 天。 支付金额：118.86；评价分：2。  [政策问答] 根据已命中的政策章节：Compensation B |
| off_topic_rejected | True | 5.87 | ok | I can only help with e-commerce after-sales support: order status, policy questions, and refund/cancellation/address/invoice/complaint requests. |

This eval runs the public product API boundary: `/customer/chat`, `/review/sessions`, review approval, unauthorized order access, and customer self-confirmation blocking.
