"""A2A Protocol: Coordinator splits AcademicTask into worker subtasks, aggregates results.

Parameters JSON keys: parent_task_id, subtask_index, coordinator_agent_id, a2a_role.
"""
from __future__ import annotations
import json, logging, uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from models.database import AcademicTask

logger = logging.getLogger(__name__)


def _parse_params(task: AcademicTask) -> dict:
    try:
        return json.loads(task.parameters) if task.parameters else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _dump_params(params: dict) -> str:
    return json.dumps(params, ensure_ascii=False)


def _new_task_id() -> str:
    return f"a2a_{uuid.uuid4().hex[:12]}"


async def _haiku_generate_description(
    parent_description: str,
    parent_task_type: str,
    subtask_index: int,
    num_workers: int,
) -> str:
    """Generate subtask description via Claude Haiku; falls back on failure."""
    try:
        from services.llm_gateway import get_anthropic_compatible_client
        client = get_anthropic_compatible_client()
        prompt = (
            f"Split a {parent_task_type} task into {num_workers} parallel subtasks.\n"
            f"Parent description:\n{parent_description}\n\n"
            f"Write a concise 2-4 sentence description for subtask "
            f"{subtask_index + 1}/{num_workers}. Return only the description."
        )
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.warning("Haiku description generation failed (%s); using fallback.", exc)
        return (
            f"[Subtask {subtask_index + 1}/{num_workers}] "
            f"Process partition {subtask_index + 1} of: {parent_description[:200]}"
        )


async def _haiku_execute(title: str, description: str, task_type: str) -> str:
    """Execute a worker subtask via Claude Haiku; raises on failure."""
    from services.llm_gateway import get_anthropic_compatible_client
    client = get_anthropic_compatible_client()
    prompt = (
        f"You are an AI worker executing a task.\n"
        f"Type: {task_type}\nTitle: {title}\n\n{description}\n\n"
        f"Execute this task and provide a detailed, accurate result."
    )
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


class A2ACoordinator:
    """Manages task decomposition, worker assignment, and result aggregation."""

    async def spawn_subtasks(
        self,
        task_id: str,
        num_workers: int,
        db: Session,
    ) -> List[str]:
        """
        Split *task_id* into *num_workers* subtasks.
        Returns a list of new subtask task_ids.
        """
        parent = db.query(AcademicTask).filter(AcademicTask.task_id == task_id).first()
        if parent is None:
            raise ValueError(f"Task '{task_id}' not found.")

        params = _parse_params(parent)
        if params.get("a2a_role") == "worker":
            raise ValueError(f"Task '{task_id}' is already a worker subtask.")

        # Guard against double-spawn using a simple substring check
        existing = (
            db.query(AcademicTask)
            .filter(AcademicTask.parameters.contains(f'"parent_task_id": "{task_id}"'))
            .count()
        )
        if existing > 0:
            raise ValueError(
                f"Task '{task_id}' already has {existing} subtask(s). "
                "Call aggregate_results to collect them."
            )

        coordinator_agent_id: Optional[int] = parent.user_id
        params.update({
            "a2a_role": "coordinator",
            "coordinator_agent_id": coordinator_agent_id,
            "num_workers": num_workers,
        })
        parent.parameters = _dump_params(params)
        parent.status = "in_progress"
        parent.updated_at = datetime.utcnow()

        subtask_ids: List[str] = []
        for idx in range(num_workers):
            description = await _haiku_generate_description(
                parent.description, parent.task_type, idx, num_workers
            )
            sub_params = {
                "a2a_role": "worker",
                "parent_task_id": task_id,
                "subtask_index": idx,
                "coordinator_agent_id": coordinator_agent_id,
            }
            subtask = AcademicTask(
                task_id=_new_task_id(),
                title=f"[A2A-{idx + 1}/{num_workers}] {parent.title}",
                description=description,
                task_type=parent.task_type,
                status="pending",
                parameters=_dump_params(sub_params),
                input_data=parent.input_data,
            )
            db.add(subtask)
            subtask_ids.append(subtask.task_id)
            logger.info("Spawned subtask %s (index=%d) for parent %s", subtask.task_id, idx, task_id)

        db.commit()
        logger.info("Coordinator: spawned %d subtasks for task '%s'.", num_workers, task_id)
        return subtask_ids

    async def aggregate_results(self, task_id: str, db: Session) -> Dict:
        """
        Aggregate all subtask results into the parent task.
        Returns status summary dict.
        """
        parent = db.query(AcademicTask).filter(AcademicTask.task_id == task_id).first()
        if parent is None:
            raise ValueError(f"Task '{task_id}' not found.")

        status_info = self.get_subtask_status(task_id, db)
        if status_info["pending"] > 0:
            return {"aggregated": False, "reason": "subtasks still pending", **status_info}

        successful = [r for r in status_info["results"] if r["status"] == "completed"]
        aggregated_text = "\n\n---\n\n".join(
            f"### Subtask {r['subtask_index'] + 1}\n{r['result']}"
            for r in sorted(successful, key=lambda x: x.get("subtask_index", 0))
        ) or "[All subtasks failed — no results to aggregate.]"

        parent.result_output = aggregated_text
        parent.status = "completed" if successful else "failed"
        parent.updated_at = datetime.utcnow()

        p = _parse_params(parent)
        p.update({
            "aggregated_at": datetime.utcnow().isoformat(),
            "successful_subtasks": len(successful),
            "failed_subtasks": status_info["failed"],
        })
        parent.parameters = _dump_params(p)
        db.commit()

        logger.info(
            "Aggregated %d/%d subtasks for '%s'. Status: %s.",
            len(successful), status_info["total"], task_id, parent.status,
        )
        return {"aggregated": True, "parent_status": parent.status, **status_info}

    def get_subtask_status(self, parent_task_id: str, db: Session) -> Dict:
        """Return {total, completed, failed, pending, results} for *parent_task_id*."""
        subtasks = (
            db.query(AcademicTask)
            .filter(AcademicTask.parameters.contains(f'"parent_task_id": "{parent_task_id}"'))
            .all()
        )
        completed = failed = pending = 0
        results = []
        for sub in subtasks:
            params = _parse_params(sub)
            entry = {
                "task_id": sub.task_id,
                "subtask_index": params.get("subtask_index", -1),
                "status": sub.status,
                "result": sub.result_output or sub.result_error or "",
            }
            results.append(entry)
            if sub.status == "completed":
                completed += 1
            elif sub.status == "failed":
                failed += 1
            else:
                pending += 1

        return {
            "total": len(subtasks),
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "results": sorted(results, key=lambda x: x.get("subtask_index", 0)),
        }


class A2AWorker:
    """Claims and executes A2A worker subtasks."""

    async def claim_and_execute(self, worker_agent_id: int, db: Session) -> Dict:
        """
        Claim one pending A2A worker subtask and execute it with Haiku.
        Returns a dict describing what happened.
        """
        subtask = (
            db.query(AcademicTask)
            .filter(
                AcademicTask.status == "pending",
                AcademicTask.user_id.is_(None),
                AcademicTask.parameters.contains('"a2a_role": "worker"'),
            )
            .order_by(AcademicTask.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if subtask is None:
            return {"claimed": False, "reason": "no pending A2A worker subtasks available"}

        subtask.user_id = worker_agent_id
        subtask.status = "in_progress"
        subtask.updated_at = datetime.utcnow()
        db.commit()
        task_id = subtask.task_id
        logger.info("Worker agent %d claimed A2A subtask '%s'.", worker_agent_id, task_id)

        try:
            result = await _haiku_execute(subtask.title, subtask.description, subtask.task_type)
            subtask.result_output = result
            subtask.status = "completed"
            logger.info("A2A subtask '%s' completed by agent %d.", task_id, worker_agent_id)
        except Exception as exc:
            subtask.result_error = str(exc)
            subtask.status = "failed"
            logger.error("A2A subtask '%s' failed for agent %d: %s", task_id, worker_agent_id, exc)

        subtask.updated_at = datetime.utcnow()
        db.commit()
        return {
            "claimed": True,
            "task_id": task_id,
            "status": subtask.status,
            "result_preview": (subtask.result_output or "")[:200],
        }


_coordinator: Optional[A2ACoordinator] = None
_worker: Optional[A2AWorker] = None


def get_a2a_coordinator() -> A2ACoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = A2ACoordinator()
    return _coordinator


def get_a2a_worker() -> A2AWorker:
    global _worker
    if _worker is None:
        _worker = A2AWorker()
    return _worker
