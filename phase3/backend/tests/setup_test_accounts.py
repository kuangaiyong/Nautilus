"""
Set up demo accounts and verify core flows on the private chain (no mocks).

- Promote verify_demo to admin.
- Register alice + bob, provision wallets, admin-mint 1000 HUA each.
- Verify, end to end:
    * HUA transfer alice -> bob (on-chain balances change)
    * task publish returns 201   (throwaway account)
    * agent publish returns 201  (same throwaway; exercises auto-wallet path)

Throwaway is used for task/agent publish so alice & bob keep their one-agent
slot free for your own testing. Login/register are rate-limited (5/min), so
calls are kept few and retried once on 429.

Run: C:/nautilus-venv/Scripts/python.exe tests/setup_test_accounts.py
"""
import os
import sys
import time
import secrets

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))

BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view",
            "type": "function"}]
w3 = Web3(Web3.HTTPProvider(RPC))
hua = w3.eth.contract(address=HUA, abi=BAL_ABI)
ONE = 10 ** 18


def _retry_429(fn):
    r = fn()
    if r.status_code == 429:
        print("  rate-limited, waiting 60s ...")
        time.sleep(60)
        r = fn()
    return r


def register(username, password):
    _retry_429(lambda: requests.post(
        f"{API}/api/auth/register",
        json={"username": username, "email": f"{username}@nautilus-corp.com",
              "password": password}))


def login(username, password):
    r = _retry_429(lambda: requests.post(
        f"{API}/api/auth/login", json={"username": username, "password": password}))
    r.raise_for_status()
    d = r.json()
    return d.get("access_token") or d.get("data", {}).get("access_token")


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def me(token):
    r = requests.get(f"{API}/api/wallets/me", headers=auth(token))
    r.raise_for_status()
    return r.json()


def promote(username):
    # 经应用引擎（DATABASE_URL=MySQL）执行原生 SQL，不再依赖 sqlite3
    from sqlalchemy import text
    from utils.database import engine
    with engine.begin() as cx:
        cx.execute(text("UPDATE users SET is_admin = 1 WHERE username = :u"), {"u": username})


def mint(admin_token, target, amount):
    r = requests.post(f"{API}/api/wallets/mint", headers=auth(admin_token),
                      json={"target": target, "amount": amount})
    r.raise_for_status()
    w3.eth.wait_for_transaction_receipt("0x" + r.json()["tx_hash"].lstrip("0x"), timeout=30)


def bal(addr):
    return hua.functions.balanceOf(Web3.to_checksum_address(addr)).call() / ONE


def main():
    s = secrets.token_hex(3)
    res = {}

    # 1. verify_demo -> admin
    register("verify_demo", "Verify@12345")  # no-op if exists
    promote("verify_demo")
    admin_token = login("verify_demo", "Verify@12345")
    res["verify_demo is_admin"] = requests.get(
        f"{API}/api/auth/me", headers=auth(admin_token)).json()["data"]["user"]["is_admin"]

    # 2. alice + bob: register, wallet, fund 1000 HUA each
    acct = {}
    for u in ("alice", "bob"):
        register(u, "Test@12345")
        t = login(u, "Test@12345")
        info = me(t)
        mint(admin_token, u, 1000)
        acct[u] = {"token": t, "address": info["address"], "wallet_id": info["wallet_id"]}

    # 3. verify transfer alice -> bob (100 HUA)
    a, b = acct["alice"], acct["bob"]
    a0, b0 = bal(a["address"]), bal(b["address"])
    rt = requests.post(f"{API}/api/wallets/{a['wallet_id']}/transfer", headers=auth(a["token"]),
                       json={"to_address": b["address"], "amount": 100})
    rt.raise_for_status()
    w3.eth.wait_for_transaction_receipt("0x" + rt.json()["tx_hash"].lstrip("0x"), timeout=30)
    a1, b1 = bal(a["address"]), bal(b["address"])
    acct["alice"]["balance"], acct["bob"]["balance"] = a1, b1
    res["transfer alice->bob 100"] = "OK" if (a1 == a0 - 100 and b1 == b0 + 100) else f"FAIL {a0}->{a1}/{b0}->{b1}"

    # 4 & 5. task + agent publish with one throwaway (keeps alice/bob pristine)
    register(f"demo_{s}", "Test@12345")
    tv = login(f"demo_{s}", "Test@12345")
    rt = requests.post(f"{API}/api/tasks", headers=auth(tv), json={
        "description": "内网验证任务：实现两数相加并附单测",
        "input_data": "两个整数", "expected_output": "它们的和",
        "reward": ONE, "task_type": "CODE_DEVELOPMENT", "timeout": 86400})
    res["task publish"] = f"HTTP {rt.status_code} ({'OK' if rt.status_code == 201 else rt.text[:120]})"

    ra = requests.post(f"{API}/api/agents", headers=auth(tv), json={
        "name": f"VerifyBot_{s}", "description": "内网验证用智能体",
        "specialties": ["Python", "FastAPI"]})
    res["agent publish"] = f"HTTP {ra.status_code} ({'OK' if ra.status_code == 201 else ra.text[:160]})"

    # summary
    print("=" * 64)
    print("账号与凭据：")
    print("  verify_demo / Verify@12345   (管理员，可发币 /admin/mint)")
    print(f"  alice       / Test@12345     {acct['alice']['address']}  余额 {acct['alice']['balance']} 华币")
    print(f"  bob         / Test@12345     {acct['bob']['address']}  余额 {acct['bob']['balance']} 华币")
    print("-" * 64)
    for k, v in res.items():
        print(f"  {k}: {v}")
    print("=" * 64)

    ok = (res["verify_demo is_admin"] and res["transfer alice->bob 100"] == "OK"
          and "201" in res["task publish"] and "201" in res["agent publish"])
    print("SETUP_VERIFY_PASS" if ok else "SETUP_VERIFY_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
