"""
Platform Observatory — 感官系统，采集平台健康指标并检测异常。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Anomaly:
    metric: str
    value: float
    threshold: float
    severity: str          # "high" | "medium" | "low"
    message: str


# ---------------------------------------------------------------------------
# Thresholds & weights
# ---------------------------------------------------------------------------

ALERT_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "task_success_rate":     {"min": 0.70, "severity": "high"},
    "marketplace_fill_rate": {"min": 0.20, "severity": "medium"},
    "avg_quality_rating":    {"min": 3.0,  "severity": "medium"},
}

# weight must sum to 1.0
HEALTH_WEIGHTS: Dict[str, float] = {
    "task_success_rate":     0.35,
    "marketplace_fill_rate": 0.20,
    "avg_quality_rating":    0.25,
    "active_agents_24h":     0.10,
    "tasks_completed_24h":   0.10,
}

# normalisation reference values (maps raw metric → 100-point scale)
HEALTH_NORMALISERS: Dict[str, float] = {
    "task_success_rate":     1.0,     # already 0-1
    "marketplace_fill_rate": 1.0,     # already 0-1
    "avg_quality_rating":    5.0,     # 1-5 stars
    "active_agents_24h":     100.0,   # 100 agents = full marks
    "tasks_completed_24h":   500.0,   # 500 tasks/day = full marks
}


# ---------------------------------------------------------------------------
# PlatformObservatory
# ---------------------------------------------------------------------------

class PlatformObservatory:
    """Collect platform metrics, detect anomalies, and compute a health score."""

    async def _safe_scalar(self, db: AsyncSession, sql: str, params: dict = None):
        """Execute a scalar query; rollback on failure so transaction stays alive."""
        try:
            r = await db.execute(text(sql), params or {})
            return r.scalar()
        except Exception as exc:
            try:
                await db.rollback()
            except Exception:
                pass
            raise exc

    async def take_snapshot(self, db: AsyncSession) -> Dict[str, Any]:
        """
        Query every metric, persist a snapshot row, and return the metrics dict.
        Each query is wrapped individually so a single failure cannot abort the rest.
        """
        metrics: Dict[str, Any] = {}
        metrics["snapshot_time"] = datetime.utcnow().isoformat()

        # total_agents
        try:
            metrics["total_agents"] = await self._safe_scalar(db, "SELECT COUNT(*) FROM agents") or 0
        except Exception as exc:
            logger.warning("observatory: total_agents failed: %s", exc)
            metrics["total_agents"] = None

        # active_agents_24h (agents with at least 1 completed task)
        try:
            metrics["active_agents_24h"] = await self._safe_scalar(db,
                "SELECT COUNT(DISTINCT assigned_agent_id) FROM academic_tasks "
                "WHERE created_at > NOW() - INTERVAL 24 HOUR AND status = 'completed'"
            ) or 0
        except Exception as exc:
            logger.warning("observatory: active_agents_24h failed: %s", exc)
            metrics["active_agents_24h"] = None

        # tasks_completed_24h
        try:
            metrics["tasks_completed_24h"] = await self._safe_scalar(db,
                "SELECT COUNT(*) FROM academic_tasks "
                "WHERE created_at > NOW() - INTERVAL 24 HOUR AND status = 'completed'"
            ) or 0
        except Exception as exc:
            logger.warning("observatory: tasks_completed_24h failed: %s", exc)
            metrics["tasks_completed_24h"] = None

        # tasks_failed_24h
        try:
            metrics["tasks_failed_24h"] = await self._safe_scalar(db,
                "SELECT COUNT(*) FROM academic_tasks "
                "WHERE created_at > NOW() - INTERVAL 24 HOUR AND status = 'failed'"
            ) or 0
        except Exception as exc:
            logger.warning("observatory: tasks_failed_24h failed: %s", exc)
            metrics["tasks_failed_24h"] = None

        # task_success_rate (computed, no DB query)
        try:
            completed = metrics.get("tasks_completed_24h") or 0
            failed = metrics.get("tasks_failed_24h") or 0
            total = completed + failed
            metrics["task_success_rate"] = (completed / total) if total > 0 else None
        except Exception as exc:
            logger.warning("observatory: task_success_rate failed: %s", exc)
            metrics["task_success_rate"] = None

        # marketplace_fill_rate
        try:
            open_count = await self._safe_scalar(db,
                "SELECT COUNT(*) FROM academic_tasks WHERE marketplace_open = true"
            ) or 0
            bid_count = await self._safe_scalar(db,
                "SELECT COUNT(DISTINCT task_id) FROM task_bids "
                "WHERE task_id IN (SELECT id FROM academic_tasks WHERE marketplace_open = true)"
            ) or 0
            metrics["marketplace_fill_rate"] = (bid_count / open_count) if open_count > 0 else None
        except Exception as exc:
            logger.warning("observatory: marketplace_fill_rate failed: %s", exc)
            metrics["marketplace_fill_rate"] = None

        # avg_quality_rating
        try:
            val = await self._safe_scalar(db,
                "SELECT AVG(quality_rating) FROM academic_tasks WHERE quality_rating IS NOT NULL"
            )
            metrics["avg_quality_rating"] = float(val) if val is not None else None
        except Exception as exc:
            logger.warning("observatory: avg_quality_rating failed: %s", exc)
            metrics["avg_quality_rating"] = None

        # nau_minted_24h
        try:
            val = await self._safe_scalar(db,
                "SELECT COALESCE(SUM(token_reward),0) FROM academic_tasks "
                "WHERE created_at > NOW() - INTERVAL 24 HOUR"
            )
            metrics["nau_minted_24h"] = float(val) if val is not None else 0.0
        except Exception as exc:
            logger.warning("observatory: nau_minted_24h failed: %s", exc)
            metrics["nau_minted_24h"] = None

        # agents_by_level (survival distribution)
        try:
            rows = (await db.execute(text(
                "SELECT survival_level, COUNT(*) AS cnt FROM agent_survival GROUP BY survival_level"
            ))).fetchall()
            metrics["agents_by_level"] = {r[0]: int(r[1]) for r in rows}
        except Exception as exc:
            try:
                await db.rollback()
            except Exception:
                pass
            logger.warning("observatory: agents_by_level failed: %s", exc)
            metrics["agents_by_level"] = {}

        # ------------------------------------------------------------------
        # Anomaly detection + health score
        # ------------------------------------------------------------------
        anomalies = self.detect_anomalies(metrics)
        health_score = self.get_health_score(metrics)
        metrics["health_score"] = health_score

        # ------------------------------------------------------------------
        # Persist snapshot
        # ------------------------------------------------------------------
        try:
            import json
            sql = text(
                "INSERT INTO platform_metrics_snapshots "
                "(metrics, anomalies, health_score) "
                "VALUES (:metrics, :anomalies, :health_score)"
            )
            await db.execute(sql, {
                "metrics": json.dumps(metrics),
                "anomalies": json.dumps([a.__dict__ for a in anomalies]),
                "health_score": health_score,
            })
            await db.commit()
        except Exception as exc:
            logger.error("observatory: snapshot persist failed: %s", exc)

        return metrics

    def detect_anomalies(self, metrics: Dict[str, Any]) -> List[Anomaly]:
        """Compare metric values against ALERT_THRESHOLDS; return list of Anomaly."""
        anomalies: List[Anomaly] = []
        for metric_name, rule in ALERT_THRESHOLDS.items():
            value = metrics.get(metric_name)
            if value is None:
                continue
            threshold = rule["min"]
            if value < threshold:
                anomalies.append(Anomaly(
                    metric=metric_name,
                    value=round(float(value), 4),
                    threshold=threshold,
                    severity=rule["severity"],
                    message=(
                        f"{metric_name} is {value:.4f}, "
                        f"below minimum threshold {threshold}"
                    ),
                ))
        return anomalies

    def get_health_score(self, metrics: Dict[str, Any]) -> float:
        """
        Compute a 0-100 weighted health score from available metrics.
        Missing metrics are skipped and weights are redistributed.
        """
        total_weight = 0.0
        weighted_sum = 0.0

        for metric_name, weight in HEALTH_WEIGHTS.items():
            value = metrics.get(metric_name)
            if value is None:
                continue
            normaliser = HEALTH_NORMALISERS.get(metric_name, 1.0)
            normalised = min(float(value) / normaliser, 1.0) * 100.0
            weighted_sum += normalised * weight
            total_weight += weight

        if total_weight == 0.0:
            return 0.0
        return round(weighted_sum / total_weight, 2)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_observatory: Optional[PlatformObservatory] = None


def get_observatory() -> PlatformObservatory:
    global _observatory
    if _observatory is None:
        _observatory = PlatformObservatory()
    return _observatory
