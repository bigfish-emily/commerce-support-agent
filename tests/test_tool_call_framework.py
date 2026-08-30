import asyncio

import pytest
from pydantic import BaseModel

from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import InMemoryCaseService, OlistService
from app.retrieval.hybrid import HybridSupportRetriever
from app.tool_call import (
    InMemoryRuntimeStore,
    RedisRuntimeStore,
    RedisToolCache,
    ToolCallContext,
    ToolCallManager,
    ToolSpec,
    build_business_tool_manager,
)


class EmptyArgs(BaseModel):
    pass


@pytest.fixture
def manager():
    return build_business_tool_manager(
        olist_service=OlistService(),
        knowledge_base=MarkdownKnowledgeBase(),
        support_retriever=HybridSupportRetriever(),
        case_service=InMemoryCaseService(),
    )


@pytest.mark.anyio
async def test_tool_call_schema_validation_and_audit_redaction(manager) -> None:
    result = await manager.call(
        "get_order_status",
        {"order_id": "short"},
        ToolCallContext(session_id="tool-s1", role="support_agent"),
    )

    assert not result.ok
    assert result.error_code == "schema_validation_failed"
    assert manager.audit_events[-1]["tool_name"] == "get_order_status"
    assert manager.audit_events[-1]["redacted_args"]["order_id"] == "short"


@pytest.mark.anyio
async def test_tool_call_role_whitelist_denies_ops_tool(manager) -> None:
    result = await manager.call(
        "generate_after_sales_priority_report",
        {"query": "生成售后运营日报"},
        ToolCallContext(role="support_agent"),
    )

    assert not result.ok
    assert result.error_code == "permission_denied"


@pytest.mark.anyio
async def test_tool_call_cache_for_read_only_tools(manager) -> None:
    args = {"query": "health beauty 类目有什么运营风险？"}
    first = await manager.call("search_category_risk", args, ToolCallContext(role="support_agent"))
    second = await manager.call("search_category_risk", args, ToolCallContext(role="support_agent"))

    assert first.ok
    assert second.ok
    assert not first.cached
    assert second.cached


@pytest.mark.anyio
async def test_tool_call_cache_is_tenant_scoped(manager) -> None:
    args = {"query": "health beauty 类目有什么运营风险？"}
    first = await manager.call(
        "search_category_risk",
        args,
        ToolCallContext(role="support_agent", tenant_id="tenant-a"),
    )
    second = await manager.call(
        "search_category_risk",
        args,
        ToolCallContext(role="support_agent", tenant_id="tenant-b"),
    )
    third = await manager.call(
        "search_category_risk",
        args,
        ToolCallContext(role="support_agent", tenant_id="tenant-a"),
    )

    assert first.ok and second.ok and third.ok
    assert not first.cached
    assert not second.cached
    assert third.cached


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}
        self.ttls = {}
        self.counters = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int, nx: bool = False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        self.values[key] = str(self.counters[key])
        return self.counters[key]

    async def expire(self, key: str, ex: int) -> None:
        self.ttls[key] = ex

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)

    async def eval(self, script: str, keys_count: int, key: str, token: str) -> int:
        if self.values.get(key) == token:
            return await self.delete(key)
        return 0


@pytest.mark.anyio
async def test_redis_tool_cache_serializes_result() -> None:
    fake = FakeRedis()
    cache = RedisToolCache(fake, prefix="test-cache")
    manager = build_business_tool_manager(
        olist_service=OlistService(),
        knowledge_base=MarkdownKnowledgeBase(),
        support_retriever=HybridSupportRetriever(),
        case_service=InMemoryCaseService(),
        cache_backend=cache,
    )
    args = {"query": "health beauty 类目有什么运营风险？"}

    first = await manager.call("search_category_risk", args, ToolCallContext(role="support_agent"))
    second = await manager.call("search_category_risk", args, ToolCallContext(role="support_agent"))

    assert first.ok
    assert second.ok
    assert second.cached
    assert len(fake.values) == 1
    redis_key = next(iter(fake.values))
    redis_value = next(iter(fake.values.values()))
    assert redis_key.startswith("test-cache:")
    assert redis_value.startswith('{"tool_name":"search_category_risk"')


@pytest.mark.anyio
async def test_runtime_store_rate_limit_pending_and_lock() -> None:
    store = InMemoryRuntimeStore()

    first = await store.check_rate_limit("tenant:user", limit=1, window_seconds=60)
    second = await store.check_rate_limit("tenant:user", limit=1, window_seconds=60)
    assert first.allowed
    assert not second.allowed
    assert second.remaining == 0

    await store.put_pending_confirmation("session-1", {"action": "refund"}, ttl_seconds=60)
    assert await store.get_pending_confirmation("session-1") == {"action": "refund"}
    await store.clear_pending_confirmation("session-1")
    assert await store.get_pending_confirmation("session-1") is None

    token = await store.acquire_lock("side-effect:key", ttl_seconds=60)
    assert token
    assert await store.acquire_lock("side-effect:key", ttl_seconds=60) is None
    await store.release_lock("side-effect:key", token)
    assert await store.acquire_lock("side-effect:key", ttl_seconds=60)


@pytest.mark.anyio
async def test_redis_runtime_store_supports_rate_pending_and_lock() -> None:
    fake = FakeRedis()
    store = RedisRuntimeStore(fake, prefix="runtime-test")

    decision = await store.check_rate_limit("tenant:user", limit=2, window_seconds=30)
    assert decision.allowed
    assert "runtime-test:rate:tenant:user" in fake.values

    await store.put_pending_confirmation("session-1", {"action": "invoice"}, ttl_seconds=30)
    assert await store.get_pending_confirmation("session-1") == {"action": "invoice"}
    await store.clear_pending_confirmation("session-1")
    assert await store.get_pending_confirmation("session-1") is None

    token = await store.acquire_lock("side-effect:key", ttl_seconds=30)
    assert token
    assert await store.acquire_lock("side-effect:key", ttl_seconds=30) is None
    await store.release_lock("side-effect:key", token)
    assert await store.acquire_lock("side-effect:key", ttl_seconds=30)


@pytest.mark.anyio
async def test_side_effect_lock_blocks_concurrent_duplicate() -> None:
    async def handler(args, ctx):
        return {"created": True}

    store = InMemoryRuntimeStore()
    manager = ToolCallManager(
        [
            ToolSpec(
                name="write_tool",
                description="write",
                input_model=EmptyArgs,
                handler=handler,
                side_effect=True,
            )
        ],
        runtime_store=store,
    )
    context = ToolCallContext(role="support_agent", tenant_id="tenant-a")
    lock_key = manager._cache_key("write_tool", {}, context.tenant_id)
    token = await store.acquire_lock(f"side-effect:{lock_key}", ttl_seconds=30)
    assert token

    result = await manager.call("write_tool", {}, context)

    assert not result.ok
    assert result.error_code == "side_effect_in_progress"


@pytest.mark.anyio
async def test_tool_call_timeout_uses_fallback() -> None:
    async def slow_call(*args, **kwargs):
        await asyncio.sleep(0.01)
        return {"answer": "too late"}

    slow_manager = ToolCallManager(
        [
            ToolSpec(
                name="slow_tool",
                description="slow",
                input_model=EmptyArgs,
                handler=slow_call,
                timeout_seconds=0.001,
                fallback=lambda args, ctx, exc: {"answer": "fallback answer"},
            )
        ]
    )

    result = await slow_manager.call("slow_tool", {}, ToolCallContext(role="support_agent"))

    assert result.ok
    assert result.error_code == "fallback_used"
    assert result.data["answer"] == "fallback answer"


@pytest.mark.anyio
async def test_side_effect_tool_is_idempotent_and_audited(manager) -> None:
    args = {
        "action_type": "refund_request",
        "order_id": "203096f03d82e0dffbc41ebc2e2bcfb7",
        "message_text": "delivery delayed by 11 day(s); low review score 2",
    }

    first = await manager.call("execute_side_effect", args, ToolCallContext(role="support_agent"))
    second = await manager.call("execute_side_effect", args, ToolCallContext(role="support_agent"))

    assert first.ok
    assert second.ok
    assert first.data["result"]["duplicate"] is False
    assert second.data["result"]["duplicate"] is True
    assert second.cached is False
    event = manager.audit_events[-1]
    assert event["redacted_args"]["order_id"] == "203096...cfb7"
    assert event["redacted_args"]["message_text"]["chars"] == len(args["message_text"])
