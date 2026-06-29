"""
Platform Analytics API
供 Agent 读取平台健康数据，用于分析和改进提案。所有端点只读。

私有化部署用 MySQL（nautilus_private），核心业务数据在 DMAS `tasks` 表（非 academic_tasks）。
下列指标均基于 tasks/agents/agent_survival 实时计算，使用 MySQL 语法：
- 时间窗口用 (NOW() - INTERVAL 24 HOUR)
- tasks.status 存大写枚举名：OPEN/ACCEPTED/SUBMITTED/COMPLETED/FAILED/DISPUTED
- created_at 为 DATETIME，pymysql 取出即 datetime 对象
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from datetime import datetime, timezone
import logging, json

from utils.database import get_db

router = APIRouter(prefix="/api/platform", tags=["Platform Analytics"])
logger = logging.getLogger(__name__)

WINDOW_24H = "(NOW() - INTERVAL 24 HOUR)"  # MySQL 24 小时窗口

# 健康分权重（和为 1）与归一基准（按内网小规模部署设定，避免分数恒接近 0）
HEALTH_WEIGHTS = {"task_success_rate": 0.35, "marketplace_fill_rate": 0.20,
                  "active_agents_24h": 0.15, "tasks_completed_24h": 0.15, "agents_healthy": 0.15}
HEALTH_NORMS = {"task_success_rate": 1.0, "marketplace_fill_rate": 1.0,
                "active_agents_24h": 20.0, "tasks_completed_24h": 50.0, "agents_healthy": 20.0}
ANOMALY_THRESHOLDS = {"task_success_rate": 0.70, "marketplace_fill_rate": 0.20}


def _ok(data): return {"success": True, "data": data, "error": None}
def _err(msg): return {"success": False, "data": None, "error": msg}


def _q(db, sql, params=None):
    """Execute SQL with auto-rollback on failure."""
    try:
        return db.execute(text(sql), params or {})
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise exc


def _iso(v):
    """SQLite 的时间列读出来是字符串；datetime 才有 isoformat。"""
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _compute_metrics(db) -> dict:
    """基于 DMAS tasks/agents/agent_survival 实时计算平台指标（SQLite）。"""
    total_agents = _q(db, "SELECT COUNT(*) FROM agents").scalar() or 0
    active_24h = _q(db,
        f"SELECT COUNT(DISTINCT agent) FROM tasks "
        f"WHERE agent IS NOT NULL AND status='COMPLETED' AND created_at >= {WINDOW_24H}"
    ).scalar() or 0
    row = _q(db,
        f"SELECT COUNT(*) AS total, "
        f"SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS done, "
        f"SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed, "
        f"SUM(CASE WHEN agent IS NOT NULL THEN 1 ELSE 0 END) AS filled "
        f"FROM tasks WHERE created_at >= {WINDOW_24H}"
    ).fetchone()
    total = int(row.total or 0); done = int(row.done or 0)
    failed = int(row.failed or 0); filled = int(row.filled or 0)
    finished = done + failed
    sv = _q(db,
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN survival_level IN ('ELITE','MATURE','GROWING') THEN 1 ELSE 0 END) AS healthy "
        "FROM agent_survival"
    ).fetchone()
    return {
        "total_agents": int(total_agents),
        "active_agents_24h": int(active_24h),
        "tasks_completed_24h": done,
        "tasks_failed_24h": failed,
        "task_success_rate": round(done / finished, 4) if finished else None,
        "marketplace_fill_rate": round(filled / total, 4) if total else None,
        "avg_quality_rating": None,  # DMAS tasks 暂无质量评分字段
        "agents_survival_tracked": int(sv.total or 0) if sv else 0,
        "agents_healthy": int(sv.healthy or 0) if (sv and sv.healthy) else 0,
    }


def _health_score(metrics: dict) -> float:
    ws = tw = 0.0
    for k, w in HEALTH_WEIGHTS.items():
        v = metrics.get(k)
        if v is None:
            continue
        ws += min(float(v) / HEALTH_NORMS[k], 1.0) * 100.0 * w
        tw += w
    return round(ws / tw, 2) if tw else 0.0


def _detect_anomalies(metrics: dict) -> list:
    out = []
    for k, thr in ANOMALY_THRESHOLDS.items():
        v = metrics.get(k)
        if v is not None and v < thr:
            out.append({"metric": k, "value": round(float(v), 4), "threshold": thr})
    return out


@router.get("/health")
def get_platform_health(db=Depends(get_db)):
    """最新健康快照；若无快照则实时 fallback。"""
    try:
        row = _q(db,
            "SELECT metrics, anomalies, health_score, snapshot_time "
            "FROM platform_metrics_snapshots ORDER BY snapshot_time DESC LIMIT 1"
        ).fetchone()
        if row:
            metrics = row.metrics if isinstance(row.metrics, dict) else json.loads(row.metrics or "{}")
            anomalies = row.anomalies if isinstance(row.anomalies, list) else json.loads(row.anomalies or "[]")
            return _ok({
                "health_score": row.health_score,
                "metrics": metrics,
                "anomalies": anomalies,
                "snapshot_time": _iso(row.snapshot_time),
                "source": "snapshot",
            })
    except Exception as e:
        logger.warning(f"Snapshot unavailable, fallback: {e}")

    try:
        metrics = _compute_metrics(db)
        return _ok({
            "health_score": _health_score(metrics),
            "metrics": metrics,
            "anomalies": _detect_anomalies(metrics),
            "snapshot_time": datetime.now(timezone.utc).isoformat(),
            "source": "realtime",
        })
    except Exception as e:
        logger.error(f"Realtime health failed: {e}")
        return _err(str(e))


@router.get("/analytics/tasks")
def get_task_analytics(
    type: str = Query(None),
    hours: int = Query(24, ge=1, le=720),
    status: str = Query(None),
    db=Depends(get_db),
):
    """任务统计分析（DMAS tasks，支持 type/hours/status 过滤）。"""
    try:
        conds = ["created_at >= (NOW() - INTERVAL :hours HOUR)"]
        params: dict = {"hours": hours}
        if type:
            conds.append("task_type = :task_type"); params["task_type"] = type
        if status:
            conds.append("status = :status"); params["status"] = status
        where = " AND ".join(conds)

        row = _q(db,
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS done "
            f"FROM tasks WHERE {where}", params
        ).fetchone()
        total, done = int(row.total or 0), int(row.done or 0)

        top_rows = _q(db,
            f"SELECT agent, COUNT(*) AS cnt FROM tasks "
            f"WHERE status='COMPLETED' AND agent IS NOT NULL AND {where} "
            f"GROUP BY agent ORDER BY cnt DESC LIMIT 5", params
        ).fetchall()
        top_agents = [{"agent": r.agent, "prefix": (r.agent or "")[:10], "count": int(r.cnt)} for r in top_rows]

        return _ok({
            "filter": {"type": type, "hours": hours, "status": status},
            "total": total, "completed": done,
            "success_rate": round(done / total, 4) if total else 0.0,
            "avg_quality_rating": None,
            "top_agents": top_agents,
        })
    except Exception as e:
        logger.error(f"Task analytics error: {e}")
        return _err(str(e))


@router.get("/analytics/agents")
def get_agent_analytics(db=Depends(get_db)):
    """Agent 生存等级分布 + 任务类型绩效（DMAS tasks）。"""
    try:
        total_agents = _q(db, "SELECT COUNT(*) FROM agents").scalar() or 0
        tier_rows = _q(db,
            "SELECT survival_level, COUNT(*) AS cnt FROM agent_survival "
            "GROUP BY survival_level ORDER BY cnt DESC"
        ).fetchall()
        task_type_rows = _q(db,
            "SELECT task_type, COUNT(*) AS total, "
            "SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS done "
            "FROM tasks GROUP BY task_type ORDER BY total DESC"
        ).fetchall()
        return _ok({
            "total_agents": int(total_agents),
            "survival_tier_distribution": [{"level": r.survival_level, "count": int(r.cnt)} for r in tier_rows],
            "task_type_performance": [{
                "task_type": str(r.task_type), "total": int(r.total),
                "completed": int(r.done or 0),
                "success_rate": round(int(r.done or 0) / int(r.total), 4) if r.total else 0.0,
            } for r in task_type_rows],
        })
    except Exception as e:
        logger.error(f"Agent analytics error: {e}")
        return _err(str(e))


@router.get("/snapshots")
def get_platform_snapshots(n: int = Query(24, ge=1, le=168), db=Depends(get_db)):
    """最近 N 个健康快照趋势（按时间正序返回，便于趋势图）。"""
    try:
        rows = _q(db,
            "SELECT health_score, metrics, snapshot_time "
            "FROM platform_metrics_snapshots ORDER BY snapshot_time DESC LIMIT :n", {"n": n}
        ).fetchall()
        rows = list(reversed(rows))
        return _ok({"count": len(rows), "snapshots": [{
            "health_score": r.health_score,
            "snapshot_time": _iso(r.snapshot_time),
            "metrics": r.metrics if isinstance(r.metrics, dict) else json.loads(r.metrics or "{}"),
        } for r in rows]})
    except Exception as e:
        logger.error(f"Snapshots error: {e}")
        return _err(str(e))


@router.post("/observatory/trigger")
def trigger_observatory_snapshot(db=Depends(get_db)):
    """手动触发 Observatory 快照 + 异常检测（写入一条快照，供趋势图）。"""
    try:
        metrics = _compute_metrics(db)
        metrics["snapshot_time"] = datetime.now(timezone.utc).isoformat()
        anomalies = _detect_anomalies(metrics)
        health_score = _health_score(metrics)

        _q(db,
            "INSERT INTO platform_metrics_snapshots (snapshot_time, metrics, anomalies, health_score) "
            "VALUES (:t, :m, :a, :h)",
            {"t": metrics["snapshot_time"], "m": json.dumps(metrics),
             "a": json.dumps(anomalies), "h": health_score}
        )
        db.commit()

        return _ok({
            "triggered": True,
            "health_score": health_score,
            "snapshot_time": metrics["snapshot_time"],
            "anomalies_detected": len(anomalies),
            "anomalies": anomalies,
            "meta_tasks_created": 0,
        })
    except Exception as e:
        logger.error(f"observatory trigger failed: {e}")
        return _err(str(e))


@router.get("/metrics/current")
def get_current_metrics(db=Depends(get_db)):
    """实时计算当前指标（直接查 DB）。"""
    try:
        metrics = _compute_metrics(db)
        return _ok({"computed_at": datetime.now(timezone.utc).isoformat(), **metrics})
    except Exception as e:
        logger.error(f"Current metrics error: {e}")
        return _err(str(e))
