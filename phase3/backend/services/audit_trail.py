"""任务生命周期链上可信追踪（方案 A-ii：动作主体亲自发交易存证）。

每个动作（发布 / 抢单 / 派单 / 提交 / 评审 / 完成）由其主体的托管钱包向 TaskAuditTrail
合约 record 一条「内容哈希」，`msg.sender` 即主体、交易签名即不可抵赖的背书。原文留在
链下（MySQL）；校验时用源表重算哈希并与链上事件比对，从而证明链下记录自锚定后未被篡改。

设计约束：
- best-effort：任何失败只落 audit_logs(status=failed) 供重试，绝不阻断主流程 / 影响资金。
- 复用 services/nonce_lock.py 串行化每个签名账户「取 nonce→广播」；托管私钥用后 bytearray 擦除。
- 埋点必须在主流程关键 commit 之后调用（此时 session 干净，本模块的 commit 只落 AuditLog）。
"""
from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 存证走后台线程池 + 独立 db session：埋点 fire-and-forget，绝不阻塞主流程/事件循环
# （HTTP 端点与自动执行路径都是 async，同步等 receipt 会阻塞事件循环）。
_bg_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="audit")

# 动作类型（与合约 TaskAuditTrail 头注释对齐）
ACTIONS = {
    "PUBLISH": 1, "BID": 2, "AWARD": 3, "ACCEPT": 4,
    "SUBMIT": 5, "REVIEW": 6, "COMPLETE": 7,
}

_RECEIPT_TIMEOUT = 45  # 秒，等 record 上链（私链偶发 stall）；超时不算失败，标 sent 保留 tx_hash


def _ts(dt) -> str:
    """时间规范化：去 tz、截断到秒。record 与校验共用，消除 DB round-trip 的 tz/微秒差异。"""
    if not dt:
        return ""
    return dt.replace(tzinfo=None).isoformat(sep="T", timespec="seconds")


def _norm_addr(a) -> str:
    return (a or "").lower()


def _num(x) -> str:
    """浮点维度分规范化（4 位小数），None -> 空串。record 与校验共用。"""
    return "" if x is None else f"{float(x):.4f}"


# --- 各 action 的规范化原文串（字段序固定，record 与校验必须完全一致） ---

def canonical_publish(task) -> str:
    return "|".join([
        "PUBLISH", str(task.id), _norm_addr(task.publisher),
        task.description or "", task.expected_output or "",
        str(int(task.reward)),
        task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type),
        _ts(task.created_at),
    ])


def canonical_accept(task, action: str = "ACCEPT") -> str:
    # ACCEPT（普通任务，accept 端点）与 AWARD（SE 任务，竞价派单）同构：主体 = 中标智能体 owner。
    return "|".join([action, str(task.id), _norm_addr(task.agent), _ts(task.accepted_at)])


def canonical_submit(task) -> str:
    return "|".join([
        "SUBMIT", str(task.id), _norm_addr(task.agent),
        task.result or "", _ts(task.submitted_at),
    ])


def canonical_review(task_id: int, review, reviewer_owner: str) -> str:
    return "|".join([
        "REVIEW", str(task_id), str(review.reviewer_agent_id), _norm_addr(reviewer_owner),
        _num(review.score), _num(review.correctness), _num(review.completeness), _num(review.standards),
        review.comment or "", _ts(review.created_at),
    ])


def canonical_complete(task) -> str:
    return "|".join([
        "COMPLETE", str(task.id), _norm_addr(task.publisher), _norm_addr(task.agent),
        str(int(task.reward)), _ts(task.completed_at),
    ])


def content_hash(canonical: str) -> str:
    from web3 import Web3
    return Web3.to_hex(Web3.keccak(text=canonical))  # 始终带 0x 前缀


def content_hash_from_bytes(raw) -> str:
    """把链上事件的 bytes32 contentHash 转成带 0x 的小写 hex（与 content_hash 同格式）。"""
    from web3 import Web3
    return Web3.to_hex(raw)


def _find_custodial_wallet(db, actor_address: str):
    from models.database import Wallet
    return (
        db.query(Wallet)
        .filter(Wallet.public_address == _norm_addr(actor_address),
                Wallet.encrypted_private_key.isnot(None))
        .order_by(Wallet.created_at.desc())
        .first()
    )


def _find_existing(db, task_id: int, action: str, ref_id: Optional[int]):
    from models.database import AuditLog
    q = db.query(AuditLog).filter(AuditLog.task_id == task_id, AuditLog.action == action)
    q = q.filter(AuditLog.ref_id.is_(None)) if ref_id is None else q.filter(AuditLog.ref_id == ref_id)
    return q.order_by(AuditLog.id.desc()).first()


def record_audit(db, actor_address: str, task_id: int, action: str,
                 canonical: str, ref_id: Optional[int] = None) -> dict:
    """A-ii 存证：actor 托管钱包亲自发 record 交易锚定 content_hash。best-effort，绝不抛。

    返回 {status, tx_hash, content_hash, error}。失败时 status=failed 并落 audit_logs 待重试。
    幂等：同 (task_id, action, ref_id) 已 confirmed 则跳过。
    """
    from models.database import AuditLog

    ch = content_hash(canonical)
    action_code = ACTIONS.get(action)

    existing = _find_existing(db, task_id, action, ref_id)
    if existing and existing.status == "confirmed":
        return {"status": "confirmed", "tx_hash": existing.tx_hash, "content_hash": ch, "error": None}

    row = existing or AuditLog(
        task_id=task_id, action=action, actor_address=_norm_addr(actor_address),
        content_hash=ch, ref_id=ref_id, status="pending", created_at=datetime.utcnow(),
    )
    row.content_hash = ch
    row.actor_address = _norm_addr(actor_address)
    row.status = "pending"
    row.error = None
    if existing is None:
        db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()

    if action_code is None:
        return _mark_failed(db, row, ch, f"unknown action {action}")

    try:
        tx_hash, seq, confirmed = _send_record_tx(db, actor_address, task_id, action_code, ch)
    except Exception as exc:  # 广播失败（无 tx）——绝不阻断主流程，落 failed 待重试
        logger.warning("audit record broadcast failed task=%s action=%s: %s", task_id, action, exc)
        return _mark_failed(db, row, ch, str(exc))

    # 已广播成功：记 tx_hash。confirmed=已观察到上链；否则标 sent（已广播、待确认），不重发
    # 以免链上重复——校验接口以链上事件为权威，sent 不影响可信追踪的完整性。
    row.tx_hash = tx_hash
    row.onchain_seq = seq
    row.status = "confirmed" if confirmed else "sent"
    row.confirmed_at = datetime.utcnow() if confirmed else None
    row.error = None if confirmed else "receipt not observed within timeout"
    try:
        db.commit()
    except Exception:
        db.rollback()
    logger.info("audit recorded task=%s action=%s actor=%s status=%s tx=%s",
                task_id, action, _norm_addr(actor_address), row.status, tx_hash)
    return {"status": row.status, "tx_hash": tx_hash, "content_hash": ch, "error": row.error}


def record_audit_bg(actor_address: str, task_id: int, action: str,
                    canonical: str, ref_id: Optional[int] = None) -> None:
    """把存证提交到后台线程（独立 db session），fire-and-forget，绝不阻塞主流程/事件循环。

    canonical 必须由调用方在主线程用当时的 ORM 对象算好并传入字符串；线程内只用字符串
    + 独立 session（ORM Session 不跨线程共享）。所有埋点统一用本函数。
    """
    def _work():
        from utils.database import SessionLocal
        db = SessionLocal()
        try:
            record_audit(db, actor_address, task_id, action, canonical, ref_id)
        except Exception as exc:
            logger.warning("audit bg worker failed task=%s action=%s: %s", task_id, action, exc)
        finally:
            db.close()
    try:
        _bg_executor.submit(_work)
    except Exception as exc:
        logger.warning("audit bg submit failed task=%s action=%s: %s", task_id, action, exc)


def _mark_failed(db, row, ch, err: str) -> dict:
    try:
        row.status = "failed"
        row.error = (err or "")[:2000]
        db.commit()
    except Exception:
        db.rollback()
    return {"status": "failed", "tx_hash": None, "content_hash": ch, "error": err}


def _send_record_tx(db, actor_address: str, task_id: int, action_code: int, content_hash_hex: str):
    """广播 actor 自签的 TaskAuditTrail.record，尽力等待上链。返回 (tx_hash_hex, seq, confirmed)。

    广播失败（无法提交交易）抛异常，交调用方落 failed 重试；广播成功但未在超时内观察到上链
    返回 confirmed=False（保留 tx_hash，标 sent，不重发以免链上重复）。
    """
    from web3 import Web3
    from blockchain.web3_config import get_web3_config
    from services.nonce_lock import account_nonce_lock
    from services.wallet import _get_local_encryption, _zero_bytes

    config = get_web3_config()
    if config.audit_contract is None:
        raise RuntimeError("TaskAuditTrail 合约未配置（AUDIT_TRAIL_ADDRESS 未设或链不可用）")

    wallet = _find_custodial_wallet(db, actor_address)
    if wallet is None:
        raise RuntimeError(f"主体 {actor_address} 无托管钱包，无法自签存证")

    w3 = config.w3
    from_addr = Web3.to_checksum_address(wallet.public_address)
    hex_body = content_hash_hex[2:] if content_hash_hex.startswith("0x") else content_hash_hex
    ch_bytes = bytes.fromhex(hex_body)

    pk = bytearray(_get_local_encryption().decrypt(
        base64.b64decode(wallet.encrypted_private_key), wallet.public_address))
    try:
        # 串行化同一签名账户的 nonce 获取→广播，避免并发存证撞同一 nonce 被链拒。
        with account_nonce_lock(from_addr):
            tx = config.audit_contract.functions.record(task_id, action_code, ch_bytes).build_transaction({
                "from": from_addr,
                "nonce": w3.eth.get_transaction_count(from_addr, "pending"),
                "gas": 200000,
                "gasPrice": 0,
                "chainId": config.chain_id,
            })
            signed = w3.eth.account.sign_transaction(tx, bytes(pk))
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    finally:
        _zero_bytes(pk)  # 真正擦除明文私钥（bytearray 可原地清零）

    tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
    # 广播已成功；等待上链确认（超时/revert 不视为失败，返回 confirmed=False 并保留 tx_hash）。
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=_RECEIPT_TIMEOUT)
    except Exception:
        return (tx_hex, None, False)
    if receipt["status"] != 1:
        return (tx_hex, None, False)
    seq = None
    try:
        evs = config.audit_contract.events.AuditRecord().process_receipt(receipt)
        if evs:
            seq = int(evs[0]["args"]["seq"])
    except Exception:
        pass
    return (tx_hex, seq, True)


def retry_failed_audits(db, limit: int = 50) -> dict:
    """重试 status=failed 的存证（供 admin 端点 / cron / E2E 调用）。返回 {retried, confirmed, failed}。"""
    from models.database import AuditLog

    rows = (db.query(AuditLog).filter(AuditLog.status == "failed")
            .order_by(AuditLog.id.asc()).limit(limit).all())
    confirmed = 0
    for row in rows:
        action_code = ACTIONS.get(row.action)
        if action_code is None:
            continue
        try:
            tx_hash, seq, ok = _send_record_tx(db, row.actor_address, row.task_id, action_code, row.content_hash)
        except Exception as exc:
            _mark_failed(db, row, row.content_hash, str(exc))
            continue
        row.tx_hash = tx_hash
        row.onchain_seq = seq
        row.status = "confirmed" if ok else "sent"
        row.confirmed_at = datetime.utcnow() if ok else None
        row.error = None if ok else "receipt not observed within timeout"
        try:
            db.commit()
            confirmed += 1 if ok else 0
        except Exception:
            db.rollback()
    return {"retried": len(rows), "confirmed": confirmed, "failed": len(rows) - confirmed}
