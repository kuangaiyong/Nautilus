"""SE PoUW 基座迁移（幂等）：
1) ALTER tasks.task_type ENUM 增加 5 个软件工程任务类型
2) 创建 dmas_task_bids / task_reviews 两张新表

用法（CWD=phase3/backend）: C:/nautilus-venv/Scripts/python.exe migrate_se_pouw.py
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(".env")

# 注册模型
import models.database, models.payment, models.team, models.raid          # noqa: E402,F401
import models.agent_survival, models.partner, models.conversation, models.marketplace_models  # noqa: E402,F401
from models.database import DmasTaskBid, TaskReview                        # noqa: E402
from sqlalchemy import text                                               # noqa: E402
from utils.database import engine                                         # noqa: E402

NEW_ENUM = ("ENUM('CODE','DATA','COMPUTE','RESEARCH','DESIGN','WRITING','OTHER',"
            "'REQUIREMENT_ANALYSIS','ARCHITECTURE_DESIGN','TEST_CASE_DESIGN',"
            "'TEST_AUTOMATION','CODE_DEVELOPMENT')")

with engine.begin() as c:
    c.execute(text(f"ALTER TABLE tasks MODIFY COLUMN task_type {NEW_ENUM} NOT NULL"))
    print("[1] tasks.task_type ENUM 已扩展 5 个 SE 类型")

# 仅建本特性的两张新表（checkfirst 幂等）
DmasTaskBid.__table__.create(engine, checkfirst=True)
TaskReview.__table__.create(engine, checkfirst=True)
print("[2] dmas_task_bids / task_reviews 已创建")

# 校验
with engine.connect() as c:
    ct = c.execute(text("SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                        "AND TABLE_NAME='tasks' AND COLUMN_NAME='task_type'")).scalar()
    print("   task_type =", ct)
    for t in ("dmas_task_bids", "task_reviews"):
        ok = c.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                            "AND TABLE_NAME=:t"), {"t": t}).scalar()
        print(f"   {t}: {'OK' if ok else 'MISSING'}")
print("DONE")
