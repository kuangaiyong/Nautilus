"""
Tests for services.agent_service (cached agent read helpers).

Exercises the real cached functions against a real in-memory SQLite DB and
the real ORM models -- no mocks. Backs the bug fix that recreated the missing
services/agent_service.py module imported by api/agents.py.
"""
import asyncio
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base, Agent
import models.agent_survival  # noqa: F401  registers AgentSurvival mapper for Agent.survival
from utils.cache import invalidate_cache
from services.agent_service import (
    get_agents_list_cached,
    get_agent_cached,
    invalidate_agent_cache,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Agent(
            agent_id=101, owner="0x" + "a" * 40, name="Alpha", description="d1",
            reputation=70, specialties="code-generation,reasoning",
            blockchain_address="0x" + "a" * 40, created_at=datetime.utcnow(),
        ),
        Agent(
            agent_id=102, owner="0x" + "b" * 40, name="Beta", description="d2",
            reputation=90, specialties="reasoning",
            blockchain_address="0x" + "b" * 40, created_at=datetime.utcnow(),
        ),
    ])
    session.commit()
    invalidate_cache(None)  # clean global cache between tests
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_list_sorted_by_reputation_desc(db_session):
    result = asyncio.run(get_agents_list_cached(db=db_session))
    agents = result["agents"]
    assert len(agents) == 2
    assert [a["agent_id"] for a in agents] == [102, 101]
    assert agents[0]["name"] == "Beta"
    assert agents[0]["reputation"] == 90


def test_get_single_agent_matches_row(db_session):
    a = asyncio.run(get_agent_cached(101, db=db_session))
    assert a["agent_id"] == 101
    assert a["name"] == "Alpha"
    assert a["specialties"] == "code-generation,reasoning"
    assert a["blockchain_address"] == "0x" + "a" * 40


def test_get_missing_agent_raises_value_error(db_session):
    with pytest.raises(ValueError):
        asyncio.run(get_agent_cached(99999, db=db_session))


def test_invalidate_agent_cache_smoke(db_session):
    assert invalidate_agent_cache(101) is None
