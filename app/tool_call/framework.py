from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import (
    InMemoryCaseService,
    OlistService,
    format_order_status,
)
from app.retrieval.hybrid import HybridSupportRetriever


class ToolCallError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class ToolCallContext(BaseModel):
    user_id: str = "demo-user"
    role: str = "support_agent"
    tenant_id: str = "olist-demo"
    session_id: str = "unknown"


class ToolCallResult(BaseModel):
    tool_name: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    cached: bool = False
    attempts: int = 1
    latency_ms: float = 0.0
    error_code: str | None = None
    error_message: str | None = None


class OrderStatusArgs(BaseModel):
    order_id: str = Field(..., min_length=32, max_length=64)


class QueryArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class PolicySearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    k: int = Field(default=3, ge=1, le=10)


class SupportSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    k: int = Field(default=3, ge=1, le=10)


class SideEffectArgs(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=64)
    order_id: str = Field(..., min_length=32, max_length=64)
    message_text: str = Field(..., min_length=1, max_length=4000)


ToolHandler = Callable[[dict[str, Any], ToolCallContext], dict[str, Any] | Awaitable[dict[str, Any]]]
FallbackHandler = Callable[[dict[str, Any], ToolCallContext, Exception], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    allowed_roles: frozenset[str] = field(default_factory=lambda: frozenset({"support_agent"}))
    side_effect: bool = False
    cache_ttl_seconds: float | None = None
    timeout_seconds: float = 2.0
    retries: int = 0
    backoff_seconds: float = 0.05
    fallback: FallbackHandler | None = None


class ToolCallManager:
    """Governed tool runner for deterministic business tools.

    The manager centralizes the eight production concerns interviewers usually
    ask about: schema validation, role allowlists, cache, async execution,
    timeout/retry/backoff, fallback, normalized output, and audit events.
    """

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._cache: dict[str, tuple[float, ToolCallResult]] = {}
        self.audit_events: list[dict[str, Any]] = []

    def list_tools(self) -> list[str]:
        return sorted(self._specs)

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolCallContext | None = None,
    ) -> ToolCallResult:
        context = context or ToolCallContext()
        started = time.perf_counter()
        spec = self._specs.get(name)
        if spec is None:
            result = self._failure(name, "unknown_tool", f"Unknown tool: {name}", started)
            self._audit(context, name, arguments, result)
            return result

        try:
            validated = spec.input_model.model_validate(arguments).model_dump()
            self._check_permission(spec, context)
        except Exception as exc:
            error_code = exc.error_code if isinstance(exc, ToolCallError) else "schema_validation_failed"
            result = self._failure(name, error_code, str(exc), started)
            self._audit(context, name, arguments, result)
            return result

        cache_key = (
            self._cache_key(name, validated)
            if spec.cache_ttl_seconds and not spec.side_effect
            else None
        )
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached and cached[0] >= time.time():
                result = cached[1].model_copy(update={"cached": True})
                self._audit(context, name, validated, result)
                return result

        result = await self._execute(spec, validated, context, started)
        if cache_key and result.ok:
            self._cache[cache_key] = (time.time() + float(spec.cache_ttl_seconds), result)
        self._audit(context, name, validated, result)
        return result

    async def _execute(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any],
        context: ToolCallContext,
        started: float,
    ) -> ToolCallResult:
        attempts = 0
        last_error: Exception | None = None
        for attempt in range(spec.retries + 1):
            attempts = attempt + 1
            try:
                data = await asyncio.wait_for(
                    _invoke(spec.handler, arguments, context),
                    timeout=spec.timeout_seconds,
                )
                return ToolCallResult(
                    tool_name=spec.name,
                    ok=True,
                    data=data,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            except Exception as exc:
                last_error = exc
                if attempt < spec.retries:
                    await asyncio.sleep(spec.backoff_seconds * (2**attempt))

        assert last_error is not None
        if spec.fallback is not None:
            fallback_data = spec.fallback(arguments, context, last_error)
            return ToolCallResult(
                tool_name=spec.name,
                ok=True,
                data=fallback_data,
                attempts=attempts,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code="fallback_used",
                error_message=str(last_error),
            )
        return self._failure(spec.name, _error_code(last_error), str(last_error), started, attempts)

    @staticmethod
    def _check_permission(spec: ToolSpec, context: ToolCallContext) -> None:
        if context.role not in spec.allowed_roles:
            raise ToolCallError(
                "permission_denied",
                f"Role {context.role!r} is not allowed to call {spec.name!r}",
            )

    @staticmethod
    def _failure(
        name: str,
        error_code: str,
        error_message: str,
        started: float,
        attempts: int = 1,
    ) -> ToolCallResult:
        return ToolCallResult(
            tool_name=name,
            ok=False,
            attempts=attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
            error_code=error_code,
            error_message=error_message,
        )

    def _audit(
        self,
        context: ToolCallContext,
        name: str,
        arguments: dict[str, Any],
        result: ToolCallResult,
    ) -> None:
        self.audit_events.append(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tenant_id": context.tenant_id,
                "session_id": context.session_id,
                "user_id": context.user_id,
                "role": context.role,
                "tool_name": name,
                "args_hash": self._cache_key(name, arguments),
                "redacted_args": _redact_args(arguments),
                "ok": result.ok,
                "cached": result.cached,
                "attempts": result.attempts,
                "latency_ms": round(result.latency_ms, 3),
                "error_code": result.error_code,
                "result_summary": _result_summary(result.data),
            }
        )

    @staticmethod
    def _cache_key(name: str, arguments: dict[str, Any]) -> str:
        payload = json.dumps({"tool": name, "args": arguments}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _invoke(
    handler: ToolHandler,
    arguments: dict[str, Any],
    context: ToolCallContext,
) -> dict[str, Any]:
    if inspect.iscoroutinefunction(handler):
        return await handler(arguments, context)
    result = await asyncio.to_thread(handler, arguments, context)
    if inspect.isawaitable(result):
        return await result
    return result


def build_business_tool_manager(
    *,
    olist_service: OlistService,
    knowledge_base: MarkdownKnowledgeBase,
    support_retriever: HybridSupportRetriever,
    case_service: InMemoryCaseService,
) -> ToolCallManager:
    support_roles = frozenset({"support_agent", "ops_manager", "admin"})
    ops_roles = frozenset({"ops_manager", "admin"})

    return ToolCallManager(
        [
            ToolSpec(
                name="get_order_status",
                description="Return deterministic order, delivery, payment, and review facts.",
                input_model=OrderStatusArgs,
                allowed_roles=support_roles,
                cache_ttl_seconds=300,
                timeout_seconds=1,
                retries=1,
                fallback=lambda args, ctx, exc: {
                    "answer": "订单事实工具暂时不可用，请稍后重试或转人工核查。",
                    "found": False,
                    "fallback_reason": str(exc),
                },
                handler=lambda args, ctx: {
                    "answer": format_order_status(olist_service.get_order_status(str(args["order_id"]))),
                    "found": olist_service.get_order_status(str(args["order_id"])) is not None,
                },
            ),
            ToolSpec(
                name="search_category_risk",
                description="Retrieve category-level logistics and review risk insights.",
                input_model=QueryArgs,
                allowed_roles=support_roles,
                cache_ttl_seconds=300,
                timeout_seconds=1,
                retries=1,
                fallback=lambda args, ctx, exc: {"insights": [], "fallback_reason": str(exc)},
                handler=lambda args, ctx: {"insights": olist_service.category_insights(str(args["query"]))},
            ),
            ToolSpec(
                name="generate_after_sales_priority_report",
                description="Generate a read-only after-sales operations decision report.",
                input_model=QueryArgs,
                allowed_roles=ops_roles,
                cache_ttl_seconds=120,
                timeout_seconds=2,
                retries=1,
                handler=lambda args, ctx: {
                    "report": olist_service.after_sales_priority_report(str(args["query"])),
                },
            ),
            ToolSpec(
                name="search_policy_knowledge",
                description="Retrieve support policy sections from markdown KB.",
                input_model=PolicySearchArgs,
                allowed_roles=support_roles,
                cache_ttl_seconds=300,
                timeout_seconds=1,
                retries=1,
                fallback=lambda args, ctx, exc: {"sections": [], "fallback_reason": str(exc)},
                handler=lambda args, ctx: {
                    "sections": [
                        {
                            "source": hit.source,
                            "section_title": hit.section_title,
                            "text": hit.text,
                            "score": hit.score,
                        }
                        for hit in knowledge_base.search(str(args["query"]), k=int(args["k"]))
                    ]
                },
            ),
            ToolSpec(
                name="search_support_examples",
                description="Retrieve similar public support conversations for answer style context.",
                input_model=SupportSearchArgs,
                allowed_roles=support_roles,
                cache_ttl_seconds=300,
                timeout_seconds=2,
                retries=1,
                fallback=lambda args, ctx, exc: {"docs": [], "fallback_reason": str(exc)},
                handler=lambda args, ctx: {
                    "docs": [
                        {
                            "doc_id": hit.doc.get("doc_id", ""),
                            "intent": hit.doc.get("intent", ""),
                            "capability": hit.doc.get("capability", ""),
                            "text": hit.doc.get("text", ""),
                            "score": hit.score,
                        }
                        for hit in support_retriever.hybrid_search(str(args["query"]), k=int(args["k"]))
                    ]
                },
            ),
            ToolSpec(
                name="prepare_side_effect",
                description="Draft an after-sales side-effect action before HITL confirmation.",
                input_model=OrderStatusArgs,
                allowed_roles=support_roles,
                cache_ttl_seconds=120,
                timeout_seconds=1,
                retries=1,
                handler=lambda args, ctx: {
                    "draft": olist_service.escalation_draft(str(args["order_id"])),
                },
            ),
            ToolSpec(
                name="execute_side_effect",
                description="Execute a confirmed idempotent side-effect action.",
                input_model=SideEffectArgs,
                allowed_roles=frozenset({"support_agent", "admin"}),
                side_effect=True,
                timeout_seconds=2,
                retries=1,
                handler=lambda args, ctx: {
                    "result": case_service.execute_action(
                        action_type=str(args["action_type"]),
                        order_id=str(args["order_id"]),
                        message_text=str(args["message_text"]),
                    )
                },
            ),
        ]
    )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, ToolCallError):
        return exc.error_code
    return "tool_execution_failed"


def _redact_args(arguments: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for key, value in arguments.items():
        if key == "order_id" and isinstance(value, str) and len(value) > 10:
            redacted[key] = f"{value[:6]}...{value[-4:]}"
        elif "message" in key or "text" in key:
            text = str(value)
            redacted[key] = {"chars": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()[:12]}
        else:
            redacted[key] = value
    return redacted


def _result_summary(data: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, list):
            summary[key] = f"list[{len(value)}]"
        elif isinstance(value, dict):
            summary[key] = f"dict[{len(value)}]"
        else:
            summary[key] = value
    return summary
