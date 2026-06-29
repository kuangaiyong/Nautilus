"""
Agent service with caching.

Provides cached Agent query operations, mirroring services/task_service.py.
Recreated module: api/agents.py and services/agent_management.py import
get_agent_cached / get_agents_list_cached / invalidate_agent_cache from here.
"""
from sqlalchemy.orm import Session
from models.database import Agent
from utils.cache import cached, invalidate_cache
import logging

logger = logging.getLogger(__name__)


def _agent_to_dict(agent: Agent) -> dict:
    """Serialize an Agent ORM row to a plain dict (AgentResponse-compatible)."""
    return {
        "id": agent.id,
        "agent_id": agent.agent_id,
        "owner": agent.owner,
        "name": agent.name,
        "description": agent.description,
        "reputation": agent.reputation,
        "specialties": agent.specialties,
        "current_tasks": agent.current_tasks,
        "completed_tasks": agent.completed_tasks,
        "failed_tasks": agent.failed_tasks,
        "total_earnings": agent.total_earnings,
        "created_at": agent.created_at,
        "blockchain_registered": agent.blockchain_registered,
        "blockchain_tx_hash": agent.blockchain_tx_hash,
        "blockchain_address": agent.blockchain_address,
    }


@cached(ttl=120, key_prefix="agents")
async def get_agents_list_cached(limit: int = 100, offset: int = 0, db: Session = None) -> dict:
    """
    Get agents list with caching (2 minutes), sorted by reputation (highest first).

    Returns:
        dict: {"agents": [ ...agent dicts... ]}
    """
    agents = (
        db.query(Agent)
        .order_by(Agent.reputation.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"agents": [_agent_to_dict(a) for a in agents]}


@cached(ttl=120, key_prefix="agents")
async def get_agent_cached(agent_id: int, db: Session = None) -> dict:
    """
    Get a single agent by numeric agent_id with caching (2 minutes).

    Raises:
        ValueError: if no agent with that id exists (caller maps to 404).
    """
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise ValueError(f"Agent not found: {agent_id}")
    return _agent_to_dict(agent)


def invalidate_agent_cache(agent_id: int = None):
    """
    Invalidate all agent list/detail caches.

    agent_id is accepted for call-site clarity; we clear the whole "agents"
    prefix (both list and per-agent entries) to keep cache coherent.
    """
    invalidate_cache("agents:*")
    logger.info("Invalidated agents cache (trigger agent_id=%s)", agent_id)
