import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph
from langgraph.types import Command

from app.agent.state import AgentState
from app.config.di import agent_graph_builder, guardrail, runtime_status, runtime_store
from app.logger import format_state, setup_logger
from app.models import (
    ChatRequest,
    ChatResponse,
    RuntimeStatusResponse,
    TraceReplayResponse,
    TraceSummaryResponse,
)
from app.trace_store import list_session_traces, record_trace, trace_summary
from app.web_console import WEB_CONSOLE_HTML

agent: StateGraph | None = None
logger = setup_logger("agent")


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


@app.get("/", response_class=HTMLResponse)
def web_console() -> HTMLResponse:
    return HTMLResponse(WEB_CONSOLE_HTML)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    session_id: str = request.session_id or str(uuid.uuid4())
    config: dict = {"configurable": {"thread_id": session_id}}

    t_start = time.perf_counter()
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("REQUEST | session=%s | message=%s", session_id, request.message[:100])

    rate_limit = await _check_rate_limit(session_id)
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
                "I can only help with Olist marketplace support, order status, "
                "category risk, and escalation tasks."
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


def _pending_confirmation_response(state: dict) -> dict[str, object]:
    pending = state.get("pending_side_effect", {}) if state else {}
    action_type = pending.get("type", "side_effect")
    expires_at = pending.get("expires_at")
    expires_hint = ""
    if isinstance(expires_at, (int, float)):
        remaining = max(int(expires_at - time.time()), 0)
        expires_hint = f"\n该确认将在 {remaining} 秒后超时自动取消。"
    return {
        "route_intent": "pending_confirmation",
        "final_answer": (
            f"当前 session 还有一个待确认的副作用动作：{action_type}。\n"
            "请先回复 yes/确认 执行，或 no/取消 放弃；如果要开始新任务，请换一个 Session ID。"
            f"{expires_hint}"
        ),
        "trajectory_events": [
            {
                "node": "chat",
                "intent": "pending_confirmation",
                "status": "blocked_new_message",
                "details": {"action_type": action_type},
            }
        ],
    }


async def _check_rate_limit(session_id: str):
    limit = _env_int("AGENT_RATE_LIMIT_PER_MINUTE", 1000)
    if limit <= 0:
        return None
    key = "tenant:olist-demo:user:demo-user:minute"
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@app.get("/observability/summary", response_model=TraceSummaryResponse)
def observability_summary() -> TraceSummaryResponse:
    return TraceSummaryResponse(**trace_summary())


@app.get("/observability/traces/{session_id}", response_model=TraceReplayResponse)
def replay_session_traces(session_id: str, limit: int = 20) -> TraceReplayResponse:
    return TraceReplayResponse(session_id=session_id, traces=list_session_traces(session_id, limit=limit))


@app.get("/runtime/status", response_model=RuntimeStatusResponse)
def get_runtime_status() -> RuntimeStatusResponse:
    return RuntimeStatusResponse(**runtime_status)
