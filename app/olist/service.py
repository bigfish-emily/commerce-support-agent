from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.olist.catalog import category_risks_by_name, full_orders_by_id, load_dataset, load_order_facts_index
from app.olist.retrieval import adaptive_category_retrieval


@dataclass(frozen=True)
class OrderStatusView:
    order_id: str
    status: str
    customer_state: str
    purchase_timestamp: str
    estimated_delivery_date: str
    delivered_customer_date: str
    delay_days: int | None
    payment_value: float
    review_score: int | None
    category_summary: str
    refund_status: str | None = None
    address_change_status: str | None = None
    invoice_status: str | None = None


class OrderProjectionStore(Protocol):
    """Read the sandbox state produced by approved after-sales actions."""

    def get_order_projection(self, order_id: str) -> dict[str, Any]:
        """Return the latest derived state for an order."""


class OlistService:
    def __init__(self, projection_store: OrderProjectionStore | None = None) -> None:
        self._by_id = full_orders_by_id()
        self._category_risks = category_risks_by_name()
        self._projection_store = projection_store

    def set_projection_store(self, projection_store: OrderProjectionStore) -> None:
        """Attach the durable after-sales projection after DI has been wired."""
        self._projection_store = projection_store

    def metadata(self) -> dict:
        metadata = dict(load_dataset()["metadata"])
        metadata["full_order_count"] = load_order_facts_index().get("metadata", {}).get("order_count")
        metadata["category_risk_count"] = len(self._category_risks)
        return metadata

    def find_order_id(self, text: str) -> str | None:
        match = re.search(r"[0-9a-f]{32}", text.lower())
        return match.group(0) if match else None

    def get_order_status(self, order_id: str) -> OrderStatusView | None:
        order = self._by_id.get(order_id)
        if order is None:
            return None
        projection = self._projection_store.get_order_projection(order_id) if self._projection_store else {}
        categories = [p["category"] for p in order["products"]]
        return OrderStatusView(
            order_id=order["order_id"],
            status=str(projection.get("order_status") or order["status"]),
            customer_state=order["customer_state"],
            purchase_timestamp=order["purchase_timestamp"],
            estimated_delivery_date=order["estimated_delivery_date"],
            delivered_customer_date=order["delivered_customer_date"],
            delay_days=order["delay_days"],
            payment_value=order["payment_value"],
            review_score=order["review_score"],
            category_summary="、".join(sorted(set(categories))),
            refund_status=_as_optional_text(projection.get("refund_status")),
            address_change_status=_as_optional_text(projection.get("address_change_status")),
            invoice_status=_as_optional_text(projection.get("invoice_status")),
        )

    def category_insights(self, query: str) -> list[dict[str, object]]:
        hits = adaptive_category_retrieval(query, sorted(self._category_risks))
        matched_categories = [hit.category for hit in hits if hit.category in self._category_risks]
        if matched_categories:
            categories = matched_categories[:4]
        else:
            categories = sorted(
                self._category_risks,
                key=lambda name: int(self._category_risks[name]["order_count"]),
                reverse=True,
            )[:4]

        insights: list[dict[str, object]] = []
        for cat in categories:
            risk = self._category_risks[cat]
            insights.append(
                {
                    "name": cat,
                    "price": float(risk["avg_payment_value"]),
                    "description": (
                        f"{cat} category has {risk['order_count']} full-data orders, "
                        f"delay_rate={risk['delay_rate']:.2%}, "
                        f"low_review_rate={risk['low_review_rate']:.2%}, "
                        f"cancellation_rate={risk['cancellation_rate']:.2%}."
                    ),
                    "rules": (
                        "Use order_status for exact order facts; use escalation for delayed "
                        "or low-review follow-up."
                    ),
                    "sample_order_ids": risk["sample_order_ids"][:3],
                }
            )
        return insights

    def after_sales_priority_report(
        self,
        query: str = "",
        top_categories: int = 5,
        top_orders: int = 8,
    ) -> dict[str, object]:
        category_rows = []
        for category, risk in self._category_risks.items():
            order_count = int(risk["order_count"])
            risk_score = _ops_risk_score(risk)
            category_rows.append(
                {
                    "category": category,
                    "risk_score": risk_score,
                    "order_count": order_count,
                    "delay_rate": risk["delay_rate"],
                    "low_review_rate": risk["low_review_rate"],
                    "cancellation_rate": risk["cancellation_rate"],
                    "avg_delay_days": risk.get("avg_delay_days", 0),
                    "sample_order_ids": risk["sample_order_ids"][:3],
                    "recommended_action": _category_action(risk),
                }
            )
        high_risk_categories = sorted(
            category_rows,
            key=lambda item: (float(item["risk_score"]), int(item["order_count"])),
            reverse=True,
        )[:top_categories]

        order_rows = []
        for order in self._by_id.values():
            priority_score, reasons = _order_priority(order)
            if priority_score <= 0:
                continue
            order_rows.append(
                {
                    "order_id": order["order_id"],
                    "priority_score": priority_score,
                    "status": order["status"],
                    "category_summary": "、".join(sorted({p["category"] for p in order["products"]})),
                    "delay_days": order["delay_days"],
                    "review_score": order["review_score"],
                    "payment_value": order["payment_value"],
                    "reasons": reasons,
                    "recommended_action": _order_action(order),
                }
            )
        priority_orders = sorted(
            order_rows,
            key=lambda item: (float(item["priority_score"]), float(item["payment_value"] or 0)),
            reverse=True,
        )[:top_orders]

        return {
            "query": query,
            "summary": (
                f"Identified {len(high_risk_categories)} high-risk categories and "
                f"{len(priority_orders)} priority after-sales orders from Olist facts."
            ),
            "high_risk_categories": high_risk_categories,
            "priority_orders": priority_orders,
            "decision_rules": [
                "delay_rate, low_review_rate, cancellation_rate, and order_count drive category priority",
                "delayed/canceled/low-review/high-value orders are ranked for after-sales follow-up",
                "recommended actions are read-only suggestions; refunds/cancellations still require HITL",
            ],
        }

    def escalation_draft(self, order_id: str) -> dict | None:
        status = self.get_order_status(order_id)
        if status is None:
            return None

        reasons = []
        if status.delay_days is not None and status.delay_days > 0:
            reasons.append(f"delivery delayed by {status.delay_days} day(s)")
        if status.review_score is not None and status.review_score <= 2:
            reasons.append(f"low review score {status.review_score}")
        if status.status == "canceled":
            reasons.append("order was canceled")
        if not reasons:
            reasons.append("customer follow-up requested")

        message = (
            f"您好，我们正在跟进您的订单 {order_id[:8]}。"
            f"当前状态为 {status.status}，类目：{status.category_summary}。"
            f"本次跟进原因：{'; '.join(reasons)}。"
            "我们会核查物流与售后记录，并在确认后提供补偿或后续处理方案。"
        )
        return {
            "order_id": order_id,
            "reason": "; ".join(reasons),
            "message_text": message,
        }


class InMemoryCaseService:
    def __init__(self) -> None:
        self._cases: dict[str, dict] = {}
        self._idempotency_index: dict[str, str] = {}
        self._order_projections: dict[str, dict[str, Any]] = {}
        self._order_events: list[dict[str, Any]] = []

    def open_case(self, order_id: str, message_text: str) -> str:
        result = self.execute_action(
            action_type="open_support_case",
            order_id=order_id,
            message_text=message_text,
        )
        return str(result["result_id"])

    def execute_action(self, action_type: str, order_id: str, message_text: str) -> dict[str, object]:
        idempotency_key = _idempotency_key(action_type, order_id, message_text)
        if idempotency_key in self._idempotency_index:
            case_id = self._idempotency_index[idempotency_key]
            record = self._cases[case_id]
            was_executed = record.get("status") == "executed"
            event: dict[str, Any] | None = None
            if not was_executed:
                record["status"] = "executed"
                event = self._apply_order_event(action_type, order_id, case_id)
                record["order_event"] = event
            return {
                "result_id": case_id,
                "duplicate": was_executed,
                "idempotency_key": idempotency_key,
                "record": record,
                "order_event": event,
            }

        digest = hashlib.sha1(idempotency_key.encode()).hexdigest()[:10]
        case_id = f"{_case_prefix(action_type)}-{digest}"
        self._idempotency_index[idempotency_key] = case_id
        self._cases[case_id] = {
            "case_id": case_id,
            "action_type": action_type,
            "order_id": order_id,
            "message_text": message_text,
            "status": "executed",
            "idempotency_key": idempotency_key,
        }
        event = self._apply_order_event(action_type, order_id, case_id)
        self._cases[case_id]["order_event"] = event
        return {
            "result_id": case_id,
            "duplicate": False,
            "idempotency_key": idempotency_key,
            "record": self._cases[case_id],
            "order_event": event,
        }

    def reset(self) -> None:
        self._cases.clear()
        self._idempotency_index.clear()
        self._order_projections.clear()
        self._order_events.clear()


    def get(self, case_id: str) -> dict | None:
        return self._cases.get(case_id)

    def get_by_session(self, session_id: str) -> dict | None:
        for record in self._cases.values():
            if record.get("session_id") == session_id:
                return record
        return None

    def submit_for_review(
        self,
        *,
        action_type: str,
        order_id: str,
        message_text: str,
        session_id: str,
        user_id: str,
        expires_at: float | None,
        case_payload: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        idempotency_key = _idempotency_key(action_type, order_id, message_text)
        if idempotency_key in self._idempotency_index:
            case_id = self._idempotency_index[idempotency_key]
            return {
                "result_id": case_id,
                "duplicate": True,
                "idempotency_key": idempotency_key,
                "record": self._cases[case_id],
            }
        digest = hashlib.sha1(idempotency_key.encode()).hexdigest()[:10]
        case_id = f"{_case_prefix(action_type)}-{digest}"
        self._idempotency_index[idempotency_key] = case_id
        self._cases[case_id] = {
            "case_id": case_id,
            "idempotency_key": idempotency_key,
            "action_type": action_type,
            "order_id": order_id,
            "message_text": message_text,
            "status": "pending_review",
            "session_id": session_id,
            "user_id": user_id,
            "expires_at": expires_at,
            "appeal_count": 0,
            "case_payload": dict(case_payload or {}),
        }
        return {
            "result_id": case_id,
            "duplicate": False,
            "idempotency_key": idempotency_key,
            "record": self._cases[case_id],
        }

    def mark_review_result(self, session_id: str, status: str, reviewer_id: str = "") -> dict | None:
        record = self.get_by_session(session_id)
        if record is None:
            return None
        record["status"] = status
        record["reviewer_id"] = reviewer_id
        return record

    def appeal_case(self, case_id: str, user_id: str, reason: str) -> dict | None:
        record = self._cases.get(case_id)
        if record is None or record.get("user_id") != user_id:
            return None
        if record.get("status") not in {"rejected", "timeout_canceled"}:
            return record
        record["status"] = "appealed_pending_review"
        record["appeal_reason"] = reason
        record["appeal_count"] = int(record.get("appeal_count") or 0) + 1
        return record

    def list_pending(self, limit: int = 50) -> list[dict]:
        return [
            dict(record)
            for record in self._cases.values()
            if record.get("status") in {"pending_review", "appealed_pending_review"}
        ][:limit]

    def get_order_projection(self, order_id: str) -> dict[str, Any]:
        return dict(self._order_projections.get(order_id, {}))

    def count_order_events(self, order_id: str) -> int:
        return sum(1 for event in self._order_events if event.get("order_id") == order_id)

    def _apply_order_event(self, action_type: str, order_id: str, case_id: str) -> dict[str, Any]:
        projection = self._order_projections.setdefault(order_id, {"order_id": order_id})
        patch = _order_state_patch(action_type)
        projection.update(patch)
        event = {
            "event_id": f"evt-{len(self._order_events) + 1}",
            "case_id": case_id,
            "order_id": order_id,
            "action_type": action_type,
            "event_type": "after_sales_action_executed",
            "state_patch": patch,
        }
        self._order_events.append(event)
        return event


class SQLiteCaseService:
    """Persistent side-effect case store with business-granularity idempotency."""

    def __init__(self, path: str | Path = "data/cases.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def open_case(self, order_id: str, message_text: str) -> str:
        result = self.execute_action(
            action_type="open_support_case",
            order_id=order_id,
            message_text=message_text,
        )
        return str(result["result_id"])

    def execute_action(self, action_type: str, order_id: str, message_text: str) -> dict[str, object]:
        idempotency_key = _idempotency_key(action_type, order_id, message_text)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT * FROM cases WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                record = dict(existing)
                order_event: dict[str, Any] | None = None
                if record["status"] != "executed":
                    conn.execute(
                        """
                        UPDATE cases
                        SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE case_id = ?
                        """,
                        ("executed", record["case_id"]),
                    )
                    record = dict(
                        conn.execute(
                            "SELECT * FROM cases WHERE case_id = ?",
                            (record["case_id"],),
                        ).fetchone()
                    )
                    order_event = self._append_order_event(
                        conn,
                        action_type=action_type,
                        order_id=order_id,
                        case_id=str(record["case_id"]),
                    )
                return {
                    "result_id": record["case_id"],
                    "duplicate": existing["status"] == "executed",
                    "idempotency_key": idempotency_key,
                    "record": record,
                    "order_event": order_event,
                }

            digest = hashlib.sha1(idempotency_key.encode()).hexdigest()[:10]
            case_id = f"{_case_prefix(action_type)}-{digest}"
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, idempotency_key, action_type, order_id,
                    message_text, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (case_id, idempotency_key, action_type, order_id, message_text, "executed"),
            )
            record = dict(
                conn.execute(
                    "SELECT * FROM cases WHERE case_id = ?",
                    (case_id,),
                ).fetchone()
            )
            order_event = self._append_order_event(
                conn,
                action_type=action_type,
                order_id=order_id,
                case_id=case_id,
            )
        return {
            "result_id": case_id,
            "duplicate": False,
            "idempotency_key": idempotency_key,
            "record": record,
            "order_event": order_event,
        }

    def reset(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM cases")
            conn.execute("DELETE FROM order_events")
            conn.execute("DELETE FROM order_projections")

    def get(self, case_id: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
            return dict(row) if row else None

    def get_by_session(self, session_id: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM cases WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def submit_for_review(
        self,
        *,
        action_type: str,
        order_id: str,
        message_text: str,
        session_id: str,
        user_id: str,
        expires_at: float | None,
        case_payload: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        idempotency_key = _idempotency_key(action_type, order_id, message_text)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT * FROM cases WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                record = dict(existing)
                return {
                    "result_id": record["case_id"],
                    "duplicate": True,
                    "idempotency_key": idempotency_key,
                    "record": record,
                }

            digest = hashlib.sha1(idempotency_key.encode()).hexdigest()[:10]
            case_id = f"{_case_prefix(action_type)}-{digest}"
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, idempotency_key, action_type, order_id, message_text,
                    status, created_at, updated_at, session_id, user_id, expires_at,
                    appeal_count, case_payload
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    ?, ?, ?, 0, ?
                )
                """,
                (
                    case_id,
                    idempotency_key,
                    action_type,
                    order_id,
                    message_text,
                    "pending_review",
                    session_id,
                    user_id,
                    expires_at,
                    json.dumps(case_payload or {}, ensure_ascii=True, sort_keys=True),
                ),
            )
            record = dict(conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone())
        return {
            "result_id": case_id,
            "duplicate": False,
            "idempotency_key": idempotency_key,
            "record": record,
        }

    def mark_review_result(self, session_id: str, status: str, reviewer_id: str = "") -> dict | None:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT case_id FROM cases WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE cases
                SET status = ?, reviewer_id = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE case_id = ?
                """,
                (status, reviewer_id, row["case_id"]),
            )
            updated = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (row["case_id"],),
            ).fetchone()
            return dict(updated) if updated else None

    def appeal_case(self, case_id: str, user_id: str, reason: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            if row is None or row["user_id"] != user_id:
                return None
            if row["status"] not in {"rejected", "timeout_canceled"}:
                return dict(row)
            conn.execute(
                """
                UPDATE cases
                SET status = 'appealed_pending_review',
                    appeal_reason = ?,
                    appeal_count = COALESCE(appeal_count, 0) + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE case_id = ?
                """,
                (reason, case_id),
            )
            updated = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
            return dict(updated) if updated else None

    def list_pending(self, limit: int = 50) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM cases
                WHERE status IN ('pending_review', 'appealed_pending_review')
                ORDER BY updated_at ASC, created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_order_projection(self, order_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT projection_json FROM order_projections WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        if row is None:
            return {}
        try:
            payload = json.loads(str(row["projection_json"]))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def count_order_events(self, order_id: str) -> int:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM order_events WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    action_type TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(cases)").fetchall()}
            additions = {
                "updated_at": "TEXT",
                "session_id": "TEXT",
                "user_id": "TEXT",
                "reviewer_id": "TEXT",
                "expires_at": "REAL",
                "appeal_count": "INTEGER DEFAULT 0",
                "appeal_reason": "TEXT",
                "case_payload": "TEXT",
            }
            for column, ddl in additions.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE cases ADD COLUMN {column} {ddl}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_events (
                    event_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    state_patch_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_projections (
                    order_id TEXT PRIMARY KEY,
                    projection_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _append_order_event(
        self,
        conn: sqlite3.Connection,
        *,
        action_type: str,
        order_id: str,
        case_id: str,
    ) -> dict[str, Any]:
        """Append an approved action and update its materialized sandbox view."""
        event_id = f"evt-{uuid.uuid4().hex[:16]}"
        patch = _order_state_patch(action_type)
        row = conn.execute(
            "SELECT projection_json FROM order_projections WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        projection: dict[str, Any] = {"order_id": order_id}
        if row is not None:
            try:
                loaded = json.loads(str(row[0]))
                if isinstance(loaded, dict):
                    projection.update(loaded)
            except json.JSONDecodeError:
                pass
        projection.update(patch)
        conn.execute(
            """
            INSERT INTO order_events (
                event_id, case_id, order_id, action_type, event_type, state_patch_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (
                event_id,
                case_id,
                order_id,
                action_type,
                "after_sales_action_executed",
                json.dumps(patch, ensure_ascii=True, sort_keys=True),
            ),
        )
        conn.execute(
            """
            INSERT INTO order_projections (order_id, projection_json, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(order_id) DO UPDATE SET
                projection_json = excluded.projection_json,
                updated_at = excluded.updated_at
            """,
            (order_id, json.dumps(projection, ensure_ascii=True, sort_keys=True)),
        )
        return {
            "event_id": event_id,
            "case_id": case_id,
            "order_id": order_id,
            "action_type": action_type,
            "event_type": "after_sales_action_executed",
            "state_patch": patch,
        }


def _idempotency_key(action_type: str, order_id: str, message_text: str) -> str:
    reason = _reason_code(action_type, message_text)
    return f"olist-demo:{action_type}:{order_id}:{reason}"


def _as_optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _order_state_patch(action_type: str) -> dict[str, str]:
    """Map an approved sandbox action to the customer-visible order projection.

    Olist is historical data, so this intentionally models a request lifecycle
    rather than claiming to move real money or alter a real logistics provider.
    """
    patches: dict[str, dict[str, str]] = {
        "cancel_order": {"order_status": "canceled", "cancellation_status": "confirmed"},
        "refund_request": {"refund_status": "requested"},
        "change_address": {"address_change_status": "requested"},
        "invoice_request": {"invoice_status": "requested"},
        "complaint_escalation": {"complaint_status": "opened"},
        "open_support_case": {"support_status": "opened"},
    }
    return dict(patches.get(action_type, {"support_status": "opened"}))


def _reason_code(action_type: str, message_text: str) -> str:
    lowered = message_text.lower()
    reasons = []
    if "delayed" in lowered or "延迟" in lowered:
        reasons.append("delivery_delay")
    if "low review" in lowered or "低分" in lowered:
        reasons.append("low_review")
    if "canceled" in lowered or "取消" in lowered:
        reasons.append("canceled")
    if not reasons:
        reasons.append("generic")
    return "+".join(sorted(reasons))


def _case_prefix(action_type: str) -> str:
    prefixes = {
        "open_support_case": "CASE",
        "refund_request": "REFUND",
        "cancel_order": "CANCEL",
        "change_address": "ADDR",
        "invoice_request": "INV",
        "complaint_escalation": "COMP",
    }
    return prefixes.get(action_type, "CASE")


def format_order_status(status: OrderStatusView | None) -> str:
    if status is None:
        return "没有找到该订单。请确认 order_id 是否完整。"
    delay = "暂无延迟信息" if status.delay_days is None else f"延迟 {status.delay_days} 天"
    lifecycle = []
    if status.refund_status:
        lifecycle.append(f"退款申请：{status.refund_status}")
    if status.address_change_status:
        lifecycle.append(f"改址申请：{status.address_change_status}")
    if status.invoice_status:
        lifecycle.append(f"发票申请：{status.invoice_status}")
    lifecycle_text = f"\n售后处理状态：{'；'.join(lifecycle)}。" if lifecycle else ""
    return (
        f"订单 {status.order_id} 当前状态：{status.status}。\n"
        f"客户州：{status.customer_state}；类目：{status.category_summary}。\n"
        f"下单时间：{status.purchase_timestamp}；预计送达：{status.estimated_delivery_date}；"
        f"实际送达：{status.delivered_customer_date or '未送达'}；{delay}。\n"
        f"支付金额：{status.payment_value:.2f}；"
        f"评价分：{status.review_score if status.review_score is not None else '暂无'}。"
        f"{lifecycle_text}"
    )


def format_after_sales_report(report: dict[str, object]) -> str:
    categories = report.get("high_risk_categories", [])
    orders = report.get("priority_orders", [])
    lines = ["审核台优先处理建议：", str(report.get("summary", "")), "", "高风险类目 Top："]
    for item in categories[:5]:
        lines.append(
            "- {category}: risk_score={risk_score:.3f}, delay={delay_rate:.2%}, "
            "low_review={low_review_rate:.2%}, cancel={cancellation_rate:.2%}; {action}".format(
                category=item["category"],
                risk_score=float(item["risk_score"]),
                delay_rate=float(item["delay_rate"]),
                low_review_rate=float(item["low_review_rate"]),
                cancellation_rate=float(item["cancellation_rate"]),
                action=item["recommended_action"],
            )
        )
    lines.extend(["", "优先跟进订单 Top："])
    for item in orders[:8]:
        lines.append(
            "- {order_id}: score={priority_score:.2f}, status={status}, delay={delay}, "
            "review={review}; {action}".format(
                order_id=item["order_id"],
                priority_score=float(item["priority_score"]),
                status=item["status"],
                delay=item["delay_days"],
                review=item["review_score"],
                action=item["recommended_action"],
            )
        )
    lines.append("")
    lines.append("注意：以上是审核台只读排序建议；退款、取消、改地址、发票和工单创建仍需 HITL 确认。")
    return "\n".join(lines)


def _ops_risk_score(risk: dict) -> float:
    volume_factor = min(int(risk["order_count"]) / 10000, 1.0)
    return round(
        float(risk["delay_rate"]) * 0.35
        + float(risk["low_review_rate"]) * 0.40
        + float(risk["cancellation_rate"]) * 0.15
        + volume_factor * 0.10,
        4,
    )


def _category_action(risk: dict) -> str:
    actions = []
    if float(risk["delay_rate"]) >= 0.08:
        actions.append("check logistics SLA and delayed-order follow-up")
    if float(risk["low_review_rate"]) >= 0.12:
        actions.append("review customer feedback and improve support scripts")
    if float(risk["cancellation_rate"]) >= 0.02:
        actions.append("inspect cancellation reasons before promotion")
    return "; ".join(actions) if actions else "monitor weekly trend"


def _order_priority(order: dict) -> tuple[float, list[str]]:
    score = 0.0
    reasons = []
    delay_days = order.get("delay_days")
    review_score = order.get("review_score")
    if isinstance(delay_days, int) and delay_days > 0:
        score += min(delay_days, 30) * 0.5
        reasons.append(f"delayed {delay_days} day(s)")
    if isinstance(review_score, int) and review_score <= 2:
        score += (3 - review_score) * 4
        reasons.append(f"low review score {review_score}")
    if order.get("status") == "canceled":
        score += 5
        reasons.append("canceled order")
    payment_value = float(order.get("payment_value") or 0)
    if payment_value >= 300:
        score += 2
        reasons.append(f"high value {payment_value:.2f}")
    return round(score, 2), reasons


def _order_action(order: dict) -> str:
    if order.get("status") == "canceled":
        return "prepare cancellation explanation and retention check"
    review_score = order.get("review_score")
    delay_days = order.get("delay_days")
    if isinstance(delay_days, int) and delay_days > 0 and isinstance(review_score, int) and review_score <= 2:
        return "prepare escalation draft and proactive apology"
    if isinstance(delay_days, int) and delay_days > 0:
        return "send delayed-delivery follow-up"
    if isinstance(review_score, int) and review_score <= 2:
        return "inspect review issue and support recovery"
    return "monitor"
