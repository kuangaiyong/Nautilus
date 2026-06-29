"""SQLite -> MySQL 全量迁移。

源:  SQLite  nautilus_private.db（私有化运行期的真实数据）
目标:MySQL   nautilus_private（root@127.0.0.1:3306）

做三件事，幂等可重跑：
1) create_all 建全部 SQLAlchemy 模型表（33 张）
2) 用 MySQL DDL 建 6 张 Alembic-only 的观测/进化表（模型里没有）
3) 把 SQLite 中所有非空业务表的数据拷到 MySQL（FK 检查临时关闭；
   datetime 归一为 MySQL 格式；WeiInt/VARCHAR 列以字符串原样保真）

用法（CWD=phase3/backend）:  C:/nautilus-venv/Scripts/python.exe migrate_to_mysql.py
"""
import os
import re
import sqlite3

MYSQL_URL = "mysql+pymysql://root:VideoEval2026@127.0.0.1:3306/nautilus_private?charset=utf8mb4"
os.environ["DATABASE_URL"] = MYSQL_URL  # 让 utils/models 的 import 走 MySQL

# 注册全部模型（与 main.py 完全一致）
import models.database, models.payment, models.team, models.raid          # noqa: E402,F401
import models.agent_survival, models.partner, models.conversation, models.marketplace_models  # noqa: E402,F401
from models.database import Base                                           # noqa: E402
from sqlalchemy import create_engine, text                                # noqa: E402
import pymysql                                                            # noqa: E402

SQLITE_DB = "nautilus_private.db"

# 6 张模型里没有、只在 Alembic 迁移里定义的观测/进化表（json-ish 字段用 LONGTEXT，
# 与现有代码 json.dumps/json.loads 的字符串读写方式一致；省略跨表 FK 以免迁移期约束冲突）。
RAW_DDL = [
    """CREATE TABLE IF NOT EXISTS platform_metrics_snapshots (
        id INT AUTO_INCREMENT PRIMARY KEY,
        snapshot_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        metrics LONGTEXT NOT NULL,
        anomalies LONGTEXT,
        health_score DOUBLE,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_snapshots_time (snapshot_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS platform_evolution_log (
        id VARCHAR(16) PRIMARY KEY,
        proposal_id VARCHAR(64) NOT NULL,
        proposer_agent_id INT NOT NULL,
        version_str VARCHAR(20) NOT NULL,
        minor_version INT NOT NULL UNIQUE,
        change_type VARCHAR(50),
        proposed_change LONGTEXT,
        sandbox_result LONGTEXT,
        metric_delta DOUBLE NOT NULL DEFAULT 0,
        nau_rewarded DOUBLE NOT NULL DEFAULT 0,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_evolution_proposal (proposal_id),
        INDEX idx_evolution_version (minor_version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS pending_nau_rewards (
        id INT AUTO_INCREMENT PRIMARY KEY,
        agent_id INT NOT NULL,
        amount DOUBLE NOT NULL,
        reason TEXT,
        source_id VARCHAR(64),
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        tx_hash VARCHAR(66),
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fulfilled_at DATETIME,
        INDEX idx_pending_nau_agent (agent_id),
        INDEX idx_pending_nau_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS sandbox_experiments (
        id VARCHAR(64) PRIMARY KEY,
        proposal_id VARCHAR(64),
        proposed_change LONGTEXT,
        status VARCHAR(20),
        sandbox_traffic_pct DOUBLE,
        observation_hours DOUBLE,
        baseline_metrics LONGTEXT,
        sandbox_metrics LONGTEXT,
        ends_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finalized_at DATETIME
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS platform_improvement_proposals (
        id VARCHAR(36) PRIMARY KEY,
        task_id INT,
        agent_id INT,
        root_cause TEXT NOT NULL,
        proposed_change LONGTEXT NOT NULL,
        expected_impact TEXT,
        rollback_plan TEXT,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        vote_score DOUBLE NOT NULL DEFAULT 0,
        vote_count INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_proposals_task_id (task_id),
        INDEX idx_proposals_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS platform_proposal_votes (
        id INT AUTO_INCREMENT PRIMARY KEY,
        proposal_id VARCHAR(36) NOT NULL,
        agent_id INT NOT NULL,
        vote INT NOT NULL,
        weight DOUBLE NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_proposal_agent_vote (proposal_id, agent_id),
        INDEX idx_votes_proposal_id (proposal_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]

_DT_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def _norm_dt(v):
    """'2026-06-27T11:45:07.123+00:00' / '...Z' -> '2026-06-27 11:45:07.123'（MySQL DATETIME 可解析）。"""
    if not isinstance(v, str) or not _DT_HEAD.match(v):
        return v
    s = v.replace("T", " ")
    s = re.split(r"[+]", s)[0]
    if s.endswith("Z"):
        s = s[:-1]
    return s.strip()


def build_schema():
    eng = create_engine(MYSQL_URL)
    Base.metadata.create_all(eng)
    with eng.begin() as cx:
        for ddl in RAW_DDL:
            cx.execute(text(ddl))
    eng.dispose()
    print("[schema] create_all + 6 raw 表完成")


def mysql_columns(myc):
    """{table: {col: data_type}} for current DB."""
    cur = myc.cursor()
    cur.execute(
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA='nautilus_private'"
    )
    out = {}
    for tbl, col, dt in cur.fetchall():
        out.setdefault(tbl, {})[col] = dt
    return out


def copy_data():
    sl = sqlite3.connect(SQLITE_DB)
    sl.row_factory = sqlite3.Row
    my = pymysql.connect(host="127.0.0.1", port=3306, user="root",
                         password="VideoEval2026", database="nautilus_private",
                         charset="utf8mb4", autocommit=False)
    mycols = mysql_columns(my)
    # SQLite 中的非空表（跳过 sqlite 内部表）
    tables = [r[0] for r in sl.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    cur = my.cursor()
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    report = []
    for t in tables:
        if t not in mycols:
            continue  # 模型/raw 都没有的表（如纯 SQLite 辅助表），跳过
        rows = sl.execute(f'SELECT * FROM "{t}"').fetchall()
        if not rows:
            continue
        src_cols = rows[0].keys()
        cols = [c for c in src_cols if c in mycols[t]]  # 只迁两边都有的列
        dt_cols = {c for c in cols if mycols[t][c] in ("datetime", "timestamp", "date")}
        cur.execute(f"DELETE FROM `{t}`")  # 幂等：先清后灌
        placeholders = ",".join(["%s"] * len(cols))
        collist = ",".join(f"`{c}`" for c in cols)
        sql = f"INSERT INTO `{t}` ({collist}) VALUES ({placeholders})"
        data = []
        for r in rows:
            vals = []
            for c in cols:
                v = r[c]
                if c in dt_cols:
                    v = _norm_dt(v)
                vals.append(v)
            data.append(vals)
        cur.executemany(sql, data)
        report.append((t, len(rows), len(cols)))
    cur.execute("SET FOREIGN_KEY_CHECKS=1")
    my.commit()
    # 校验：逐表比对行数
    print("[data] 迁移与校验:")
    ok = True
    for t, n, ncol in report:
        cur.execute(f"SELECT COUNT(*) FROM `{t}`")
        m = cur.fetchone()[0]
        flag = "OK" if m == n else "MISMATCH"
        if m != n:
            ok = False
        print(f"   {t:30s} sqlite={n:4d} -> mysql={m:4d} ({ncol} cols) {flag}")
    sl.close()
    my.close()
    print("[data] 全部一致" if ok else "[data] 存在不一致，请检查")
    return ok


if __name__ == "__main__":
    build_schema()
    copy_data()
