"""
迁移：把 wei 计价列从 64 位整数加宽为 String(80)/VARCHAR。

背景
----
私有化改造把结算币换成 18 位精度的华币(HUA)，奖励等金额以 wei(最小单位)存储。
但 ``BigInteger`` 是 64 位有符号整数(上限约 9.22e18，即 ~9.22 华币)，任意 ≥ ~9.22
华币的金额都会溢出——发任务直接 500。

模型已把这些列改为 ``WeiInt``(以十进制字符串存储、Python 侧仍是 int)。对**新库**
``create_all`` 会直接建成 ``VARCHAR(80)``；对**存量 MySQL 库**需本脚本把既有
``BIGINT`` 列重建为 ``VARCHAR(80)``。

用法
----
    python migrate_wei_columns.py                 # 用 .env 的 DATABASE_URL(MySQL)
    python migrate_wei_columns.py <db_url>        # 显式 MySQL 连接串

幂等：已是 VARCHAR 的列会被 batch_alter_table 原样重建，不影响数据。
运行前请备份数据库。
"""
import os
import sys

from sqlalchemy import create_engine, String, BigInteger, inspect
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


def _table_exists(engine, name: str) -> bool:
    return name in inspect(engine).get_table_names()


def migrate(db_url: str) -> None:
    engine = create_engine(db_url)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        for table, cols in WEI_COLUMNS.items():
            if not _table_exists(engine, table):
                print(f"  跳过 {table}（不存在）")
                continue
            with op.batch_alter_table(table) as batch:
                for c in cols:
                    batch.alter_column(c, type_=String(80), existing_type=BigInteger())
            print(f"  {table}: {', '.join(cols)} -> String(80)/VARCHAR")
    print("迁移完成")


def main() -> None:
    if len(sys.argv) > 1:
        db_url = sys.argv[1]
    else:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            print("错误：未设置 DATABASE_URL，且未提供连接串参数")
            sys.exit(1)
    print(f"目标数据库: {db_url}")
    migrate(db_url)


if __name__ == "__main__":
    main()
