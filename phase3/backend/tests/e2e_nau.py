"""
Real on-chain E2E for NAU balance display (no mocks).

Mint NAU to alice via the NautilusToken owner (mintForTask), then confirm
GET /api/wallets/me returns `nau` matching the on-chain balanceOf.

Run: C:/nautilus-venv/Scripts/python.exe tests/e2e_nau.py
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
NAU = Web3.to_checksum_address(os.getenv("NAU_TOKEN_ADDRESS"))
OWNER_PK = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("BLOCKCHAIN_PRIVATE_KEY")
CHAIN_ID = int(os.getenv("PRIVATE_CHAIN_ID", "13370"))

ABI = [
    {"inputs": [{"name": "agent", "type": "address"}, {"name": "amount", "type": "uint256"},
                {"name": "taskType", "type": "string"}],
     "name": "mintForTask", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]

w3 = Web3(Web3.HTTPProvider(RPC))
nau = w3.eth.contract(address=NAU, abi=ABI)
ONE = 10 ** 18


def login(u, p):
    for _ in range(2):
        r = requests.post(f"{API}/api/auth/login", json={"username": u, "password": p})
        if r.status_code == 429:
            time.sleep(60); continue
        r.raise_for_status()
        d = r.json()
        return d.get("access_token") or d.get("data", {}).get("access_token")


def me(t):
    return requests.get(f"{API}/api/wallets/me", headers={"Authorization": f"Bearer {t}"}).json()


def mint_nau(to_addr, amount):
    owner = w3.eth.account.from_key(OWNER_PK)
    tx = nau.functions.mintForTask(Web3.to_checksum_address(to_addr), amount * ONE, "CODE").build_transaction({
        "from": owner.address, "nonce": w3.eth.get_transaction_count(owner.address, "pending"),
        "gas": 200000, "gasPrice": 0, "chainId": CHAIN_ID,
    })
    signed = w3.eth.account.sign_transaction(tx, OWNER_PK)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(h, timeout=30)


def main():
    token = login("alice", "Test@12345")
    info = me(token)
    addr = info["address"]
    print(f"alice address: {addr}")
    print(f"/me before    : nau={info.get('nau')} hua={info.get('hua')}")

    chain_before = nau.functions.balanceOf(Web3.to_checksum_address(addr)).call() / ONE
    rcpt = mint_nau(addr, 25)
    print(f"minted 25 NAU : tx status={rcpt.status}")
    chain_after = nau.functions.balanceOf(Web3.to_checksum_address(addr)).call() / ONE

    info2 = me(token)
    api_nau = info2.get("nau")
    print(f"chain NAU     : {chain_before} -> {chain_after}")
    print(f"/me after     : nau={api_nau}  hua={info2.get('hua')}  eth={info2.get('eth')}")

    ok = (rcpt.status == 1 and chain_after == chain_before + 25
          and abs(api_nau - chain_after) < 1e-9)
    print("E2E_NAU_PASS" if ok else "E2E_NAU_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
