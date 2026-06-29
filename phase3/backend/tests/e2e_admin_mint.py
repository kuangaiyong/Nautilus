"""
Real on-chain E2E for admin 华币 minting (no mocks).

Flow: register admin + target users -> promote admin (is_admin=1 in DB) ->
admin POST /api/wallets/mint {target: <username>, amount} -> assert target's
on-chain HUA balance increased. Also asserts a non-admin gets 403.

Requires: geth at PRIVATE_RPC (chainId 13370) and backend at API.
Run: C:/nautilus-venv/Scripts/python.exe tests/e2e_admin_mint.py
"""
import os
import sys
import sqlite3
import secrets

import requests
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./nautilus_private.db")
DB_PATH = DB_URL.replace("sqlite:///", "").lstrip("./") or "nautilus_private.db"

BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view",
            "type": "function"}]

w3 = Web3(Web3.HTTPProvider(RPC))
hua = w3.eth.contract(address=HUA, abi=BAL_ABI)
assert w3.is_connected(), "geth not reachable"


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
    return requests.get(f"{API}/api/wallets/me",
                        headers={"Authorization": f"Bearer {token}"}).json()


def promote_admin(username):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (username,))
    con.commit()
    changed = con.total_changes
    con.close()
    return changed


def bal(addr):
    return hua.functions.balanceOf(Web3.to_checksum_address(addr)).call()


def main():
    s = secrets.token_hex(3)
    admin_user, target_user = f"e2e_admin_{s}", f"e2e_mtarget_{s}"

    admin_token = reg_login(admin_user)
    target_token = reg_login(target_user)
    if promote_admin(admin_user) != 1:
        print("FAILED to promote admin in DB"); sys.exit(1)

    target = me(target_token)
    print(f"target : {target['address']}")
    before = bal(target["address"])

    one = 10 ** 18
    # 1) admin mints to target by USERNAME
    r = requests.post(f"{API}/api/wallets/mint",
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"target": target_user, "amount": 500})
    print(f"mint HTTP {r.status_code}: {r.text[:200]}")
    r.raise_for_status()
    tx = r.json()["tx_hash"]
    if not tx.startswith("0x"):
        tx = "0x" + tx
    rcpt = w3.eth.wait_for_transaction_receipt(tx, timeout=30)
    after = bal(target["address"])
    print(f"balance: {before/one} -> {after/one} HUA  (tx status={rcpt.status})")

    minted_ok = rcpt.status == 1 and after == before + 500 * one

    # 2) non-admin (the target user) must be forbidden
    r2 = requests.post(f"{API}/api/wallets/mint",
                       headers={"Authorization": f"Bearer {target_token}"},
                       json={"target": target_user, "amount": 1})
    print(f"non-admin mint HTTP {r2.status_code} (expect 403)")
    forbidden_ok = r2.status_code == 403

    # 3) admin minting to a raw 0x address also works
    rand_addr = "0x" + secrets.token_hex(20)
    r3 = requests.post(f"{API}/api/wallets/mint",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"target": rand_addr, "amount": 10})
    if r3.ok:
        w3.eth.wait_for_transaction_receipt("0x" + r3.json()["tx_hash"].lstrip("0x"), timeout=30)
    addr_ok = r3.ok and bal(rand_addr) == 10 * one
    print(f"mint-by-address HTTP {r3.status_code}  balance={bal(rand_addr)/one} HUA")

    ok = minted_ok and forbidden_ok and addr_ok
    print("E2E_ADMIN_MINT_PASS" if ok else "E2E_ADMIN_MINT_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
