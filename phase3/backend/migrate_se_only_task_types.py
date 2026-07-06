"""任务类型全面 SE 化迁移（幂等）：

1) 备份 tasks.task_type 快照到 tasks_tasktype_bak_20260705（含 id，可按 id 还原回滚）
2) ALTER tasks.task_type ENUM = 旧 7 值 ∪ SE 8 值（过渡态，允许 UPDATE 映射）
3) UPDATE 存量旧类型 → SE 类型（CODE→CODE_DEVELOPMENT 等）
4) ALTER 收窄为纯 8 个 SE 值（strict mode 下有漏网旧值会报错中止，天然安全阀）

回滚：按备份表还原 task_type 后，把 ENUM ALTER 回旧定义（见 migrate_se_pouw.py 的 NEW_ENUM）。

用法（CWD=phase3/backend）: C:/nautilus-venv/Scripts/python.exe migrate_se_only_task_types.py
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import text                # noqa: E402
from utils.database import engine          # noqa: E402

# 旧通用类型 → SE 类型映射（当前存量仅 CODE 有行，其余为兜底）
OLD_TO_SE = {
    "CODE": "CODE_DEVELOPMENT",
    "DATA": "CODE_DEVELOPMENT",
    "COMPUTE": "CODE_DEVELOPMENT",
    "RESEARCH": "REQUIREMENT_ANALYSIS",
    "DESIGN": "ARCHITECTURE_DESIGN",
    "WRITING": "DOCUMENTATION",
    "OTHER": "CODE_DEVELOPMENT",
}
# 与 models.database.TaskType 一致的 8 个 SE 类型
SE8 = (
    "REQUIREMENT_ANALYSIS", "ARCHITECTURE_DESIGN", "CODE_DEVELOPMENT", "CODE_REVIEW",
    "TEST_CASE_DESIGN", "TEST_AUTOMATION", "DEPLOYMENT_OPS", "DOCUMENTATION",
)

UNION_ENUM = "ENUM(" + ",".join(f"'{v}'" for v in (*OLD_TO_SE, *SE8)) + ")"
FINAL_ENUM = "ENUM(" + ",".join(f"'{v}'" for v in SE8) + ")"
BAK = "tasks_tasktype_bak_20260705"

with engine.begin() as c:
    # 1) 备份快照（幂等：已存在则不重建，保留首次迁移前的原值）
    c.execute(text(f"CREATE TABLE IF NOT EXISTS {BAK} AS SELECT id, task_type FROM tasks"))
    n_bak = c.execute(text(f"SELECT COUNT(*) FROM {BAK}")).scalar()
    print(f"[1] 备份表 {BAK}: {n_bak} 行")

    # 2) 过渡 ENUM（旧 ∪ 新），使旧值行可被 UPDATE
    c.execute(text(f"ALTER TABLE tasks MODIFY COLUMN task_type {UNION_ENUM} NOT NULL"))
    print("[2] task_type ENUM 已切到过渡态（旧 7 + SE 8）")

    # 3) 存量映射
    for old, new in OLD_TO_SE.items():
        n = c.execute(text("UPDATE tasks SET task_type = :new WHERE task_type = :old"),
                      {"new": new, "old": old}).rowcount
        if n:
            print(f"[3] {old} -> {new}: {n} 行")

    # 4) 收窄为纯 SE 8 值（若仍有旧值，strict mode 会在此报错中止）
    c.execute(text(f"ALTER TABLE tasks MODIFY COLUMN task_type {FINAL_ENUM} NOT NULL"))
    print("[4] task_type ENUM 已收窄为 8 个 SE 类型")

# 校验
with engine.connect() as c:
    ct = c.execute(text(
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='tasks' AND COLUMN_NAME='task_type'")).scalar()
    print("   task_type =", ct)
    print("   迁移后分布:")
    for row in c.execute(text("SELECT task_type, COUNT(*) FROM tasks GROUP BY task_type")):
        print("    ", row[0], row[1])
print("DONE")
