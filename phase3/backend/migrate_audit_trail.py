"""链上可信追踪基座迁移（幂等）：创建 audit_logs 表。

用法（CWD=phase3/backend）: C:/nautilus-venv/Scripts/python.exe migrate_audit_trail.py

存量 MySQL 库由 create_all/独立脚本管理（非 Alembic）。本脚本仅新建本特性的一张表，
checkfirst 幂等，不触碰任何既有表结构。
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(".env")

# 注册模型
import models.database, models.payment, models.team, models.raid          # noqa: E402,F401
import models.agent_survival, models.partner, models.conversation, models.marketplace_models  # noqa: E402,F401
from models.database import AuditLog                                       # noqa: E402
from sqlalchemy import text                                               # noqa: E402
from utils.database import engine                                         # noqa: E402

# 仅建本特性的新表（checkfirst 幂等）
AuditLog.__table__.create(engine, checkfirst=True)
print("[1] audit_logs 已创建")

# 校验
with engine.connect() as c:
    ok = c.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                        "AND TABLE_NAME='audit_logs'")).scalar()
    print(f"   audit_logs: {'OK' if ok else 'MISSING'}")
print("DONE")
