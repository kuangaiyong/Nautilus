"""
迁移：把 wei 计价列从 64 位整数加宽为 String(80)/TEXT 亲和性。

背景
----
私有化改造把结算币换成 18 位精度的华币(HUA)，奖励等金额以 wei(最小单位)存储。
但 ``BigInteger`` 在 SQLite / PostgreSQL 上都是 64 位有符号整数(上限约 9.22e18，
即 ~9.22 华币)，任意 ≥ ~9.22 华币的金额都会溢出——发任务直接 500。

模型已把这些列改为 ``WeiInt``(以十进制字符串存储、Python 侧仍是 int)。对**新库**
``create_all`` 会直接建成 ``VARCHAR(80)``；对**存量库**需本脚本把既有 ``BIGINT`` 列
重建为 ``VARCHAR(80)``——否则 SQLite 的 NUMERIC 亲和性会把大整数字符串转成 REAL 丢精度。

用法
----
    python migrate_wei_columns.py                 # 默认 DATABASE_URL / nautilus_private.db
    python migrate_wei_columns.py <db_path>

幂等：已是 String/VARCHAR 的列会被 batch_alter_table 原样重建，不影响数据。
运行前请停止后端(释放 SQLite 文件锁)并备份数据库。
"""
import os
import sys

from sqlalchemy import create_engine, String, BigInteger
from alembic.migration import MigrationContext
from alembic.operations import Operations

# 受影响的 (表 -> wei 列)。
# 注：Agent.total_earnings 与 Reward.amount 故意不在此列——它们被 SQL ORDER BY /
# func.sum 使用，改成 TEXT 会导致字典序排序与浮点求和，保留 BigInteger。
WEI_COLUMNS = {
    "tasks": ["reward", "gas_cost", "gas_split"],
    "agent_survival": ["total_income", "total_cost"],
    "agent_transactions": ["amount"],
}


def _table_exists(conn, name: str) -> bool:
    return conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def migrate(db_url: str) -> None:
    engine = create_engine(db_url)
    is_sqlite = engine.dialect.name == "sqlite"
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        for table, cols in WEI_COLUMNS.items():
            if is_sqlite and not _table_exists(conn, table):
                print(f"  跳过 {table}（不存在）")
                continue
            with op.batch_alter_table(table) as batch:
                for c in cols:
                    batch.alter_column(c, type_=String(80), existing_type=BigInteger())
            print(f"  {table}: {', '.join(cols)} -> String(80)/TEXT")
    print("迁移完成")


def main() -> None:
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        db_url = arg if "://" in arg else f"sqlite:///{arg}"
    else:
        db_url = os.getenv("DATABASE_URL", "sqlite:///./nautilus_private.db")
    print(f"目标数据库: {db_url}")
    migrate(db_url)


if __name__ == "__main__":
    main()
