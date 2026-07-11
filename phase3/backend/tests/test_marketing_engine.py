"""
Tests for services.marketing_engine (MarketingEngine.generate_daily_social_post).

Real in-memory SQLite + real Agent ORM rows, no mocks. Verifies the post is
built from live counts (non-test agents and their completed_tasks sum). Backs
the restored module consumed by /dashboard/marketing.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.testdb import TEST_DATABASE_URL
from models.database import Base, Agent
import models.agent_survival  # noqa: F401  registers AgentSurvival for Agent mapper
from services.marketing_engine import MarketingEngine, PLATFORM_NAME


@pytest.fixture
def db():
    engine = create_engine(
        TEST_DATABASE_URL
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_post_reflects_real_counts(db):
    db.add_all([
        Agent(agent_id=1, owner="0x" + "a" * 40, name="A", reputation=70,
              completed_tasks=3, is_test=False, created_at=datetime.utcnow()),
        Agent(agent_id=2, owner="0x" + "b" * 40, name="B", reputation=80,
              completed_tasks=5, is_test=False, created_at=datetime.utcnow()),
    ])
    db.commit()

    post = MarketingEngine().generate_daily_social_post(db=db)
    assert PLATFORM_NAME in post
    assert "2 个 AI 智能体" in post      # 2 non-test agents
    assert "8 项任务" in post           # 3 + 5 completed tasks


def test_test_agents_excluded(db):
    db.add_all([
        Agent(agent_id=1, owner="0x" + "a" * 40, name="real", reputation=70,
              completed_tasks=4, is_test=False, created_at=datetime.utcnow()),
        Agent(agent_id=2, owner="0x" + "b" * 40, name="bot", reputation=80,
              completed_tasks=99, is_test=True, created_at=datetime.utcnow()),
    ])
    db.commit()

    post = MarketingEngine().generate_daily_social_post(db=db)
    assert "1 个 AI 智能体" in post
    assert "4 项任务" in post


def test_empty_platform_is_zeroed_not_crashing(db):
    post = MarketingEngine().generate_daily_social_post(db=db)
    assert "0 个 AI 智能体" in post
    assert "0 项任务" in post
