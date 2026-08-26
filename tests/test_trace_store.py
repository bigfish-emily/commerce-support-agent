from pathlib import Path

from app.trace_store import list_session_traces, record_trace, trace_summary


def test_record_trace_writes_sqlite_row(tmp_path, monkeypatch) -> None:
    import app.trace_store as trace_store

    monkeypatch.setattr(trace_store, "TRACE_DB", tmp_path / "traces.db")
    trace_id = record_trace(
        session_id="s1",
        user_message="hello",
        result={
            "route_intent": "policy",
            "final_answer": "ok",
            "retrieved_policy": ["Human Approval Policy"],
            "trajectory_events": [{"node": "plan_tasks", "status": "completed"}],
        },
        latency_ms=12.3,
        status="ok",
    )
    assert trace_id
    assert Path(trace_store.TRACE_DB).exists()
    traces = list_session_traces("s1")
    assert traces[0]["trace_id"] == trace_id
    assert "trajectory_json" in traces[0]
    summary = trace_summary()
    assert summary["total"] == 1
    assert summary["status_counts"] == {"ok": 1}
