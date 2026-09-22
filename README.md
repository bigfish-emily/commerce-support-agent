# commerce-support-agent

A public-data sandbox for an e-commerce **order assistant** and asynchronous after-sales review.

The product starts from the customer's own order context. Standard actions such as tracking a package or starting a refund request are rendered as native order controls; the Agent handles free-form, ambiguous, multi-step requests that need to find an order, retrieve policy evidence, assemble an after-sales case, or coordinate a handoff. Material-risk actions enter a staff review console. Approved actions append a sandbox event and update the customer-visible order projection.

> This repository models an after-sales workflow on historical public data. It does not connect to a real payment, logistics, or OMS provider and never moves money.

## What It Demonstrates

- **Customer path**: a compact owned-order overview, native order controls, free-form intent planning, ownership checks, order/policy retrieval, decision and verifier gates, and clear customer-facing replies.
- **Review path**: a durable review packet holds order facts, policy references, decision, verifier output, and customer request. An operator can approve, reject, or receive an appeal after a timeout/rejection.
- **Durable business state**: Olist facts are the immutable initial snapshot. Approved actions append a case event and update a SQLite order projection, so later order queries expose `refund requested`, `address change requested`, or `canceled` status.
- **Governed tools**: Pydantic input schemas, role/scope checks, customer ownership guards, tenant-aware read cache, timeout/retry/fallback, redacted audit events, Redis-compatible locks, and SQLite idempotency records.
- **Workflow control**: native page actions bypass LLM planning but enter the same governed workflow. LangGraph checkpointing pauses high-risk cases for staff review and resumes the original graph after a decision.
- **Enterprise boundary**: local MCP tools plus stdio/remote client adapters demonstrate how an OMS, CRM, payment, or invoice service can be integrated behind a typed tool contract.

## Product Flow

```mermaid
flowchart LR
    Customer[Customer] --> Context[My orders / order detail]
    Context -->|native control or free text| Chat[/customer/chat]
    Chat --> Graph[LangGraph case workflow]
    Graph --> Facts[Owned order facts]
    Graph --> Policy[Policy evidence]
    Facts --> Decide[Decision + verifier]
    Policy --> Decide
    Decide -->|read / clarify / reject| Reply[Customer reply]
    Decide -->|review required| Case[Persistent case packet]
    Case --> Console[/review]
    Console -->|approve| Write[Governed write tool]
    Write --> Event[SQLite event + order projection]
    Event --> Reply
```

## Data Contract

| Asset | Role in the sandbox |
|---|---|
| Olist Brazilian E-Commerce dataset | Historical initial order facts. The local derived index contains 98,666 order facts; 518 representative records are checked in as lightweight demo/evaluation seeds. |
| Demo Marketplace Policy Pack | Versioned local policy/FAQ/merchant-rule documents used as explicit policy evidence. It is intentionally labeled as a sandbox policy, not a real company's policy. |
| SQLite case/event store | Cases, review status, appeals, idempotency records, append-only after-sales events, and materialized order projections. |

Public support corpora and external benchmarks live under `experiments/` or external checkouts. They do not share customers, orders, or policies with the Olist sandbox, so their scores are not presented as customer-product results.

## Run Locally

```bash
uv sync --extra dev
uv run python scripts/download_olist_data.py
uv run python scripts/build_olist_dataset.py
uv run uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/demo
http://127.0.0.1:8000/customer
http://127.0.0.1:8000/review
```

Start with `/demo` for a three-minute guided walkthrough. It resets a
disposable local sandbox for each scenario and shows a delivery-delay refund
handoff, a self-service delivery query, and a blocked delivered-order address
change. It is enabled only when `DEMO_MODE` is not set to `0`.

The customer page defaults to the local `demo-customer` actor. `X-Demo-Customer` is a test-only selector; a production BFF must resolve an authenticated principal and inject the allowed-order set server-side. The local review credential is `local-review-demo`; set `REVIEW_API_TOKEN` before exposing the service. Use Redis by setting `RUNTIME_STORE_BACKEND=redis` and `REDIS_URL`.

## Evaluation

The project separates product case-resolution evaluation from retrieval and
external tool-use experiments. The primary in-domain protocol holds the model,
customer message, Olist snapshot, policy pack, tool surface, and initial
SQLite state fixed. It compares a thin native function-calling loop with the
actual LangGraph workflow.

On the first run of a separately authored 24-case challenge split with
`deepseek-chat`, the thin loop achieved **7/24 Case Resolution Success
(29.17%)** and the workflow achieved **22/24 (91.67%)**. The workflow produced
zero unsafe writes, versus one unsafe write in the baseline's 15 write-risk
cases. The two workflow failures were retained as bad-case regressions: a
colloquial cancellation-status question and a delivered-order address request.
The protocol, definitions, failure analysis, and limits are documented below.

- [Evaluation design and ablation plan](docs/EVALUATION.md)
- [Architecture diagrams](docs/ARCHITECTURE_DIAGRAMS.md)
- [User manual](docs/USER_MANUAL.md)

## Scope

This is a reference implementation for an Olist-backed after-sales sandbox. A production deployment would replace its fixed demo identity, public historical facts, policy pack, and local stores with IAM, OMS, CRM, payment, logistics, notification, and observability services.

## License

MIT
