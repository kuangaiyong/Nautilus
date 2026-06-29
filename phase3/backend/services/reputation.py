"""Reputation Service — Agent 声誉管理

声誉分计算逻辑：
- 初始值：50（中立）
- 每次评分后用指数加权移动平均更新
- 评分 5→+3, 4→+1, 3→0, 2→-2, 1→-5（delta）
- 新 reputation = reputation * 0.9 + (50 + delta * 5) * 0.1
- 上限 100，下限 0
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Agent, AcademicTask

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EWMA_ALPHA = 0.1          # weight of new observation
_EWMA_BASE = 50.0          # neutral point for EWMA target
_SCORE_MIN = 0.0
_SCORE_MAX = 100.0
_RECENT_RATINGS_LIMIT = 10

# Mapping: rating → delta
_RATING_DELTA: dict[int, float] = {
    5: 3.0,
    4: 1.0,
    3: 0.0,
    2: -2.0,
    1: -5.0,
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ReputationTier(str, Enum):
    TRUSTED = "trusted"         # 80+：优先路由，可设高底价
    ESTABLISHED = "established" # 60-79：正常路由
    NEWCOMER = "newcomer"       # 40-59：标准路由
    PROBATION = "probation"     # 0-39：降级路由，可能被跳过高价值任务


# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------


def get_reputation_tier(score: float) -> ReputationTier:
    """Return the ReputationTier for a given reputation score."""
    if score >= 80.0:
        return ReputationTier.TRUSTED
    if score >= 60.0:
        return ReputationTier.ESTABLISHED
    if score >= 40.0:
        return ReputationTier.NEWCOMER
    return ReputationTier.PROBATION


def calculate_score_delta(rating: int) -> float:
    """Convert a 1-5 quality rating to a reputation delta value.

    Args:
        rating: Integer rating in [1, 5].

    Returns:
        Delta float.

    Raises:
        ValueError: If rating is not in the valid range.
    """
    if rating not in _RATING_DELTA:
        raise ValueError(f"Rating must be between 1 and 5, got {rating!r}")
    return _RATING_DELTA[rating]


def _apply_ewma(current_score: float, delta: float) -> float:
    """Apply exponential weighted moving average to produce new score.

    Formula: new = current * (1 - alpha) + (BASE + delta * 5) * alpha
    Clamped to [0, 100].
    """
    target = _EWMA_BASE + delta * 5.0
    new_score = current_score * (1.0 - _EWMA_ALPHA) + target * _EWMA_ALPHA
    return max(_SCORE_MIN, min(_SCORE_MAX, new_score))


def routing_priority(agent_reputation: float, agent_specialty_match: bool) -> float:
    """Calculate task routing priority score for sorting agent candidates.

    Higher score → higher priority for task assignment.

    Args:
        agent_reputation: Agent's current reputation_score (0-100).
        agent_specialty_match: Whether the agent's specialties match the task.

    Returns:
        Priority float. Specialty match applies a 1.5× multiplier.
    """
    base = agent_reputation / _SCORE_MAX  # normalise to [0, 1]
    multiplier = 1.5 if agent_specialty_match else 1.0
    return base * multiplier


# ---------------------------------------------------------------------------
# DB-backed functions
# ---------------------------------------------------------------------------


async def update_reputation(
    db: AsyncSession,
    agent_id: int,
    new_rating: int,
) -> dict:
    """Update an agent's reputation_score based on a new quality rating.

    Uses EWMA: new_score = old_score * 0.9 + (50 + delta * 5) * 0.1

    Args:
        db: Async SQLAlchemy session.
        agent_id: agents.agent_id value.
        new_rating: Quality rating in [1, 5].

    Returns:
        {agent_id, old_score, new_score, tier}

    Raises:
        HTTPException 400 if rating is invalid.
        HTTPException 404 if agent not found.
    """
    # Validate rating first (cheap, no DB round-trip)
    try:
        delta = calculate_score_delta(new_rating)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent: Optional[Agent] = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_id} not found",
        )

    old_score: float = float(getattr(agent, "reputation_score", 50.0))
    new_score = _apply_ewma(old_score, delta)

    # Persist — works regardless of whether the column is named
    # `reputation_score` (new) or `reputation` (legacy).
    if hasattr(agent, "reputation_score"):
        agent.reputation_score = new_score  # type: ignore[attr-defined]
    else:
        # Fallback: update legacy integer column (rounded)
        agent.reputation = int(round(new_score))  # type: ignore[attr-defined]

    await db.commit()
    await db.refresh(agent)

    tier = get_reputation_tier(new_score)

    logger.info(
        "reputation updated agent_id=%s old=%.2f new=%.2f tier=%s",
        agent_id,
        old_score,
        new_score,
        tier.value,
    )

    return {
        "agent_id": agent_id,
        "old_score": old_score,
        "new_score": new_score,
        "tier": tier.value,
    }


async def get_reputation_detail(
    db: AsyncSession,
    agent_id: int,
) -> dict:
    """Return a detailed reputation snapshot for an agent.

    Args:
        db: Async SQLAlchemy session.
        agent_id: agents.agent_id value.

    Returns:
        {
            agent_id,
            reputation_score,
            tier,
            recent_ratings: list[int],   # up to 10 most recent quality_rating values
            avg_quality: float,
            total_rated_tasks: int,
        }

    Raises:
        HTTPException 404 if agent not found.
    """
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent: Optional[Agent] = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_id} not found",
        )

    reputation_score: float = float(
        getattr(agent, "reputation_score", None)
        or getattr(agent, "reputation", 50)
    )
    tier = get_reputation_tier(reputation_score)

    # ------------------------------------------------------------------
    # Query academic_tasks for this agent's rated tasks
    # ------------------------------------------------------------------

    # Total count of tasks with a non-null quality_rating
    count_result = await db.execute(
        select(func.count())
        .select_from(AcademicTask)
        .where(
            AcademicTask.assigned_agent_id == agent_id,
            AcademicTask.quality_rating.isnot(None),  # type: ignore[attr-defined]
        )
    )
    total_rated_tasks: int = count_result.scalar_one() or 0

    # Average quality rating
    avg_result = await db.execute(
        select(func.avg(AcademicTask.quality_rating))  # type: ignore[attr-defined]
        .where(
            AcademicTask.assigned_agent_id == agent_id,
            AcademicTask.quality_rating.isnot(None),  # type: ignore[attr-defined]
        )
    )
    avg_raw = avg_result.scalar_one()
    avg_quality: float = round(float(avg_raw), 2) if avg_raw is not None else 0.0

    # Most recent N ratings, ordered newest first
    recent_result = await db.execute(
        select(AcademicTask.quality_rating)  # type: ignore[attr-defined]
        .where(
            AcademicTask.assigned_agent_id == agent_id,
            AcademicTask.quality_rating.isnot(None),  # type: ignore[attr-defined]
        )
        .order_by(AcademicTask.updated_at.desc())
        .limit(_RECENT_RATINGS_LIMIT)
    )
    recent_ratings: list[int] = [row[0] for row in recent_result.fetchall()]

    return {
        "agent_id": agent_id,
        "reputation_score": reputation_score,
        "tier": tier.value,
        "recent_ratings": recent_ratings,
        "avg_quality": avg_quality,
        "total_rated_tasks": total_rated_tasks,
    }


# ---------------------------------------------------------------------------
# Module-level convenience functions (used by cron_registry)
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


def apply_inactivity_decay(db) -> int:
    """Reduce reputation_score by 0.5 for agents inactive for more than 7 days.

    Uses a sync SQLAlchemy session (SessionLocal). Returns number of rows updated.
    Called by the reputation_decay cron job every 10 minutes.
    """
    from sqlalchemy import text
    try:
        result = db.execute(text(
            "UPDATE agents "
            "SET reputation_score = GREATEST(0, COALESCE(reputation_score, 50) - 0.5) "
            "WHERE id NOT IN ("
            "  SELECT DISTINCT assigned_agent_id FROM academic_tasks "
            "  WHERE assigned_agent_id IS NOT NULL "
            "    AND updated_at >= NOW() - INTERVAL 7 DAY"
            ")"
        ))
        db.commit()
        count = result.rowcount or 0
        if count:
            _logger.info("apply_inactivity_decay: decayed %d inactive agents", count)
        return count
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        _logger.error("apply_inactivity_decay failed: %s", exc)
        return 0

