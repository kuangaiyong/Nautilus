"""F1 回归：LLM 网关不可用时 SE 三专家评审 fail-closed。

真实 DB + 真实 LLM（无 mock），两阶段：
- 阶段A（LLM 不可用：pop LLM_BASE_URL）→ 评审回落 degraded，不持久化，complete=False、n=0
  （修复前：回落中性分 3.0 会被持久化 → avg=3.0≥3.0 → passed=True → 误结算/铸币）
- 阶段B（LLM 可用：恢复）→ 真实 3 专家评审，complete=True、n>=3，持久化 3 条

运行（CWD=phase3/backend）：C:/nautilus-venv/Scripts/python.exe tests/e2e_review_failclosed.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()  # 从 phase3/backend/.env 加载 LLM_BASE_URL 等（CWD 须为 backend）

from utils.database import SessionLocal
import models.agent_survival  # noqa: F401  注册 AgentSurvival mapper（Agent 有跨模块 relationship）
from models.database import Task, TaskType, TaskStatus, TaskReview, Agent


def main():
    db = SessionLocal()
    agents = db.query(Agent).filter(Agent.owner.isnot(None)).all()
    assert len(agents) >= 5, f"需要≥5个有 owner 的智能体，当前 {len(agents)}"
    publisher = agents[0].owner
    executor = agents[1].owner

    t = Task(
        task_id=f"test_f1_{uuid.uuid4().hex[:8]}",
        publisher=publisher,
        description="实现两数之和函数 add(a,b) 并附 assert 测试",
        input_data="",
        expected_output="可运行且通过断言的 Python 代码",
        reward=10 ** 18,
        task_type=TaskType.CODE_DEVELOPMENT,
        status=TaskStatus.SUBMITTED,
        agent=executor,
        result="def add(a, b):\n    return a + b\n\nassert add(1, 2) == 3\n",
        timeout=86400,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    tid = t.id
    ok = False
    try:
        from services.se_pouw_flow import run_expert_reviews

        # 阶段A：LLM 不可用
        saved = os.environ.pop("LLM_BASE_URL", None)
        db.rollback()  # 清 MySQL 可重复读快照
        resA = run_expert_reviews(db, tid)
        nA = db.query(TaskReview).filter(TaskReview.task_id == tid).count()
        print(f"PHASE_A (llm_off): complete={resA['complete']} passed={resA['passed']} n={resA['n']} persisted={nA}")
        okA = (resA["complete"] is False) and (resA["n"] == 0) and (nA == 0)

        # 阶段B：恢复 LLM
        if saved is not None:
            os.environ["LLM_BASE_URL"] = saved
        db.rollback()
        resB = run_expert_reviews(db, tid)
        nB = db.query(TaskReview).filter(TaskReview.task_id == tid).count()
        print(f"PHASE_B (llm_on):  complete={resB['complete']} passed={resB['passed']} n={resB['n']} persisted={nB}")
        okB = (resB["complete"] is True) and (resB["n"] >= 3) and (nB >= 3)

        ok = okA and okB
        print(f"okA={okA} okB={okB}")
        print("F1_PASS" if ok else "F1_FAIL")
    finally:
        db.query(TaskReview).filter(TaskReview.task_id == tid).delete()
        db.query(Task).filter(Task.id == tid).delete()
        db.commit()
        db.close()
    sys.exit(0 if ok else 1)


main()
