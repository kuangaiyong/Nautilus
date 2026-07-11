"""auto_review_and_settle_submitted_se_tasks 回归测试：SUBMITTED 的 SE 任务自动
3 专家评审 + 达标结算(→COMPLETED)。

背景 bug：SUBMITTED→评审→COMPLETED 此前无自动驱动，任务卡在等发布者手动点「完成」。
本函数以发布者身份复用 api.tasks.complete_task 的完整评审+结算逻辑；此处 monkeypatch
complete_task，聚焦「筛选哪些任务 + limit + 异常隔离 + 状态统计」的确定性回归
（真实 3 专家 LLM 评审与链上华币/NAU 结算由 e2e_* 脚本覆盖，不在此 mock）。

注：当前 TaskType 8 类全为 SE，is_se_task 恒真，故不构造「非 SE」用例（模型层已无此值）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.testdb import TEST_DATABASE_URL
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base, Task, User, TaskType, TaskStatus
import api.tasks as api_tasks
import services.se_pouw_flow as flow

engine = create_engine(
    TEST_DATABASE_URL
)
TestingSessionLocal = sessionmaker(bind=engine)


import pytest


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = TestingSessionLocal()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(bind=engine)


def _user(db, uid, wallet):
    db.add(User(id=uid, username=f"u{uid}", email=f"u{uid}@t.io",
                hashed_password="x", wallet_address=wallet))


def _task(db, tid, publisher, status, result="交付物内容"):
    db.add(Task(id=tid, task_id=f"t_{tid}", publisher=publisher,
                description="设计微信登录测试用例", reward=1000,
                task_type=TaskType.TEST_CASE_DESIGN, status=status,
                agent="0xagentexecutor", result=result, timeout=3600,
                created_at=datetime.utcnow()))


def _fake_ok(called):
    async def fake_complete(task_id, current_user, db):
        called.append(task_id)
        t = db.query(Task).filter(Task.id == task_id).first()
        t.status = TaskStatus.COMPLETED
        db.commit()
        return t
    return fake_complete


async def test_only_submitted_settled(db, monkeypatch):
    """只结算 SUBMITTED 任务；ACCEPTED/OPEN 不动。"""
    called = []
    monkeypatch.setattr(api_tasks, "complete_task", _fake_ok(called))
    _user(db, 1, "0xPUB")
    _task(db, 10, "0xPUB", TaskStatus.SUBMITTED)
    _task(db, 11, "0xPUB", TaskStatus.ACCEPTED)
    _task(db, 12, "0xPUB", TaskStatus.OPEN)
    db.commit()

    n = await flow.auto_review_and_settle_submitted_se_tasks(db, limit=10)
    assert n == 1
    assert called == [10]
    assert db.query(Task).filter(Task.id == 10).first().status == TaskStatus.COMPLETED
    assert db.query(Task).filter(Task.id == 11).first().status == TaskStatus.ACCEPTED
    assert db.query(Task).filter(Task.id == 12).first().status == TaskStatus.OPEN


async def test_limit_caps_per_round(db, monkeypatch):
    """每轮最多处理 limit 个，其余留待下轮。"""
    called = []
    monkeypatch.setattr(api_tasks, "complete_task", _fake_ok(called))
    _user(db, 1, "0xPUB")
    for tid in (10, 11, 12):
        _task(db, tid, "0xPUB", TaskStatus.SUBMITTED)
    db.commit()

    n = await flow.auto_review_and_settle_submitted_se_tasks(db, limit=2)
    assert n == 2
    assert len(called) == 2
    remaining = [t.id for t in db.query(Task).filter(Task.status == TaskStatus.SUBMITTED).all()]
    assert len(remaining) == 1  # 剩 1 个下轮再处理


async def test_http_exception_isolated_and_retryable(db, monkeypatch):
    """某任务 503(评审不全)/400(余额不足) → 跳过、留 SUBMITTED、不中断后续任务。"""
    from fastapi import HTTPException

    async def fake_complete(task_id, current_user, db):
        if task_id == 10:
            raise HTTPException(status_code=503, detail="专家评审未完成")
        t = db.query(Task).filter(Task.id == task_id).first()
        t.status = TaskStatus.COMPLETED
        db.commit()
        return t
    monkeypatch.setattr(api_tasks, "complete_task", fake_complete)

    _user(db, 1, "0xPUB")
    _task(db, 10, "0xPUB", TaskStatus.SUBMITTED)  # 抛 503 → 留 SUBMITTED
    _task(db, 11, "0xPUB", TaskStatus.SUBMITTED)  # 正常 → COMPLETED
    db.commit()

    n = await flow.auto_review_and_settle_submitted_se_tasks(db, limit=10)
    assert n == 1
    assert db.query(Task).filter(Task.id == 10).first().status == TaskStatus.SUBMITTED
    assert db.query(Task).filter(Task.id == 11).first().status == TaskStatus.COMPLETED


async def test_no_publisher_user_skipped(db, monkeypatch):
    """发布者钱包无对应 User → 跳过，不调 complete_task，任务保持 SUBMITTED。"""
    called = []
    monkeypatch.setattr(api_tasks, "complete_task", _fake_ok(called))
    _task(db, 10, "0xNOUSER", TaskStatus.SUBMITTED)  # 无对应 User
    db.commit()

    n = await flow.auto_review_and_settle_submitted_se_tasks(db, limit=10)
    assert n == 0
    assert called == []
    assert db.query(Task).filter(Task.id == 10).first().status == TaskStatus.SUBMITTED
