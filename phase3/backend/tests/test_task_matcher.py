"""撮合评分回归测试。

复现并防回归一个真实 bug：`calculate_agent_score` 原先拿任务类型枚举（CODE/DATA…）
去比对智能体技能标签（python/fastapi…），二者词表不相交，专长分恒为 0；叠加信誉
按 /1000 归一（智能体出生信誉 100 只得 3 分），任何智能体都达不到 min_score=50，
任务永远无法被自动分配。修复后：有相关技能且空闲的智能体应稳过 50。
"""
from types import SimpleNamespace

from task_matcher import calculate_agent_score

MIN_SCORE = 50.0  # auto_assign_task 的默认门槛


# 注：撮合评分测试沿用遗留非 SE 类型字符串（"CODE"）。TaskType 枚举已全面 SE 化，
# check_and_assign_tasks 会排除全部 SE 任务；calculate_agent_score 仅对遗留存量
# 任务有意义，其 TASK_TYPE_SKILLS 词表仍以旧类型为键，故此处保留 "CODE"。
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


# ---------------------------------------------------------------------------
# 任务类型全面 SE 化：自动撮合必须排除 SE 任务（防抢跑 60s 竞价窗、
# 防把 SE 任务投进自动执行队列）。SE 派单唯一自动路径 = se_marketplace 竞价 cron。
# ---------------------------------------------------------------------------

def test_all_task_types_are_se():
    """TaskType 8 类全部被 is_se_task 认定（自动撮合对新任务恒 no-op 的前提）。"""
    from models.database import TaskType
    from services import se_pouw

    assert len(TaskType) == 8
    for t in TaskType:
        assert se_pouw.is_se_task(t), f"{t.name} 应为 SE 任务类型"
    assert not se_pouw.is_se_task("CODE")  # 遗留类型不算 SE


async def test_check_and_assign_skips_se_tasks(monkeypatch):
    """check_and_assign_tasks 对 OPEN 的 SE 任务不调用 auto_assign_task。"""
    from unittest.mock import MagicMock
    import task_matcher

    se_task = SimpleNamespace(id=101, task_type="CODE_DEVELOPMENT")
    legacy_task = SimpleNamespace(id=102, task_type="CODE")
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [se_task, legacy_task]

    assigned = []

    async def fake_assign(task_id, _db, min_score=50.0):
        assigned.append(task_id)
        return None

    monkeypatch.setattr(task_matcher, "auto_assign_task", fake_assign)
    await task_matcher.check_and_assign_tasks(db)

    assert 101 not in assigned, "SE 任务不得被自动撮合派单"
    assert assigned == [102], "遗留非 SE 任务仍走自动撮合"
