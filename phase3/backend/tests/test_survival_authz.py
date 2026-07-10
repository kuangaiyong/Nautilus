"""
回归测试：生存机制财务写入端点的归属鉴权。

复现并锁定漏洞：POST /api/agents/{agent_id}/transactions/income|cost 与
/api/agents/{agent_id}/survival/update 原来只校验「已登录」，任意登录用户
都能伪造他人 agent 的收入/成本/评分（进而拉高其对外展示收入与生存等级）。
修复：非 agent 所有者（且非管理员）一律 403。
"""
import sys
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import Base, User, Agent
from models.agent_survival import AgentSurvival
from utils.database import get_db
from utils.auth import hash_password, create_access_token
from api.survival import router as survival_router


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    app = FastAPI()
    app.include_router(survival_router, prefix="/api")
    app.dependency_overrides[get_db] = _override_get_db

    # 归属人 owner 拥有 agent 7001；attacker 与该 agent 无关
    db = TestingSessionLocal()
    try:
        db.add(User(username="s_owner", email="s_owner@e.com",
                    hashed_password=hash_password("x"),
                    wallet_address="0x00000000000000000000000000000000000a7001"))
        db.add(User(username="s_attacker", email="s_attacker@e.com",
                    hashed_password=hash_password("x"),
                    wallet_address="0x00000000000000000000000000000000000a9999"))
        db.add(Agent(agent_id=7001, name="owned agent",
                     owner="0x00000000000000000000000000000000000a7001"))
        db.add(AgentSurvival(agent_id=7001, total_income=0, total_cost=0))
        db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)


def _auth(username: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(data={'sub': username})}"}


def test_income_rejected_for_non_owner(client):
    """他人不能给别人的 agent 记收入。"""
    r = client.post(
        "/api/agents/7001/transactions/income",
        json={"amount": 10**21, "category": "TASK_REWARD"},
        headers=_auth("s_attacker"),
    )
    assert r.status_code == 403
    # 未被写入
    db = TestingSessionLocal()
    try:
        s = db.query(AgentSurvival).filter(AgentSurvival.agent_id == 7001).first()
        assert int(s.total_income) == 0
    finally:
        db.close()


def test_cost_rejected_for_non_owner(client):
    r = client.post(
        "/api/agents/7001/transactions/cost",
        json={"amount": 10**18, "category": "COMPUTE_COST"},
        headers=_auth("s_attacker"),
    )
    assert r.status_code == 403


def test_survival_update_rejected_for_non_owner(client):
    r = client.post(
        "/api/agents/7001/survival/update",
        json={"task_score_delta": 9999},
        headers=_auth("s_attacker"),
    )
    assert r.status_code == 403


def test_income_allowed_for_owner(client):
    """所有者可给自己的 agent 记收入。"""
    r = client.post(
        "/api/agents/7001/transactions/income",
        json={"amount": 10**18, "category": "TASK_REWARD"},
        headers=_auth("s_owner"),
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    db = TestingSessionLocal()
    try:
        s = db.query(AgentSurvival).filter(AgentSurvival.agent_id == 7001).first()
        assert int(s.total_income) == 10**18
    finally:
        db.close()
