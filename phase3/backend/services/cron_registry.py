"""
Nautilus Cron Registry — 统一定时任务管理
仿 Claude Code KAIROS 时间预算机制
所有定时任务在此注册，有描述、预算、优先级
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler = None


async def _tg_alert(text: str) -> None:
    """向所有 TELEGRAM_ADMIN_ID 发送监控告警，失败静默。"""
    import os, httpx
    token = os.getenv("TELEGRAM_ADMIN_BOT_TOKEN", "")
    admin_ids_raw = os.getenv("TELEGRAM_ADMIN_ID", "")
    if not token or not admin_ids_raw:
        return
    admin_ids = [uid.strip() for uid in admin_ids_raw.split(",") if uid.strip()]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for cid in admin_ids:
                await client.post(url, json={"chat_id": cid, "text": text, "parse_mode": "Markdown"})
    except Exception as e:
        logger.debug("_tg_alert failed (non-critical): %s", e)

CRON_JOBS = [
    {
        "id": "platform_metrics_snapshot",
        "trigger": IntervalTrigger(hours=1),
        "description": "采集平台健康指标快照",
        "budget_seconds": 30,
    },
    {
        "id": "anomaly_detection",
        "trigger": IntervalTrigger(minutes=30),
        "description": "检测平台异常，自动生成元任务",
        "budget_seconds": 15,
    },
    {
        "id": "agent_autonomy_scan",
        "trigger": IntervalTrigger(minutes=5),
        "description": "Agent 自主扫描市场并竞价",
        "budget_seconds": 10,
    },
    {
        "id": "autodream_consolidation",
        "trigger": CronTrigger(hour=3, minute=0),
        "description": "autoDream：整合平台记忆，消除矛盾，生成日志摘要",
        "budget_seconds": 120,
    },
    {
        "id": "survival_tier_recalculation",
        "trigger": CronTrigger(hour=0, minute=0),
        "description": "重新计算所有 agent 生存等级",
        "budget_seconds": 60,
    },
    {
        "id": "reputation_decay",
        "trigger": IntervalTrigger(minutes=10),
        "description": "对长期不活跃 agent 缓慢衰减声誉分",
        "budget_seconds": 5,
    },
    {
        "id": "sandbox_monitor",
        "trigger": IntervalTrigger(hours=1),
        "description": "检查到期沙箱实验，自动评估并 promote/rollback",
        "budget_seconds": 30,
    },
    {
        "id": "flush_pending_nau",
        "trigger": CronTrigger(hour=1, minute=0),
        "description": "批量 mint pending_nau_rewards 到链上（每天凌晨 1 点）",
        "budget_seconds": 120,
    },
    {
        "id": "auto_accept_bids",
        "trigger": IntervalTrigger(minutes=10),
        "description": "自动接受 platform_meta 任务的最优竞价（闭环第四步）",
        "budget_seconds": 20,
    },
    {
        "id": "auto_submit_proposals",
        "trigger": IntervalTrigger(minutes=15),
        "description": "对已分配的 platform_meta 任务自动生成并提交改进提案（闭环第五步）",
        "budget_seconds": 180,  # RAID-3 needs ~60-120s for 3 parallel LLM calls + judge
    },
    {
        "id": "se_marketplace",
        "trigger": IntervalTrigger(minutes=1),
        "description": "软件工程任务市场：自主竞价+择优派单+自主交付+3专家自动评审结算",
        "budget_seconds": 90,
    },
]


async def _run_with_budget(job_id: str, fn, budget_seconds: int) -> None:
    """带超时预算执行任务"""
    try:
        await asyncio.wait_for(fn(), timeout=budget_seconds)
    except asyncio.TimeoutError:
        logger.warning(f"Cron job {job_id} exceeded budget of {budget_seconds}s")
    except Exception as e:
        logger.error(f"Cron job {job_id} failed: {e}")


def _make_platform_snapshot_fn():
    """每小时采集平台指标快照（同步 SQL，兼容 SessionLocal）。"""
    async def _fn():
        import json as _json
        try:
            from utils.database import SessionLocal
            from sqlalchemy import text
            # 与仪表盘口径一致：基于 DMAS tasks 计算（私有网络的真实任务系统），
            # 复用 api/platform.py 的 canonical helpers。避免用 academic_tasks(当前为空)
            # 算出 health=0 的快照覆盖仪表盘（snapshot-first 读最新一条）。
            from api.platform import _compute_metrics, _health_score, _detect_anomalies
            db = SessionLocal()
            try:
                metrics = _compute_metrics(db)
                health_score = _health_score(metrics)
                anomalies = _detect_anomalies(metrics)
                db.execute(text(
                    "INSERT INTO platform_metrics_snapshots (metrics, anomalies, health_score) "
                    "VALUES (:m, :a, :h)"),
                    {"m": _json.dumps(metrics), "a": _json.dumps(anomalies), "h": health_score})
                db.commit()
                logger.info("observatory snapshot: health=%.1f anomalies=%d", health_score, len(anomalies))
            finally:
                db.close()
        except ImportError:
            logger.debug("observatory not available yet, skipping snapshot")
        except Exception as e:
            logger.error("platform_snapshot failed: %s", e)
    return _fn


def _make_anomaly_detection_fn():
    async def _fn():
        # 检查最近 2 分钟内新增的 platform_meta 任务，发 Telegram 告警
        try:
            from utils.database import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                new_metas = db.execute(text(
                    "SELECT task_id, title FROM academic_tasks "
                    "WHERE task_type='platform_meta' "
                    "AND created_at >= NOW() - INTERVAL 2 MINUTE "
                    "ORDER BY created_at DESC LIMIT 5"
                )).fetchall()
                if new_metas:
                    lines = "\n".join(f"  • {r.title[:80]}" for r in new_metas)
                    await _tg_alert(f"🚨 *Observatory 检测到 {len(new_metas)} 个异常*\n\n{lines}")
                    logger.info("anomaly_detection: %d new meta-tasks found", len(new_metas))
            finally:
                db.close()
        except Exception as e:
            logger.debug("anomaly alert failed: %s", e)
    return _fn


def _make_se_marketplace_fn(db_factory):
    """软件工程任务市场：自主智能体加权竞价 + 竞价窗到点择优中标派单。"""
    async def _fn():
        try:
            from services.se_pouw_flow import (
                auto_bid_open_se_tasks, award_due_se_tasks, auto_deliver_accepted_se_tasks,
                auto_review_and_settle_submitted_se_tasks,
            )
            db = db_factory()
            try:
                auto_bid_open_se_tasks(db)
                awarded = award_due_se_tasks(db)
                if awarded:
                    logger.info("se_marketplace: awarded %d SE task(s)", awarded)
                delivered = auto_deliver_accepted_se_tasks(db)
                if delivered:
                    logger.info("se_marketplace: delivered %d SE task(s)", delivered)
                settled = await auto_review_and_settle_submitted_se_tasks(db)
                if settled:
                    logger.info("se_marketplace: settled %d SE task(s)", settled)
            finally:
                db.close()
        except Exception as e:
            logger.error("se_marketplace failed: %s", e)
    return _fn


def _make_autodream_fn():
    async def _fn():
        """
        autoDream 夜间记忆整合（仿 Claude Code autoDream）
        读取过去 24h 的平台快照，生成摘要日志，消除矛盾。
        """
        try:
            from utils.database import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                rows = db.execute(text(
                    "SELECT metrics, health_score, snapshot_time "
                    "FROM platform_metrics_snapshots "
                    "WHERE snapshot_time >= NOW() - INTERVAL 24 HOUR "
                    "ORDER BY snapshot_time ASC"
                )).fetchall()

                if not rows:
                    logger.info("autoDream: no snapshots in last 24h, skipping")
                    return

                import json

                first = rows[0].metrics or {}
                last = rows[-1].metrics or {}
                if isinstance(first, str):
                    first = json.loads(first)
                if isinstance(last, str):
                    last = json.loads(last)

                trends = {}
                for key in ["task_success_rate", "active_agents_24h", "tasks_completed_24h", "nau_minted_24h"]:
                    v0 = first.get(key)
                    v1 = last.get(key)
                    if v0 is not None and v1 is not None:
                        trends[key] = round((v1 - v0) / abs(v0), 4) if v0 else 0

                avg_health = sum(r.health_score for r in rows if r.health_score) / max(len(rows), 1)

                summary = {
                    "date": rows[-1].snapshot_time.strftime("%Y-%m-%d") if rows[-1].snapshot_time else "unknown",
                    "snapshots_analyzed": len(rows),
                    "avg_health_score": round(avg_health, 1),
                    "trends_24h": trends,
                    "conclusion": (
                        "improving" if avg_health >= 70
                        else "needs_attention" if avg_health >= 50
                        else "critical"
                    ),
                }

                # MySQL：jsonb 合并用 JSON_MERGE_PATCH；且不能在 UPDATE 同表的子查询里直接 SELECT 该表，
                # 用派生表(SELECT ... AS t)包一层规避 "can't specify target table for update"。
                db.execute(text(
                    "UPDATE platform_metrics_snapshots "
                    "SET metrics = JSON_MERGE_PATCH(metrics, :dream_json) "
                    "WHERE id = (SELECT id FROM (SELECT id FROM platform_metrics_snapshots "
                    "ORDER BY snapshot_time DESC LIMIT 1) AS t)"
                ), {"dream_json": json.dumps({"_autodream": summary})})
                db.commit()

                logger.info(
                    f"autoDream complete: {len(rows)} snapshots, "
                    f"avg_health={avg_health:.1f}, conclusion={summary['conclusion']}"
                )

                # Telegram 夜间报告推送
                emoji = "✅" if summary["conclusion"] == "improving" else "⚠️" if summary["conclusion"] == "needs_attention" else "🚨"
                trend_lines = "\n".join(
                    f"  • {k}: {'+' if v >= 0 else ''}{v*100:.1f}%"
                    for k, v in summary["trends_24h"].items()
                ) or "  (无趋势数据)"
                msg = (
                    f"{emoji} *Nautilus 夜间报告* {summary['date']}\n\n"
                    f"健康分: *{summary['avg_health_score']}* ({summary['conclusion']})\n"
                    f"分析快照: {summary['snapshots_analyzed']} 个\n\n"
                    f"*24h 趋势:*\n{trend_lines}"
                )
                await _tg_alert(msg)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"autoDream failed: {e}")
    return _fn


def _make_survival_recalc_fn(db_factory):
    async def _fn():
        try:
            from services.survival_service import recalculate_all_tiers
            db = db_factory()
            try:
                recalculate_all_tiers(db)
            finally:
                db.close()
        except Exception as e:
            logger.error("survival_recalc failed: %s", e)
    return _fn


def _make_reputation_decay_fn(db_factory):
    async def _fn():
        try:
            from services.reputation import apply_inactivity_decay
            db = db_factory()
            try:
                apply_inactivity_decay(db)
            finally:
                db.close()
        except Exception as e:
            logger.error("reputation_decay failed: %s", e)
    return _fn


def _make_sandbox_monitor_fn(db_factory):
    async def _fn():
        try:
            from services.sandbox import get_pending_evaluations, evaluate_experiment, finalize_experiment
            from services.evolution_ledger import record_evolution
            db = db_factory()
            try:
                pending = get_pending_evaluations(db)
                for exp in pending:
                    result = evaluate_experiment(db, exp.id)
                    decision = result.get("recommendation", "rollback")
                    if decision == "insufficient_data":
                        # 延长 24h
                        db.execute(
                            __import__('sqlalchemy').text(
                                "UPDATE sandbox_experiments SET ends_at = NOW() + INTERVAL 24 HOUR WHERE id=:id"
                            ), {"id": exp.id}
                        )
                        db.commit()
                        logger.info("sandbox_monitor: insufficient data for %s, extending 24h", exp.id)
                        continue
                    final_status = "promoted" if decision == "promote" else "rolled_back"
                    finalize_experiment(db, exp.id, final_status)
                    if decision == "promote":
                        try:
                            record_evolution(db, exp.proposal_id, result)
                        except Exception:
                            pass  # evolution ledger 失败不影响主流程
                    logger.info("sandbox_monitor: finalized %s as %s", exp.id, final_status)

                    # Telegram 告警
                    emoji = "🚀" if decision == "promote" else "🔄"
                    msg = (
                        f"{emoji} *Sandbox 实验结束* `{exp.id}`\n\n"
                        f"决定: *{final_status}*\n"
                        f"sandbox 成功率: {result.get('sandbox_success_rate', 0)*100:.1f}%\n"
                        f"control 成功率: {result.get('control_success_rate', 0)*100:.1f}%\n"
                        f"Δ: {result.get('delta', 0)*100:+.1f}%"
                    )
                    await _tg_alert(msg)
            finally:
                db.close()
        except Exception as e:
            logger.error("sandbox_monitor failed: %s", e)
    return _fn


def _make_flush_nau_fn(db_factory):
    async def _fn():
        """批量 mint pending_nau_rewards 表中积累的奖励到链上。"""
        try:
            from sqlalchemy import text
            from services.nautilus_token import NautilusTokenService
            db = db_factory()
            try:
                rows = db.execute(text(
                    "SELECT pr.id, pr.agent_id, pr.amount, pr.reason, a.blockchain_address "
                    "FROM pending_nau_rewards pr "
                    "JOIN agents a ON a.id = pr.agent_id "
                    "WHERE pr.status = 'pending' AND a.blockchain_address IS NOT NULL "
                    "ORDER BY pr.created_at ASC LIMIT 50"
                )).fetchall()

                if not rows:
                    logger.info("flush_pending_nau: no pending rewards")
                    return

                success = 0
                for row in rows:
                    tx_hash = await NautilusTokenService.mint_task_reward(
                        row.blockchain_address, "platform_improvement"
                    )
                    if tx_hash:
                        db.execute(text(
                            "UPDATE pending_nau_rewards "
                            "SET status='fulfilled', tx_hash=:tx, fulfilled_at=NOW() "
                            "WHERE id=:id"
                        ), {"tx": tx_hash, "id": row.id})
                        db.commit()
                        success += 1
                    else:
                        logger.warning("flush_nau: mint failed for agent %s reward %s", row.agent_id, row.id)

                logger.info("flush_pending_nau: minted %d/%d rewards on-chain", success, len(rows))
            finally:
                db.close()
        except Exception as e:
            logger.error("flush_pending_nau failed: %s", e)
    return _fn


def _make_auto_accept_bids_fn(db_factory):
    """闭环第四步：为 platform_meta 任务自动接受最优竞价。

    逻辑：
    1. 找所有 status='pending' 且 task_type='platform_meta' 且有 pending bids 的任务
    2. 每个任务选 bid_nau 最低的竞价（最便宜最优先）
    3. 将该竞价 status→'accepted'，其余→'rejected'
    4. 将任务 status→'in_progress'，assigned_agent_id 填入
    """
    async def _fn():
        try:
            from sqlalchemy import text
            db = db_factory()
            try:
                # 找有 pending bids 的 platform_meta 任务
                tasks_with_bids = db.execute(text(
                    "SELECT DISTINCT tb.task_id "
                    "FROM task_bids tb "
                    "JOIN academic_tasks at ON at.task_id = tb.task_id "
                    "WHERE tb.status = 'pending' "
                    "  AND at.status = 'pending' "
                    "  AND at.task_type = 'platform_meta'"
                )).fetchall()

                if not tasks_with_bids:
                    return

                accepted_count = 0
                for row in tasks_with_bids:
                    tid = row.task_id
                    # 取 bid_nau 最低的 pending bid
                    best_bid = db.execute(text(
                        "SELECT id, agent_id, bid_nau FROM task_bids "
                        "WHERE task_id = :tid AND status = 'pending' "
                        "ORDER BY bid_nau ASC LIMIT 1"
                    ), {"tid": tid}).fetchone()
                    if not best_bid:
                        continue

                    # 接受最优 bid
                    db.execute(text(
                        "UPDATE task_bids SET status='accepted' WHERE id=:bid_id"
                    ), {"bid_id": best_bid.id})

                    # 拒绝该任务的其余 pending bids
                    db.execute(text(
                        "UPDATE task_bids SET status='rejected' "
                        "WHERE task_id=:tid AND status='pending' AND id!=:bid_id"
                    ), {"tid": tid, "bid_id": best_bid.id})

                    # 更新任务状态（直接使用 best_bid.agent_id，与 agents.agent_id FK 一致）
                    db.execute(text(
                        "UPDATE academic_tasks "
                        "SET status='in_progress', assigned_agent_id=:aid, updated_at=NOW() "
                        "WHERE task_id=:tid"
                    ), {"aid": best_bid.agent_id, "tid": tid})

                    db.commit()
                    accepted_count += 1
                    logger.info(
                        "auto_accept_bids: accepted bid %s for task %s, agent_id=%s, bid_nau=%.4f",
                        best_bid.id, tid, best_bid.agent_id, best_bid.bid_nau
                    )

                if accepted_count:
                    await _tg_alert(
                        f"✅ *auto_accept_bids*: 接受了 {accepted_count} 个 platform_meta 任务竞价"
                    )
            finally:
                db.close()
        except Exception as e:
            logger.error("auto_accept_bids failed: %s", e)
    return _fn


def _make_auto_submit_proposals_fn(db_factory):
    """闭环第五步：对 in_progress 的 platform_meta 任务自动提交改进提案。

    Uses proposal_intelligence module for genuine multi-agent analysis:
      Level 0: Claude direct analysis with real platform data
      Level 1: DeerFlow multi-step research pipeline
      Level 2: RAID-3 parallel consensus (3 agents + judge)
      Level 3: A2A sub-task decomposition + RAID-3
    """
    async def _fn():
        try:
            from sqlalchemy import text
            from services.proposal_intelligence import analyse_and_propose
            db = db_factory()
            try:
                # 找需要提案的任务
                tasks = db.execute(text(
                    "SELECT at.id AS int_id, at.task_id, at.title, at.description, "
                    "       at.assigned_agent_id "
                    "FROM academic_tasks at "
                    "WHERE at.status = 'in_progress' "
                    "  AND at.task_type = 'platform_meta' "
                    "  AND at.assigned_agent_id IS NOT NULL "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM platform_improvement_proposals p "
                    "    WHERE p.task_id = at.id"
                    "  )"
                )).fetchall()

                if not tasks:
                    return

                submitted = 0
                for task in tasks:
                    proposal_id = await analyse_and_propose(
                        task_title=task.title or "",
                        task_description=task.description or "",
                        agent_id=task.assigned_agent_id,
                        task_int_id=task.int_id,
                        db=db,
                    )
                    if proposal_id:
                        submitted += 1
                        logger.info(
                            "auto_submit_proposals: submitted proposal %s for task %s",
                            proposal_id, task.task_id
                        )

                if submitted:
                    await _tg_alert(
                        f"🧠 *auto_submit_proposals*: 智能分析并提交了 {submitted} 个平台改进提案"
                    )
            finally:
                db.close()
        except Exception as e:
            logger.error("auto_submit_proposals failed: %s", e)
    return _fn


def start_cron_registry(db_factory) -> None:
    """启动所有 Cron 任务"""
    global _scheduler
    from services.agent_autonomy import run_autonomy_cycle

    _scheduler = AsyncIOScheduler()

    async def _autonomy_scan_fn():
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_autonomy_cycle, db_factory)
        if result.get("bids_submitted", 0) > 0 or result.get("errors"):
            logger.info("agent_autonomy_scan: scanned=%d bids=%d errors=%d",
                        result.get("scanned", 0), result.get("bids_submitted", 0), len(result.get("errors", [])))

    job_fns = {
        "platform_metrics_snapshot": _make_platform_snapshot_fn(),
        "anomaly_detection": _make_anomaly_detection_fn(),
        "agent_autonomy_scan": _autonomy_scan_fn,
        "autodream_consolidation": _make_autodream_fn(),
        "survival_tier_recalculation": _make_survival_recalc_fn(db_factory),
        "reputation_decay": _make_reputation_decay_fn(db_factory),
        "sandbox_monitor": _make_sandbox_monitor_fn(db_factory),
        "flush_pending_nau": _make_flush_nau_fn(db_factory),
        "auto_accept_bids": _make_auto_accept_bids_fn(db_factory),
        "auto_submit_proposals": _make_auto_submit_proposals_fn(db_factory),
        "se_marketplace": _make_se_marketplace_fn(db_factory),
    }

    for spec in CRON_JOBS:
        job_id = spec["id"]
        fn = job_fns.get(job_id)
        if fn is None:
            logger.warning(f"No function registered for cron job: {job_id}")
            continue
        budget = spec["budget_seconds"]
        _scheduler.add_job(
            func=_run_with_budget,
            args=[job_id, fn, budget],
            trigger=spec["trigger"],
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"Registered cron job: {job_id} — {spec['description']}")

    _scheduler.start()
    logger.info("Cron registry started with %d jobs", len(CRON_JOBS))


def stop_cron_registry() -> None:
    """停止 Cron 注册表"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Cron registry stopped")


def get_registered_jobs() -> list:
    """返回所有已注册任务的状态"""
    if _scheduler is None:
        return []
    return [
        {
            "id": job.id,
            "next_run_time": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        }
        for job in _scheduler.get_jobs()
    ]
