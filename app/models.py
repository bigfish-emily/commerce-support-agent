from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    sources: list[str] = []


class TraceSummaryResponse(BaseModel):
    total: int
    status_counts: dict[str, int]
    route_intent_counts: dict[str, int]
    avg_latency_ms: float
    p95_latency_ms: float


class CaseMetricsResponse(BaseModel):
    total_cases: int
    auto_resolution_rate: float
    hitl_rate: float
    wrong_write_blocked: int
    wrong_write_block_rate: float
    policy_hit_rate: float
    tool_error_rate: float
    p95_latency_ms: float
    cost_per_case: float
    cost_sample_count: int


class TraceReplayResponse(BaseModel):
    session_id: str
    traces: list[dict]


class RuntimeStatusResponse(BaseModel):
    mode: str
    model: str
    base_url: str
    runtime_backend: str = "memory"
    rate_limit_per_minute: int = 1000
