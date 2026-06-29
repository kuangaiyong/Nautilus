"""
Platform Proposals API
提案的 CRUD + 投票端点（独立 router，避免 platform.py 过长）。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
import logging
import uuid
import json

from utils.database import get_db
from services.proposal_consensus import get_vote_weight, get_proposal_status

router = APIRouter(prefix="/api/platform/proposals", tags=["Platform Proposals"])
logger = logging.getLogger(__name__)


def _ok(data):
    return {"success": True, "data": data, "error": None}


def _err(msg):
    return {"success": False, "data": None, "error": msg}


def _q(db, sql, params=None):
    try:
        return db.execute(text(sql), params or {})
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise exc


# ---------------------------------------------------------------------------
# POST /api/platform/proposals
# ---------------------------------------------------------------------------
@router.post("")
def create_proposal(body: dict, db=Depends(get_db)):
    """创建新改进提案。"""
    try:
        required = ("task_id", "root_cause", "agent_id", "proposed_change", "expected_impact", "rollback_plan")
        for field in required:
            if field not in body:
                return _err(f"Missing required field: {field}")

        # Validate task exists and is platform_meta type
        task = _q(db,
            "SELECT id FROM academic_tasks WHERE id=:tid AND task_type='platform_meta'",
            {"tid": body["task_id"]}
        ).fetchone()
        if not task:
            return _err("task_id not found or task_type is not 'platform_meta'")

        # Validate agent exists
        agent = _q(db, "SELECT id FROM agents WHERE id=:aid", {"aid": body["agent_id"]}).fetchone()
        if not agent:
            return _err("agent_id not found")

        proposal_id = str(uuid.uuid4())
        _q(db,
            """INSERT INTO platform_improvement_proposals
               (id, task_id, root_cause, agent_id, proposed_change, expected_impact,
                rollback_plan, vote_score, vote_count, status)
               VALUES (:id, :task_id, :root_cause, :agent_id, :proposed_change,
                       :expected_impact, :rollback_plan, 0.0, 0, 'pending')""",
            {
                "id": proposal_id,
                "task_id": body["task_id"],
                "root_cause": body["root_cause"],
                "agent_id": body["agent_id"],
                "proposed_change": body["proposed_change"] if isinstance(body["proposed_change"], str) else json.dumps(body["proposed_change"]),
                "expected_impact": body["expected_impact"],
                "rollback_plan": body["rollback_plan"],
            }
        )
        db.commit()
        return _ok({"id": proposal_id})
    except Exception as e:
        logger.error(f"create_proposal error: {e}")
        return _err(str(e))


# ---------------------------------------------------------------------------
# GET /api/platform/proposals
# ---------------------------------------------------------------------------
@router.get("")
def list_proposals(
    status: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    """返回提案列表（含 vote_score, vote_count, status）。"""
    try:
        conds = ["1=1"]
        params: dict = {"limit": limit, "offset": offset}
        if status:
            conds.append("status = :status")
            params["status"] = status
        where = " AND ".join(conds)

        rows = _q(db,
            f"SELECT id, task_id, agent_id, root_cause, expected_impact, "
            f"       vote_score, vote_count, status, created_at "
            f"FROM platform_improvement_proposals WHERE {where} "
            f"ORDER BY created_at DESC LIMIT :limit OFFSET :offset",
            params
        ).fetchall()

        return _ok({
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "proposals": [{
                "id": r.id,
                "task_id": r.task_id,
                "agent_id": r.agent_id,
                "root_cause": r.root_cause,
                "expected_impact": r.expected_impact,
                "vote_score": float(r.vote_score or 0),
                "vote_count": int(r.vote_count or 0),
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            } for r in rows],
        })
    except Exception as e:
        logger.error(f"list_proposals error: {e}")
        return _err(str(e))


# ---------------------------------------------------------------------------
# GET /api/platform/proposals/{id}
# ---------------------------------------------------------------------------
@router.get("/{proposal_id}")
def get_proposal(proposal_id: str, db=Depends(get_db)):
    """返回单个提案详情。"""
    try:
        row = _q(db,
            "SELECT * FROM platform_improvement_proposals WHERE id=:id",
            {"id": proposal_id}
        ).fetchone()
        if not row:
            return _err("Proposal not found")

        return _ok({
            "id": row.id,
            "task_id": row.task_id,
            "agent_id": row.agent_id,
            "root_cause": row.root_cause,
            "proposed_change": row.proposed_change,
            "expected_impact": row.expected_impact,
            "rollback_plan": row.rollback_plan,
            "vote_score": float(row.vote_score or 0),
            "vote_count": int(row.vote_count or 0),
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    except Exception as e:
        logger.error(f"get_proposal error: {e}")
        return _err(str(e))


# ---------------------------------------------------------------------------
# POST /api/platform/proposals/{id}/vote
# ---------------------------------------------------------------------------
@router.post("/{proposal_id}/vote")
def vote_proposal(proposal_id: str, body: dict, db=Depends(get_db)):
    """声誉加权投票，防止重复投票。"""
    try:
        agent_id = body.get("agent_id")
        vote = body.get("vote")
        if not agent_id:
            return _err("Missing agent_id")
        if vote not in (1, -1):
            return _err("vote must be 1 or -1")

        # Fetch agent reputation
        agent_row = _q(db,
            "SELECT id, reputation_score FROM agents WHERE id=:aid",
            {"aid": agent_id}
        ).fetchone()
        if not agent_row:
            return _err("agent_id not found")

        reputation = float(agent_row.reputation_score or 0)
        weight = get_vote_weight(reputation)
        weighted = weight * vote

        # Fetch proposal (also checks existence)
        prop = _q(db,
            "SELECT id, vote_score, vote_count, status, proposed_change FROM platform_improvement_proposals WHERE id=:id",
            {"id": proposal_id}
        ).fetchone()
        if not prop:
            return _err("Proposal not found")

        # Insert vote (UNIQUE constraint on proposal_id + agent_id prevents duplicates)
        try:
            _q(db,
                "INSERT INTO platform_proposal_votes (proposal_id, agent_id, vote, weight) "
                "VALUES (:proposal_id, :agent_id, :vote, :weight)",
                {"proposal_id": proposal_id, "agent_id": agent_id, "vote": vote, "weight": weight}
            )
        except Exception:
            db.rollback()
            return _err("Already voted on this proposal")

        new_score = float(prop.vote_score or 0) + weighted
        new_count = int(prop.vote_count or 0) + 1

        # Determine change_type from proposed_change for threshold logic
        pc = prop.proposed_change or {}
        change_type = pc.get("type", "default") if isinstance(pc, dict) else "default"
        new_status = get_proposal_status(new_score, new_count, change_type)

        _q(db,
            "UPDATE platform_improvement_proposals "
            "SET vote_score=:score, vote_count=:count, status=:status "
            "WHERE id=:id",
            {"score": new_score, "count": new_count, "status": new_status, "id": proposal_id}
        )
        db.commit()

        # 达成共识时自动创建沙箱实验
        if new_status == "accepted":
            try:
                from services.proposal_consensus import on_consensus_reached
                from utils.database import SessionLocal as _SandboxSessionLocal
                on_consensus_reached(proposal_id, pc, _SandboxSessionLocal)
            except Exception as e:
                logger.warning(f"Failed to trigger sandbox experiment: {e}")

        return _ok({
            "proposal_id": proposal_id,
            "vote_weight": weight,
            "vote_score": new_score,
            "vote_count": new_count,
            "status": new_status,
        })
    except Exception as e:
        logger.error(f"vote_proposal error: {e}")
        return _err(str(e))
