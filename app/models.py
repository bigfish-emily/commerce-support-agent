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


class TraceReplayResponse(BaseModel):
    session_id: str
    traces: list[dict]


class RuntimeStatusResponse(BaseModel):
    mode: str
    model: str
    base_url: str
