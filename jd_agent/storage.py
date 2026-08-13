"""SQLite 存储 -- 事件日志、模板库、历史案例"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path.home() / ".workbuddy" / "jd_agent.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """初始化数据库表"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS templates (
            template_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            job_title TEXT NOT NULL,
            platform TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            tags TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS case_index (
            run_id TEXT PRIMARY KEY,
            job_title TEXT,
            platform TEXT,
            status TEXT DEFAULT '进行中',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
        CREATE INDEX IF NOT EXISTS idx_case_created ON case_index(created_at);
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 事件日志
# ---------------------------------------------------------------------------

def log_event(event: str, run_id: str, metadata: dict[str, Any]) -> None:
    """记录事件"""
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO events (run_id, event, metadata, created_at) VALUES (?, ?, ?, ?)",
        (run_id, event, json.dumps(metadata, ensure_ascii=False), now),
    )

    # 更新案例索引
    job_title = metadata.get("job_title", "")
    platform = metadata.get("platform", "")
    if event == "intake_extracted":
        conn.execute(
            "INSERT OR REPLACE INTO case_index (run_id, job_title, platform, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, job_title, platform, "需求澄清中", now),
        )
    elif event == "jd_generated":
        conn.execute(
            "UPDATE case_index SET job_title = ?, platform = ?, status = ? WHERE run_id = ?",
            (job_title, platform, "待审批", run_id),
        )
    elif event == "jd_approved":
        conn.execute(
            "UPDATE case_index SET status = ? WHERE run_id = ?",
            ("已审批", run_id),
        )
    elif event == "email_sent":
        conn.execute(
            "UPDATE case_index SET status = ? WHERE run_id = ?",
            ("已发布", run_id),
        )

    conn.commit()
    conn.close()


def recent_events(run_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """获取指定 run_id 的最近事件"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
        (run_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 模板管理
# ---------------------------------------------------------------------------

def save_template(name: str, job_title: str, platform: str, content: str, tags: list[str] | None = None) -> str:
    """保存 JD 模板"""
    template_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "INSERT INTO templates (template_id, name, job_title, platform, content, created_at, tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (template_id, name, job_title, platform, content, now, json.dumps(tags or [], ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return template_id


def load_templates(search: str = "") -> list[dict[str, Any]]:
    """加载模板列表"""
    conn = _get_conn()
    if search:
        rows = conn.execute(
            "SELECT * FROM templates WHERE name LIKE ? OR job_title LIKE ? ORDER BY created_at DESC",
            (f"%{search}%", f"%{search}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM templates ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    templates = []
    for row in rows:
        t = dict(row)
        t["tags"] = json.loads(t.get("tags", "[]"))
        templates.append(t)
    return templates


def delete_template(template_id: str) -> None:
    """删除模板"""
    conn = _get_conn()
    conn.execute("DELETE FROM templates WHERE template_id = ?", (template_id,))
    conn.commit()
    conn.close()


def get_template(template_id: str) -> dict[str, Any] | None:
    """获取单个模板"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM templates WHERE template_id = ?", (template_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    t = dict(row)
    t["tags"] = json.loads(t.get("tags", "[]"))
    return t


# ---------------------------------------------------------------------------
# 历史案例
# ---------------------------------------------------------------------------

def all_cases(limit: int = 100, search: str = "") -> list[dict[str, Any]]:
    """获取所有历史案例"""
    conn = _get_conn()
    if search:
        rows = conn.execute(
            """SELECT ci.*, COUNT(e.id) as event_count
               FROM case_index ci
               LEFT JOIN events e ON ci.run_id = e.run_id
               WHERE ci.job_title LIKE ? OR ci.platform LIKE ? OR ci.status LIKE ?
               GROUP BY ci.run_id
               ORDER BY ci.created_at DESC
               LIMIT ?""",
            (f"%{search}%", f"%{search}%", f"%{search}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT ci.*, COUNT(e.id) as event_count
               FROM case_index ci
               LEFT JOIN events e ON ci.run_id = e.run_id
               GROUP BY ci.run_id
               ORDER BY ci.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def case_events(run_id: str) -> list[dict[str, Any]]:
    """获取指定案例的所有事件"""
    return recent_events(run_id, limit=200)


def case_stats() -> dict[str, Any]:
    """获取全局统计"""
    conn = _get_conn()
    total_cases = conn.execute("SELECT COUNT(*) as c FROM case_index").fetchone()["c"]
    total_events = conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
    total_templates = conn.execute("SELECT COUNT(*) as c FROM templates").fetchone()["c"]

    status_counts: dict[str, int] = {}
    rows = conn.execute("SELECT status, COUNT(*) as c FROM case_index GROUP BY status").fetchall()
    for row in rows:
        status_counts[row["status"]] = row["c"]

    conn.close()
    return {
        "total_cases": total_cases,
        "total_events": total_events,
        "total_templates": total_templates,
        "status_counts": status_counts,
    }
