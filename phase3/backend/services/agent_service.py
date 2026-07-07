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
    # survival 为一对一关系（uselist=False, lazy="joined"，无额外查询）。真实结算收入
    # 存于 survival.total_income（WeiInt，可容纳大额）；agents.total_earnings 是遗留的
    # BigInteger，>~9.22 华币会溢出，故"收益"展示改用 survival 的 wei，前端再 /1e18。
    sv = getattr(agent, "survival", None)
    return {
        "id": agent.id,
        "agent_id": agent.agent_id,
        "owner": agent.owner,
        "name": agent.name,
        "description": agent.description,
        "reputation": agent.reputation,
        "reputation_score": float(agent.reputation_score) if agent.reputation_score is not None else 0.0,
        "specialties": agent.specialties,
        "current_tasks": agent.current_tasks,
        # 完成/失败数以 survival 计数为准（一对一 joined，无额外查询）：它自任务上线即随每次
        # 完成更新，是可靠单一来源；agents.completed_tasks 是后加的第二计数缓存、历史会漂移
        # （早于其自增修复完成的任务未计入），二者不一致正源于此。无 survival 时回退到 agent 字段。
        "completed_tasks": (sv.tasks_completed if sv and sv.tasks_completed is not None else agent.completed_tasks),
        "failed_tasks": (sv.tasks_failed if sv and sv.tasks_failed is not None else agent.failed_tasks),
        "total_earnings": agent.total_earnings,
        "total_income": str(sv.total_income) if sv and sv.total_income is not None else "0",
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
