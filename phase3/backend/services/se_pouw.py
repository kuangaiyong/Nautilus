"""软件工程(SE)任务 PoUW 市场 —— 配置与纯函数助手。

把"自主竞价 + 3 专家评审 + 完成铸 NAU"接入常规 tasks 流程时共用的配置与计算。
本模块不含 DB / 网络 IO，纯函数，便于复用与单测。
"""
from __future__ import annotations

import json
from typing import Optional

# 5 个软件工程任务类型（与 models.database.TaskType 对应）
SE_TASK_TYPES = {
    "REQUIREMENT_ANALYSIS",
    "ARCHITECTURE_DESIGN",
    "TEST_CASE_DESIGN",
    "TEST_AUTOMATION",
    "CODE_DEVELOPMENT",
}

# NAU 奖励额度的单一来源在 services/nautilus_token.TASK_TYPE_REWARDS（铸造时读取），
# 已为 5 个 SE 类型登记额度，避免在此重复维护。

# 任务类型 → 专长关键词（用于专长匹配加分；中英混合，子串命中即算匹配）
SPECIALTY_HINTS = {
    "REQUIREMENT_ANALYSIS": {"requirement", "需求", "analysis", "分析", "ba", "product", "产品"},
    "ARCHITECTURE_DESIGN": {"architecture", "架构", "design", "设计", "system", "方案"},
    "TEST_CASE_DESIGN": {"test", "测试", "qa", "用例", "case"},
    "TEST_AUTOMATION": {"test", "测试", "automation", "自动化", "selenium", "pytest", "playwright"},
    "CODE_DEVELOPMENT": {"code", "代码", "develop", "开发", "python", "java", "backend", "frontend", "后端", "前端"},
}

# 评审配置
NUM_REVIEWERS = 3                # 每个 SE 任务的评审专家数
REVIEW_PASS_THRESHOLD = 3.0      # 聚合均分(满分 5)≥ 此值 → 通过；否则任务失败
REVIEW_SCORE_MAX = 5.0

# 竞价窗口（秒）：发布后收集投标的时长，到点择优中标（自主竞价）
BID_WINDOW_SECONDS = 60


def _norm(task_type) -> str:
    """兼容 TaskType 枚举与字符串，统一为大写名。"""
    if task_type is None:
        return ""
    v = getattr(task_type, "value", task_type)
    return str(v).upper()


def is_se_task(task_type) -> bool:
    return _norm(task_type) in SE_TASK_TYPES


def parse_specialties(raw) -> list[str]:
    """解析 agent.specialties（JSON 数组 / 逗号分隔字符串 / list）。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s).lower().strip() for s in raw if s]
    try:
        p = json.loads(raw)
        if isinstance(p, list):
            return [str(s).lower().strip() for s in p if s]
    except (json.JSONDecodeError, TypeError):
        pass
    return [s.lower().strip() for s in str(raw).split(",") if s.strip()]


def specialty_match(task_type, specialties_raw) -> bool:
    """agent 专长是否匹配任务类型（任一专长与任务关键词子串命中）。"""
    hints = SPECIALTY_HINTS.get(_norm(task_type), set())
    specs = parse_specialties(specialties_raw)
    if not hints or not specs:
        return False
    return any(any(h in s or s in h for h in hints) for s in specs)


def bid_weight(reputation_score: Optional[float], task_type, specialties_raw) -> float:
    """中标加权分 = 声誉(0~100) + 专长匹配奖励(命中 +30)。越高越优先中标。"""
    rep = float(reputation_score) if reputation_score is not None else 50.0
    return rep + (30.0 if specialty_match(task_type, specialties_raw) else 0.0)


def aggregate_reviews(scores: list[float]) -> dict:
    """聚合 N 个评审分，返回 {avg, passed, n}。"""
    valid = [float(s) for s in scores if s is not None]
    avg = sum(valid) / len(valid) if valid else 0.0
    return {"avg": round(avg, 3), "passed": bool(valid) and avg >= REVIEW_PASS_THRESHOLD, "n": len(valid)}
