"""Customer-visible conversation ledger, separate from internal execution traces."""

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "support_conversations.db"


def initialize() -> None:
    with sqlite3.connect(DB) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, sender TEXT NOT NULL,
                content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS conversation_session
                ON conversation_messages(session_id, id);
            CREATE TABLE IF NOT EXISTS conversation_modes (
                session_id TEXT PRIMARY KEY, human INTEGER NOT NULL DEFAULT 0
            );
        """)


def append_message(session_id: str, sender: str, content: str) -> None:
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO conversation_messages(session_id, sender, content) VALUES (?, ?, ?)",
            (session_id, sender, content),
        )


def messages(session_id: str) -> list[dict]:
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, sender, content, created_at FROM conversation_messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT 100", (session_id,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def human_mode(session_id: str) -> bool:
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT human FROM conversation_modes WHERE session_id = ?", (session_id,),
        ).fetchone()
    return bool(row and row[0])


def recent_sessions() -> list[dict]:
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT session_id, MAX(created_at) AS updated_at FROM conversation_messages "
            "GROUP BY session_id ORDER BY MAX(id) DESC LIMIT 50"
        ).fetchall()
    return [dict(row) for row in rows]


def set_human_mode(session_id: str, enabled: bool) -> None:
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO conversation_modes VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET human=excluded.human",
            (session_id, int(enabled)),
        )
