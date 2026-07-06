"""
Data Labeling SaaS API - B2B labeling service with Raid consensus.

Wraps the BatchProcessor engine as a REST API for external clients.
"""
from fastapi import APIRouter, HTTPException, Request, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime, timezone
from slowapi import Limiter
from slowapi.util import get_remote_address
import asyncio
import logging
import os

router = APIRouter()
logger = logging.getLogger(__name__)

TESTING = os.getenv("TESTING", "false").lower() == "true"
limiter = Limiter(key_func=get_remote_address, enabled=not TESTING)

# Concurrency limit: at most 3 labeling jobs dispatched in parallel
_task_semaphore = asyncio.Semaphore(3)

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------
_labeling_jobs: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Enums & Models
# ---------------------------------------------------------------------------

class LabelingType(str, Enum):
    SENTIMENT = "sentiment"
    CLASSIFICATION = "classification"
    ENTITY_EXTRACTION = "entity_extraction"
    INTENT = "intent"
    SPAM = "spam"
    TOXICITY = "toxicity"
    CUSTOM = "custom"
    # Autonomous driving / embodied AI types
    OBJECT_DETECTION = "object_detection"
    SCENE_CLASSIFICATION = "scene_classification"
    ACTION_RECOGNITION = "action_recognition"
    SAFETY_CRITICAL = "safety_critical"
    ENVIRONMENTAL = "environmental"


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"



class LabelingItemResult(BaseModel):
    """Single labeled item in results."""
    id: Any = None
    text: str = ""
    label: str = ""
    confidence: float = 0.0
    consensus_votes: str = ""
    agreement_rate: float = 0.0
    consensus_met: bool = False


class QualityReport(BaseModel):
    """Quality metrics for a completed job."""
    total_items: int = 0
    num_agents: int = 0
    labeling_type: str = ""
    consensus_rate: float = 0.0
    avg_confidence: float = 0.0
    avg_agreement: float = 0.0
    label_distribution: Dict[str, int] = {}
    low_confidence_count: int = 0
    disagreement_count: int = 0
    needs_human_review: int = 0


class LabelingJobResponse(BaseModel):
    """Response representing a labeling job."""
    job_id: str
    name: str
    labeling_type: str
    status: str
    total_items: int
    completed_items: int
    created_at: str
    updated_at: str
    processing_time_s: Optional[float] = None
    results: Optional[List[LabelingItemResult]] = None
    quality_report: Optional[QualityReport] = None


class LabelingJobListResponse(BaseModel):
    """Paginated list of labeling jobs."""
    jobs: List[LabelingJobResponse]
    total: int
    page: int
    limit: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_to_response(job: dict) -> LabelingJobResponse:
    """Convert internal job dict to response model."""
    results = None
    if job.get("results"):
        results = [LabelingItemResult(**r) for r in job["results"]]

    quality_report = None
    if job.get("quality_report") and "error" not in job["quality_report"]:
        quality_report = QualityReport(**job["quality_report"])

    completed_items = len(job.get("results", [])) if job.get("results") else 0

    return LabelingJobResponse(
        job_id=job["job_id"],
        name=job["name"],
        labeling_type=job["labeling_type"],
        status=job["status"],
        total_items=job["total_items"],
        completed_items=completed_items,
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        processing_time_s=job.get("processing_time_s"),
        results=results,
        quality_report=quality_report,
    )


def _map_labeling_type(api_type: LabelingType) -> str:
    """Map API labeling type to BatchProcessor labeling type."""
    mapping = {
        LabelingType.SENTIMENT: "sentiment",
        LabelingType.CLASSIFICATION: "text_classification",
        LabelingType.ENTITY_EXTRACTION: "entity_extraction",
        LabelingType.INTENT: "intent",
        LabelingType.SPAM: "spam_detection",
        LabelingType.TOXICITY: "toxicity",
        LabelingType.CUSTOM: "custom",
        # AD / embodied AI types map through as-is
        LabelingType.OBJECT_DETECTION: "object_detection",
        LabelingType.SCENE_CLASSIFICATION: "scene_classification",
        LabelingType.ACTION_RECOGNITION: "action_recognition",
        LabelingType.SAFETY_CRITICAL: "safety_critical",
        LabelingType.ENVIRONMENTAL: "environmental",
    }
    return mapping.get(api_type, "custom")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs")
@limiter.limit("5/minute")
async def create_labeling_job(request: Request):
    """[已下线] 数据标注任务创建入口已收敛：任务发布唯一入口为 POST /api/tasks。"""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": {
                "code": "ENDPOINT_RETIRED",
                "message": "任务发布唯一入口为 POST /api/tasks（软件工程任务市场：自主竞价 + 3 专家评审 + 华币/NAU 奖励）",
            }
        },
    )


@router.get("/jobs/{job_id}", response_model=LabelingJobResponse)
@limiter.limit("60/minute")
async def get_labeling_job(request: Request, job_id: str):
    """
    Get the status and results of a labeling job.

    **Rate Limit**: 60 requests per minute

    **Path Parameters**:
    - `job_id`: The job identifier returned from create

    **Returns**: Job details including status, results, and quality report.
    """
    job = _labeling_jobs.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "JOB_NOT_FOUND",
                    "message": f"Labeling job '{job_id}' not found",
                }
            },
        )
    return _job_to_response(job)


@router.get("/jobs", response_model=LabelingJobListResponse)
@limiter.limit("30/minute")
async def list_labeling_jobs(
    request: Request,
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    labeling_type: Optional[LabelingType] = Query(
        None, description="Filter by labeling type"
    ),
    job_status: Optional[JobStatus] = Query(
        None, alias="status", description="Filter by status"
    ),
):
    """
    List labeling jobs with pagination and optional filters.

    **Rate Limit**: 30 requests per minute

    **Query Parameters**:
    - `page`: Page number (default 1)
    - `limit`: Items per page (default 20, max 100)
    - `labeling_type`: Filter by labeling type
    - `status`: Filter by job status

    **Returns**: Paginated list of labeling jobs.
    """
    jobs = list(_labeling_jobs.values())

    if labeling_type is not None:
        jobs = [j for j in jobs if j["labeling_type"] == labeling_type.value]
    if job_status is not None:
        jobs = [j for j in jobs if j["status"] == job_status.value]

    jobs.sort(key=lambda j: j["created_at"], reverse=True)

    total = len(jobs)
    start = (page - 1) * limit
    page_jobs = jobs[start:start + limit]

    return LabelingJobListResponse(
        jobs=[_job_to_response(j) for j in page_jobs],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/jobs/{job_id}/export")
@limiter.limit("30/minute")
async def export_labeling_results(
    request: Request,
    job_id: str,
    fmt: str = Query("json", alias="format", description="Export format: json or csv"),
):
    """
    Export labeling results as CSV or JSON.

    **Rate Limit**: 30 requests per minute

    **Query Parameters**:
    - `format`: `csv` or `json` (default `json`)

    **CSV columns**: id, text, label, confidence, consensus_votes, agreement_rate
    **JSON**: Full results with votes and quality metrics.
    """
    job = _labeling_jobs.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "JOB_NOT_FOUND",
                    "message": f"Labeling job '{job_id}' not found",
                }
            },
        )

    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "JOB_NOT_COMPLETED",
                    "message": f"Job is in '{job['status']}' status. Export requires completed status.",
                    "details": {"current_status": job["status"]},
                }
            },
        )

    if fmt == "csv":
        return _export_csv(job)
    else:
        return _export_json(job)


@router.delete("/jobs/{job_id}")
@limiter.limit("10/minute")
async def delete_labeling_job(request: Request, job_id: str):
    """
    Cancel or delete a labeling job.

    Only pending jobs can be cancelled. Completed/failed jobs are deleted.

    **Rate Limit**: 10 requests per minute
    """
    job = _labeling_jobs.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "JOB_NOT_FOUND",
                    "message": f"Labeling job '{job_id}' not found",
                }
            },
        )

    if job["status"] == JobStatus.PROCESSING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "JOB_IN_PROGRESS",
                    "message": "Cannot delete a job that is currently processing",
                    "details": {"current_status": job["status"]},
                }
            },
        )

    del _labeling_jobs[job_id]
    logger.info("Labeling job deleted: %s", job_id)

    return {
        "success": True,
        "data": {"job_id": job_id, "deleted": True},
        "error": None,
    }


# ---------------------------------------------------------------------------
# Price quote
# ---------------------------------------------------------------------------

@router.get("/quote")
@limiter.limit("60/minute")
async def get_price_quote(
    request: Request,
    labeling_type: str = Query(
        ..., alias="type", description="Labeling type (e.g. object_detection)"
    ),
    items: int = Query(..., ge=1, description="Number of items to label"),
    agents: int = Query(3, ge=1, le=10, description="Number of consensus agents"),
):
    """
    Get a price quote for a labeling job.

    **Rate Limit**: 60 requests per minute

    **Query Parameters**:
    - `type`: Labeling type
    - `items`: Number of items
    - `agents`: Number of agents (default 3)

    **Returns**: Price quote with cost breakdown.
    """
    from services.labeling_pricing import LabelingPricingService

    pricing = LabelingPricingService()
    quote = pricing.get_price_quote(labeling_type, items, agents)
    return {"success": True, "data": quote, "error": None}


@router.get("/prices")
@limiter.limit("30/minute")
async def list_labeling_prices(request: Request):
    """
    List all available labeling types and their per-item prices.

    **Rate Limit**: 30 requests per minute
    """
    from services.labeling_pricing import LabelingPricingService

    pricing = LabelingPricingService()
    return {
        "success": True,
        "data": {
            "prices": pricing.list_prices(),
            "currency": "RMB",
            "note": "Per-item price before agent multiplier. Total = price * items * agents.",
        },
        "error": None,
    }


# ---------------------------------------------------------------------------
# CSV batch upload
# ---------------------------------------------------------------------------

@router.post("/jobs/upload")
@limiter.limit("5/minute")
async def create_labeling_job_from_csv(request: Request):
    """[已下线] CSV 批量标注创建入口已收敛：任务发布唯一入口为 POST /api/tasks。"""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": {
                "code": "ENDPOINT_RETIRED",
                "message": "任务发布唯一入口为 POST /api/tasks（软件工程任务市场：自主竞价 + 3 专家评审 + 华币/NAU 奖励）",
            }
        },
    )


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def _export_csv(job: dict) -> PlainTextResponse:
    """Build CSV export from job results."""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "text", "label", "confidence", "consensus_votes", "agreement_rate"])

    for item in (job.get("results") or []):
        writer.writerow([
            item.get("id", ""),
            item.get("text", "")[:500],
            item.get("label", ""),
            f"{item.get('confidence', 0):.3f}",
            item.get("consensus_votes", ""),
            f"{item.get('agreement_rate', 0):.3f}",
        ])

    return PlainTextResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={job['job_id']}.csv"},
    )


def _export_json(job: dict):
    """Build JSON export with full quality metrics."""
    return {
        "job_id": job["job_id"],
        "name": job["name"],
        "labeling_type": job["labeling_type"],
        "status": job["status"],
        "total_items": job["total_items"],
        "processing_time_s": job.get("processing_time_s"),
        "items": job.get("results") or [],
        "quality_report": job.get("quality_report") or {},
    }


# ---------------------------------------------------------------------------
# Background dispatch
# ---------------------------------------------------------------------------

async def _dispatch_labeling_job(job_id: str) -> None:
    """
    Background coroutine that runs a labeling job through the
    BatchProcessor with Raid consensus and stores results.

    Guarded by _task_semaphore to allow at most 3 concurrent dispatches,
    preventing resource exhaustion while enabling parallel execution.
    """
    async with _task_semaphore:
        await _dispatch_labeling_job_inner(job_id)


async def _dispatch_labeling_job_inner(job_id: str) -> None:
    """Inner dispatch logic, called under the semaphore."""
    job = _labeling_jobs.get(job_id)
    if not job:
        logger.error("Dispatch: labeling job %s not found", job_id)
        return

    if job["status"] != JobStatus.PENDING.value:
        logger.warning("Dispatch: job %s not pending (status=%s)", job_id, job["status"])
        return

    job["status"] = JobStatus.PROCESSING.value
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("Dispatching labeling job %s to BatchProcessor", job_id)

    try:
        from labeling.batch_processor import BatchProcessor

        processor = BatchProcessor()

        # Convert raw text items to the format BatchProcessor expects
        items = [
            {"id": i + 1, "text": text}
            for i, text in enumerate(job["items_raw"])
        ]

        batch_job = await processor.process_batch(
            items=items,
            labeling_type=_map_labeling_type(LabelingType(job["labeling_type"])),
            labels=job.get("labels") or [],
            description=job.get("instructions") or f"Label items as {job['labeling_type']}",
            num_agents=job["num_agents"],
            consensus_threshold=job["consensus_threshold"],
            job_id=job_id,
        )

        now = datetime.now(timezone.utc).isoformat()
        job["results"] = batch_job.results
        job["quality_report"] = batch_job.quality_report
        job["processing_time_s"] = batch_job.processing_time_s
        job["status"] = batch_job.status.value if hasattr(batch_job.status, "value") else batch_job.status
        job["updated_at"] = now

        logger.info(
            "Labeling job %s finished: status=%s items=%d time=%.2fs",
            job_id, job["status"], len(batch_job.results), batch_job.processing_time_s,
        )

    except Exception as exc:
        logger.error("Labeling job %s dispatch error: %s", job_id, exc, exc_info=True)
        job["status"] = JobStatus.FAILED.value
        job["quality_report"] = {"error": str(exc)}
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
