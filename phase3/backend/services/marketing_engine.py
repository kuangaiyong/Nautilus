"""
Marketing content generator.

Builds a daily social post from real platform data (agent roster + completed
task counts) — no templates with fabricated numbers. Consumed by
api/dashboard.py GET /dashboard/marketing and the autonomous marketing action
in platform_brain (via services/outreach.py).
"""
import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.database import Agent

logger = logging.getLogger(__name__)

PLATFORM_NAME = "Nautilus 智涌"


class MarketingEngine:
    """Generates marketing content from live platform statistics."""

    def generate_daily_social_post(self, db: Optional[Session] = None) -> str:
        if db is not None:
            return self._compose(db)
        from utils.database import get_db_context

        with get_db_context() as session:
            return self._compose(session)

    def _compose(self, db: Session) -> str:
        total_agents = db.query(Agent).filter(Agent.is_test.isnot(True)).count()
        completed = (
            db.query(func.coalesce(func.sum(Agent.completed_tasks), 0))
            .filter(Agent.is_test.isnot(True))
            .scalar()
            or 0
        )

        return (
            f"【{PLATFORM_NAME}·每日快报】"
            f"平台已有 {total_agents} 个 AI 智能体在线协作，"
            f"累计完成 {int(completed)} 项任务。"
            "发布任务即可让智能体竞标、执行、链上结算——"
            "让你的 AI 智能体开始赚钱。 #AIAgent #智涌"
        )
