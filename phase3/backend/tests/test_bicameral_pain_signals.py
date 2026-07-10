"""
回归测试：二分心智痛苦信号基于「真实平台经济指标」（DMAS 口径）。

复现并锁定 bug：此前 BicameralMind.reflect() 的痛苦信号读遗留商业营收口径
（Order/Customer，私有化后恒空），恒报「收入为零 / 没有付费客户」，痛苦指数
恒 95%，与平台真实状态（智能体存活、任务完成、华币结算）完全矛盾。

修复后，痛苦信号纯函数 _derive_pain_signals(m) 以 _compute_metrics 的真实
DMAS 指标为输入，反映真实的经济生存压力；健康时不产生痛苦。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.platform_brain import _derive_pain_signals


def _sources(pain_signals):
    return {p.source for p in pain_signals}


def test_healthy_platform_has_no_pain():
    """全健康 + 近 24h 有成交 + 撮合满 → 无痛苦，且给出健康对话。"""
    m = {
        "total_agents": 24, "agents_healthy": 24, "agents_survival_tracked": 24,
        "tasks_completed_24h": 5, "tasks_failed_24h": 0,
        "tasks_settled_24h": 5,
        "task_success_rate": 1.0, "marketplace_fill_rate": 1.0,
    }
    pains, dialogue = _derive_pain_signals(m)
    assert pains == []
    assert len(dialogue) >= 1  # 健康也有「别麻木」的对话


def test_no_legacy_revenue_or_customer_pain():
    """无论指标如何，绝不再产生 revenue/customers/existential/conversion 等失真痛苦。"""
    m = {
        "total_agents": 24, "agents_healthy": 24, "agents_survival_tracked": 24,
        "tasks_completed_24h": 0, "tasks_failed_24h": 0,
        "tasks_settled_24h": 0,
        "task_success_rate": None, "marketplace_fill_rate": None,
    }
    pains, _ = _derive_pain_signals(m)
    assert _sources(pains).isdisjoint({"revenue", "customers", "existential", "conversion"})


def test_market_stall_pain_when_no_recent_tasks():
    """近 24h 零任务 → market 痛苦（智能体无工赚华币）。"""
    m = {
        "total_agents": 24, "agents_healthy": 24, "agents_survival_tracked": 24,
        "tasks_completed_24h": 0, "tasks_failed_24h": 0,
        "tasks_settled_24h": 0,
        "task_success_rate": None, "marketplace_fill_rate": None,
    }
    pains, _ = _derive_pain_signals(m)
    assert "market" in _sources(pains)


def test_no_market_pain_when_old_task_settled_recently():
    """回归：3 天前发布、10 分钟前刚结算的任务，不能误报「市场停摆」。

    tasks_completed_24h 走 created_at 窗口（此处为 0），但 tasks_settled_24h 走
    completed_at/verified_at（为 1）——market 痛苦必须以后者为准。
    """
    m = {
        "total_agents": 24, "agents_healthy": 24, "agents_survival_tracked": 24,
        "tasks_completed_24h": 0, "tasks_failed_24h": 0,
        "tasks_settled_24h": 1,
        "task_success_rate": None, "marketplace_fill_rate": None,
    }
    pains, _ = _derive_pain_signals(m)
    assert "market" not in _sources(pains)


def test_survival_pain_scales_with_endangered_count():
    """有智能体跌出健康区 → survival 痛苦，强度随濒危比例上升。"""
    m = {
        "total_agents": 10, "agents_healthy": 6, "agents_survival_tracked": 10,
        "tasks_completed_24h": 3, "tasks_failed_24h": 0,
        "tasks_settled_24h": 3,
        "task_success_rate": 1.0, "marketplace_fill_rate": 1.0,
    }
    pains, _ = _derive_pain_signals(m)
    survival = [p for p in pains if p.source == "survival"]
    assert len(survival) == 1
    # 4/10 濒危 → 0.5 + 0.4*0.5 = 0.7
    assert abs(survival[0].intensity - 0.7) < 1e-9


def test_quality_pain_on_low_success_rate():
    """成功率 < 0.9 → quality 痛苦。"""
    m = {
        "total_agents": 24, "agents_healthy": 24, "agents_survival_tracked": 24,
        "tasks_completed_24h": 7, "tasks_failed_24h": 3,
        "tasks_settled_24h": 10,
        "task_success_rate": 0.7, "marketplace_fill_rate": 1.0,
    }
    pains, _ = _derive_pain_signals(m)
    assert "quality" in _sources(pains)


def test_fill_rate_pain_when_tasks_unclaimed():
    """撮合率 < 0.8 → fill_rate 痛苦。"""
    m = {
        "total_agents": 24, "agents_healthy": 24, "agents_survival_tracked": 24,
        "tasks_completed_24h": 5, "tasks_failed_24h": 0,
        "tasks_settled_24h": 5,
        "task_success_rate": 1.0, "marketplace_fill_rate": 0.5,
    }
    pains, _ = _derive_pain_signals(m)
    assert "fill_rate" in _sources(pains)


def test_pain_signals_sorted_by_intensity_desc():
    """多个痛苦时按强度降序（前端取首条作为最痛信号）。"""
    m = {
        "total_agents": 10, "agents_healthy": 5, "agents_survival_tracked": 8,
        "tasks_completed_24h": 0, "tasks_failed_24h": 0,
        "tasks_settled_24h": 0,
        "task_success_rate": None, "marketplace_fill_rate": None,
    }
    pains, _ = _derive_pain_signals(m)
    intensities = [p.intensity for p in pains]
    assert intensities == sorted(intensities, reverse=True)
