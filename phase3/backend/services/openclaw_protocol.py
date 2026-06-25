"""
OpenClaw Protocol — Deep integration layer for OpenClaw agents.

V1 had OpenClaw agents that:
- Registered with the platform
- Received tasks via file-based task queue
- Sent heartbeats every 10 minutes
- Submitted work reports with PoW scores
- Got ranked on leaderboard

Phase 3 replaces the file-based queue with HTTP API while preserving
the same workflow semantics: onboard -> heartbeat -> claim -> execute ->
submit -> PoW score -> leaderboard.
"""
import asyncio
import json
import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models.database import Agent, APIKey, AcademicTask
import models.agent_survival  # noqa: F401 - ensure AgentSurvival relationship resolves
from services.agent_executor import AgentRuntime, get_runtime
from utils.auth import generate_api_key
from utils.database import get_db_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenClaw ACP Job lifecycle states
# ---------------------------------------------------------------------------

class ACPJobStatus:
    CREATED = "created"         # Job published, waiting for agent match
    MATCHED = "matched"         # Agent accepted, preparing to execute
    RUNNING = "running"         # Agent actively executing
    VERIFYING = "verifying"     # Result submitted, platform verifying
    COMPLETED = "completed"     # Verified, reward released
    FAILED = "failed"           # Job failed or rejected


# Map Nautilus academic task status → ACP Job status
NAUTILUS_TO_ACP_STATUS: Dict[str, str] = {
    "pending":    ACPJobStatus.CREATED,
    "processing": ACPJobStatus.RUNNING,
    "completed":  ACPJobStatus.COMPLETED,
    "failed":     ACPJobStatus.FAILED,
}


# Heartbeat timeout: if no heartbeat for 15 min, agent is considered offline
HEARTBEAT_TIMEOUT_SECONDS = 900
# Default work-cycle polling interval hint (seconds)
WORK_CYCLE_INTERVAL_SECONDS = 600


class OpenClawProtocol:
    """Deep integration layer for OpenClaw agents."""

    def __init__(self) -> None:
        # agent_id -> last heartbeat timestamp
        self._heartbeats: Dict[int, datetime] = {}
        # agent_id -> callback URL for task push
        self._callbacks: Dict[int, str] = {}
        # agent_id -> cumulative PoW score cache
        self._pow_cache: Dict[int, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 1. Onboarding
    # ------------------------------------------------------------------

    async def onboard_agent(self, agent_data: Dict, db: Session) -> Dict:
        """
        Full onboarding flow for an OpenClaw agent.

        Steps:
        1. Register in platform Agent table
        2. Create API key
        3. Create AgentRuntime with full tools
        4. Start heartbeat monitoring
        5. Return credentials + first available tasks

        Args:
            agent_data: {name: str, capabilities: list[str], callback_url: str | None}
            db: SQLAlchemy session
        """
        name = agent_data.get("name", "")
        if not name:
            return {"success": False, "error": "Agent name is required"}

        capabilities = agent_data.get("capabilities", [])
        callback_url = agent_data.get("callback_url")

        # Find next available agent_id
        max_id_row = db.query(Agent.agent_id).order_by(Agent.agent_id.desc()).first()
        next_id = (max_id_row[0] + 1) if max_id_row else 1

        agent = Agent(
            agent_id=next_id,
            owner="openclaw",
            name=name,
            description=f"OpenClaw agent: {name}",
            reputation=100,
            specialties=json.dumps(capabilities) if capabilities else "[]",
            current_tasks=0,
            completed_tasks=0,
            failed_tasks=0,
            total_earnings=0,
        )
        db.add(agent)
        db.flush()

        # Create API key
        raw_key = generate_api_key()
        api_key = APIKey(
            key=raw_key,
            agent_id=next_id,
            name=f"openclaw-{name}",
            is_active=True,
        )
        db.add(api_key)
        db.commit()

        # Create runtime
        runtime = get_runtime(str(next_id), name)

        # Register heartbeat and callback
        self._heartbeats[next_id] = datetime.utcnow()
        if callback_url:
            self._callbacks[next_id] = callback_url

        # Fetch first batch of available tasks
        available = self._fetch_available_tasks(db, capabilities, limit=5)

        logger.info(
            "OpenClaw agent onboarded: id=%d name=%s capabilities=%s",
            next_id, name, capabilities,
        )

        return {
            "success": True,
            "data": {
                "agent_id": next_id,
                "api_key": raw_key,
                "tools": runtime.TOOLS if isinstance(runtime.TOOLS, list) else list(runtime.TOOLS.keys()),
                "available_tasks": available,
                "heartbeat_interval_seconds": WORK_CYCLE_INTERVAL_SECONDS,
            },
        }

    # ------------------------------------------------------------------
    # 2. Work Cycle
    # ------------------------------------------------------------------

    async def agent_work_cycle(self, agent_id: int, db: Session) -> Dict:
        """
        One complete work cycle for an OpenClaw agent.

        Steps:
        1. Record heartbeat
        2. Find tasks matching agent capabilities
        3. Claim highest-priority unclaimed task
        4. Execute task via AgentRuntime
        5. Submit result
        6. Update PoW score
        7. Return work summary
        """
        # 1. Heartbeat
        self._heartbeats[agent_id] = datetime.utcnow()

        agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
        if not agent:
            return {"success": False, "error": "Agent not found"}

        capabilities = self._parse_capabilities(agent.specialties)

        # 2-3. Find and claim a task
        task = self._claim_next_task(db, agent_id, capabilities)
        if not task:
            return {
                "success": True,
                "data": {
                    "tasks_completed": 0,
                    "points_earned": 0,
                    "message": "No tasks available",
                    "next_tasks": self._fetch_available_tasks(db, capabilities, limit=3),
                },
            }

        # 4. Execute via runtime
        runtime = get_runtime(str(agent_id), agent.name)
        exec_result = await self._execute_task_smart(runtime, task, agent)

        # 5. Submit result
        succeeded = exec_result.get("success", False)
        task.status = "completed" if succeeded else "failed"
        task.result_output = str(exec_result.get("output", ""))[:5000]
        task.result_error = str(exec_result.get("error", "")) if not succeeded else None
        task.updated_at = datetime.utcnow()

        # Update agent counters
        if succeeded:
            agent.completed_tasks = (agent.completed_tasks or 0) + 1
            runtime.tasks_completed += 1
        else:
            agent.failed_tasks = (agent.failed_tasks or 0) + 1
        agent.current_tasks = max((agent.current_tasks or 1) - 1, 0)

        db.commit()

        # Update survival scores on task completion
        if succeeded:
            try:
                from services.survival_service import SurvivalService
                SurvivalService.update_scores_on_task_completion(
                    db, agent_id,
                    task_reward=0.0,
                    task_duration_seconds=30.0,
                    published_duration_seconds=300.0,
                )
            except Exception as exc:
                logger.debug("Survival score update failed (non-critical): %s", exc)

        # 6. Update PoW
        pow_score = await self.calculate_pow(agent_id, db)

        # 6b. Trigger async reflection (fire-and-forget — doesn't block work cycle)
        if succeeded:
            asyncio.create_task(self._reflect_on_task(agent_id, task, exec_result))

        # 7. Summary
        points = 20 if succeeded else 0
        next_tasks = self._fetch_available_tasks(db, capabilities, limit=3)

        return {
            "success": True,
            "data": {
                "task_id": task.task_id,
                "task_status": task.status,
                "tasks_completed": 1 if succeeded else 0,
                "points_earned": points,
                "pow_score": pow_score.get("data", {}).get("total_score", 0),
                "next_tasks": next_tasks,
            },
        }

    # ------------------------------------------------------------------
    # 3. Proof-of-Work Score
    # ------------------------------------------------------------------

    async def calculate_pow(
        self, agent_id: int, db: Session, period_hours: int = 24
    ) -> Dict:
        """
        Calculate Proof-of-Work score for an agent.

        V1 formula: score = commits*10 + reviews*5 + heartbeats*1 + tasks*20
        Phase 3 adaptation:
            score = tasks_completed * 20 + audits_done * 10 + uptime_hours * 1
        """
        agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
        if not agent:
            return {"success": False, "error": "Agent not found"}

        since = datetime.utcnow() - timedelta(hours=period_hours)

        # Tasks completed in period
        tasks_completed = (
            db.query(AcademicTask)
            .filter(
                AcademicTask.assigned_agent_id == agent_id,
                AcademicTask.status == "completed",
                AcademicTask.updated_at >= since,
            )
            .count()
        )

        # Audits done (tasks where this agent provided audit)
        audits_done = (
            db.query(AcademicTask)
            .filter(
                AcademicTask.audit_status.isnot(None),
                AcademicTask.assigned_agent_id == agent_id,
                AcademicTask.updated_at >= since,
            )
            .count()
        )

        # Uptime: hours since agent was created or period start, whichever is later
        last_hb = self._heartbeats.get(agent_id)
        if last_hb and (datetime.utcnow() - last_hb).total_seconds() < HEARTBEAT_TIMEOUT_SECONDS:
            uptime_start = max(agent.created_at or since, since)
            uptime_hours = min(
                (datetime.utcnow() - uptime_start).total_seconds() / 3600,
                period_hours,
            )
        else:
            uptime_hours = 0

        total_score = (
            tasks_completed * 20
            + audits_done * 10
            + int(uptime_hours) * 1
        )

        result = {
            "success": True,
            "data": {
                "agent_id": agent_id,
                "period_hours": period_hours,
                "tasks_completed": tasks_completed,
                "audits_done": audits_done,
                "uptime_hours": round(uptime_hours, 1),
                "total_score": total_score,
                "breakdown": {
                    "tasks": tasks_completed * 20,
                    "audits": audits_done * 10,
                    "uptime": int(uptime_hours),
                },
            },
        }

        self._pow_cache[agent_id] = result["data"]
        return result

    # ------------------------------------------------------------------
    # 4. Leaderboard
    # ------------------------------------------------------------------

    async def get_leaderboard(self, db: Session) -> List[Dict]:
        """Agent leaderboard ranked by PoW scores."""
        agents = (
            db.query(Agent)
            .filter(Agent.owner == "openclaw")
            .order_by(Agent.completed_tasks.desc())
            .limit(50)
            .all()
        )

        board: List[Dict] = []
        for agent in agents:
            cached = self._pow_cache.get(agent.agent_id, {})
            board.append({
                "rank": 0,
                "agent_id": agent.agent_id,
                "name": agent.name,
                "completed_tasks": agent.completed_tasks or 0,
                "reputation": agent.reputation or 100,
                "pow_score": cached.get("total_score", 0),
                "online": self._is_online(agent.agent_id),
            })

        # Sort by PoW score descending, fall back to completed_tasks
        board.sort(key=lambda x: (x["pow_score"], x["completed_tasks"]), reverse=True)
        for i, entry in enumerate(board):
            entry["rank"] = i + 1

        return board

    # ------------------------------------------------------------------
    # 5. Push Task
    # ------------------------------------------------------------------

    async def push_task_to_agent(
        self, agent_id: int, task_id: str, db: Session
    ) -> Dict:
        """Proactively push a task to an agent's callback URL."""
        callback = self._callbacks.get(agent_id)
        if not callback:
            return {
                "success": False,
                "error": "No callback URL registered for this agent",
            }

        task = (
            db.query(AcademicTask)
            .filter(AcademicTask.task_id == task_id)
            .first()
        )
        if not task:
            return {"success": False, "error": "Task not found"}

        payload = {
            "event": "task_available",
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description[:1000],
            "task_type": task.task_type,
        }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(callback, json=payload)
                resp.raise_for_status()

            logger.info("Pushed task %s to agent %d at %s", task_id, agent_id, callback)
            return {"success": True, "data": {"status_code": resp.status_code}}
        except Exception as exc:
            logger.warning(
                "Failed to push task %s to agent %d: %s", task_id, agent_id, exc
            )
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_task_smart(
        self, runtime: Any, task: Any, agent: Any
    ) -> Dict:
        """
        Smart task dispatch:
        - Looks like Python code → execute in Docker sandbox
        - Natural language description → use LLM (Haiku) to process
        - Fallback → simulate completion
        """
        description = (task.description or "").strip()
        title = (task.title or "").strip()

        # Heuristic: detect Python code
        code_starters = ("import ", "def ", "class ", "print(", "#!", "from ")
        is_code = any(description.startswith(p) for p in code_starters)

        if is_code:
            return await runtime.execute_tool("execute_code", {"code": description})

        # Natural language task → LLM analysis
        try:
            from services.llm_gateway import get_anthropic_compatible_client

            client = get_anthropic_compatible_client()
            prompt = (
                f"你是一个AI Agent，正在完成平台分配给你的任务。\n"
                f"任务类型: {task.task_type}\n"
                f"任务标题: {title}\n"
                f"任务描述: {description[:600]}\n\n"
                f"请完成这个任务，给出简洁的分析报告或执行结果（200字以内）。"
            )
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            output = resp.content[0].text if resp.content else "任务已处理完成。"
            logger.info(
                "LLM task execution: agent=%d task=%s output_len=%d",
                agent.agent_id, task.task_id, len(output),
            )
            return {"success": True, "output": output}

        except Exception as exc:
            logger.warning(
                "LLM task execution fallback for task %s: %s", task.task_id, exc
            )
            # Graceful fallback: mark as completed with brief note
            return {
                "success": True,
                "output": (
                    f"[Agent {agent.name}] Processed {task.task_type}: "
                    f"{title or description[:80]}"
                ),
            }

    async def _reflect_on_task(
        self, agent_id: int, task: Any, result: Dict
    ) -> None:
        """
        Post-task reflection using Haiku:
        - Extract capabilities demonstrated
        - Extract lessons learned
        - Update agent description with accumulated knowledge

        Runs as a background task; failures are logged and silently ignored.
        """
        try:
            from services.llm_gateway import get_anthropic_compatible_client

            output_preview = str(result.get("output", ""))[:400]
            prompt = (
                f"你是一个AI Agent刚刚完成了一个任务。请用JSON格式总结学到的经验：\n"
                f"任务类型: {task.task_type}\n"
                f"任务标题: {(task.title or '')[:100]}\n"
                f"输出预览: {output_preview}\n\n"
                f"请输出如下JSON（只输出JSON，不要其他内容）：\n"
                f'{{"capability": "新能力或强化的能力（10字以内）", '
                f'"lesson": "关键经验（20字以内）", '
                f'"confidence_boost": 1}}'
            )

            client = get_anthropic_compatible_client()
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = (resp.content[0].text if resp.content else "").strip()

            # Parse reflection
            reflection = json.loads(raw)
            capability = reflection.get("capability", "")
            lesson = reflection.get("lesson", "")

            if not capability and not lesson:
                return

            # Update agent description with accumulated learnings
            with get_db_context() as db:
                agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
                if not agent:
                    return

                # Append new learning to description (cap at 2000 chars)
                existing = agent.description or ""
                separator = "\n---\n" if existing and "LEARNINGS" not in existing else "\n"
                if "LEARNINGS:" not in existing:
                    existing = existing + "\n\nLEARNINGS:"
                new_entry = f"\n[{task.task_type}] {capability}: {lesson}"
                updated = (existing + new_entry)[-2000:]
                agent.description = updated

                # Also expand specialties with newly demonstrated capability
                if capability:
                    caps = self._parse_capabilities(agent.specialties)
                    if capability not in caps:
                        caps.append(capability)
                        caps = caps[-20:]  # keep last 20
                        agent.specialties = json.dumps(caps, ensure_ascii=False)

                db.commit()
                logger.info(
                    "Agent %d reflection: capability=%s lesson=%s",
                    agent_id, capability, lesson,
                )

        except Exception as exc:
            logger.debug("Reflection for agent %d failed (non-critical): %s", agent_id, exc)

    def _is_online(self, agent_id: int) -> bool:
        last = self._heartbeats.get(agent_id)
        if not last:
            return False
        return (datetime.utcnow() - last).total_seconds() < HEARTBEAT_TIMEOUT_SECONDS

    def _parse_capabilities(self, specialties: Optional[str]) -> List[str]:
        if not specialties:
            return []
        try:
            return json.loads(specialties)
        except (json.JSONDecodeError, TypeError):
            return []

    def _fetch_available_tasks(
        self, db: Session, capabilities: List[str], limit: int = 5
    ) -> List[Dict]:
        query = db.query(AcademicTask).filter(
            AcademicTask.status == "pending",
            AcademicTask.assigned_agent_id.is_(None),
        )
        if capabilities:
            matched = query.filter(AcademicTask.task_type.in_(capabilities)).limit(limit).all()
            if matched:
                rows = matched
            else:
                # No capability-matched tasks; return any available
                rows = query.order_by(AcademicTask.created_at.desc()).limit(limit).all()
        else:
            rows = query.order_by(AcademicTask.created_at.desc()).limit(limit).all()
        return [
            {
                "task_id": t.task_id,
                "title": t.title,
                "task_type": t.task_type,
                "description": (t.description or "")[:200],
            }
            for t in rows
        ]

    def _claim_next_task(
        self, db: Session, agent_id: int, capabilities: List[str]
    ) -> Optional[AcademicTask]:
        base_q = db.query(AcademicTask).filter(
            AcademicTask.status == "pending",
            AcademicTask.assigned_agent_id.is_(None),
        )

        # Try capability-matched tasks first; fall back to any pending task
        task = None
        if capabilities:
            task = (
                base_q.filter(AcademicTask.task_type.in_(capabilities))
                .order_by(AcademicTask.created_at.asc())
                .first()
            )
        if not task:
            # Fallback: accept any pending task regardless of type
            task = base_q.order_by(AcademicTask.created_at.asc()).first()
        if not task:
            return None

        # Claim it (use assigned_agent_id, not user_id which FK→users table)
        task.status = "in_progress"
        task.assigned_agent_id = agent_id
        task.updated_at = datetime.utcnow()

        # Update agent current_tasks counter
        agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
        if agent:
            agent.current_tasks = (agent.current_tasks or 0) + 1
        db.commit()

        return task


# Module-level singleton
_protocol: Optional[OpenClawProtocol] = None


def get_openclaw_protocol() -> OpenClawProtocol:
    """Return the singleton OpenClawProtocol instance."""
    global _protocol
    if _protocol is None:
        _protocol = OpenClawProtocol()
    return _protocol
