"""Durable review commands bridge case state and independent graph checkpoints."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path


class ReviewCommandStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS review_commands (
                command_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                updated_at REAL NOT NULL)""")

    def enqueue(self, case: dict, *, resume_value: str, status: str, reviewer_id: str) -> str:
        command_id = f"{case['case_id']}:{case.get('appeal_count') or 0}"
        payload = json.dumps(dict(resume_value=resume_value, status=status, reviewer_id=reviewer_id))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO review_commands(command_id,session_id,payload,updated_at) "
                "VALUES(?,?,?,?)",
                (command_id, case["session_id"], payload, time.time()),
            )
        return command_id

    def enqueue_expired(self, now: float) -> int:
        # Selection and command insertion share the business database transaction.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM cases WHERE status IN ('pending_review','appealed_pending_review') "
                "AND expires_at <= ?",
                (now,),
            ).fetchall()
            count = 0
            for row in rows:
                payload = json.dumps(
                    dict(
                        resume_value="__hitl_timeout__",
                        status="hitl_timeout_canceled",
                        reviewer_id="system_timeout",
                    )
                )
                count += db.execute(
                    "INSERT OR IGNORE INTO review_commands(command_id,session_id,payload,updated_at) "
                    "VALUES(?,?,?,?)",
                    (f"{row['case_id']}:{row['appeal_count'] or 0}", row["session_id"], payload, now),
                ).rowcount
            return count

    def claim(self, command_id: str | None = None, lease: float = 120) -> dict | None:
        now = time.time()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM review_commands WHERE status != 'done' AND lease_until < ? "
                "AND (? IS NULL OR command_id = ?) ORDER BY updated_at LIMIT 1",
                (now, command_id, command_id),
            ).fetchone()
            if not row:
                return None
            owner = uuid.uuid4().hex
            db.execute(
                "UPDATE review_commands SET owner=?,lease_until=?,attempts=attempts+1,status='running' "
                "WHERE command_id=?",
                (owner, now + lease, row["command_id"]),
            )
            return {**dict(row), "owner": owner, "payload": json.loads(row["payload"])}

    def renew(self, command: dict, lease: float = 120) -> bool:
        with closing(sqlite3.connect(self.path)) as db, db:
            return bool(
                db.execute(
                    "UPDATE review_commands SET lease_until=? WHERE command_id=? AND owner=? "
                    "AND status='running' AND lease_until>?",
                    (time.time() + lease, command["command_id"], command["owner"], time.time()),
                ).rowcount
            )

    def finish(self, command: dict, error: str | None = None) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE review_commands SET status=?,lease_until=?,last_error=?,updated_at=? "
                "WHERE command_id=? AND owner=? AND lease_until>?",
                (
                    "pending" if error else "done",
                    time.time() + min(300, 2 ** min(command["attempts"] + 1, 8)) if error else 0,
                    error,
                    time.time(),
                    command["command_id"],
                    command["owner"],
                    time.time(),
                ),
            )
