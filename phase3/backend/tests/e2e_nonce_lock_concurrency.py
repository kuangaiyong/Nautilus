"""W1/W2/W3 回归：链上交易的 per-account nonce 锁 + 私钥擦除（真实链+真实托管钱包，无 mock）。

本轮 api/wallets.py 评审修复点：
- W1: pay_hua_from_custodial / transfer_hua 并发同账户转账，统一用 services.nonce_lock
      .account_nonce_lock 串行化"取 pending nonce→广播"，防并发撞同一 nonce 致部分华币
      结算被链拒/顶替而静默丢失。
- W2: 解密的托管私钥放入 bytearray，用后 _zero_bytes 原地清零（最小化明文驻留）。
- W3: mint_hua 与 nautilus_token.mint_task_reward（同一 minter 账户）共享 per-account 锁。
- W5: mint_collaborative_reward（学术协作铸币，academic_tasks 真实调用）也纳入同一 minter
      锁并改用 pending nonce，闭合与已锁路径并发仍撞 nonce 的缺口。

默认（有锁）：并发 K 笔同账户 pay_hua 全部成功上链、余额精确、链上 nonce 为连续排列；
并发 K 次 NAU mint 全部拿到唯一 tx；单次 pay 的私钥 bytearray 用后被清零 -> NONCE_LOCK_PASS。
（修复前：并发各自取同一 pending nonce，仅一笔上链，其余被链拒 -> success<K -> FAIL。）

运行(CWD=phase3/backend)：C:/nautilus-venv/Scripts/python.exe tests/e2e_nonce_lock_concurrency.py
"""
import os
import sys
import secrets
import asyncio
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from web3 import Web3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()  # 从 phase3/backend/.env 读 RPC/合约/minter key（CWD 须为 backend）

import models.agent_survival  # noqa: F401  注册 AgentSurvival mapper（Agent 跨模块 relationship）
from utils.database import SessionLocal
from models.database import User
from services.wallet import pay_hua_from_custodial
import services.wallet as wallet_mod
from services.nautilus_token import NautilusTokenService

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
OWNER_PK = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("BLOCKCHAIN_PRIVATE_KEY")
CHAIN_ID = int(os.getenv("PRIVATE_CHAIN_ID", "13370"))
ONE = 10 ** 18
K = 6

HUA_ABI = [
    {"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "mint", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]
w3 = Web3(Web3.HTTPProvider(RPC))
hua = w3.eth.contract(address=HUA, abi=HUA_ABI)
assert w3.is_connected(), "geth 不可达"


def _is_tx(x) -> bool:
    return isinstance(x, str) and not x.startswith("ERR") and len(x.replace("0x", "")) == 64


def _norm(x: str) -> str:
    return x if x.startswith("0x") else "0x" + x


def reg_login(username, password="Verify@12345"):
    requests.post(f"{API}/api/auth/register",
                  json={"username": username, "email": f"{username}@nautilus-corp.com",
                        "password": password})
    r = requests.post(f"{API}/api/auth/login",
                      json={"username": username, "password": password})
    r.raise_for_status()
    d = r.json()
    return d.get("access_token") or d.get("data", {}).get("access_token")


def me(token):
    r = requests.get(f"{API}/api/wallets/me", headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def owner_mint(to_addr, amount_wei):
    owner = w3.eth.account.from_key(OWNER_PK)
    tx = hua.functions.mint(Web3.to_checksum_address(to_addr), amount_wei).build_transaction({
        "from": owner.address, "nonce": w3.eth.get_transaction_count(owner.address),
        "gas": 120000, "gasPrice": 0, "chainId": CHAIN_ID,
    })
    signed = w3.eth.account.sign_transaction(tx, OWNER_PK)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(h, timeout=30)


def bal(addr):
    return hua.functions.balanceOf(Web3.to_checksum_address(addr)).call()


def _pay_one(payer_username, to_addr, amount_units):
    """每线程独立 Session（SQLAlchemy Session 非线程安全）内调 pay_hua。返回 tx 或 'ERR:...'。"""
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == payer_username).first()
        return pay_hua_from_custodial(db, u, to_addr, amount_units)
    except Exception as e:
        return f"ERR:{e}"
    finally:
        db.close()


def test_w2_key_erasure(payer_username, to_addr):
    """W2：单次 pay 解密出的 32B 私钥 bytearray 用后被 _zero_bytes 清零（spy 只观测、仍真擦）。"""
    holder = {}
    orig = wallet_mod._zero_bytes

    def spy(data):
        if "before" not in holder and isinstance(data, bytearray) and len(data) == 32:
            holder["before"] = bytes(data)
            orig(data)
            holder["after"] = bytes(data)
        else:
            orig(data)

    wallet_mod._zero_bytes = spy
    try:
        res = _pay_one(payer_username, to_addr, 1 * ONE)
    finally:
        wallet_mod._zero_bytes = orig
    before = holder.get("before", b"")
    after = holder.get("after", b"")
    after_zero = after == b"\x00" * 32
    ok = _is_tx(res) and len(before) == 32 and any(before) and after_zero
    print(f"W2 key-erasure: tx={str(res)[:18]} priv32_before_nonzero={any(before)} "
          f"priv_after_allzero={after_zero} -> {'OK' if ok else 'FAIL'}")
    return ok


def test_w1_concurrent_transfers(payer_username, payer_addr):
    recips = ["0x" + secrets.token_hex(20) for _ in range(K)]
    p0 = bal(payer_addr)
    r0 = [bal(a) for a in recips]
    with ThreadPoolExecutor(max_workers=K) as ex:
        results = list(ex.map(lambda a: _pay_one(payer_username, a, 10 * ONE), recips))
    txs = []
    for res in results:
        if _is_tx(res):
            w3.eth.wait_for_transaction_receipt(_norm(res), timeout=30)
            txs.append(_norm(res))
    p1 = bal(payer_addr)
    r1 = [bal(a) for a in recips]
    nonces = sorted(w3.eth.get_transaction(t)["nonce"] for t in txs) if txs else []
    contiguous = bool(nonces) and nonces == list(range(nonces[0], nonces[0] + K))
    all_ok = (len(set(txs)) == K and p1 == p0 - K * 10 * ONE
              and all(r1[i] == r0[i] + 10 * ONE for i in range(K)) and contiguous)
    print(f"W1 concurrent: success={len(txs)}/{K} unique_tx={len(set(txs))} "
          f"payer-={(p0 - p1) // ONE}HUA nonces={nonces} contiguous={contiguous} "
          f"-> {'OK' if all_ok else 'FAIL'}")
    for res in results:
        if not _is_tx(res):
            print(f"   non-tx result: {str(res)[:120]}")
    return all_ok


def _mint_one(addr):
    try:
        return asyncio.run(NautilusTokenService.mint_task_reward(addr, "CODE_DEVELOPMENT"))
    except Exception as e:
        return f"ERR:{e}"


def test_w3_concurrent_mint():
    addrs = ["0x" + secrets.token_hex(20) for _ in range(K)]
    with ThreadPoolExecutor(max_workers=K) as ex:
        results = list(ex.map(_mint_one, addrs))
    txs = [_norm(r) for r in results if _is_tx(r)]
    ok = len(txs) == K and len(set(txs)) == K
    print(f"W3 concurrent NAU mint: got_tx={len(txs)}/{K} unique={len(set(txs))} "
          f"-> {'OK' if ok else 'FAIL'}")
    for r in results:
        if not _is_tx(r):
            print(f"   non-tx result: {str(r)[:120]}")
    return ok


def _mint_collab_one(coord, researchers):
    try:
        return asyncio.run(
            NautilusTokenService.mint_collaborative_reward(coord, researchers, "research_synthesis")
        )
    except Exception as e:
        return f"ERR:{e}"


def test_w5_collaborative_mint():
    """W5：并发 K 个协作铸币（各 coordinator+2 researcher=3 笔），共 3K 笔须全部唯一上链。"""
    jobs = [("0x" + secrets.token_hex(20), ["0x" + secrets.token_hex(20) for _ in range(2)])
            for _ in range(K)]
    with ThreadPoolExecutor(max_workers=K) as ex:
        results = list(ex.map(lambda j: _mint_collab_one(*j), jobs))
    all_tx = []
    for r in results:
        if isinstance(r, list):
            all_tx += [_norm(x) for x in r if _is_tx(x)]
    expected = 3 * K
    sample_status = w3.eth.wait_for_transaction_receipt(all_tx[0], timeout=30).status if all_tx else 0
    ok = len(all_tx) == expected and len(set(all_tx)) == expected and sample_status == 1
    print(f"W5 concurrent collaborative mint: tx={len(all_tx)}/{expected} "
          f"unique={len(set(all_tx))} sample_status={sample_status} -> {'OK' if ok else 'FAIL'}")
    for r in results:
        if not isinstance(r, list):
            print(f"   non-list result: {str(r)[:120]}")
    return ok


def main():
    s = secrets.token_hex(3)
    payer_user = f"e2e_nlock_{s}"
    token = reg_login(payer_user)
    payer = me(token)
    print(f"payer: {payer['address']} wallet_id={payer['wallet_id']}")
    owner_mint(payer["address"], 1000 * ONE)
    print(f"minted 1000 HUA to payer; balance={bal(payer['address']) // ONE} HUA")

    oks = [
        ("W2", test_w2_key_erasure(payer_user, "0x" + secrets.token_hex(20))),
        ("W1", test_w1_concurrent_transfers(payer_user, payer["address"])),
        ("W3", test_w3_concurrent_mint()),
        ("W5", test_w5_collaborative_mint()),
    ]
    ok = all(v for _, v in oks)
    print("RESULTS:", ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in oks))
    print("NONCE_LOCK_PASS" if ok else "NONCE_LOCK_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
