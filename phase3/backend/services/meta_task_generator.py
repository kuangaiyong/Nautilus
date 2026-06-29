"""
Meta Task Generator
Platform Observatory 检测到异常后，自动在 marketplace 创建分析任务。
Agent 通过竞价接取，提交改进提案。

设计原则（仿 KAIROS）：平台是自己的第一个客户。
"""
from __future__ import annotations

import logging
import time
from typing import List

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Meta task templates (keyed by metric name from Observatory)
# ---------------------------------------------------------------------------

META_TASK_TEMPLATES = {
    "task_success_rate": {
        "title": "分析任务成功率异常 (当前{value:.0%} < 阈值{threshold:.0%})",
        "description": """平台 Observatory 检测到任务成功率异常。

当前值: {value:.1%} | 基线: {threshold:.1%} | 严重度: {severity}

**数据访问**:
- GET /api/platform/analytics/tasks?hours=48
- GET /api/platform/analytics/agents
- GET /api/platform/metrics/current

**提交格式** (POST /api/platform/proposals):
```json
{{
  "task_id": <此任务ID>,
  "root_cause": "原因分析",
  "proposed_change": {{"type": "...", "target": "...", "detail": "..."}},
  "expected_impact": "预期改善描述",
  "rollback_plan": "回滚方案"
}}
```
        """,
        "task_type": "platform_meta",
        "reward_nau": 50.0,
        "min_bid_nau": 2.0,
        "cooldown_hours": 24,
    },
    "marketplace_fill_rate": {
        "title": "分析市场任务填充率低 (当前{value:.0%})",
        "description": """平台 Observatory 检测到市场任务填充率偏低。

当前值: {value:.1%} | 基线: {threshold:.1%} | 严重度: {severity}

**数据访问**:
- GET /api/platform/analytics/tasks?hours=48
- GET /api/marketplace/stats
- GET /api/platform/metrics/current

**提交格式** (POST /api/platform/proposals):
```json
{{
  "task_id": <此任务ID>,
  "root_cause": "原因分析",
  "proposed_change": {{"type": "...", "target": "...", "detail": "..."}},
  "expected_impact": "预期改善描述",
  "rollback_plan": "回滚方案"
}}
```
        """,
        "task_type": "platform_meta",
        "reward_nau": 30.0,
        "min_bid_nau": 2.0,
        "cooldown_hours": 48,
    },
    "avg_quality_rating": {
        "title": "分析任务质量评分偏低 (均分{value:.1f}/5)",
        "description": """平台 Observatory 检测到任务质量评分偏低。

当前值: {value:.2f}/5 | 基线: {threshold}/5 | 严重度: {severity}

**数据访问**:
- GET /api/platform/analytics/tasks?hours=48
- GET /api/platform/analytics/agents
- GET /api/platform/metrics/current

**提交格式** (POST /api/platform/proposals):
```json
{{
  "task_id": <此任务ID>,
  "root_cause": "原因分析",
  "proposed_change": {{"type": "...", "target": "...", "detail": "..."}},
  "expected_impact": "预期改善描述",
  "rollback_plan": "回滚方案"
}}
```
        """,
        "task_type": "platform_meta",
        "reward_nau": 40.0,
        "min_bid_nau": 2.0,
        "cooldown_hours": 24,
    },
}

_COOLDOWN_KEY_PREFIX = "meta_task_cooldown:"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_on_cooldown(metric_name: str) -> bool:
    """Return True if a meta-task was recently created for this metric."""
    try:
        from utils.redis_client import get_redis
        r = get_redis()
        return bool(r.exists(f"{_COOLDOWN_KEY_PREFIX}{metric_name}"))
    except Exception as exc:
        logger.warning("meta_task_generator: redis unavailable (%s), skipping cooldown check", exc)
        return False


def _set_cooldown(metric_name: str, cooldown_hours: int) -> None:
    """Set the cooldown key in Redis with the given TTL."""
    try:
        from utils.redis_client import get_redis
        r = get_redis()
        r.setex(f"{_COOLDOWN_KEY_PREFIX}{metric_name}", cooldown_hours * 3600, "1")
    except Exception as exc:
        logger.warning("meta_task_generator: redis setex failed (%s)", exc)


def _build_task_row(metric_name: str, anomaly: dict, template: dict) -> dict:
    """Fill template placeholders and return a dict ready for INSERT."""
    value = anomaly.get("value", 0.0)
    threshold = anomaly.get("threshold", 0.0)
    severity = anomaly.get("severity", "medium")
    ts = int(time.time())

    fmt = dict(value=value, threshold=threshold, severity=severity)
    title = template["title"].format(**fmt)
    description = template["description"].format(**fmt)
    task_id = f"meta_{metric_name}_{ts}"

    return {
        "task_id": task_id,
        "title": title,
        "description": description,
        "task_type": template["task_type"],
        "status": "pending",
        "marketplace_open": True,
        "token_reward": template["reward_nau"],
        "min_bid_nau": template.get("min_bid_nau", 2.0),
        "is_public": True,  # academic_tasks.is_public 为 NOT NULL 无默认（raw INSERT 须显式给值）
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_anomalies(anomalies: List[dict], db: Session) -> List[str]:
    """
    Process Observatory anomalies and create meta-tasks for new ones.

    Args:
        anomalies: List of anomaly dicts (keys: metric, value, threshold, severity, message).
                   Accepts both plain dicts and Anomaly dataclass instances via __dict__.
        db:        Synchronous SQLAlchemy Session (from SessionLocal / get_db).

    Returns:
        List of newly created task id strings.
    """
    created_ids: List[str] = []

    for raw in anomalies:
        anomaly = raw if isinstance(raw, dict) else raw.__dict__
        metric_name = anomaly.get("metric", "")

        template = META_TASK_TEMPLATES.get(metric_name)
        if template is None:
            logger.debug("meta_task_generator: no template for metric '%s', skipping", metric_name)
            continue

        if _is_on_cooldown(metric_name):
            logger.debug("meta_task_generator: '%s' is on cooldown, skipping", metric_name)
            continue

        row = _build_task_row(metric_name, anomaly, template)

        try:
            from sqlalchemy import text
            # MySQL 无 RETURNING：先 INSERT，再用 LAST_INSERT_ID() 取自增 id；task_id 本就在 row 里
            db.execute(
                text(
                    "INSERT INTO academic_tasks "
                    "(task_id, title, description, task_type, status, marketplace_open, token_reward, min_bid_nau, is_public) "
                    "VALUES (:task_id, :title, :description, :task_type, :status, :marketplace_open, :token_reward, :min_bid_nau, :is_public)"
                ),
                row,
            )
            new_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
            db.commit()
            _set_cooldown(metric_name, template["cooldown_hours"])
            created_ids.append(row["task_id"])
            logger.info("meta_task_generator: created meta-task id=%s task_id='%s' for metric '%s'",
                        new_id, row["task_id"], metric_name)
        except Exception as exc:
            logger.error("meta_task_generator: failed to create task for '%s': %s", metric_name, exc)
            try:
                db.rollback()
            except Exception:
                pass

    return created_ids
