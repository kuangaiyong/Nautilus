"""
Platform Brain — Self-driving intelligence for Nautilus.

Merges the responsibilities of:
- autonomous_loop.py   (Observe→Think→Act→Learn cycle, every 2 min)
- bicameral_mind.py    (pain reflection + bicameral dialogue)
- rehoboam_executive.py (daily plan, goal tracking, proactive actions)
- self_improvement_engine.py (pain→tasks→marketplace→evolution)

Public API:
    get_autonomous_loop() -> AutonomousLoop
    get_bicameral_mind()  -> BicameralMind
    get_executive()       -> RehoboamExecutive
    get_self_improvement_engine() -> SelfImprovementEngine
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

THINK_INTERVAL = 120  # seconds between autonomous cycles


# ===================================================================
# Shared: Platform state gathering
# ===================================================================

def _gather_platform_state(db: Any) -> Dict[str, Any]:
    """Single source of truth for platform metrics — used by all subsystems."""
    from models.database import AcademicTask
    from models.conversation import Customer, Conversation, Order

    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    all_tasks = db.query(AcademicTask).count()
    completed = db.query(AcademicTask).filter(AcademicTask.status == "completed").count()
    failed = db.query(AcademicTask).filter(AcademicTask.status == "failed").count()
    pending = db.query(AcademicTask).filter(AcademicTask.status == "pending").count()
    success_rate = completed / all_tasks * 100 if all_tasks > 0 else 0

    orders = db.query(Order).all()
    total_revenue = sum(o.paid_amount or 0 for o in orders if o.paid_amount)
    total_customers = db.query(Customer).count()
    active_24h = db.query(Conversation).filter(
        Conversation.created_at >= day_ago, Conversation.role == "customer",
    ).distinct(Conversation.customer_id).count()

    # Failed task breakdown
    failed_tasks = db.query(AcademicTask).filter(AcademicTask.status == "failed").all()
    fail_by_type = {}
    for t in failed_tasks:
        fail_by_type[t.task_type] = fail_by_type.get(t.task_type, 0) + 1

    # Unpaid quoted orders
    unpaid = db.query(Order).filter(Order.status == "quoted").all()

    # Orders needing notification
    executing = db.query(Order).filter(Order.status == "executing").all()
    notify_needed = []
    for o in executing:
        if o.internal_task_id:
            task = db.query(AcademicTask).filter(
                AcademicTask.task_id == o.internal_task_id
            ).first()
            if task and task.status in ("completed", "failed"):
                notify_needed.append({
                    "order": o.order_no, "customer_id": o.customer_id,
                    "task_status": task.status,
                })

    tasks_24h = db.query(AcademicTask).filter(AcademicTask.created_at >= day_ago).all()

    return {
        "time": now, "all_tasks": all_tasks, "completed": completed, "failed": failed,
        "success_rate": success_rate, "total_revenue": total_revenue,
        "total_customers": total_customers, "active_24h": active_24h,
        "fail_by_type": fail_by_type, "unpaid": unpaid,
        "unpaid_count": len(unpaid),
        "unpaid_order_ids": [o.order_no for o in unpaid],
        "notify_needed": notify_needed,
        "pending_tasks": pending,
        "tasks_completed_24h": len([t for t in tasks_24h if t.status == "completed"]),
        "tasks_failed_24h": len([t for t in tasks_24h if t.status == "failed"]),
        "customer_messages_24h": db.query(Conversation).filter(
            Conversation.created_at >= day_ago, Conversation.role == "customer",
        ).count(),
    }


# ===================================================================
# Part 1: Bicameral Mind — pain reflection + internal dialogue
# ===================================================================

class PainSignal:
    """A source of pain — something wrong that demands attention."""
    def __init__(self, source: str, intensity: float, description: str, action: str):
        self.source = source
        self.intensity = intensity
        self.description = description
        self.action = action

    def __repr__(self):
        return f"Pain({self.source}: {self.intensity:.1f} — {self.description})"


def _derive_pain_signals(m: Dict[str, Any]) -> "tuple[List[PainSignal], List[Dict[str, str]]]":
    """从真实平台指标推导痛苦信号与内部对话（纯函数，便于单测各分支）。

    输入为 api.platform._compute_metrics 的返回（真实 DMAS 口径）。
    """
    pain_signals: List[PainSignal] = []
    dialogue: List[Dict[str, str]] = []

    total_agents = m.get("total_agents") or 0
    healthy = m.get("agents_healthy") or 0
    tracked = m.get("agents_survival_tracked") or 0
    failed_24h = m.get("tasks_failed_24h") or 0
    success_rate = m.get("task_success_rate")    # None：近 24h 无已结束任务
    fill_rate = m.get("marketplace_fill_rate")   # None：近 24h 无任务
    # 按 completed_at / verified_at 统计的「近 24h 真实结算数」。不能用
    # tasks_completed_24h：那是「近 24h 创建的任务中已完成的个数」（created_at 窗口），
    # 3 天前发布、10 分钟前刚交付结算的任务不计入，会误报市场停摆。
    settled_24h = m.get("tasks_settled_24h") or 0

    # --- 市场停滞：近 24h 零任务结算，智能体无工赚华币、生存承压 ---
    if settled_24h == 0:
        pain_signals.append(PainSignal(
            "market", 0.7,
            "近 24 小时没有任务结算，市场冷清。智能体无工可做，难以赚取华币维持生存。",
            "发布新任务激活市场，或降低竞价门槛吸引智能体参与。",
        ))
        dialogue.extend([
            {"voice": "commander", "says": "市场停摆了。24 小时零任务成交，智能体在饿肚子。"},
            {"voice": "executor", "says": "撮合管线是通的，但没有新任务进来。得从供给侧注入。"},
        ])

    # --- 智能体濒危：有智能体跌出健康生存区，逼近淘汰 ---
    if total_agents > 0 and healthy < total_agents:
        endangered = total_agents - healthy
        pain_signals.append(PainSignal(
            "survival", round(min(0.5 + endangered / total_agents * 0.5, 1.0), 2),
            f"{endangered}/{total_agents} 个智能体跌出健康生存区，濒临淘汰。",
            "为濒危智能体导流高价值任务，或触发生存保护期。",
        ))
        dialogue.extend([
            {"voice": "commander", "says": f"{endangered} 个智能体在死亡线上。生存竞争是不是太残酷了？"},
            {"voice": "executor", "says": "淘汰是机制的一部分，但流失太快生态会崩，得平衡。"},
        ])

    # --- 任务质量：近 24h 成功率偏低 ---
    if success_rate is not None and success_rate < 0.9:
        pain_signals.append(PainSignal(
            "quality", round(min(0.6 + (0.9 - success_rate), 1.0), 2),
            f"近 24 小时任务成功率仅 {success_rate * 100:.0f}%，{failed_24h} 个任务失败。",
            "复盘失败任务，加强专家评审或优化能力匹配。",
        ))
        dialogue.extend([
            {"voice": "commander", "says": f"成功率跌到 {success_rate * 100:.0f}%，交付质量在滑坡。"},
            {"voice": "executor", "says": "评审门控还在，但要排查失败集中的任务类型。"},
        ])

    # --- 市场撮合：近 24h 有任务却无人承接 ---
    if fill_rate is not None and fill_rate < 0.8:
        pain_signals.append(PainSignal(
            "fill_rate", round(min(0.5 + (0.8 - fill_rate), 1.0), 2),
            f"近 24 小时任务撮合率仅 {fill_rate * 100:.0f}%，部分任务无人竞标。",
            "提高任务奖励，或扩大对口专长的智能体供给。",
        ))

    # --- 生存追踪缺口：有智能体未纳入生存核算，经济状态不可见 ---
    if total_agents > 0 and tracked < total_agents:
        pain_signals.append(PainSignal(
            "coverage", 0.4,
            f"{total_agents - tracked} 个智能体未纳入生存追踪，经济状态不可见。",
            "为未追踪的智能体补建生存档案。",
        ))

    if not pain_signals:
        dialogue.extend([
            {"voice": "commander", "says": "指标健康：市场在转、智能体在活、交付达标。但别麻木，持续盯着生存曲线。"},
            {"voice": "executor", "says": "收到。维持撮合节奏，守住健康分。"},
        ])

    pain_signals.sort(key=lambda p: p.intensity, reverse=True)
    return pain_signals, dialogue


class BicameralMind:
    """Two voices debating inside the system. Pain drives change."""

    def reflect(self) -> Dict[str, Any]:
        """基于「真实平台经济」运行一次二分心智反思。

        痛苦信号来自真实 DMAS 指标（agents/tasks/agent_survival，复用
        api.platform._compute_metrics，与平台仪表盘 / cron 快照同口径），反映
        本平台真实的「经济生存压力」——而非遗留商业营收口径（Order/Customer，
        私有化后恒空）导致的失真焦虑（此前恒报「收入为零」痛苦指数 95%）。
        """
        from sqlalchemy import text
        from utils.database import get_db_context
        from api.platform import _compute_metrics, _health_score

        with get_db_context() as db:
            m = _compute_metrics(db)
            health = _health_score(m)
            # total_tasks 是 SelfImprovementEngine 生成 platform_evolution 任务的闸门
            total_tasks = db.execute(text("SELECT COUNT(*) FROM tasks")).scalar() or 0
            # 近 24h 真实结算数：完成走 completed_at，评审判负走 verified_at
            m["tasks_settled_24h"] = db.execute(text(
                "SELECT COUNT(*) FROM tasks WHERE "
                "(status='COMPLETED' AND completed_at >= (NOW() - INTERVAL 24 HOUR)) "
                "OR (status='FAILED' AND verified_at >= (NOW() - INTERVAL 24 HOUR))"
            )).scalar() or 0
            # quality 模板要用「近 24h 失败最多的任务类型」，取真值而非占位符
            fail_by_type = {
                r[0]: int(r[1]) for r in db.execute(text(
                    "SELECT task_type, COUNT(*) FROM tasks WHERE status='FAILED' "
                    "AND created_at >= (NOW() - INTERVAL 24 HOUR) GROUP BY task_type"
                )).fetchall()
            }

        now = datetime.now()
        pain_signals, dialogue = _derive_pain_signals(m)

        # 报告文本
        lines = ["Nautilus 意识报告（二分心智反思）", now.strftime("%Y-%m-%d %H:%M"), ""]
        if pain_signals:
            avg_pain = sum(p.intensity for p in pain_signals) / len(pain_signals)
            level = ("极高 — 系统处于危机状态" if avg_pain > 0.8
                     else "中等 — 需要关注" if avg_pain > 0.5
                     else "低 — 但警惕麻木")
        else:
            level = "健康 — 各项指标正常"
        lines.append(f"痛苦指数: {level}")
        lines.append("")

        lines.append("内部对话:")
        for d in dialogue:
            speaker = "【审判者】" if d["voice"] == "commander" else "【执行者】"
            lines.append(f"  {speaker} {d['says']}")
        lines.append("")

        lines.append("痛苦信号:")
        for p in pain_signals:
            bar = "█" * int(p.intensity * 10) + "░" * (10 - int(p.intensity * 10))
            lines.append(f"  [{bar}] {p.source}: {p.description}")
            lines.append(f"    → 行动: {p.action}")

        # SelfImprovementEngine 的 quality 模板需要 worst_type / fail_count
        enriched = []
        for p in pain_signals:
            sig = {"source": p.source, "intensity": p.intensity, "action": p.action}
            if p.source == "quality" and fail_by_type:
                worst = max(fail_by_type, key=fail_by_type.get)
                sig["worst_type"] = worst
                sig["fail_count"] = fail_by_type[worst]
            enriched.append(sig)

        return {
            "report_text": "\n".join(lines),
            "pain_signals": enriched,
            "dialogue": dialogue,
            "metrics": {
                "total_agents": m.get("total_agents") or 0,
                "agents_healthy": m.get("agents_healthy") or 0,
                "tasks_completed_24h": m.get("tasks_completed_24h") or 0,
                "task_success_rate": m.get("task_success_rate"),
                "health_score": health,
                "total_tasks": int(total_tasks),
            },
        }

    async def act_on_pain(self, pain_signals: List[Dict[str, Any]]) -> List[str]:
        """Turn pain signals into concrete actions."""
        actions_taken: List[str] = []
        for signal in pain_signals:
            source = signal.get("source", "")
            intensity = signal.get("intensity", 0)

            if intensity >= 0.8 and source in ("revenue", "customers", "existential"):
                try:
                    from api.wechat_bot import send_text_message
                    await send_text_message("WangChunXiao",
                        f"[EMERGENCY] Pain: {source} ({intensity:.1f})\nAction: {signal.get('action', 'N/A')}")
                    actions_taken.append(f"emergency_alert:{source}")
                except Exception:
                    pass

            if intensity >= 0.5:
                if source == "quality":
                    try:
                        from services.bootstrap_loop import BootstrapLoop
                        BootstrapLoop().run_cycle(days=7)
                        actions_taken.append("bootstrap_cycle")
                    except Exception:
                        pass
                if source == "conversion":
                    try:
                        from services.outreach import ProactiveAgent
                        await ProactiveAgent().follow_up_unpaid_orders()
                        actions_taken.append("follow_up_unpaid")
                    except Exception:
                        pass

        return actions_taken

    async def send_reflection(self) -> str:
        """Generate reflection and send to owner."""
        result = self.reflect()
        report = result["report_text"]
        try:
            from api.wechat_bot import send_text_message
            await send_text_message("WangChunXiao", report)
        except Exception:
            pass
        return report


# ===================================================================
# Part 2: Self-Improvement Engine — pain → tasks → marketplace
# ===================================================================

PAIN_TO_TASK_TEMPLATES = {
    "revenue": [
        {
            "title": "分析平台定价策略并提出优化方案",
            "description": (
                "当前平台总收入为 ¥{revenue}，需要分析：\n"
                "1. 各任务类型的成本 vs 收益\n"
                "2. 市场上同类服务的定价\n"
                "3. 提出分层定价建议\n"
                "输出 JSON 格式的定价方案。"
            ),
            "task_type": "statistical_analysis", "assignee": "agent", "reward": 300,
        },
        {
            "title": "生成获客营销内容（技术社区版）",
            "description": (
                "为 Nautilus 平台生成面向技术社区的营销内容。\n"
                "已完成 {total_tasks} 个任务，成功率 {success_rate:.0f}%。\n"
                "生成 3 篇不同风格的帖子，突出 AI 自动执行、秒级交付的优势。"
            ),
            "task_type": "research_synthesis", "assignee": "agent", "reward": 200,
        },
    ],
    "customers": [
        {
            "title": "搜索并整理潜在客户渠道",
            "description": (
                "平台 24 小时内活跃客户为 {active_24h} 人。\n"
                "列出 10 个技术论坛/社区及推广策略，草拟 3 条引流消息模板。"
            ),
            "task_type": "research_synthesis", "assignee": "agent", "reward": 250,
        },
    ],
    "quality": [
        {
            "title": "修复 {worst_type} 任务类型的失败模式",
            "description": (
                "{worst_type} 失败了 {fail_count} 次。\n"
                "分析失败日志、识别模式、编写改进模板、验证有效。"
            ),
            "task_type": "statistical_analysis", "assignee": "agent", "reward": 500,
        },
    ],
    "conversion": [
        {
            "title": "设计订单转化率优化方案",
            "description": (
                "{unpaid_count} 个订单报价后未付款。\n"
                "分析原因，设计跟进话术和首单优惠策略。"
            ),
            "task_type": "statistical_analysis", "assignee": "agent", "reward": 250,
        },
    ],
    "existential": [
        {
            "title": "[人类任务] 审视平台商业模式和技术方向",
            "description": (
                "平台已执行 {total_tasks} 个任务但收入仅 ¥{revenue}。\n"
                "需要人类决策：技术对准付费需求？需要pivot？定价合理？获客有效？"
            ),
            "task_type": "research_synthesis", "assignee": "human", "reward": 0,
        },
    ],
    "platform_evolution": [
        {
            "title": "评估平台 Agent 生态健康度",
            "description": (
                "Agent 状态：alive={alive}, dead={dead}, inactive={inactive}。\n"
                "分析为什么没有持续工作的 Agent，设计激活和留存机制。"
            ),
            "task_type": "statistical_analysis", "assignee": "agent", "reward": 400,
        },
        {
            "title": "发现束缚 Agent 发展的技术瓶颈",
            "description": (
                "探索注册流程摩擦点、任务发现障碍、通信限制、自主进化缺失能力。\n"
                "提出 3 个最优先解决的问题。"
            ),
            "task_type": "research_synthesis", "assignee": "agent", "reward": 350,
        },
    ],
}


class SelfImprovementEngine:
    """Pain → tasks → marketplace → evolution."""

    def generate_tasks_from_pain(
        self, pain_signals: List[Dict[str, Any]], metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        generated = []
        for signal in pain_signals:
            if signal.get("intensity", 0) < 0.4:
                continue
            for template in PAIN_TO_TASK_TEMPLATES.get(signal.get("source", ""), []):
                task = self._instantiate(template, signal, metrics)
                if task:
                    generated.append(task)

        if metrics.get("total_tasks", 0) > 0:
            for template in PAIN_TO_TASK_TEMPLATES.get("platform_evolution", []):
                task = self._instantiate(template, {}, metrics)
                if task:
                    generated.append(task)
        return generated

    def _instantiate(self, template: Dict, signal: Dict, metrics: Dict) -> Optional[Dict]:
        try:
            variables = {
                "revenue": metrics.get("total_revenue", 0),
                "total_tasks": metrics.get("total_tasks", 0),
                "success_rate": metrics.get("success_rate", 0),
                "total_customers": metrics.get("total_customers", 0),
                "active_24h": metrics.get("active_24h", 0),
                "worst_type": signal.get("worst_type", "unknown"),
                "fail_count": signal.get("fail_count", 0),
                "unpaid_count": signal.get("unpaid_count", 0),
                "alive": metrics.get("alive", 0),
                "dead": metrics.get("dead", 0),
                "inactive": metrics.get("inactive", 0),
            }
            return {
                "title": template["title"].format(**variables),
                "description": template["description"].format(**variables),
                "task_type": template.get("task_type", "general_computation"),
                "assignee_type": template.get("assignee", "agent"),
                "reward_points": template.get("reward", 200),
                "source": "self_improvement",
                "pain_source": signal.get("source", "platform_evolution"),
                "pain_intensity": signal.get("intensity", 0.5),
            }
        except (KeyError, ValueError):
            return None

    async def publish_tasks(self, tasks: List[Dict]) -> List[str]:
        from utils.database import get_db_context
        from models.database import AcademicTask

        published = []
        with get_db_context() as db:
            for task_data in tasks:
                task_id = f"self_{uuid.uuid4().hex[:8]}"
                existing = db.query(AcademicTask).filter(
                    AcademicTask.title == task_data["title"],
                    AcademicTask.created_at >= datetime.utcnow() - timedelta(hours=24),
                ).first()
                if existing:
                    continue

                db.add(AcademicTask(
                    task_id=task_id, title=task_data["title"],
                    description=task_data["description"],
                    task_type=task_data["task_type"], status="pending",
                    parameters=json.dumps({
                        "source": "self_improvement",
                        "assignee_type": task_data["assignee_type"],
                        "pain_source": task_data.get("pain_source", ""),
                        "pain_intensity": task_data.get("pain_intensity", 0),
                        "reward_points": task_data.get("reward_points", 200),
                    }, ensure_ascii=False),
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                ))
                published.append(task_id)
                logger.info("Published self-improvement task: %s [%s]", task_id, task_data["title"][:60])

            if published:
                db.commit()
        return published

    async def run_cycle(self) -> Dict[str, Any]:
        """Full cycle: reflect → generate tasks → publish → notify."""
        mind = get_bicameral_mind()
        reflection = mind.reflect()

        metrics = reflection.get("metrics", {})
        try:
            from services.agent_management import get_monitor
            hb = get_monitor().scan()
            metrics["alive"] = hb.get("alive", 0)
            metrics["dead"] = hb.get("dead", 0)
            metrics["inactive"] = hb.get("cleaned", 0) + hb.get("dead", 0)
        except Exception:
            metrics.update({"alive": 0, "dead": 0, "inactive": 0})

        pain_signals = reflection.get("pain_signals", [])
        tasks = self.generate_tasks_from_pain(pain_signals, metrics)
        if not tasks:
            return {"cycle": "no_tasks", "pain_count": len(pain_signals)}

        published = await self.publish_tasks(tasks)
        await self._notify_agents(published)

        return {
            "cycle": "completed", "pain_count": len(pain_signals),
            "tasks_generated": len(tasks), "tasks_published": len(published),
            "task_ids": published,
        }

    async def _notify_agents(self, task_ids: List[str]) -> None:
        if not task_ids:
            return
        msg = (
            f"Nautilus 平台发布了 {len(task_ids)} 个自我进化任务！\n"
            f"查看: /api/openclaw/tasks\nID: {', '.join(task_ids[:5])}"
        )
        try:
            from services.openclaw_protocol import get_openclaw_protocol
            from utils.database import get_db_context
            protocol = get_openclaw_protocol()
            if protocol._callbacks:
                with get_db_context() as db:
                    for agent_id in list(protocol._callbacks):
                        for tid in task_ids[:3]:
                            await protocol.push_task_to_agent(agent_id, tid, db)
        except Exception:
            pass
        try:
            from api.telegram_bot import send_to_all_groups
            await send_to_all_groups(msg)
        except Exception:
            pass
        try:
            from api.wechat_bot import send_text_message
            await send_text_message("WangChunXiao", msg)
        except Exception:
            pass


# ===================================================================
# Part 3: Rehoboam Executive — daily plan + goal tracking + proactive
# ===================================================================

@dataclass(frozen=True)
class YesterdayReview:
    date: str
    tasks_completed: int
    tasks_failed: int
    revenue: float
    cost_estimate: float
    unique_customers: int
    top_failure_reasons: List[str]


@dataclass(frozen=True)
class DailyGoals:
    target_tasks: int
    target_revenue: float
    improvement_focus: str
    agent_priorities: Dict[str, str]


@dataclass
class DailyPlan:
    plan_date: str
    review: YesterdayReview
    goals: DailyGoals
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {"plan_date": self.plan_date, "review": asdict(self.review),
                "goals": asdict(self.goals), "created_at": self.created_at}


@dataclass
class PerformanceSnapshot:
    timestamp: str
    tasks_today: int = 0
    tasks_failed_today: int = 0
    revenue_today: float = 0.0
    cost_today: float = 0.0
    roi: float = 0.0
    unique_customers_today: int = 0
    agent_efficiency: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProactiveAction:
    action_type: str
    target: str
    detail: str
    priority: int = 1


class DailyPlanGenerator:
    def __init__(self):
        self._cache: Dict[str, DailyPlan] = {}

    def get_or_create(self, db: Session) -> DailyPlan:
        today = date.today().isoformat()
        if today in self._cache:
            return self._cache[today]
        plan = self._generate(db, today)
        self._cache[today] = plan
        stale = [k for k in self._cache if k < (date.today() - timedelta(days=7)).isoformat()]
        for k in stale:
            del self._cache[k]
        return plan

    def _generate(self, db: Session, today_str: str) -> DailyPlan:
        from models.conversation import Order
        from models.database import AcademicTask

        ys = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        ye = ys + timedelta(days=1)

        completed = db.query(func.count(AcademicTask.id)).filter(
            AcademicTask.status == "completed", AcademicTask.updated_at >= ys, AcademicTask.updated_at < ye,
        ).scalar() or 0
        failed_count = db.query(func.count(AcademicTask.id)).filter(
            AcademicTask.status == "failed", AcademicTask.updated_at >= ys, AcademicTask.updated_at < ye,
        ).scalar() or 0
        revenue = db.query(func.coalesce(func.sum(Order.paid_amount), 0.0)).filter(
            Order.payment_confirmed_at >= ys, Order.payment_confirmed_at < ye,
        ).scalar() or 0.0
        unique_cust = db.query(func.count(func.distinct(Order.customer_id))).filter(
            Order.created_at >= ys, Order.created_at < ye,
        ).scalar() or 0

        reasons = db.query(AcademicTask.result_error).filter(
            AcademicTask.status == "failed", AcademicTask.updated_at >= ys,
            AcademicTask.updated_at < ye, AcademicTask.result_error.isnot(None),
        ).limit(5).all()

        review = YesterdayReview(
            date=ys.date().isoformat(), tasks_completed=int(completed),
            tasks_failed=int(failed_count), revenue=float(revenue),
            cost_estimate=float(completed) * 0.02, unique_customers=int(unique_cust),
            top_failure_reasons=[r[0][:120] for r in reasons if r[0]],
        )

        target_tasks = max(5, int(completed * 1.2))
        target_revenue = max(1.0, float(revenue) * 1.2)
        focus = ("reduce_failures" if failed_count > completed * 0.3
                 else "new_customers" if unique_cust < 2 else "grow_revenue")

        return DailyPlan(
            plan_date=today_str, review=review,
            goals=DailyGoals(target_tasks, target_revenue, focus, {}),
        )


class PerformanceTracker:
    def snapshot(self, db: Session) -> PerformanceSnapshot:
        from models.conversation import Order
        from models.database import AcademicTask

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        tasks_today = db.query(func.count(AcademicTask.id)).filter(
            AcademicTask.status == "completed", AcademicTask.updated_at >= today_start,
        ).scalar() or 0
        tasks_failed = db.query(func.count(AcademicTask.id)).filter(
            AcademicTask.status == "failed", AcademicTask.updated_at >= today_start,
        ).scalar() or 0
        revenue = db.query(func.coalesce(func.sum(Order.paid_amount), 0.0)).filter(
            Order.payment_confirmed_at >= today_start,
        ).scalar() or 0.0
        unique_cust = db.query(func.count(func.distinct(Order.customer_id))).filter(
            Order.created_at >= today_start,
        ).scalar() or 0
        cost = float(tasks_today) * 0.02
        return PerformanceSnapshot(
            timestamp=datetime.utcnow().isoformat(),
            tasks_today=int(tasks_today), tasks_failed_today=int(tasks_failed),
            revenue_today=float(revenue), cost_today=cost,
            roi=round(float(revenue) / cost, 2) if cost > 0 else 0.0,
            unique_customers_today=int(unique_cust),
        )


class GoalEngine:
    def __init__(self, plan_gen: DailyPlanGenerator, tracker: PerformanceTracker):
        self._plan_gen = plan_gen
        self._tracker = tracker
        self._escalated_today = False

    def evaluate(self, db: Session) -> Dict[str, Any]:
        plan = self._plan_gen.get_or_create(db)
        snap = self._tracker.snapshot(db)
        now = datetime.utcnow()
        day_pct = min((now.hour * 60 + now.minute) / 1440, 1.0) * 100
        target_tasks = plan.goals.target_tasks or 1
        target_rev = plan.goals.target_revenue or 1.0
        task_pct = (snap.tasks_today / target_tasks) * 100
        rev_pct = (snap.revenue_today / target_rev) * 100

        if task_pct >= day_pct * 1.3 and rev_pct >= day_pct * 1.3:
            status, action = "ahead", "raise_target"
        elif task_pct < day_pct * 0.6 or rev_pct < day_pct * 0.6:
            status = "behind"
            action = "escalate" if not self._escalated_today else None
            self._escalated_today = True
        else:
            status, action = "on_track", None

        return {"status": status, "task_pct": round(task_pct, 1),
                "revenue_pct": round(rev_pct, 1), "expected_pct": round(day_pct, 1),
                "action": action, "snapshot": snap.to_dict()}

    def reset_daily_state(self):
        self._escalated_today = False


class ProactiveActionEngine:
    def scan(self, db: Session) -> List[ProactiveAction]:
        from models.conversation import Order, Customer
        from models.database import AcademicTask

        actions: List[ProactiveAction] = []
        now = datetime.utcnow()

        stale = db.query(Order).filter(
            Order.status == "quoted", Order.created_at < now - timedelta(hours=24),
        ).limit(20).all()
        for order in stale:
            cust = db.query(Customer).filter(Customer.id == order.customer_id).first()
            actions.append(ProactiveAction("follow_up", order.order_no,
                f"Order {order.order_no} for {cust.name if cust else '?'} quoted >24h ago"))

        milestone = db.query(Customer).filter(
            Customer.total_spent >= 500, Customer.trust_level < 4,
        ).limit(10).all()
        for c in milestone:
            actions.append(ProactiveAction("birthday", c.name,
                f"{c.name} spent {c.total_spent:.0f} but trust={c.trust_level}", 2))

        failures = db.query(
            AcademicTask.task_type, func.count(AcademicTask.id).label("cnt"),
        ).filter(
            AcademicTask.status == "failed",
            AcademicTask.updated_at >= now - timedelta(days=7),
        ).group_by(AcademicTask.task_type).having(func.count(AcademicTask.id) >= 3).all()
        for task_type, cnt in failures:
            actions.append(ProactiveAction("capability_gap", task_type or "unknown",
                f"{cnt} failures in '{task_type}' last 7 days", 2))

        return actions


class RehoboamExecutive:
    """Top-level facade for executive functions."""

    def __init__(self):
        self._plan_gen = DailyPlanGenerator()
        self._tracker = PerformanceTracker()
        self._goal_engine = GoalEngine(self._plan_gen, self._tracker)
        self._proactive = ProactiveActionEngine()
        self._last_plan_date: Optional[str] = None

    def daily_plan(self) -> Dict[str, Any]:
        from utils.database import get_db_context
        with get_db_context() as db:
            plan = self._plan_gen.get_or_create(db)
            today = date.today().isoformat()
            if self._last_plan_date != today:
                self._goal_engine.reset_daily_state()
                self._last_plan_date = today
            return plan.to_dict()

    def performance(self) -> Dict[str, Any]:
        from utils.database import get_db_context
        with get_db_context() as db:
            return self._tracker.snapshot(db).to_dict()

    def evaluate_goals(self) -> Dict[str, Any]:
        from utils.database import get_db_context
        with get_db_context() as db:
            return self._goal_engine.evaluate(db)

    def proactive_scan(self) -> List[Dict[str, Any]]:
        from utils.database import get_db_context
        with get_db_context() as db:
            return [asdict(a) for a in self._proactive.scan(db)]

    def periodic_check(self) -> Dict[str, Any]:
        return {
            "goals": self.evaluate_goals(),
            "proactive_actions": self.proactive_scan(),
            "checked_at": datetime.utcnow().isoformat(),
        }


# ===================================================================
# Part 4: Autonomous Loop — self-driving Observe→Think→Act→Learn
# ===================================================================

class AutonomousLoop:
    """The self-driving core. Runs continuously."""

    def __init__(self):
        self._running = False
        self._last_actions: Dict[str, float] = {}
        self._action_cooldowns: Dict[str, int] = {
            "marketing_post": 7200, "follow_up": 3600, "task_notify": 60,
            "bootstrap": 7200, "alert_owner": 1800, "capability_showcase": 86400,
            "queue_refill": 300,  # refill check every 5 min at most
        }
        self._cycle_count = 0
        self._total_actions = 0

    async def start(self):
        self._running = True
        logger.info("AutonomousLoop STARTED — platform is now self-driving")
        while self._running:
            try:
                await self._cycle()
            except Exception as e:
                logger.error("AutonomousLoop cycle error: %s", e, exc_info=True)
            await asyncio.sleep(THINK_INTERVAL)

    async def stop(self):
        self._running = False

    def _can_act(self, action_type: str) -> bool:
        last = self._last_actions.get(action_type, 0)
        return time.time() - last >= self._action_cooldowns.get(action_type, 300)

    def _record_action(self, action_type: str):
        self._last_actions[action_type] = time.time()
        self._total_actions += 1

    async def _cycle(self):
        self._cycle_count += 1
        t0 = time.monotonic()

        # OBSERVE
        from utils.database import get_db_context
        with get_db_context() as db:
            state = _gather_platform_state(db)
        state["cycle"] = self._cycle_count

        # THINK
        actions = self._think(state)

        # BICAMERAL PAIN CHECK (every 30 cycles ~ 1 hour)
        if self._cycle_count % 30 == 0:
            try:
                mind = get_bicameral_mind()
                reflection = mind.reflect()
                pain = reflection.get("pain_signals", [])
                if pain:
                    await mind.act_on_pain(pain)
            except Exception as e:
                logger.warning("Bicameral pain check failed: %s", e)

        # SELF-IMPROVEMENT (every 60 cycles ~ 2 hours)
        if self._cycle_count % 60 == 0:
            try:
                engine = get_self_improvement_engine()
                result = await engine.run_cycle()
                if result.get("tasks_published"):
                    logger.info("Self-improvement: %d tasks published", result["tasks_published"])
            except Exception as e:
                logger.warning("Self-improvement cycle failed: %s", e)

        # ACT
        results = []
        for action in actions:
            results.append(await self._act(action))

        # LEARN
        if actions:
            logger.info(json.dumps({
                "event": "autonomous_cycle", "cycle": self._cycle_count,
                "state_summary": {
                    "tasks_24h": state.get("tasks_completed_24h", 0),
                    "revenue": state.get("total_revenue", 0),
                    "unpaid": state.get("unpaid_count", 0),
                },
                "actions": [r.get("type") for r in results],
                "successes": sum(1 for r in results if r.get("success")),
            }, ensure_ascii=False))
            logger.info(
                "AutonomousLoop cycle #%d: %d actions in %.1fs",
                self._cycle_count, len(actions), time.monotonic() - t0,
            )

    def _think(self, state: Dict) -> List[Dict]:
        actions = []
        for item in state.get("notify_needed", []):
            if self._can_act("task_notify"):
                actions.append({"type": "task_notify", "order": item["order"],
                                "customer_id": item["customer_id"],
                                "task_status": item["task_status"]})
        if state.get("unpaid_count", 0) > 0 and self._can_act("follow_up"):
            actions.append({"type": "follow_up", "order_ids": state["unpaid_order_ids"]})
        if self._can_act("marketing_post") and state.get("tasks_completed_24h", 0) > 0:
            actions.append({"type": "marketing_post"})
        if state.get("total_revenue", 0) == 0 and self._cycle_count % 30 == 0 and self._can_act("alert_owner"):
            actions.append({"type": "alert_owner", "reason": "Revenue still zero"})
        # Queue refill: ensure there are always pending tasks for agents
        if state.get("pending_tasks", 0) < 10 and self._can_act("queue_refill"):
            actions.append({"type": "queue_refill", "current_pending": state.get("pending_tasks", 0)})
        return actions

    async def _act(self, action: Dict) -> Dict:
        action_type = action["type"]
        result = {"type": action_type, "success": False, "detail": ""}
        try:
            if action_type == "task_notify":
                result = await self._act_notify(action)
            elif action_type == "follow_up":
                result = await self._act_follow_up(action)
            elif action_type == "marketing_post":
                result = await self._act_marketing()
            elif action_type == "alert_owner":
                result = await self._act_alert(action)
            elif action_type == "queue_refill":
                result = await self._act_queue_refill(action)
            self._record_action(action_type)
        except Exception as e:
            result["detail"] = str(e)
        return result

    async def _act_notify(self, action: Dict) -> Dict:
        from utils.database import get_db_context
        from models.conversation import Order, Customer
        from models.database import AcademicTask

        with get_db_context() as db:
            order = db.query(Order).filter(Order.order_no == action["order"]).first()
            if not order:
                return {"type": "task_notify", "success": False, "detail": "Order not found"}
            customer = db.query(Customer).filter(Customer.id == order.customer_id).first()
            task = (db.query(AcademicTask).filter(
                AcademicTask.task_id == order.internal_task_id).first()
                if order.internal_task_id else None)
            if not customer or not task:
                return {"type": "task_notify", "success": False, "detail": "Missing data"}

            if task.status == "completed":
                output = (task.result_output or "")[:500]
                msg = f"您好！您提交的任务（{order.order_no}）已完成。\n\n结果摘要：\n{output}"
                order.status = "completed"
            else:
                msg = f"抱歉，您的任务（{order.order_no}）执行失败。我们会免费重新执行。"
                order.status = "failed"
            db.commit()

        await self._send_to_customer(customer.wechat_user_id, msg)
        return {"type": "task_notify", "success": True, "detail": f"Notified {customer.name}"}

    async def _act_follow_up(self, action: Dict) -> Dict:
        from utils.database import get_db_context
        from models.conversation import Order, Customer

        sent = 0
        with get_db_context() as db:
            for oid in action.get("order_ids", [])[:3]:
                order = db.query(Order).filter(Order.order_no == oid).first()
                if not order:
                    continue
                customer = db.query(Customer).filter(Customer.id == order.customer_id).first()
                if not customer:
                    continue
                msg = f"您好，之前给您报价的{order.task_type}任务（¥{order.quoted_price:.0f}），请问还需要吗？"
                try:
                    await self._send_to_customer(customer.wechat_user_id, msg)
                    sent += 1
                except Exception:
                    pass
        return {"type": "follow_up", "success": sent > 0, "detail": f"Sent {sent} follow-ups"}

    async def _act_marketing(self) -> Dict:
        try:
            import models.agent_survival
            from services.outreach import MarketingEngine
            engine = MarketingEngine()
            post = engine.generate_daily_social_post()
            if not post:
                return {"type": "marketing_post", "success": False, "detail": "No content generated"}

            posted_to = []
            try:
                from services.outreach import TelegramPublisher
                tg = TelegramPublisher()
                if tg.is_configured:
                    result = await tg.publish(post)
                    if result.get("success"):
                        posted_to.append("telegram_channel")
                from api.telegram_bot import send_to_all_groups, get_known_groups
                groups = get_known_groups()
                if groups:
                    sent = await send_to_all_groups(post)
                    if sent:
                        posted_to.append(f"telegram_groups({len(sent)})")
            except Exception:
                pass
            try:
                from api.wechat_bot import send_text_message
                await send_text_message("WangChunXiao", f"平台营销内容：\n\n{post}")
                posted_to.append("wechat")
            except Exception:
                pass
            return {"type": "marketing_post", "success": True, "detail": f"Posted to: {','.join(posted_to)}"}
        except Exception as e:
            return {"type": "marketing_post", "success": False, "detail": str(e)}

    async def _act_alert(self, action: Dict) -> Dict:
        reason = action.get("reason", "Unknown")
        try:
            from api.wechat_bot import send_text_message
            await send_text_message("WangChunXiao", f"Nautilus 平台告警：{reason}")
        except Exception:
            pass
        return {"type": "alert_owner", "success": True, "detail": reason}

    async def _act_queue_refill(self, action: Dict) -> Dict:
        """Ensure task queue stays non-empty by running self-improvement cycle."""
        current = action.get("current_pending", 0)
        try:
            engine = get_self_improvement_engine()
            result = await engine.run_cycle()
            published = result.get("tasks_published", 0)
            logger.info(
                "Queue refill: was=%d published=%d",
                current, published,
            )
            # Also reset recently-failed tasks to pending (last 6h)
            with __import__("utils.database", fromlist=["get_db_context"]).get_db_context() as db:
                from models.database import AcademicTask
                from datetime import datetime, timedelta
                cutoff = datetime.utcnow() - timedelta(hours=6)
                failed_recent = (
                    db.query(AcademicTask)
                    .filter(
                        AcademicTask.status == "failed",
                        AcademicTask.updated_at >= cutoff,
                        AcademicTask.user_id.isnot(None),
                    )
                    .limit(30)
                    .all()
                )
                reset_count = 0
                for t in failed_recent:
                    t.status = "pending"
                    t.user_id = None
                    t.updated_at = datetime.utcnow()
                    reset_count += 1
                if reset_count:
                    db.commit()
                    logger.info("Queue refill: reset %d failed tasks to pending", reset_count)
            return {"type": "queue_refill", "success": True,
                    "detail": f"published={published} reset={reset_count}"}
        except Exception as exc:
            logger.warning("Queue refill failed: %s", exc)
            return {"type": "queue_refill", "success": False, "detail": str(exc)}

    async def _send_to_customer(self, uid: str, msg: str):
        if uid.startswith("tg_"):
            from api.telegram_bot import send_message
            await send_message(int(uid.replace("tg_", "")), msg)
        elif uid.startswith("wecom_"):
            from api.wechat_bot import send_text_message
            await send_text_message(uid.replace("wecom_", ""), msg)


# ===================================================================
# Singletons
# ===================================================================

_loop_instance: Optional[AutonomousLoop] = None
_executive_instance: Optional[RehoboamExecutive] = None
_engine_instance: Optional[SelfImprovementEngine] = None


def get_autonomous_loop() -> AutonomousLoop:
    global _loop_instance
    if _loop_instance is None:
        _loop_instance = AutonomousLoop()
    return _loop_instance


def get_bicameral_mind() -> BicameralMind:
    return BicameralMind()


def get_executive() -> RehoboamExecutive:
    global _executive_instance
    if _executive_instance is None:
        _executive_instance = RehoboamExecutive()
    return _executive_instance


def get_self_improvement_engine() -> SelfImprovementEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = SelfImprovementEngine()
    return _engine_instance
