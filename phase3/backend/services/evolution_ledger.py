"""
Evolution Ledger — 平台进化历史记录
每次沙箱实验 promoted，写入进化账本。
链上合约未部署时，奖励记录在 pending_nau_rewards 表，合约上线后批量 mint。
"""
from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 提案改进→NAU 奖励映射（baseline）
NAU_REWARD_BY_TYPE = {
    "routing_weight": 100.0,    # 调整任务路由权重
    "reward_parameter": 150.0,  # 调整奖励参数
    "threshold_adjust": 80.0,   # 调整阈值
    "default": 50.0,            # 其他类型
}

# 改善幅度奖励乘数（delta 越大，奖励越多）
def _improvement_multiplier(delta: float) -> float:
    if delta >= 0.20:
        return 3.0   # 改善 ≥ 20%，3倍奖励
    elif delta >= 0.10:
        return 2.0   # 改善 ≥ 10%，2倍奖励
    elif delta >= 0.05:
        return 1.5   # 改善 ≥ 5%，1.5倍奖励
    else:
        return 1.0


def record_evolution(db: Session, proposal_id: str, sandbox_result: Dict[str, Any]) -> Optional[str]:
    """
    记录一次成功的平台进化：
    1. 写入 platform_evolution_log
    2. 写入 pending_nau_rewards（给提案者）

    Returns: evolution entry id，失败返回 None
    """
    try:
        # 获取提案详情
        prop = db.execute(text(
            "SELECT id, agent_id, proposed_change FROM platform_improvement_proposals WHERE id=:id"
        ), {"id": proposal_id}).fetchone()
        if not prop:
            logger.warning("evolution_ledger: proposal %s not found", proposal_id)
            return None

        pc = prop.proposed_change or {}
        if isinstance(pc, str):
            import json as _json
            pc = _json.loads(pc)

        change_type = pc.get("type", "default")
        delta = sandbox_result.get("delta", 0.0)

        # 计算 NAU 奖励
        base_nau = NAU_REWARD_BY_TYPE.get(change_type, NAU_REWARD_BY_TYPE["default"])
        nau_reward = round(base_nau * _improvement_multiplier(delta), 2)

        # 计算版本号：当前最大 minor_version + 1
        last_version = db.execute(text(
            "SELECT minor_version FROM platform_evolution_log ORDER BY minor_version DESC LIMIT 1"
        )).scalar()
        next_minor = (last_version or 0) + 1
        version_str = f"v1.{next_minor}"

        entry_id = f"evo_{next_minor:04d}"

        db.execute(text(
            """INSERT INTO platform_evolution_log
               (id, proposal_id, proposer_agent_id, version_str, minor_version,
                change_type, proposed_change, sandbox_result, metric_delta,
                nau_rewarded, status)
               VALUES (:id, :proposal_id, :proposer_agent_id, :version_str, :minor_version,
                       :change_type, :proposed_change, :sandbox_result,
                       :metric_delta, :nau_rewarded, 'active')"""
        ), {
            "id": entry_id,
            "proposal_id": proposal_id,
            "proposer_agent_id": prop.agent_id,
            "version_str": version_str,
            "minor_version": next_minor,
            "change_type": change_type,
            "proposed_change": json.dumps(pc),
            "sandbox_result": json.dumps(sandbox_result),
            "metric_delta": delta,
            "nau_rewarded": nau_reward,
        })

        # 写入 pending NAU 奖励（待合约部署后 mint）
        db.execute(text(
            """INSERT INTO pending_nau_rewards
               (agent_id, amount, reason, source_id, status)
               VALUES (:agent_id, :amount, :reason, :source_id, 'pending')"""
        ), {
            "agent_id": prop.agent_id,
            "amount": nau_reward,
            "reason": f"Platform improvement proposal {proposal_id} promoted ({version_str})",
            "source_id": entry_id,
        })

        db.commit()
        logger.info(
            "evolution_ledger: recorded %s (proposal=%s, delta=%.3f, nau=%.1f)",
            entry_id, proposal_id, delta, nau_reward
        )
        return entry_id

    except Exception as e:
        logger.error("evolution_ledger: record_evolution failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def get_ledger_summary(db: Session) -> Dict[str, Any]:
    """返回进化账本摘要。"""
    try:
        total = db.execute(text("SELECT COUNT(*) FROM platform_evolution_log")).scalar() or 0
        latest = db.execute(text(
            "SELECT version_str, metric_delta, nau_rewarded, created_at "
            "FROM platform_evolution_log ORDER BY minor_version DESC LIMIT 1"
        )).fetchone()
        total_nau = db.execute(text(
            "SELECT COALESCE(SUM(nau_rewarded), 0) FROM platform_evolution_log"
        )).scalar() or 0.0

        return {
            "total_evolutions": int(total),
            "total_nau_distributed": float(total_nau),
            "latest_version": latest.version_str if latest else None,
            "latest_delta": float(latest.metric_delta) if latest else None,
            "latest_at": latest.created_at.isoformat() if latest and latest.created_at else None,
        }
    except Exception as e:
        logger.error("evolution_ledger: get_ledger_summary failed: %s", e)
        return {"error": str(e)}
