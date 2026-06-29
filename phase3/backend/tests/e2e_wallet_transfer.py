"""
Real on-chain E2E for custodial 华币 transfer (no mocks).

Flow: login -> GET /api/wallets/me (provision + balances) -> mint HUA to payer
(owner key) -> POST /api/wallets/{id}/transfer to a freshly-registered
recipient -> assert on-chain balanceOf changed for both sides.

Requires: geth at PRIVATE_RPC (chainId 13370) and backend at API.
Run: C:/nautilus-venv/Scripts/python.exe tests/e2e_wallet_transfer.py
"""
import os
import sys
import time
import secrets

import requests
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
OWNER_PK = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("BLOCKCHAIN_PRIVATE_KEY")
CHAIN_ID = int(os.getenv("PRIVATE_CHAIN_ID", "13370"))

HUA_ABI = [
    {"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "mint", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]

w3 = Web3(Web3.HTTPProvider(RPC))
hua = w3.eth.contract(address=HUA, abi=HUA_ABI)
assert w3.is_connected(), "geth not reachable"


def _token(username, password):
    requests.post(f"{API}/api/auth/register",
                  json={"username": username, "email": f"{username}@nautilus-corp.com",
                        "password": password})
    r = requests.post(f"{API}/api/auth/login",
                      json={"username": username, "password": password})
    r.raise_for_status()
    d = r.json()
    return d.get("access_token") or d.get("data", {}).get("access_token")


def _me(token):
    r = requests.get(f"{API}/api/wallets/me",
                     headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def _mint(to_addr, amount_wei):
    owner = w3.eth.account.from_key(OWNER_PK)
    tx = hua.functions.mint(Web3.to_checksum_address(to_addr), amount_wei).build_transaction({
        "from": owner.address,
        "nonce": w3.eth.get_transaction_count(owner.address),
        "gas": 120000, "gasPrice": 0, "chainId": CHAIN_ID,
    })
    signed = w3.eth.account.sign_transaction(tx, OWNER_PK)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(h, timeout=30)


def bal(addr):
    return hua.functions.balanceOf(Web3.to_checksum_address(addr)).call()


def main():
    suffix = secrets.token_hex(3)
    payer_user = f"e2e_payer_{suffix}"
    recip_user = f"e2e_recip_{suffix}"

    payer_token = _token(payer_user, "Verify@12345")
    recip_token = _token(recip_user, "Verify@12345")

    payer = _me(payer_token)
    recip = _me(recip_token)
    print(f"payer  : {payer['address']} wallet_id={payer['wallet_id']}")
    print(f"recip  : {recip['address']}")

    one = 10 ** 18
    _mint(payer["address"], 1000 * one)
    p0, r0 = bal(payer["address"]), bal(recip["address"])
    print(f"before : payer={p0/one} HUA  recip={r0/one} HUA")

    resp = requests.post(
        f"{API}/api/wallets/{payer['wallet_id']}/transfer",
        headers={"Authorization": f"Bearer {payer_token}"},
        json={"to_address": recip["address"], "amount": 100},
    )
    print(f"transfer HTTP {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    tx_hash = resp.json()["tx_hash"]
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    print(f"tx     : {tx_hash} status={rcpt.status} block={rcpt.blockNumber}")

    p1, r1 = bal(payer["address"]), bal(recip["address"])
    print(f"after  : payer={p1/one} HUA  recip={r1/one} HUA")

    ok = (rcpt.status == 1 and p1 == p0 - 100 * one and r1 == r0 + 100 * one)
    # Also confirm the API balance view reflects on-chain state.
    api_bal = _me(payer_token)["hua"]
    print(f"api /me payer hua = {api_bal}")
    ok = ok and abs(api_bal - p1 / one) < 1e-9

    # Regression for the false-success fix: an over-balance transfer must be
    # rejected with 400 (pre-flight eth_call), not 200 + a silently-reverting tx.
    over = (p1 // one) + 1000
    resp2 = requests.post(
        f"{API}/api/wallets/{payer['wallet_id']}/transfer",
        headers={"Authorization": f"Bearer {payer_token}"},
        json={"to_address": recip["address"], "amount": over},
    )
    p2 = bal(payer["address"])
    print(f"over-balance transfer HTTP {resp2.status_code} (expect 400); payer unchanged: {p2 == p1}")
    ok = ok and resp2.status_code == 400 and p2 == p1

    print("E2E_TRANSFER_PASS" if ok else "E2E_TRANSFER_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
