"""按签名账户(EOA)串行化链上交易的"取 pending nonce→签名→广播"。

同一个外部账户在多处发交易——华币托管转账(services.wallet.pay_hua_from_custodial)、
用户主动华币转账(api.wallets.transfer_hua)、管理员铸华币(api.wallets.mint_hua)、
NAU 铸币(services.nautilus_token.mint_task_reward)——若并发各自取 "pending" nonce，
会拿到同一个 nonce，使后广播的交易顶替/丢弃前一笔，导致部分结算/铸币静默丢失。

所有发交易路径统一用 account_nonce_lock(addr) 取锁后再"取 nonce→广播"：同账户串行、
不同账户并行。进程内锁（单后端进程足够；若将来多进程部署需换分布式锁）。
"""
from __future__ import annotations

import threading
from collections import defaultdict

_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_guard = threading.Lock()


def account_nonce_lock(address: str) -> threading.Lock:
    """返回给定账户地址(大小写无关)对应的进程内锁；同一地址恒返回同一把锁。"""
    key = (address or "").lower()
    with _guard:  # 保护 defaultdict 首次建键，避免并发下竞态创建两把锁
        return _locks[key]
