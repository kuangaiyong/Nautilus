"""创建平台观测台所需的 3 张表（SQLite）。

私有库由 create_all 管理、未跑 Alembic，故这些只在 Alembic 迁移里定义的表在
SQLite 中不存在，导致 /api/platform/snapshots|sandbox|evolution 报 "no such table"。
本脚本幂等（IF NOT EXISTS），改前请先备份 nautilus_private.db。

运行：  C:/nautilus-venv/Scripts/python.exe migrate_platform_observatory.py
"""
import os
import sqlite3

DB = os.getenv("SQLITE_DB", "nautilus_private.db")

DDL = [
    """CREATE TABLE IF NOT EXISTS platform_metrics_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_time TEXT NOT NULL DEFAULT (datetime('now')),
        metrics TEXT NOT NULL,
        anomalies TEXT DEFAULT '[]',
        health_score REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_time ON platform_metrics_snapshots(snapshot_time DESC)",
    """CREATE TABLE IF NOT EXISTS sandbox_experiments (
        id TEXT PRIMARY KEY,
        proposal_id TEXT,
        proposed_change TEXT,
        status TEXT,
        sandbox_traffic_pct REAL,
        observation_hours REAL,
        baseline_metrics TEXT,
        sandbox_metrics TEXT,
        ends_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        finalized_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS platform_evolution_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_str TEXT,
        minor_version INTEGER,
        change_type TEXT,
        metric_delta REAL,
        nau_rewarded REAL,
        status TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]


def main():
    con = sqlite3.connect(DB)
    try:
        for ddl in DDL:
            con.execute(ddl)
        con.commit()
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND (name LIKE 'platform_%' OR name LIKE 'sandbox_%') ORDER BY name"
        ).fetchall()]
        print("观测台表已就绪:", tables)
    finally:
        con.close()


if __name__ == "__main__":
    main()
