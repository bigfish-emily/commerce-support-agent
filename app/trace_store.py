from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

TRACE_DB = Path("data") / "agent_traces.db"


def record_trace(
    *,
    session_id: str,
    user_message: str,
    result: dict[str, Any],
    latency_ms: float,
    status: str,
) -> str:
    TRACE_DB.parent.mkdir(parents=True, exist_ok=True)
    trace_id = str(uuid.uuid4())
    with sqlite3.connect(TRACE_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_trace (
                trace_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                session_id TEXT NOT NULL,
                route_intent TEXT,
                user_message TEXT NOT NULL,
                final_answer TEXT,
                sources_json TEXT,
                trajectory_json TEXT,
                latency_ms REAL NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        _ensure_trace_schema(conn)
        sources = result.get("retrieved_insights", []) or result.get("retrieved_policy", [])
        conn.execute(
            """
            INSERT INTO agent_trace (
                trace_id, created_at, session_id, route_intent, user_message, final_answer,
                sources_json, trajectory_json, latency_ms, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                time.time(),
                session_id,
                result.get("route_intent", ""),
                user_message[:1000],
                str(result.get("final_answer", ""))[:2000],
                json.dumps(sources, ensure_ascii=False),
                json.dumps(result.get("trajectory_events", []), ensure_ascii=False),
                latency_ms,
                status,
            ),
        )
    return trace_id


def _ensure_trace_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_trace)").fetchall()}
    if "route_intent" not in columns:
        conn.execute("ALTER TABLE agent_trace ADD COLUMN route_intent TEXT")
    if "trajectory_json" not in columns:
        conn.execute("ALTER TABLE agent_trace ADD COLUMN trajectory_json TEXT")


def list_session_traces(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    if not TRACE_DB.exists():
        return []
    with sqlite3.connect(TRACE_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT trace_id, created_at, session_id, route_intent, user_message,
                   final_answer, sources_json, trajectory_json, latency_ms, status
            FROM agent_trace
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def trace_summary() -> dict[str, Any]:
    if not TRACE_DB.exists():
        return {
            "total": 0,
            "status_counts": {},
            "route_intent_counts": {},
            "avg_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
        }
    with sqlite3.connect(TRACE_DB) as conn:
        rows = conn.execute(
            "SELECT status, route_intent, latency_ms FROM agent_trace ORDER BY created_at"
        ).fetchall()
    latencies = sorted(float(row[2]) for row in rows)
    status_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    for status, route_intent, _latency in rows:
        status_counts[str(status)] = status_counts.get(str(status), 0) + 1
        route = str(route_intent or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1
    p95_index = int(0.95 * (len(latencies) - 1)) if latencies else 0
    return {
        "total": len(rows),
        "status_counts": status_counts,
        "route_intent_counts": route_counts,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": round(latencies[p95_index], 2) if latencies else 0.0,
    }
