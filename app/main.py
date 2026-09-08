import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph
from langgraph.types import Command

from app.agent.state import AgentState
from app.config.di import agent_graph_builder, guardrail, runtime_status, runtime_store
from app.logger import format_state, setup_logger
from app.models import (
    CaseMetricsResponse,
    ChatRequest,
    ChatResponse,
    ReviewActionRequest,
    ReviewSessionResponse,
    RuntimeStatusResponse,
    TraceReplayResponse,
    TraceSummaryResponse,
)
from app.trace_store import case_metrics, list_session_traces, record_trace, trace_summary

agent: StateGraph | None = None
logger = setup_logger("agent")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ORDER_ID_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")
DEFAULT_CUSTOMER_ORDER_IDS: dict[str, set[str]] = {
    "demo-customer": {"203096f03d82e0dffbc41ebc2e2bcfb7"},
    "customer-demo": {"203096f03d82e0dffbc41ebc2e2bcfb7"},
    "customer-smoke": {"203096f03d82e0dffbc41ebc2e2bcfb7"},
}

DEFAULT_ROLE_SCOPES: dict[str, list[str]] = {
    "customer": [
        "orders:read",
        "policy:read",
        "support_examples:read",
        "after_sales:assess",
        "after_sales:draft",
    ],
    "support_agent": [
        "orders:read",
        "analytics:read",
        "policy:read",
        "support_examples:read",
        "after_sales:assess",
        "after_sales:draft",
    ],
    "ops_manager": [
        "orders:read",
        "analytics:read",
        "policy:read",
        "support_examples:read",
        "after_sales:ops_report",
    ],
    "after_sales_operator": [
        "orders:read",
        "analytics:read",
        "policy:read",
        "support_examples:read",
        "after_sales:assess",
        "after_sales:draft",
        "after_sales:write",
    ],
    "admin": ["*"],
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: manage AsyncSqliteSaver lifecycle."""
    global agent
    async with AsyncSqliteSaver.from_conn_string("data/checkpoints.db") as checkpointer:
        agent = agent_graph_builder.build(checkpointer=checkpointer)
        logger.info("Checkpointer ready (AsyncSqliteSaver: data/checkpoints.db)")
        yield
    logger.info("Checkpointer closed")


app = FastAPI(title="Olist Marketplace Support Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="static")


@app.get("/", response_class=FileResponse)
def app_index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/customer", response_class=FileResponse)
def customer_app() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "customer" / "index.html")


@app.get("/review", response_class=FileResponse)
def review_app() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "review" / "index.html")


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Internal/debug entrypoint. Product traffic should use /customer/chat."""
    session_id: str = request.session_id or str(uuid.uuid4())
    config: dict = {"configurable": {"thread_id": session_id}}
    auth_scopes = _resolve_auth_scopes(request.role, request.auth_scopes)

    t_start = time.perf_counter()
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("REQUEST | session=%s | message=%s", session_id, request.message[:100])

    rate_limit = await _check_rate_limit(request)
    if rate_limit is not None and not rate_limit.allowed:
        result = {
            "route_intent": "rate_limited",
            "final_answer": (
                "当前请求过于频繁，请稍后再试。"
                f"预计 {rate_limit.reset_after_seconds} 秒后恢复。"
            ),
        }
        record_trace(
            session_id=session_id,
            user_message=request.message,
            result=result,
            latency_ms=(time.perf_counter() - t_start) * 1000,
            status="rate_limited",
        )
        return ChatResponse(answer=str(result["final_answer"]), session_id=session_id, sources=[])

    # Check if there's a pending interrupt for this session
    snapshot = await agent.aget_state(config)
    has_interrupt = bool(snapshot.next)
    history_messages = snapshot.values.get("messages") if snapshot.values else None

    # Input guardrail with conversation history for context
    t0 = time.perf_counter()
    input_check = await guardrail.check_input(request.message, history=history_messages)
    logger.info(
        "GUARD INPUT  [%.2fs] | on_topic=%s | reason=%s",
        time.perf_counter() - t0,
        input_check.on_topic,
        input_check.reason,
    )

    if not input_check.on_topic:
        logger.info("DONE [%.2fs] | rejected by input guardrail", time.perf_counter() - t_start)
        rejected_result = {
            "route_intent": "input_guard",
            "final_answer": (
                "I can only help with e-commerce after-sales support: order status, "
                "policy questions, and refund/cancellation/address/invoice/complaint requests."
            ),
        }
        record_trace(
            session_id=session_id,
            user_message=request.message,
            result=rejected_result,
            latency_ms=(time.perf_counter() - t_start) * 1000,
            status="input_guard_rejected",
        )
        return ChatResponse(
            answer=rejected_result["final_answer"],
            session_id=session_id,
            sources=[],
        )

    # Invoke graph - resume from interrupt or start new run
    t_graph = time.perf_counter()
    if has_interrupt:
        if await _pending_confirmation_expired(session_id, snapshot.values):
            logger.info("GRAPH RESUME | pending side effect expired, canceling")
            result = await agent.ainvoke(Command(resume="__hitl_timeout__"), config)
            record_trace(
                session_id=session_id,
                user_message=request.message,
                result=result,
                latency_ms=(time.perf_counter() - t_start) * 1000,
                status="hitl_timeout_canceled",
            )
            return ChatResponse(
                answer=str(result["final_answer"]),
                session_id=session_id,
                sources=_response_sources(result),
            )
        if _is_customer_request(request):
            pending_result = _pending_confirmation_response(snapshot.values, customer_view=True)
            record_trace(
                session_id=session_id,
                user_message=request.message,
                result=pending_result,
                latency_ms=(time.perf_counter() - t_start) * 1000,
                status="pending_staff_review",
            )
            return ChatResponse(
                answer=str(pending_result["final_answer"]),
                session_id=session_id,
                sources=[],
            )
        if not _is_confirmation_reply(request.message):
            pending_result = _pending_confirmation_response(snapshot.values)
            record_trace(
                session_id=session_id,
                user_message=request.message,
                result=pending_result,
                latency_ms=(time.perf_counter() - t_start) * 1000,
                status="pending_confirmation",
            )
            return ChatResponse(
                answer=str(pending_result["final_answer"]),
                session_id=session_id,
                sources=[],
            )
        logger.info("GRAPH RESUME | resuming from interrupt")
        result = await agent.ainvoke(Command(resume=request.message), config)
    else:
        initial_state: AgentState = {
            "session_id": session_id,
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "role": request.role,
            "auth_scopes": auth_scopes,
            "channel": request.channel,
            "messages": [{"role": "user", "content": request.message}],
            "route_intent": "",
            "task_plan": [],
            "completed_tasks": [],
            "trajectory_events": [],
            "artifacts": {},
            "retrieved_insights": [],
            "retrieved_policy": [],
            "retrieved_support_docs": [],
            "after_sales_cases": [],
            "final_answer": "",
        }
        logger.info("GRAPH INPUT%s", format_state(initial_state))
        result = await agent.ainvoke(initial_state, config)

    logger.info(
        "GRAPH OUTPUT [%.2fs]%s",
        time.perf_counter() - t_graph,
        format_state(result),
    )

    # Output guardrail - LLM validates the response before returning to user
    t1 = time.perf_counter()
    output_check = await guardrail.check_output(result.get("final_answer", ""))
    logger.info(
        "GUARD OUTPUT [%.2fs] | valid=%s | reason=%s",
        time.perf_counter() - t1,
        output_check.valid,
        output_check.reason,
    )

    if not output_check.valid:
        logger.warning("GUARD OUTPUT rejected response: %s", output_check.reason)
        record_trace(
            session_id=session_id,
            user_message=request.message,
            result=result,
            latency_ms=(time.perf_counter() - t_start) * 1000,
            status="output_guard_rejected",
        )
        return ChatResponse(
            answer="I'm sorry, I ran into an issue processing your request. Could you try again or rephrase?",
            session_id=session_id,
            sources=_response_sources(result),
        )

    logger.info("DONE [%.2fs] | total request time", time.perf_counter() - t_start)

    record_trace(
        session_id=session_id,
        user_message=request.message,
        result=result,
        latency_ms=(time.perf_counter() - t_start) * 1000,
        status="ok",
    )

    return ChatResponse(
        answer=result["final_answer"],
        session_id=session_id,
        sources=_response_sources(result),
    )


@app.post("/customer/chat", response_model=ChatResponse)
async def customer_chat(request: ChatRequest) -> ChatResponse:
    """Customer-facing self-service entrypoint with minimal read/draft scopes."""
    customer_user_id = request.user_id if request.user_id != "demo-user" else "demo-customer"
    ownership_denial = _customer_order_access_denied(request, customer_user_id=customer_user_id)
    if ownership_denial is not None:
        return ownership_denial
    customer_request = request.model_copy(
        update={
            "role": "customer",
            "channel": "customer_self_service",
            "auth_scopes": _resolve_auth_scopes("customer", request.auth_scopes),
            "user_id": customer_user_id,
        }
    )
    return await chat(customer_request)


@app.get("/review/sessions/{session_id}", response_model=ReviewSessionResponse)
async def get_review_session(
    session_id: str,
    role: str = Query(default="after_sales_operator"),
    auth_scopes: list[str] | None = Query(default=None),
    x_review_token: str | None = Header(default=None, alias="X-Review-Token"),
) -> ReviewSessionResponse:
    """Return the staff-facing HITL review packet for a paused case."""
    _authorize_review(role, auth_scopes, x_review_token)
    config: dict = {"configurable": {"thread_id": session_id}}
    snapshot = await agent.aget_state(config)
    values = dict(snapshot.values or {})
    return _review_session_response(session_id, bool(snapshot.next), values)


@app.post("/review/sessions/{session_id}/approve", response_model=ChatResponse)
async def approve_review_session(
    session_id: str,
    request: ReviewActionRequest | None = None,
    x_review_token: str | None = Header(default=None, alias="X-Review-Token"),
) -> ChatResponse:
    """Approve a paused side-effect case as an after-sales operator."""
    role = request.role if request else "after_sales_operator"
    auth_scopes = request.auth_scopes if request else None
    _authorize_review(role, auth_scopes, x_review_token)
    return await _resume_review_session(
        session_id,
        resume_value="__review_approve__",
        status="review_approved",
        reviewer_id=(request.reviewer_id if request else "demo-reviewer"),
    )


@app.post("/review/sessions/{session_id}/reject", response_model=ChatResponse)
async def reject_review_session(
    session_id: str,
    request: ReviewActionRequest | None = None,
    x_review_token: str | None = Header(default=None, alias="X-Review-Token"),
) -> ChatResponse:
    """Reject a paused side-effect case and clear the HITL checkpoint."""
    role = request.role if request else "after_sales_operator"
    auth_scopes = request.auth_scopes if request else None
    _authorize_review(role, auth_scopes, x_review_token)
    return await _resume_review_session(
        session_id,
        resume_value="no",
        status="review_rejected",
        reviewer_id=(request.reviewer_id if request else "demo-reviewer"),
    )


def _response_sources(result: dict) -> list[str]:
    artifacts = result.get("artifacts", {})
    if isinstance(artifacts, dict):
        sources = artifacts.get("insight_sources", []) or artifacts.get("policy_sources", [])
        return [str(source) for source in sources]
    insights = result.get("retrieved_insights", [])
    if insights and isinstance(insights[0], dict):
        return [str(item.get("name", "")) for item in insights]
    policies = result.get("retrieved_policy", [])
    if policies and isinstance(policies[0], dict):
        return [str(item.get("section_title", "")) for item in policies]
    return [str(source) for source in (insights or policies)]


def _resolve_auth_scopes(role: str, provided_scopes: list[str] | None) -> list[str]:
    if provided_scopes is not None:
        if role == "customer":
            allowed = set(DEFAULT_ROLE_SCOPES["customer"])
            return [scope for scope in provided_scopes if scope in allowed]
        return list(provided_scopes)
    return list(DEFAULT_ROLE_SCOPES.get(role, []))


def _customer_order_access_denied(
    request: ChatRequest,
    *,
    customer_user_id: str,
) -> ChatResponse | None:
    requested_order_ids = _extract_order_ids(request.message)
    if not requested_order_ids:
        return None
    allowed_order_ids = _allowed_customer_order_ids(customer_user_id)
    unauthorized = [order_id for order_id in requested_order_ids if order_id not in allowed_order_ids]
    if not unauthorized:
        return None

    session_id = request.session_id or str(uuid.uuid4())
    answer = (
        "为了保护订单隐私，我只能处理当前账号名下的订单。"
        "请确认登录账号或订单号后再试。"
    )
    record_trace(
        session_id=session_id,
        user_message=request.message,
        result={
            "route_intent": "auth_guard",
            "final_answer": answer,
            "trajectory_events": [
                {
                    "node": "customer_order_access_guard",
                    "intent": "auth_guard",
                    "status": "blocked_unauthorized_order",
                    "details": {
                        "user_id": customer_user_id,
                        "requested_count": len(requested_order_ids),
                    },
                }
            ],
        },
        latency_ms=0,
        status="unauthorized_order_access",
    )
    return ChatResponse(answer=answer, session_id=session_id, sources=[])


def _extract_order_ids(text: str) -> list[str]:
    return [match.group(0).lower() for match in ORDER_ID_RE.finditer(text)]


def _allowed_customer_order_ids(user_id: str) -> set[str]:
    allowed = set(DEFAULT_CUSTOMER_ORDER_IDS.get(user_id, set()))
    configured = os.environ.get("DEMO_CUSTOMER_ORDER_IDS", "")
    allowed.update(order_id.strip().lower() for order_id in configured.split(",") if order_id.strip())
    return allowed


def _authorize_review(
    role: str,
    provided_scopes: list[str] | None,
    review_token: str | None,
) -> None:
    expected_token = os.environ.get("REVIEW_API_TOKEN", "local-review-demo")
    if review_token != expected_token:
        raise HTTPException(status_code=403, detail="review_token_required")
    scopes = _resolve_auth_scopes(role, provided_scopes)
    if role not in {"after_sales_operator", "admin"}:
        raise HTTPException(status_code=403, detail="review_role_required")
    if "*" not in scopes and "after_sales:write" not in scopes:
        raise HTTPException(status_code=403, detail="after_sales_write_scope_required")


def _is_confirmation_reply(message: str) -> bool:
    normalized = message.lower().strip()
    return normalized in {
        "yes",
        "yeah",
        "y",
        "confirm",
        "ok",
        "okay",
        "no",
        "n",
        "cancel",
        "确认",
        "创建",
        "升级",
        "取消",
        "不用",
        "不要",
        "算了",
        "放弃",
        "否",
    }


def _pending_confirmation_response(state: dict, *, customer_view: bool = False) -> dict[str, object]:
    pending = state.get("pending_side_effect", {}) if state else {}
    action_type = pending.get("type", "side_effect")
    expires_at = pending.get("expires_at")
    expires_hint = ""
    if isinstance(expires_at, (int, float)):
        remaining = max(int(expires_at - time.time()), 0)
        expires_hint = f"\n该确认将在 {remaining} 秒后超时自动取消。"
    if customer_view:
        final_answer = (
            f"你的售后申请正在等待工作人员审核：{action_type}。\n"
            "涉及退款、取消、改地址或投诉升级的动作需要由售后人员在审核台确认；"
            "你无需回复 yes/no 来触发执行。"
            f"{expires_hint}"
        )
    else:
        final_answer = (
            f"当前 session 还有一个待确认的副作用动作：{action_type}。\n"
            "请先回复 yes/确认 执行，或 no/取消 放弃；如果要开始新任务，请换一个 Session ID。"
            f"{expires_hint}"
        )
    return {
        "route_intent": "pending_confirmation",
        "final_answer": final_answer,
        "trajectory_events": [
            {
                "node": "chat",
                "intent": "pending_confirmation",
                "status": "blocked_new_message",
                "details": {"action_type": action_type},
            }
        ],
    }


async def _check_rate_limit(request: ChatRequest):
    limit = _env_int("AGENT_RATE_LIMIT_PER_MINUTE", 1000)
    if limit <= 0:
        return None
    key = f"tenant:{request.tenant_id}:user:{request.user_id}:minute"
    return await runtime_store.check_rate_limit(key, limit=limit, window_seconds=60)


async def _pending_confirmation_expired(session_id: str, state: dict) -> bool:
    pending = state.get("pending_side_effect", {}) if state else {}
    if not pending.get("requires_confirmation"):
        return False
    if os.environ.get("RUNTIME_STORE_STRICT_HITL_TTL", "1").strip().lower() not in {"0", "false", "no"}:
        runtime_pending = await runtime_store.get_pending_confirmation(session_id)
        if runtime_pending is None:
            return True
    expires_at = pending.get("expires_at")
    return isinstance(expires_at, (int, float)) and time.time() >= float(expires_at)


async def _resume_review_session(
    session_id: str,
    *,
    resume_value: str,
    status: str,
    reviewer_id: str,
) -> ChatResponse:
    config: dict = {"configurable": {"thread_id": session_id}}
    snapshot = await agent.aget_state(config)
    if not snapshot.next:
        return ChatResponse(answer="该 session 当前没有待审核售后动作。", session_id=session_id, sources=[])

    t_start = time.perf_counter()
    if await _pending_confirmation_expired(session_id, snapshot.values):
        result = await agent.ainvoke(Command(resume="__hitl_timeout__"), config)
        status = "hitl_timeout_canceled"
    else:
        result = await agent.ainvoke(Command(resume=resume_value), config)
    record_trace(
        session_id=session_id,
        user_message=f"[staff_review:{status}:{reviewer_id}]",
        result=result,
        latency_ms=(time.perf_counter() - t_start) * 1000,
        status=status,
    )
    return ChatResponse(
        answer=str(result.get("final_answer", "")),
        session_id=session_id,
        sources=_response_sources(result),
    )


def _review_session_response(session_id: str, has_pending: bool, state: dict) -> ReviewSessionResponse:
    draft = dict(state.get("escalation_draft", {}) or {})
    pending = dict(state.get("pending_side_effect", {}) or {})
    cases = list(state.get("after_sales_cases", []) or [])
    completed = list(state.get("completed_tasks", []) or [])
    events = list(state.get("trajectory_events", []) or [])
    action_type = draft.get("action_type") or pending.get("type") or ""
    order_id = str(draft.get("order_id") or "")
    summary = "当前没有待审核售后 case。"
    if has_pending and action_type:
        summary = f"待审核动作：{action_type}；订单：{order_id[:8] if order_id else 'unknown'}。"
    return ReviewSessionResponse(
        session_id=session_id,
        has_pending=has_pending,
        pending_side_effect=pending,
        escalation_draft=draft,
        after_sales_cases=cases,
        completed_tasks=completed,
        trajectory_events=events[-20:],
        customer_safe_summary=summary,
    )


def _is_customer_request(request: ChatRequest) -> bool:
    return request.role == "customer" or request.channel == "customer_self_service"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@app.get("/observability/summary", response_model=TraceSummaryResponse)
def observability_summary() -> TraceSummaryResponse:
    return TraceSummaryResponse(**trace_summary())


@app.get("/observability/case-metrics", response_model=CaseMetricsResponse)
def observability_case_metrics() -> CaseMetricsResponse:
    return CaseMetricsResponse(**case_metrics())


@app.get("/observability/traces/{session_id}", response_model=TraceReplayResponse)
def replay_session_traces(
    session_id: str,
    limit: int = 20,
    role: str = Query(default="after_sales_operator"),
    auth_scopes: list[str] | None = Query(default=None),
    x_review_token: str | None = Header(default=None, alias="X-Review-Token"),
) -> TraceReplayResponse:
    _authorize_review(role, auth_scopes, x_review_token)
    return TraceReplayResponse(session_id=session_id, traces=list_session_traces(session_id, limit=limit))


@app.get("/runtime/status", response_model=RuntimeStatusResponse)
def get_runtime_status() -> RuntimeStatusResponse:
    return RuntimeStatusResponse(**runtime_status)
