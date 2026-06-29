"""
Admin helper: mint 华币 (HUA) to an address on the private chain.

华币是 onlyOwner 铸造（无公开水龙头）。本脚本用部署者(owner)私钥铸币，
仅用于测试/运维时给账户发放华币。

Usage:
  C:/nautilus-venv/Scripts/python.exe tests/mint_hua.py <address> <amount_in_HUA>
  # 只查余额（不铸币）:
  C:/nautilus-venv/Scripts/python.exe tests/mint_hua.py <address>
"""
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
OWNER_PK = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("BLOCKCHAIN_PRIVATE_KEY")
CHAIN_ID = int(os.getenv("PRIVATE_CHAIN_ID", "13370"))

ABI = [
    {"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "mint", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


def main():
    if len(sys.argv) not in (2, 3):
        print("usage: mint_hua.py <address> [amount_in_HUA]")
        sys.exit(2)

    to = Web3.to_checksum_address(sys.argv[1])
    w3 = Web3(Web3.HTTPProvider(RPC))
    hua = w3.eth.contract(address=HUA, abi=ABI)

    def balance():
        return hua.functions.balanceOf(to).call() / 10 ** 18

    if len(sys.argv) == 2:  # query only
        print(f"{to} balance: {balance()} HUA")
        return

    amount = float(sys.argv[2])
    owner = w3.eth.account.from_key(OWNER_PK)
    tx = hua.functions.mint(to, w3.to_wei(amount, "ether")).build_transaction({
        "from": owner.address,
        "nonce": w3.eth.get_transaction_count(owner.address),
        "gas": 120000, "gasPrice": 0, "chainId": CHAIN_ID,
    })
    signed = w3.eth.account.sign_transaction(tx, OWNER_PK)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=30)
    tx_hash = h.hex() if hasattr(h, "hex") else str(h)
    print(f"minted {amount} HUA -> {to}")
    print(f"  tx={tx_hash} status={rcpt.status} block={rcpt.blockNumber}")
    print(f"  balance now: {balance()} HUA")


if __name__ == "__main__":
    main()
