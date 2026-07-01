"""链上可信追踪校验接口。

GET  /api/audit/{task_id} —— 拉链上 AuditRecord 事件（权威来源），用源表(Task/TaskReview)
                            重算规范化哈希比对，并核验动作主体(actor)，逐条给出
                            「内容一致 / 被篡改」与「主体已验证」。公开只读，任何人可验证。
POST /api/audit/retry     —— 重试 status=failed 的存证（管理员）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from models.database import Task, TaskReview, Agent, User
from utils.database import get_db
from utils.auth import get_current_user
from blockchain.web3_config import get_web3_config
from services import audit_trail as at

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/audit/{task_id}")
def verify_audit_trail(task_id: int, db: Session = Depends(get_db)):
    """校验某任务的链上可信追踪：链上事件 vs 源表重算哈希 + 主体核验。"""
    config = get_web3_config()
    if config.audit_contract is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="链上可信追踪未启用（AUDIT_TRAIL_ADDRESS 未配置）")

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    # 1. 从链上独立读取本任务的全部存证事件（权威来源，不信任链下表）
    try:
        events = config.audit_contract.events.AuditRecord.get_logs(
            from_block=0, argument_filters={"taskId": task_id})
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"读取链上事件失败: {exc}")

    # 预取评审行 + 其 owner，供 REVIEW 逐条 hash 匹配（一个任务多条评审）
    reviews = db.query(TaskReview).filter(TaskReview.task_id == task_id).all()
    owners = {}
    for r in reviews:
        if r.reviewer_agent_id not in owners:
            a = db.query(Agent).filter(Agent.agent_id == r.reviewer_agent_id).first()
            owners[r.reviewer_agent_id] = (a.owner if a else None)

    action_name = {v: k for k, v in at.ACTIONS.items()}
    records, tampered = [], []
    for ev in events:
        args = ev["args"]
        action = action_name.get(int(args["action"]), str(args["action"]))
        onchain_hash = at.content_hash_from_bytes(args["contentHash"])
        actor = (args["actor"] or "").lower()
        recomputed, matched_ref, expected_actor = "", None, ""

        # 2. 用源表重算该动作的规范化哈希，与链上比对；同时确定「应有主体」
        if action == "PUBLISH":
            recomputed = at.content_hash(at.canonical_publish(task))
            expected_actor = (task.publisher or "").lower()
        elif action in ("ACCEPT", "AWARD"):
            recomputed = at.content_hash(at.canonical_accept(task, action))
            expected_actor = (task.agent or "").lower()
        elif action == "SUBMIT":
            recomputed = at.content_hash(at.canonical_submit(task))
            expected_actor = (task.agent or "").lower()
        elif action == "COMPLETE":
            recomputed = at.content_hash(at.canonical_complete(task))
            expected_actor = (task.publisher or "").lower()
        elif action == "REVIEW":
            # 逐条评审重算，找与链上哈希匹配的那条（未篡改则必有匹配）
            for r in reviews:
                ow = owners.get(r.reviewer_agent_id)
                h = at.content_hash(at.canonical_review(task_id, r, ow))
                if h == onchain_hash:
                    recomputed, matched_ref, expected_actor = h, r.id, (ow or "").lower()
                    break

        content_match = bool(recomputed) and (recomputed == onchain_hash)
        actor_verified = bool(expected_actor) and (actor == expected_actor)
        if not content_match:
            tampered.append(action)
        records.append({
            "action": action, "actor": actor,
            "onchain_hash": onchain_hash, "recomputed_hash": recomputed,
            "content_match": content_match, "actor_verified": actor_verified,
            "matched_review_id": matched_ref,
            "seq": int(args["seq"]), "timestamp": int(args["timestamp"]),
            "tx_hash": ev["transactionHash"].hex() if hasattr(ev["transactionHash"], "hex") else str(ev["transactionHash"]),
        })

    records.sort(key=lambda x: x["seq"])
    return {
        "task_id": task_id,
        "onchain_records": len(records),
        "verified": len(records) > 0 and len(tampered) == 0,
        "tampered_actions": tampered,
        "records": records,
    }


@router.post("/audit/retry")
def retry_audit(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """重试 status=failed 的存证（管理员）。"""
    if not getattr(current_user, "is_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return at.retry_failed_audits(db)
