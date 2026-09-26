"""Durable quarantine for writes whose completion cannot yet be established."""

import sqlite3
from contextlib import closing
from pathlib import Path


class WriteJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS write_attempts ("
                "operation_key TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "evidence_ref TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    def begin(self, key: str) -> bool:
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO write_attempts(operation_key,status) VALUES (?, 'submitted')",
                (key,),
            )
            return cursor.rowcount == 1

    def finish(self, key: str, succeeded: bool) -> None:
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            if succeeded:
                # The business store now owns durable successful deduplication.
                db.execute("DELETE FROM write_attempts WHERE operation_key=?", (key,))
            else:
                db.execute(
                    "UPDATE write_attempts SET status='outcome_unknown', "
                    "updated_at=CURRENT_TIMESTAMP WHERE operation_key=?",
                    (key,),
                )

    def release_unstarted(self, key: str) -> None:
        """Only the reserving caller may use this before dispatching a handler."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM write_attempts WHERE operation_key=? AND status='submitted'", (key,))

    def resolve(self, key: str, *, evidence_ref: str) -> None:
        """Internal reconciliation only; caller must verify provider/local state first."""
        if not evidence_ref.strip():
            raise ValueError("A reconciliation evidence reference is required")
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS write_reconciliations ("
                "operation_key TEXT, evidence_ref TEXT, resolved_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "INSERT INTO write_reconciliations(operation_key,evidence_ref) VALUES (?,?)",
                (key, evidence_ref),
            )
            db.execute("DELETE FROM write_attempts WHERE operation_key=?", (key,))
