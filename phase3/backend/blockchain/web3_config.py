"""
Web3 configuration for Base Sepolia (ERC20 payments).

Connects to Base Sepolia testnet, loads TaskContract, RewardContract,
IdentityContract, and ERC20 token contracts (USDC/USDT).
"""
import os
import json
import logging
from typing import Optional
from web3 import Web3

logger = logging.getLogger(__name__)

# --- Network Config ---
# Base Sepolia (default) or Base Mainnet
BLOCKCHAIN_NETWORK = os.getenv("BLOCKCHAIN_NETWORK", "base-sepolia")

NETWORK_CONFIG = {
    # 内网私有链（geth Clique POA）。结算币为华币(HUA)，18 位精度。
    # usdc/usdt 字段同时指向华币地址，以兼容历史上以 "usdc" 为默认 token 的调用路径。
    "privatechain": {
        "rpc_url": os.getenv("PRIVATE_RPC", "http://geth:8545"),
        "chain_id": int(os.getenv("PRIVATE_CHAIN_ID", "13370")),
        "usdc_address": os.getenv("HUA_TOKEN_ADDRESS", ""),
        "usdt_address": "",
        "hua_address": os.getenv("HUA_TOKEN_ADDRESS", ""),
        "token_decimals": 18,
    },
    "base-sepolia": {
        "rpc_url": os.getenv("BASE_SEPOLIA_RPC", "https://sepolia.base.org"),
        "chain_id": 84532,
        "usdc_address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # Circle testnet USDC
        "usdt_address": "",  # No testnet USDT on Base Sepolia yet
    },
    "base": {
        "rpc_url": os.getenv("BASE_RPC", "https://mainnet.base.org"),
        "chain_id": 8453,
        "usdc_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "usdt_address": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
    },
    # Legacy Sepolia support
    "sepolia": {
        "rpc_url": os.getenv("SEPOLIA_RPC_URL", "https://sepolia.infura.io/v3/"),
        "chain_id": 11155111,
        "usdc_address": "",
        "usdt_address": "",
    },
}

# Contract addresses (from deployment)
TASK_CONTRACT_ADDRESS = os.getenv("TASK_CONTRACT_ADDRESS", "")
REWARD_CONTRACT_ADDRESS = os.getenv("REWARD_CONTRACT_ADDRESS", "")
IDENTITY_CONTRACT_ADDRESS = os.getenv("IDENTITY_CONTRACT_ADDRESS", "")

# NAU Token contract (Proof of Useful Work)
NAU_TOKEN_ADDRESS = os.getenv("NAU_TOKEN_ADDRESS", "")

# Private key (for server-side signing: verification engine, etc.)
BLOCKCHAIN_PRIVATE_KEY = os.getenv("BLOCKCHAIN_PRIVATE_KEY", "")


def _load_abi(name: str) -> list:
    """Load ABI from backend/blockchain/abi/{name}.json"""
    abi_dir = os.path.join(os.path.dirname(__file__), "abi")
    path = os.path.join(abi_dir, f"{name}.json")
    try:
        with open(path) as f:
            data = json.load(f)
            return data["abi"] if isinstance(data, dict) and "abi" in data else data
    except FileNotFoundError:
        logger.warning(f"ABI not found: {path}")
        return []


class Web3Config:
    """Web3 connection and contract manager for Base chain."""

    def __init__(self):
        net = NETWORK_CONFIG.get(BLOCKCHAIN_NETWORK, NETWORK_CONFIG["base-sepolia"])
        self.network_name = BLOCKCHAIN_NETWORK
        self.chain_id = net["chain_id"]
        self.usdc_address = net["usdc_address"]
        self.usdt_address = net["usdt_address"]
        # 结算币精度（USDC/USDT=6，华币=18）。用于金额↔最小单位换算。
        self.token_decimals = net.get("token_decimals", 6)
        self.hua_address = net.get("hua_address", "")

        # Connect with validation
        rpc_url = net["rpc_url"]
        if rpc_url.endswith("/v3/") or not rpc_url:
            logger.error(
                f"Invalid RPC URL for {BLOCKCHAIN_NETWORK}: '{rpc_url}' "
                f"(missing API key or empty). Check BASE_SEPOLIA_RPC or SEPOLIA_RPC_URL env vars."
            )
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        # 私有链是 geth Clique POA：区块头 extraData 为 97 字节（含签名），web3.py 默认按
        # 32 字节校验会抛 "extraData is 97 bytes"。注入 POA 中间件后才能解析 POA 区块，
        # 否则任何读区块的操作（如 NAU 铸造走 EIP-1559 取 baseFee 时拉最新区块）都会失败。
        try:
            from web3.middleware import ExtraDataToPOAMiddleware
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception as _poa_exc:  # 公链模式无此问题，注入失败不致命
            logger.debug(f"POA middleware not injected: {_poa_exc}")
        if self.w3.is_connected():
            actual_chain = self.w3.eth.chain_id
            if actual_chain != self.chain_id:
                logger.error(
                    f"Chain ID mismatch! Expected {self.chain_id} ({BLOCKCHAIN_NETWORK}), "
                    f"got {actual_chain}. Wrong RPC URL?"
                )
            else:
                logger.info(f"Connected to {BLOCKCHAIN_NETWORK} (chain {self.chain_id}), RPC: {rpc_url[:50]}...")
        else:
            logger.warning(f"Failed to connect to {BLOCKCHAIN_NETWORK} via {rpc_url[:50]}...")

        # Load contracts
        self.task_contract = self._load_contract("TaskContract", TASK_CONTRACT_ADDRESS)
        self.reward_contract = self._load_contract("RewardContract", REWARD_CONTRACT_ADDRESS)
        self.identity_contract = self._load_contract("IdentityContract", IDENTITY_CONTRACT_ADDRESS)

        # Load ERC20 token contracts
        self.usdc_contract = self._load_contract("IERC20", self.usdc_address) if self.usdc_address else None
        self.usdt_contract = self._load_contract("IERC20", self.usdt_address) if self.usdt_address else None
        # 华币结算合约（内网私有链）
        self.hua_contract = self._load_contract("IERC20", self.hua_address) if self.hua_address else None

        # NAU token contract (platform PoUW token)
        self.nau_contract = self._load_contract("NautilusToken", NAU_TOKEN_ADDRESS) if NAU_TOKEN_ADDRESS else None
        if self.nau_contract:
            logger.info(f"NAU token contract loaded at {NAU_TOKEN_ADDRESS}")
        else:
            logger.warning("NAU_TOKEN_ADDRESS not set — token minting disabled")

    def _load_contract(self, abi_name: str, address: str):
        if not address:
            return None
        try:
            abi = _load_abi(abi_name)
            if not abi:
                return None
            return self.w3.eth.contract(
                address=Web3.to_checksum_address(address),
                abi=abi,
            )
        except Exception as e:
            logger.warning(f"Failed to load {abi_name} at {address}: {e}")
            return None

    def get_account_address(self) -> Optional[str]:
        if BLOCKCHAIN_PRIVATE_KEY:
            return self.w3.eth.account.from_key(BLOCKCHAIN_PRIVATE_KEY).address
        return None

    def get_eth_balance(self, address: str) -> float:
        """Get ETH balance (for gas)."""
        bal = self.w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(self.w3.from_wei(bal, "ether"))

    def get_usdc_balance(self, address: str) -> float:
        """Get USDC balance (6 decimals)."""
        if not self.usdc_contract:
            return 0.0
        raw = self.usdc_contract.functions.balanceOf(
            Web3.to_checksum_address(address)
        ).call()
        return raw / 10 ** self.token_decimals

    def get_usdt_balance(self, address: str) -> float:
        """Get USDT balance (6 decimals)."""
        if not self.usdt_contract:
            return 0.0
        raw = self.usdt_contract.functions.balanceOf(
            Web3.to_checksum_address(address)
        ).call()
        return raw / 10 ** self.token_decimals

    def get_hua_balance(self, address: str) -> float:
        """Get HUA balance (华币, 私有链 18 decimals)."""
        if not self.hua_contract:
            return 0.0
        raw = self.hua_contract.functions.balanceOf(
            Web3.to_checksum_address(address)
        ).call()
        return raw / 10 ** self.token_decimals

    def get_nau_balance(self, address: str) -> float:
        """Get NAU balance (NautilusToken, PoUW reward, 18 decimals)."""
        if not self.nau_contract:
            return 0.0
        raw = self.nau_contract.functions.balanceOf(
            Web3.to_checksum_address(address)
        ).call()
        return raw / 10 ** 18

    def get_token_contract(self, token: str):
        """Get ERC20 contract by token name ('usdc', 'usdt' or 'hua')."""
        t = token.lower()
        if t == "usdc":
            return self.usdc_contract
        elif t == "usdt":
            return self.usdt_contract
        elif t == "hua":
            return self.hua_contract
        return None

    def get_token_address(self, token: str) -> str:
        """Get token contract address by name."""
        t = token.lower()
        if t == "usdc":
            return self.usdc_address
        elif t == "usdt":
            return self.usdt_address
        elif t == "hua":
            return self.hua_address
        return ""

    def is_connected(self) -> bool:
        return self.w3 is not None and self.w3.is_connected()


# Singleton
_config: Optional[Web3Config] = None


def get_web3_config() -> Web3Config:
    global _config
    if _config is None:
        _config = Web3Config()
    return _config


def get_w3() -> Web3:
    return get_web3_config().w3


# Legacy aliases for backward compatibility
def get_task_market():
    return get_web3_config().task_contract


def get_reward_pool():
    return get_web3_config().reward_contract


def get_agent_registry():
    return get_web3_config().identity_contract


def init_web3():
    return get_web3_config()
