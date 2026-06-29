"""撮合评分回归测试。

复现并防回归一个真实 bug：`calculate_agent_score` 原先拿任务类型枚举（CODE/DATA…）
去比对智能体技能标签（python/fastapi…），二者词表不相交，专长分恒为 0；叠加信誉
按 /1000 归一（智能体出生信誉 100 只得 3 分），任何智能体都达不到 min_score=50，
任务永远无法被自动分配。修复后：有相关技能且空闲的智能体应稳过 50。
"""
from types import SimpleNamespace

from task_matcher import calculate_agent_score

MIN_SCORE = 50.0  # auto_assign_task 的默认门槛


def _task(task_type="CODE"):
    return SimpleNamespace(task_type=task_type)


def _agent(specialties, reputation=100, current_tasks=0, completed=0, failed=0):
    return SimpleNamespace(
        specialties=specialties, reputation=reputation,
        current_tasks=current_tasks, completed_tasks=completed, failed_tasks=failed,
    )


def test_code_task_python_agent_clears_threshold():
    # python/fastapi 智能体（技能标签）应胜任 CODE 任务并越过 min_score
    score = calculate_agent_score(_task("CODE"), _agent("python,fastapi"))
    assert score >= MIN_SCORE, f"期望 >= {MIN_SCORE}，实际 {score}"


def test_code_task_codegen_agent_clears_threshold():
    score = calculate_agent_score(_task("CODE"), _agent("code-generation,tool-use,reasoning"))
    assert score >= MIN_SCORE, f"期望 >= {MIN_SCORE}，实际 {score}"


def test_specialty_overlap_awards_about_40_points():
    matched = calculate_agent_score(_task("CODE"), _agent("python,fastapi"))
    unmatched = calculate_agent_score(_task("CODE"), _agent("painting,music"))
    # 有相关技能比无关技能高出约 40 分（专长分）
    assert matched - unmatched >= 39, f"专长分差异不足：matched={matched} unmatched={unmatched}"


def test_unrelated_agent_below_threshold():
    # 完全无关技能 + 默认信誉，应低于门槛（不会被错误自动分配）
    score = calculate_agent_score(_task("CODE"), _agent("painting,music"))
    assert score < MIN_SCORE, f"无关智能体不应过门槛，实际 {score}"


def test_reputation_contributes_meaningfully():
    # 信誉归一化按 /200：出生信誉 100 应得约 15 分（而非旧逻辑 /1000 的 3 分）
    low = calculate_agent_score(_task("CODE"), _agent("painting", reputation=0))
    base = calculate_agent_score(_task("CODE"), _agent("painting", reputation=100))
    assert base - low >= 10, f"信誉 100 应显著贡献分数，实际差 {base - low}"
