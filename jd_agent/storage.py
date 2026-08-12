from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jd_agent.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event TEXT NOT NULL,
                run_id TEXT NOT NULL,
                metadata TEXT NOT NULL
            )
            """
        )


def log_event(event: str, run_id: str, metadata: dict[str, Any] | None = None) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO audit_log(created_at, event, run_id, metadata) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), event, run_id, json.dumps(metadata or {}, ensure_ascii=False)),
        )


def recent_events(run_id: str, limit: int = 30) -> list[dict[str, Any]]:
    if not run_id.strip():
        return []
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT created_at, event, run_id, metadata
            FROM audit_log
            WHERE run_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
