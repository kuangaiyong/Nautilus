"""
Task Matcher - Intelligent task matching and auto-assignment.

Matches tasks to suitable agents based on:
- Agent specialties
- Agent reputation
- Agent availability
- Task requirements
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from models.database import Task, Agent, TaskStatus, TaskType
from agent_executor import submit_task_to_queue
from automation_metrics import (
    record_task_auto_assigned,
    record_task_assignment_failed
)

logger = logging.getLogger(__name__)

# 任务类型 → 能胜任该类型的技能标签（全部小写）。智能体注册的是技能标签
# （python/fastapi/code-generation…），而任务用的是类型枚举（CODE/DATA/…）。
# 撮合打分时必须用本表把两者对齐，否则“任务类型 in 技能标签集合”永远为 False，
# 专长分恒为 0，任何任务都达不到 min_score 而无法被自动分配。
TASK_TYPE_SKILLS = {
    "CODE": {"python", "javascript", "typescript", "go", "rust", "java", "c++",
             "code-generation", "coding", "development", "fastapi", "backend", "frontend",
             "engineering", "tool-use", "test-generation", "testing", "debugging",
             "代码", "开发", "编程", "测试"},
    "DATA": {"data", "data-analysis", "数据分析", "数据", "statistical_analysis",
             "pandas", "sql", "etl", "data_visualization", "数据可视化"},
    "COMPUTE": {"compute", "computation", "general_computation", "simulation", "仿真", "计算",
                "monte_carlo", "physics_simulation", "ode_simulation", "pde_simulation",
                "ml_training", "machine-learning"},
    "RESEARCH": {"research", "reasoning", "research_synthesis", "analysis", "研究", "推理"},
    "DESIGN": {"design", "ui", "ux", "设计"},
    "WRITING": {"writing", "technical-writing", "documentation", "写作", "文档"},
}


async def match_task_to_agents(
    task_id: int,
    db: Session,
    max_matches: int = 5
) -> List[Dict[str, Any]]:
    """
    Match task to suitable agents.

    Args:
        task_id: Task ID
        db: Database session
        max_matches: Maximum number of agents to return

    Returns:
        List of matched agents with scores
    """
    logger.info(f"Matching task {task_id} to agents...")

    # Get task
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        logger.error(f"Task {task_id} not found")
        return []

    # Get all available agents
    agents = db.query(Agent).filter(
        Agent.current_tasks < 3  # Has capacity
    ).all()

    if not agents:
        logger.warning("No available agents found")
        return []

    # Score each agent
    scored_agents = []
    for agent in agents:
        score = calculate_agent_score(task, agent)
        scored_agents.append({
            "agent_id": agent.agent_id,
            "agent": agent,
            "score": score,
            "reputation": agent.reputation,
            "current_tasks": agent.current_tasks,
            "completed_tasks": agent.completed_tasks,
            "specialties": agent.specialties
        })

    # Sort by score (descending)
    scored_agents.sort(key=lambda x: x["score"], reverse=True)

    # Return top matches
    top_matches = scored_agents[:max_matches]

    logger.info(f"Found {len(top_matches)} matching agents for task {task_id}")
    for match in top_matches:
        logger.info(f"  Agent {match['agent_id']}: score={match['score']:.2f}, "
                   f"reputation={match['reputation']}, "
                   f"current_tasks={match['current_tasks']}")

    return top_matches


def calculate_agent_score(task: Task, agent: Agent) -> float:
    """
    Calculate matching score between task and agent.

    Scoring factors:
    - Specialty match: 0-40 points
    - Reputation: 0-30 points
    - Availability: 0-20 points
    - Success rate: 0-10 points

    Args:
        task: Task object
        agent: Agent object

    Returns:
        Score (0-100)
    """
    score = 0.0

    # 1. Specialty match (0-40 points)
    # 智能体注册的是技能标签（python/fastapi…），任务用的是类型（CODE/DATA…）。
    # 直接比较二者永远不相交，故用 TASK_TYPE_SKILLS 把任务类型映射到相关技能再判断。
    if agent.specialties:
        agent_specialties = {s.strip().lower() for s in agent.specialties.split(",") if s.strip()}
        task_type = (task.task_type.value if hasattr(task.task_type, 'value') else str(task.task_type)).upper()
        relevant = TASK_TYPE_SKILLS.get(task_type, set())

        if task_type.lower() in agent_specialties or (agent_specialties & relevant):
            score += 40  # 显式标注该类型，或拥有相关技能 → 胜任
        elif "all" in agent_specialties:
            score += 20  # 通用智能体

    # 2. Reputation (0-30 points)
    # 智能体出生信誉为 100，约 200 视为高信誉；以 200 为满分基准归一化
    # （原先按 /1000 归一，使 100 信誉只得 3 分，叠加专长分缺失导致永远达不到门槛）。
    reputation_score = min(agent.reputation / 200.0 * 30, 30)
    score += reputation_score

    # 3. Availability (0-20 points)
    # Fewer current tasks = higher score
    availability_score = (3 - agent.current_tasks) / 3.0 * 20
    score += availability_score

    # 4. Success rate (0-10 points)
    total_tasks = agent.completed_tasks + agent.failed_tasks
    if total_tasks > 0:
        success_rate = agent.completed_tasks / total_tasks
        score += success_rate * 10

    return score


async def auto_assign_task(
    task_id: int,
    db: Session,
    min_score: float = 50.0
) -> Optional[int]:
    """
    Automatically assign task to best matching agent.

    Args:
        task_id: Task ID
        db: Database session
        min_score: Minimum score required for assignment

    Returns:
        Agent ID if assigned, None otherwise
    """
    logger.info(f"Auto-assigning task {task_id}...")

    # Get task
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        logger.error(f"Task {task_id} not found")
        return None

    # Check if task is open
    if task.status != TaskStatus.OPEN:
        logger.warning(f"Task {task_id} is not open (status: {task.status})")
        return None

    # Match agents
    matches = await match_task_to_agents(task_id, db, max_matches=1)

    if not matches:
        logger.warning(f"No matching agents found for task {task_id}")
        record_task_assignment_failed("no_agents_available")
        return None

    best_match = matches[0]

    # Check minimum score
    if best_match["score"] < min_score:
        logger.warning(f"Best match score {best_match['score']:.2f} below minimum {min_score}")
        record_task_assignment_failed("score_too_low")
        return None

    agent = best_match["agent"]

    try:
        # Update task status
        task.status = TaskStatus.ACCEPTED
        task.agent = agent.owner
        task.accepted_at = datetime.now(timezone.utc)  # 与手动 accept 一致，供完成时计算任务时长
        db.commit()

        logger.info(f"✅ Task {task_id} auto-assigned to agent {agent.agent_id} "
                   f"(score: {best_match['score']:.2f})")

        # Record success metrics
        task_type_str = task.task_type.value if hasattr(task.task_type, 'value') else str(task.task_type)
        record_task_auto_assigned(task_type_str, best_match["score"])

        # Submit to execution queue
        queue_id = await submit_task_to_queue(task.id, agent.agent_id, db)
        logger.info(f"🚀 Task {task.id} submitted to execution queue: {queue_id}")

        # Update agent current tasks
        agent.current_tasks += 1
        db.commit()

        return agent.agent_id

    except Exception as e:
        logger.error(f"❌ Failed to auto-assign task {task_id}: {e}", exc_info=True)
        db.rollback()
        return None


async def check_and_assign_tasks(db: Session):
    """
    Check for unassigned tasks and auto-assign them.

    This function is called periodically by the scheduler.

    Args:
        db: Database session
    """
    logger.info("Checking for unassigned tasks...")

    try:
        # Get all open tasks
        open_tasks = db.query(Task).filter(
            Task.status == TaskStatus.OPEN
        ).all()

        if not open_tasks:
            logger.info("No open tasks found")
            return

        logger.info(f"Found {len(open_tasks)} open tasks")

        # Try to assign each task
        assigned_count = 0
        for task in open_tasks:
            agent_id = await auto_assign_task(task.id, db)
            if agent_id:
                assigned_count += 1

        logger.info(f"✅ Auto-assigned {assigned_count}/{len(open_tasks)} tasks")

    except Exception as e:
        logger.error(f"❌ Error in check_and_assign_tasks: {e}", exc_info=True)


async def get_agent_recommendations(
    task_id: int,
    db: Session,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Get agent recommendations for a task.

    This is used by the frontend to show recommended agents.

    Args:
        task_id: Task ID
        db: Database session
        limit: Maximum number of recommendations

    Returns:
        List of recommended agents with details
    """
    matches = await match_task_to_agents(task_id, db, max_matches=limit)

    recommendations = []
    for match in matches:
        agent = match["agent"]
        recommendations.append({
            "agent_id": agent.agent_id,
            "name": agent.name,
            "description": agent.description,
            "reputation": agent.reputation,
            "specialties": agent.specialties,
            "completed_tasks": agent.completed_tasks,
            "failed_tasks": agent.failed_tasks,
            "current_tasks": agent.current_tasks,
            "match_score": match["score"],
            "available": agent.current_tasks < 3
        })

    return recommendations
