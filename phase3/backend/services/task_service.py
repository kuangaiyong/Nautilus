"""
Task service with caching.

This module provides Task query operations with Redis caching
to improve performance for repeated queries.
"""
from sqlalchemy.orm import Session
from models.database import Task
from utils.cache import cached, invalidate_cache
from monitoring.metrics import task_queries
import logging
logger = logging.getLogger(__name__)


def get_tasks_cached(status: str = None, task_type: str = None, skip: int = 0, limit: int = 20, db: Session = None) -> dict:
    """
    Get tasks list with caching (2 minutes).

    NOTE: Session must NOT be included in cache key; use pool_id instead.

    Args:
        status: Filter by task status (optional)
        task_type: Filter by task type (optional)
        skip: Number of records to skip for pagination (offset, optional)
        limit: Maximum number of tasks to return
        db: Database session

    Returns:
        dict: List of tasks
    """
    # Build cache key without including the Session object
    cache_key = f"tasks:status={status}:type={task_type}:skip={skip}:limit={limit}"

    # Check cache first（SimpleCache 内部自动 Redis→内存 fallback，返回已反序列化对象）
    from utils.cache import get_cache
    cache = get_cache()
    try:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result
    except Exception as e:
        logger.warning(f"Cache lookup failed: {e}")

    # Cache miss or error; query database
    query = db.query(Task)

    if status:
        query = query.filter(Task.status == status)

    if task_type:
        query = query.filter(Task.task_type == task_type)

    tasks = query.order_by(Task.created_at.desc()).offset(skip).limit(limit).all()

    task_queries.labels(cached='miss').inc()

    result = {
        "tasks": [
            {
                "id": task.id,
                "task_id": task.task_id,
                "publisher": task.publisher,
                "description": task.description,
                "input_data": task.input_data,
                "expected_output": task.expected_output,
                "reward": task.reward,
                "task_type": task.task_type,
                "status": task.status,
                "agent": task.agent,
                "result": task.result,
                "timeout": task.timeout,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "accepted_at": task.accepted_at.isoformat() if task.accepted_at else None,
                "submitted_at": task.submitted_at.isoformat() if task.submitted_at else None,
                "verified_at": task.verified_at.isoformat() if task.verified_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "blockchain_tx_hash": task.blockchain_tx_hash,
                "blockchain_accept_tx": task.blockchain_accept_tx,
                "blockchain_submit_tx": task.blockchain_submit_tx,
                "blockchain_complete_tx": task.blockchain_complete_tx,
                "blockchain_status": task.blockchain_status,
                "gas_used": task.gas_used,
                "gas_cost": task.gas_cost,
                "gas_split": task.gas_split
            }
            for task in tasks
        ]
    }

    # Cache the result (TTL 120 seconds；SimpleCache 内部序列化 + Redis/内存)
    try:
        cache.set(cache_key, result, 120)
    except Exception as e:
        logger.warning(f"Failed to cache result: {e}")

    return result


def invalidate_tasks_cache():
    """
    Invalidate all task list caches.
    """
    invalidate_cache("tasks:*")
    logger.info("Invalidated tasks cache")
