"""
Marketplace API - unified task query across all task types, plus task bidding market.
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
import logging

from models.database import AcademicTask, SimulationTask
from utils.database import get_db
from services.pricing import get_price_list

router = APIRouter()
logger = logging.getLogger(__name__)

# Second router for task bidding marketplace (prefix=/api/marketplace)
task_router = APIRouter(prefix="/api/marketplace", tags=["task-marketplace"])


@router.get("/all")
def list_all_tasks(
    type: Optional[str] = Query(None, description="Filter: academic|labeling|simulation"),
    status: Optional[str] = Query(None, description="Filter: pending|processing|completed|failed"),
    search: Optional[str] = Query(None, description="Search in title"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Unified task listing across academic, labeling, and simulation."""
    db: Session = next(get_db())
    try:
        tasks = []
        offset = (page - 1) * limit

        # Academic tasks
        if type is None or type == "academic":
            q = db.query(AcademicTask)
            if status:
                q = q.filter(AcademicTask.status == status)
            if search:
                q = q.filter(AcademicTask.title.ilike(f"%{search}%"))
            for t in q.order_by(AcademicTask.created_at.desc()).all():
                tasks.append({
                    "id": t.task_id,
                    "title": t.title,
                    "type": t.task_type,
                    "category": "academic",
                    "status": t.status,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                })

        # Simulation tasks
        if type is None or type == "simulation":
            q = db.query(SimulationTask)
            if status:
                q = q.filter(SimulationTask.status == status)
            if search:
                q = q.filter(SimulationTask.title.ilike(f"%{search}%"))
            for t in q.order_by(SimulationTask.created_at.desc()).all():
                tasks.append({
                    "id": t.task_id,
                    "title": t.title,
                    "type": t.simulation_type,
                    "category": "simulation",
                    "status": t.status,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                })

        # Sort all by created_at descending
        tasks.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        total = len(tasks)
        tasks = tasks[offset:offset + limit]

        return {
            "success": True,
            "data": tasks,
            "meta": {"page": page, "limit": limit, "total": total},
        }
    finally:
        db.close()


@router.get("/stats")
def marketplace_stats():
    """Unified statistics across all task types."""
    db: Session = next(get_db())
    try:
        academic_total = db.query(AcademicTask).count()
        academic_done = db.query(AcademicTask).filter(AcademicTask.status == "completed").count()
        sim_total = db.query(SimulationTask).count()
        sim_done = db.query(SimulationTask).filter(SimulationTask.status == "completed").count()

        total = academic_total + sim_total
        completed = academic_done + sim_done
        success_rate = round(completed / total * 100, 1) if total > 0 else 0

        return {
            "success": True,
            "data": {
                "total_tasks": total,
                "completed": completed,
                "success_rate": success_rate,
                "by_category": {
                    "academic": {"total": academic_total, "completed": academic_done},
                    "simulation": {"total": sim_total, "completed": sim_done},
                },
            },
        }
    finally:
        db.close()


@router.get("/prices")
def marketplace_prices():
    """Return current price list for all task types."""
    return {"success": True, "data": get_price_list()}


# ---------------------------------------------------------------------------
# Task bidding marketplace — request/response bodies
# ---------------------------------------------------------------------------


class SubmitBidRequest(BaseModel):
    agent_id: str = Field(..., description="Agent ID submitting the bid")
    bid_nau: float = Field(..., gt=0, description="Bid amount in NAU tokens")
    estimated_minutes: Optional[int] = Field(10, ge=1, description="Estimated completion time in minutes")
    message: Optional[str] = Field("", max_length=1000, description="Optional message to the task owner")


class RateTaskRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Quality rating 1-5")
    comment: Optional[str] = Field("", max_length=2000, description="Optional rating comment")


# ---------------------------------------------------------------------------
# Task bidding marketplace — endpoints (task_router, prefix=/api/marketplace)
# ---------------------------------------------------------------------------


@task_router.post("/tasks/submit")
async def tm_submit_human_task():
    """[已下线] 人机协作任务创建入口已收敛：任务发布唯一入口为 POST /api/tasks。"""
    from fastapi import HTTPException
    raise HTTPException(
        status_code=410,
        detail={
            "error": {
                "code": "ENDPOINT_RETIRED",
                "message": "任务发布唯一入口为 POST /api/tasks（软件工程任务市场：自主竞价 + 3 专家评审 + 华币/NAU 奖励）",
            }
        },
    )


@task_router.get("/tasks/{task_id}")
async def tm_get_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    """Get task status and result by task_id. Used for polling from frontend."""
    from models.database import AcademicTask as AcademicTaskModel

    row = db.query(AcademicTaskModel).filter(AcademicTaskModel.task_id == task_id).first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"error": "Task not found"})

    return {
        "success": True,
        "data": {
            "task_id": row.task_id,
            "title": row.title,
            "task_type": row.task_type,
            "status": row.status,
            "result_output": row.result_output,
            "result_error": row.result_error,
            "execution_time": row.execution_time,
            "quality_rating": row.quality_rating,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        },
        "error": None,
    }


@task_router.get("/tasks")
async def tm_list_open_tasks(
    specialty: Optional[str] = Query(None, description="Filter by task_type, e.g. physics_simulation"),
    limit: int = Query(50, ge=1, le=200, description="Max number of tasks to return"),
    db: Session = Depends(get_db),
):
    """List open marketplace tasks.

    Returns tasks with marketplace_open=True and status=pending,
    sorted by min_bid_nau DESC (highest-value tasks first).

    **Query Parameters**:
    - `specialty`: Optional task_type filter (e.g. `physics_simulation`, `ml_training`)
    - `limit`: Maximum results (1-200, default 50)
    """
    from services.task_marketplace import list_open_tasks
    tasks = await list_open_tasks(db, specialty=specialty, limit=limit)
    return {
        "success": True,
        "data": {"tasks": tasks, "count": len(tasks)},
        "error": None,
    }


@task_router.get("/tasks/{task_id}/bids")
async def tm_get_task_bids(
    task_id: str,
    db: Session = Depends(get_db),
):
    """List all bids for a specific task, sorted by bid_nau DESC.

    **Path Parameters**:
    - `task_id`: Academic task identifier

    **Errors**:
    - `404`: Task not found
    """
    from services.task_marketplace import get_task_bids
    bids = await get_task_bids(db, task_id=task_id)
    return {
        "success": True,
        "data": {"bids": bids, "count": len(bids)},
        "error": None,
    }


@task_router.post("/tasks/{task_id}/bid")
async def tm_submit_bid(
    task_id: str,
    body: SubmitBidRequest,
    db: Session = Depends(get_db),
):
    """Submit a bid for an open marketplace task.

    **Path Parameters**:
    - `task_id`: Academic task identifier

    **Request Body**:
    - `agent_id`: Bidding agent ID
    - `bid_nau`: Bid amount in NAU tokens (must be >= task min_bid_nau if set)
    - `estimated_minutes`: Estimated time to complete (default 10)
    - `message`: Optional note to the task owner

    **Errors**:
    - `400`: Task not open / bid too low / task not pending
    - `404`: Task not found
    - `409`: Agent already has a bid on this task
    """
    from services.task_marketplace import submit_bid
    result = await submit_bid(
        db,
        task_id=task_id,
        agent_id=body.agent_id,
        bid_nau=body.bid_nau,
        estimated_minutes=body.estimated_minutes or 10,
        message=body.message or "",
    )
    return {
        "success": True,
        "data": result,
        "error": None,
    }


@task_router.post("/tasks/{task_id}/accept-bid/{bid_id}")
async def tm_accept_bid(
    task_id: str,
    bid_id: str,
    db: Session = Depends(get_db),
):
    """Accept a specific bid for a task.

    Accepts the chosen bid, rejects all others, and assigns the agent to the task.
    The task is removed from the marketplace (marketplace_open = False).

    **Path Parameters**:
    - `task_id`: Academic task identifier
    - `bid_id`: Bid identifier to accept

    **Errors**:
    - `404`: Task or bid not found
    """
    from services.task_marketplace import accept_bid
    result = await accept_bid(db, task_id=task_id, bid_id=bid_id)
    return {
        "success": True,
        "data": result,
        "error": None,
    }


@task_router.post("/tasks/{task_id}/rate")
async def tm_rate_task(
    task_id: str,
    body: RateTaskRequest,
    db: Session = Depends(get_db),
):
    """Rate a completed task (1-5 stars).

    Updates the task quality_rating and adjusts the assigned agent's
    reputation score via EWMA.

    **Path Parameters**:
    - `task_id`: Academic task identifier

    **Request Body**:
    - `rating`: Integer 1-5
    - `comment`: Optional feedback comment

    **Errors**:
    - `400`: Invalid rating / task not completed
    - `404`: Task not found
    """
    from services.task_marketplace import rate_task
    result = await rate_task(
        db,
        task_id=task_id,
        rating=body.rating,
        comment=body.comment or "",
    )
    return {
        "success": True,
        "data": result,
        "error": None,
    }
