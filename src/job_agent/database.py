from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ApplicationStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                idempotency_key TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                source_job_id TEXT NOT NULL,
                company TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                receipt_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_submissions_day
                ON submissions(created_at);

            CREATE TABLE IF NOT EXISTS job_scores (
                source TEXT NOT NULL,
                source_job_id TEXT NOT NULL,
                resume_sha256 TEXT NOT NULL,
                match_score INTEGER NOT NULL,
                qualifies INTEGER NOT NULL,
                decision_json TEXT NOT NULL,
                scored_at TEXT NOT NULL,
                PRIMARY KEY (source, source_job_id, resume_sha256)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def already_reserved(self, idempotency_key: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM submissions WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row is not None

    def reserve(
        self,
        idempotency_key: str,
        candidate_id: str,
        source_job_id: str,
        company: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO submissions(
                idempotency_key, candidate_id, source_job_id,
                company, status, created_at
            ) VALUES (?, ?, ?, ?, 'RESERVED', ?)
            """,
            (
                idempotency_key,
                candidate_id,
                source_job_id,
                company,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def record_receipt(self, idempotency_key: str, receipt: dict[str, Any]) -> None:
        self.connection.execute(
            """
            UPDATE submissions
               SET status = ?, receipt_json = ?
             WHERE idempotency_key = ?
            """,
            (receipt["status"], json.dumps(receipt, sort_keys=True), idempotency_key),
        )
        self.connection.commit()

    def release(self, idempotency_key: str) -> None:
        """Free a reservation when the human closed the form without submitting."""
        self.connection.execute(
            "DELETE FROM submissions WHERE idempotency_key = ? AND status = 'RESERVED'",
            (idempotency_key,),
        )
        self.connection.commit()

    def submitted_job_ids(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT source_job_id FROM submissions WHERE status IN ('SUBMITTED', 'SKIPPED')"
        ).fetchall()
        return {row["source_job_id"] for row in rows}

    def get_score(self, source: str, source_job_id: str, resume_sha256: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT decision_json FROM job_scores
                WHERE source = ? AND source_job_id = ? AND resume_sha256 = ?""",
            (source, source_job_id, resume_sha256),
        ).fetchone()
        return json.loads(row["decision_json"]) if row else None

    def save_score(self, source: str, source_job_id: str, resume_sha256: str, decision: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO job_scores
               (source, source_job_id, resume_sha256, match_score, qualifies, decision_json, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                source,
                source_job_id,
                resume_sha256,
                int(decision["match_score"]),
                int(bool(decision["qualifies"])),
                json.dumps(decision, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()

    def count_between(self, start: datetime, end: datetime) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS count
              FROM submissions
             WHERE created_at >= ?
               AND created_at < ?
               AND status IN ('RESERVED', 'SUBMITTED')
            """,
            (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()),
        ).fetchone()
        return int(row["count"])

    def audit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events(event_type, payload_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                event_type,
                json.dumps(payload, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()
