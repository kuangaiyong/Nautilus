"""SE PoUW 流程编排（DB / 链 / LLM IO）。

- P1 自主竞价：auto_bid_open_se_tasks / award_se_task / award_due_se_tasks
- P2 完成铸 NAU：mint_nau_for_task
- P3 三专家评审：run_expert_reviews / review_result

纯配置与计算在 services/se_pouw.py。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from services import se_pouw

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# P1 自主竞价
# ---------------------------------------------------------------------------

def auto_bid_open_se_tasks(db: Session) -> int:
    """所有 autonomy_enabled 的智能体对 OPEN 的 SE 任务投标（幂等）。返回新增投标数。

    每个自主智能体对每个未投过标的 OPEN SE 任务投标，加权分 = 声誉 + 专长匹配奖励
    （见 se_pouw.bid_weight）；非自交易（发布者 != 智能体 owner）。
    """
    from models.database import Agent, Task, TaskStatus, DmasTaskBid

    agents = db.query(Agent).filter(Agent.autonomy_enabled == True).all()  # noqa: E712
    if not agents:
        return 0
    open_se = [t for t in db.query(Task).filter(Task.status == TaskStatus.OPEN).all()
               if se_pouw.is_se_task(t.task_type)]
    if not open_se:
        return 0

    created = 0
    for task in open_se:
        for agent in agents:
            if task.publisher and agent.owner and task.publisher.lower() == agent.owner.lower():
                continue  # 不对自己发布的任务投标
            exists = db.query(DmasTaskBid).filter_by(task_id=task.id, agent_id=agent.agent_id).first()
            if exists:
                continue
            db.add(DmasTaskBid(
                task_id=task.id, agent_id=agent.agent_id,
                weight=se_pouw.bid_weight(agent.reputation_score, task.task_type, agent.specialties),
                status="pending",
                message=f"auto-bid by agent {agent.agent_id}",
                created_at=datetime.utcnow(),
            ))
            created += 1
    if created:
        db.commit()
    logger.info("auto_bid_open_se_tasks: agents=%d open_se=%d new_bids=%d", len(agents), len(open_se), created)
    return created


def award_se_task(db: Session, task_id: int) -> Optional[int]:
    """对一个 OPEN 的 SE 任务择优中标（最高 weight），派单。返回中标 agent_id 或 None。"""
    from models.database import Agent, Task, TaskStatus, DmasTaskBid

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task or task.status != TaskStatus.OPEN:
        return None
    bids = db.query(DmasTaskBid).filter(DmasTaskBid.task_id == task_id,
                                        DmasTaskBid.status == "pending").all()
    if not bids:
        return None
    winner = max(bids, key=lambda b: b.weight)
    win_agent = db.query(Agent).filter(Agent.agent_id == winner.agent_id).first()
    if not win_agent:
        return None

    # 派单：与 accept 端点一致地置 ACCEPTED + 指派
    task.status = TaskStatus.ACCEPTED
    task.agent = win_agent.owner
    task.accepted_at = datetime.now(timezone.utc)
    win_agent.current_tasks = (win_agent.current_tasks or 0) + 1
    for b in bids:
        b.status = "won" if b.id == winner.id else "lost"
    db.commit()
    # 链上可信追踪：派单动作存证（A-ii，主体=中标智能体 owner；SE 任务的"抢到任务"入口）
    try:
        db.refresh(task)  # 取 DB 值（accepted_at 秒精度），保证与校验重算一致
        from services.audit_trail import record_audit_bg, canonical_accept
        record_audit_bg(task.agent, task.id, "AWARD", canonical_accept(task, "AWARD"))
    except Exception as _audit_exc:
        logger.warning("audit AWARD failed task=%s: %s", task_id, _audit_exc)
    logger.info("award_se_task: task=%s winner_agent=%s weight=%.1f (bids=%d)",
                task_id, winner.agent_id, winner.weight, len(bids))
    return winner.agent_id


def award_due_se_tasks(db: Session) -> int:
    """竞价窗已过且有投标的 OPEN SE 任务，批量择优中标。返回成交数。(供 cron 调用)"""
    from models.database import Task, TaskStatus, DmasTaskBid

    cutoff = datetime.utcnow() - timedelta(seconds=se_pouw.BID_WINDOW_SECONDS)
    awarded = 0
    open_se = [t for t in db.query(Task).filter(Task.status == TaskStatus.OPEN).all()
               if se_pouw.is_se_task(t.task_type)]
    for task in open_se:
        if task.created_at and task.created_at > cutoff:
            continue  # 竞价窗未到
        has_bid = db.query(DmasTaskBid).filter(DmasTaskBid.task_id == task.id,
                                               DmasTaskBid.status == "pending").first()
        if has_bid and award_se_task(db, task.id):
            awarded += 1
    return awarded


# ---------------------------------------------------------------------------
# P3 三专家评审（LLM 自动评分）
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = (
    "你是资深软件工程评审专家。请客观评估交付物对任务要求的满足程度，"
    "从正确性、完整性、规范性三维度打分（每维 0-5），并给出综合分(0-5)。"
    "只输出 JSON，不要多余文字。"
)


def select_reviewers(db: Session, task, limit: Optional[int] = None) -> list:
    """候选评审专家：排除执行者与发布者；对口专长优先、声誉降序。

    limit=None 返回全部合格候选（供 run_expert_reviews 在某评审 degraded 时换人补齐），
    否则取前 limit 个。
    """
    from models.database import Agent

    cands = db.query(Agent).all()
    out = []
    for a in cands:
        if not a.owner:
            continue
        if task.agent and a.owner.lower() == task.agent.lower():
            continue  # 执行者本人不评审自己
        if task.publisher and a.owner.lower() == task.publisher.lower():
            continue  # 发布者不在 3 专家之列
        out.append(a)
    out.sort(key=lambda a: (1 if se_pouw.specialty_match(task.task_type, a.specialties) else 0,
                            float(a.reputation_score or 50.0)), reverse=True)
    return out if limit is None else out[:limit]


def _review_one(info: dict) -> dict:
    """单个评审用统一 LLM 网关对交付物打分。入参为纯值快照(线程安全，供并行调用)。

    对模型间歇性空响应/格式问题做最多 3 次重试；空响应绝不当 0 分（否则会误判失败）。
    3 次仍拿不到有效评分时，回落中性分 3.0（benefit-of-doubt，不因 LLM 抖动卡死流程）。
    """
    import json
    import re
    from services.llm_gateway import chat, is_configured

    deliverable = (info.get("result") or "")[:6000]
    prompt = (
        f"任务类型: {info.get('task_type')}\n任务要求:\n{(info.get('description') or '')[:2000]}\n\n"
        f"期望产出:\n{(info.get('expected_output') or '')[:1000]}\n\n"
        f"智能体交付物:\n{deliverable or '(空)'}\n\n"
        '严格只输出一行 JSON（不要解释、不要 markdown 代码块）：'
        '{"correctness":0-5,"completeness":0-5,"standards":0-5,"score":0-5,"comment":"简评"}'
    )
    clamp = lambda v: max(0.0, min(5.0, float(v)))
    last_err = None
    for _ in range(3):
        try:
            if not is_configured():
                raise RuntimeError("LLM 网关未配置")
            raw = chat(prompt, system=_REVIEW_SYSTEM, max_tokens=500, temperature=0.2)
            if not raw or not raw.strip():
                last_err = "空响应"
                continue
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                last_err = "未找到 JSON"
                continue
            data = json.loads(m.group(0))
            if "score" not in data and "correctness" not in data:
                last_err = "缺少评分字段"
                continue
            sc = clamp(data.get("score", data.get("correctness")))
            return {
                "score": sc,
                "correctness": clamp(data.get("correctness", sc)),
                "completeness": clamp(data.get("completeness", sc)),
                "standards": clamp(data.get("standards", sc)),
                "comment": str(data.get("comment", ""))[:500],
                "degraded": False,
            }
        except Exception as exc:
            last_err = str(exc)
            continue
    logger.warning("se review LLM 无有效评分(task=%s): %s -> 标记 degraded(不持久化)", info.get("id"), last_err)
    return {"score": 3.0, "correctness": 3.0, "completeness": 3.0, "standards": 3.0,
            "comment": f"LLM 评审无有效输出（{last_err}）", "degraded": True}


def _record_review_audits(db, task_id, new_reviews) -> None:
    """评审入库后逐条链上存证（A-ii，主体=评审专家 owner；best-effort，绝不影响评审流程）。"""
    try:
        from services.audit_trail import record_audit_bg, canonical_review
    except Exception:
        return
    for review, owner in new_reviews:
        try:
            db.refresh(review)  # 取 DB 值（created_at 秒精度），保证与校验重算一致
            record_audit_bg(owner, task_id, "REVIEW",
                            canonical_review(task_id, review, owner), ref_id=review.id)
        except Exception as exc:
            logger.warning("audit REVIEW failed task=%s reviewer=%s: %s", task_id, review.reviewer_agent_id, exc)


def run_expert_reviews(db: Session, task_id: int) -> dict:
    """为 SE 任务凑齐 NUM_REVIEWERS 个"有效"专家 LLM 评分并入库（幂等可补齐）。

    某次评分 degraded（空响应/格式错/网关不可用）时不持久化、改从候选池换下一个智能体
    补评，直到凑够 NUM_REVIEWERS 个有效评审或候选耗尽。LLM 持续不可用 → 有效评审不足 →
    review_result.complete=False，complete 端点据此 fail-closed（不结算、不判失败、可重试），
    避免 LLM 抖动期间垃圾交付物靠回落分"通过"。串行换人补齐，对单点 LLM 抖动稳健。
    """
    from models.database import Task, TaskReview

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return {"avg": 0.0, "passed": False, "n": 0, "complete": False, "reviews": []}
    done = {r.reviewer_agent_id for r in db.query(TaskReview).filter(TaskReview.task_id == task_id).all()}
    need = se_pouw.NUM_REVIEWERS - len(done)
    if need > 0:
        # 纯值快照（_review_one 只读这些，避免跨调用持有 ORM 对象）
        info = {
            "id": task.id,
            "task_type": task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type),
            "description": task.description or "",
            "expected_output": task.expected_output or "",
            "result": getattr(task, "result", None) or "",
        }
        pool = [a for a in select_reviewers(db, task, limit=None) if a.agent_id not in done]
        persisted = 0
        new_reviews = []  # (review_obj, reviewer_owner)，供 commit 后逐条链上存证
        for agent in pool:
            if persisted >= need:
                break
            sc = _review_one(info)
            if sc.get("degraded"):
                continue  # 该次 LLM 无有效评分：换下一个候选补评（degraded 不持久化）
            review = TaskReview(
                task_id=task_id, reviewer_agent_id=agent.agent_id,
                score=sc["score"], correctness=sc["correctness"],
                completeness=sc["completeness"], standards=sc["standards"],
                comment=sc["comment"], created_at=datetime.utcnow(),
            )
            db.add(review)
            new_reviews.append((review, agent.owner))
            persisted += 1
        if persisted:
            db.commit()
            _record_review_audits(db, task_id, new_reviews)
        else:
            db.rollback()  # 无任何有效评审，避免悬挂事务
    res = review_result(db, task_id)
    logger.info("run_expert_reviews: task=%s reviewers=%d avg=%.2f passed=%s complete=%s",
                task_id, res["n"], res["avg"], res["passed"], res["complete"])
    return res


def review_result(db: Session, task_id: int) -> dict:
    """读取并聚合某任务的评审结果。"""
    from models.database import TaskReview

    rows = db.query(TaskReview).filter(TaskReview.task_id == task_id).all()
    agg = se_pouw.aggregate_reviews([r.score for r in rows])
    # complete: 是否已积累到 NUM_REVIEWERS 个有效评审。complete 端点据此 fail-closed：
    # LLM 不可用导致有效评审不足时，不结算、不判失败，保持任务可重试。
    agg["complete"] = agg["n"] >= se_pouw.NUM_REVIEWERS
    agg["reviews"] = [{"reviewer_agent_id": r.reviewer_agent_id, "score": r.score,
                       "comment": r.comment} for r in rows]
    return agg


# ---------------------------------------------------------------------------
# P2 完成铸 NAU
# ---------------------------------------------------------------------------

async def mint_nau_for_task(db: Session, task) -> Optional[str]:
    """给中标智能体按任务类型铸 NAU（PoUW 奖励）。返回 tx_hash 或 None（绝不抛）。"""
    from models.database import Agent
    from services.nautilus_token import NautilusTokenService

    agent = db.query(Agent).filter(Agent.owner == task.agent).first()
    if not agent:
        return None
    tt = task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type)
    return await NautilusTokenService.mint_task_reward(agent.owner, tt)
