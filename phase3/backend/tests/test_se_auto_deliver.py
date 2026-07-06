"""auto_deliver_accepted_se_tasks 回归测试：SE 任务中标(ACCEPTED)后自动交付(→SUBMITTED)。

背景 bug：SE 任务竞价中标置 ACCEPTED 后，此前没有任何机制驱动中标的自主智能体生成
交付物并提交，任务永久卡在 ACCEPTED（不进自动执行器、也无 SE 专属交付流程）。

真实 LLM 端到端交付由运行期 se_marketplace cron 及 e2e 覆盖；此处隔离 LLM 依赖
(monkeypatch _generate_deliverable)，聚焦"筛选哪些任务 + 状态如何流转"的确定性回归。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models.database import Base, Task, Agent, TaskType, TaskStatus
import services.se_pouw_flow as flow

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(bind=engine)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    s = TestingSessionLocal()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(bind=engine)


def _agent(db, agent_id, owner, autonomy):
    db.add(Agent(agent_id=agent_id, name=f"agent{agent_id}", owner=owner,
                 reputation_score=60.0, autonomy_enabled=autonomy, specialties="test,测试"))


def _task(db, tid, agent_owner, status, result=None):
    db.add(Task(id=tid, task_id=f"t_{tid}", publisher="0xpublisher",
                description="设计微信登录相关的测试用例", reward=1000,
                task_type=TaskType.TEST_CASE_DESIGN, status=status,
                agent=agent_owner, result=result, timeout=3600,
                created_at=datetime.utcnow()))


def test_auto_deliver_only_autonomy_accepted_empty(db, monkeypatch):
    """只交付「指派给自主智能体、ACCEPTED、result 为空」的 SE 任务，其余保持不变。"""
    monkeypatch.setattr(flow, "_generate_deliverable", lambda *a, **k: "FAKE_DELIVERABLE")
    _agent(db, 101, "0xAUTON", True)
    _agent(db, 102, "0xMANUAL", False)
    _task(db, 1, "0xAUTON", TaskStatus.ACCEPTED)                   # 应被交付
    _task(db, 2, "0xMANUAL", TaskStatus.ACCEPTED)                 # 非自主 → 跳过
    _task(db, 3, "0xAUTON", TaskStatus.ACCEPTED, result="已交付")  # result 非空 → 跳过
    _task(db, 4, "0xAUTON", TaskStatus.OPEN)                      # 非 ACCEPTED → 跳过
    db.commit()

    n = flow.auto_deliver_accepted_se_tasks(db)
    assert n == 1
    t1 = db.query(Task).filter(Task.id == 1).first()
    assert t1.status == TaskStatus.SUBMITTED
    assert t1.result == "FAKE_DELIVERABLE"
    assert t1.submitted_at is not None
    assert db.query(Task).filter(Task.id == 2).first().status == TaskStatus.ACCEPTED
    assert db.query(Task).filter(Task.id == 3).first().status == TaskStatus.ACCEPTED
    assert db.query(Task).filter(Task.id == 4).first().status == TaskStatus.OPEN


def test_auto_deliver_skips_when_llm_unavailable(db, monkeypatch):
    """LLM 不可用(返回 None)时不写垃圾、保持 ACCEPTED 供下轮重试。"""
    monkeypatch.setattr(flow, "_generate_deliverable", lambda *a, **k: None)
    _agent(db, 201, "0xAUTON", True)
    _task(db, 10, "0xAUTON", TaskStatus.ACCEPTED)
    db.commit()

    n = flow.auto_deliver_accepted_se_tasks(db)
    assert n == 0
    t = db.query(Task).filter(Task.id == 10).first()
    assert t.status == TaskStatus.ACCEPTED
    assert not (t.result or "")
