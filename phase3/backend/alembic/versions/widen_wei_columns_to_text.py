"""widen wei-denominated columns to text to avoid 64-bit integer overflow

18 位精度结算币(华币)的 wei 金额存进 64 位 BigInteger 会在 ~9.22 个代币处溢出。
把所有 wei 计价列改为 String(80)/TEXT，可精确存储任意 uint256 金额。模型侧用
WeiInt(TypeDecorator) 仍以 Python int 读写。

注：本仓库存量库由 create_all 管理(无 alembic_version)，对其请改用
``migrate_wei_columns.py``；本迁移供 alembic / PostgreSQL 管理的部署使用。

Revision ID: widen_wei_columns
Revises: f6a1b2c3d4e5
"""
from alembic import op
import sqlalchemy as sa

revision = 'widen_wei_columns'
down_revision = 'f6a1b2c3d4e5'
branch_labels = None
depends_on = None

# 受影响的 (表 -> wei 列)。
# 注：Agent.total_earnings 与 Reward.amount 故意排除——它们被 SQL ORDER BY /
# func.sum 使用，改成 TEXT 会导致字典序排序与浮点求和，保留 BigInteger。
WEI_COLUMNS = {
    "tasks": ["reward", "gas_cost", "gas_split"],
    "agent_survival": ["total_income", "total_cost"],
    "agent_transactions": ["amount"],
}


def _alter_all(from_type, to_type, cast_sql):
    pg = op.get_bind().dialect.name == "postgresql"
    for table, cols in WEI_COLUMNS.items():
        with op.batch_alter_table(table) as batch:
            for c in cols:
                kwargs = {"existing_type": from_type, "type_": to_type}
                if pg:
                    kwargs["postgresql_using"] = f"{c}::{cast_sql}"
                batch.alter_column(c, **kwargs)


def upgrade() -> None:
    _alter_all(sa.BigInteger(), sa.String(80), "text")


def downgrade() -> None:
    _alter_all(sa.String(80), sa.BigInteger(), "bigint")
