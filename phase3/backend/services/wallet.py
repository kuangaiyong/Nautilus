"""
Wallet — Unified wallet management for Nautilus.

Merges:
- key_encryption_provider.py (AES-256-GCM encryption with HKDF)
- wallet_issuer_service.py  (HD wallet creation, import, signing)
- web3auth_service.py       (Web3Auth JWT verification)

Public API:
    KeyEncryptionProvider / LocalKeyEncryptionProvider
    WalletIssuerService
    Web3AuthService / get_web3auth_service()
"""
import base64
import logging
import os
import re
import secrets
import uuid
from abc import ABC, abstractmethod
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

_NONCE_LENGTH = 12
_KEY_LENGTH = 32
_TAG_LENGTH = 16
_DEFAULT_DERIVATION_PATH = "m/44'/60'/0'/0/0"


# ---------------------------------------------------------------------------
# Key Encryption
# ---------------------------------------------------------------------------

class KeyEncryptionProvider(ABC):
    """Abstract base class for key encryption."""

    @abstractmethod
    def encrypt(self, plaintext: bytes, address: str) -> bytes:
        """Encrypt plaintext bytes using address as salt."""

    @abstractmethod
    def decrypt(self, ciphertext: bytes, address: str) -> bytes:
        """Decrypt ciphertext bytes using address as salt."""


class LocalKeyEncryptionProvider(KeyEncryptionProvider):
    """AES-256-GCM encryption with HKDF-SHA256 per-wallet key derivation."""

    def __init__(self) -> None:
        self._master_key = self._load_master_key()

    def _load_master_key(self) -> bytes:
        raw = os.getenv("WALLET_MASTER_KEY")
        if raw:
            key = base64.b64decode(raw)
            if len(key) != _KEY_LENGTH:
                raise ValueError(
                    f"WALLET_MASTER_KEY must be {_KEY_LENGTH} bytes (base64), got {len(key)}"
                )
            logger.info("Master encryption key loaded from environment")
            return key
        # 未设主密钥：非开发环境必须 fail-fast。随机生成会使重启后既有托管私钥永久
        # 无法解密、华币资金锁死，绝不能在生产/内网静默发生。
        env = os.getenv("ENVIRONMENT", "development").lower()
        if env not in ("development", "dev", "test", "testing"):
            raise RuntimeError(
                "WALLET_MASTER_KEY 未设置：拒绝启动。生产/内网必须配置固定主密钥并妥善备份，"
                "否则重启后所有托管钱包私钥将无法解密、华币资金锁死。"
            )
        key = secrets.token_bytes(_KEY_LENGTH)
        logger.warning("WALLET_MASTER_KEY not set — random key for %s mode only", env)
        return key

    def derive_key(self, address: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=_KEY_LENGTH,
            salt=address.lower().encode("utf-8"), info=b"nautilus-wallet-key",
        ).derive(self._master_key)

    def encrypt(self, plaintext: bytes, address: str) -> bytes:
        derived = self.derive_key(address)
        nonce = secrets.token_bytes(_NONCE_LENGTH)
        return nonce + AESGCM(derived).encrypt(nonce, plaintext, None)

    def decrypt(self, ciphertext: bytes, address: str) -> bytes:
        if len(ciphertext) < _NONCE_LENGTH + _TAG_LENGTH:
            raise ValueError(f"Ciphertext too short: {len(ciphertext)} bytes")
        nonce = ciphertext[:_NONCE_LENGTH]
        derived = self.derive_key(address)
        try:
            return AESGCM(derived).decrypt(nonce, ciphertext[_NONCE_LENGTH:], None)
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}") from e


# ---------------------------------------------------------------------------
# Wallet Issuance & Management
# ---------------------------------------------------------------------------

class WalletIssuerService:
    """HD wallet creation, import, signing, and balance queries.

    Heavy dependencies (mnemonic, eth_account, bcrypt, sqlalchemy) are
    imported lazily so the module can load even when they're absent.
    """

    def __init__(self, db, encryption_provider: KeyEncryptionProvider) -> None:
        self._db = db
        self._encryption = encryption_provider

    async def create_wallet(
        self, wallet_type: str = "agent",
        agent_id: Optional[int] = None, user_id: Optional[int] = None,
    ) -> dict:
        """Create a new HD wallet. Returns wallet_id, address, mnemonic (one-time)."""
        from mnemonic import Mnemonic
        from eth_account import Account
        from models.database import Wallet
        Account.enable_unaudited_hdwallet_features()

        mnemonic_phrase = Mnemonic("english").generate(strength=128)
        acct = Account.from_mnemonic(mnemonic_phrase, account_path=_DEFAULT_DERIVATION_PATH)
        address = acct.address.lower()

        encrypted_key = self._encryption.encrypt(acct.key, address)
        wallet_id = str(uuid.uuid4())
        self._db.add(Wallet(
            wallet_id=wallet_id, public_address=address,
            encrypted_private_key=base64.b64encode(encrypted_key).decode("utf-8"),
            mnemonic_hash=_hash_mnemonic(mnemonic_phrase),
            derivation_path=_DEFAULT_DERIVATION_PATH, key_version=1,
            agent_id=agent_id, user_id=user_id,
            wallet_type=wallet_type, activation_status="created",
        ))
        await self._db.commit()
        logger.info("Wallet created: %s address=%s type=%s", wallet_id, address, wallet_type)
        return {"wallet_id": wallet_id, "address": address,
                "mnemonic": mnemonic_phrase, "derivation_path": _DEFAULT_DERIVATION_PATH}

    async def get_wallet(self, wallet_id: str):
        from sqlalchemy import select
        from models.database import Wallet
        result = await self._db.execute(select(Wallet).where(Wallet.wallet_id == wallet_id))
        return result.scalar_one_or_none()

    async def get_wallet_by_address(self, address: str):
        from sqlalchemy import select
        from models.database import Wallet
        result = await self._db.execute(
            select(Wallet).where(Wallet.public_address == address.lower())
        )
        return result.scalar_one_or_none()

    async def import_wallet(
        self, mnemonic_phrase: str,
        derivation_path: str = _DEFAULT_DERIVATION_PATH,
        wallet_type: str = "agent",
        agent_id: Optional[int] = None, user_id: Optional[int] = None,
    ) -> dict:
        """Import a wallet from an existing mnemonic."""
        from mnemonic import Mnemonic
        from eth_account import Account
        from models.database import Wallet
        Account.enable_unaudited_hdwallet_features()

        if not Mnemonic("english").check(mnemonic_phrase):
            raise ValueError("Invalid BIP39 mnemonic")

        acct = Account.from_mnemonic(mnemonic_phrase, account_path=derivation_path)
        address = acct.address.lower()

        if await self.get_wallet_by_address(address) is not None:
            raise ValueError(f"Wallet already exists for address {address}")

        encrypted_key = self._encryption.encrypt(acct.key, address)
        wallet_id = str(uuid.uuid4())
        self._db.add(Wallet(
            wallet_id=wallet_id, public_address=address,
            encrypted_private_key=base64.b64encode(encrypted_key).decode("utf-8"),
            mnemonic_hash=_hash_mnemonic(mnemonic_phrase),
            derivation_path=derivation_path, key_version=1,
            agent_id=agent_id, user_id=user_id,
            wallet_type=wallet_type, activation_status="created",
        ))
        await self._db.commit()
        logger.info("Wallet imported: %s address=%s", wallet_id, address)
        return {"wallet_id": wallet_id, "address": address, "derivation_path": derivation_path}

    async def sign_message(self, wallet_id: str, message: str) -> str:
        """Sign a message with the wallet's private key."""
        from eth_account import Account
        from eth_account.messages import encode_defunct
        wallet = await self.get_wallet(wallet_id)
        if wallet is None:
            raise ValueError(f"Wallet not found: {wallet_id}")
        pk = self._decrypt_private_key(wallet)
        try:
            return Account.sign_message(encode_defunct(text=message), private_key=bytes(pk)).signature.hex()
        finally:
            _zero_bytes(pk)

    async def sign_transaction(self, wallet_id: str, tx_dict: dict) -> str:
        """Sign a transaction with the wallet's private key."""
        from eth_account import Account
        wallet = await self.get_wallet(wallet_id)
        if wallet is None:
            raise ValueError(f"Wallet not found: {wallet_id}")
        pk = self._decrypt_private_key(wallet)
        try:
            return Account.sign_transaction(tx_dict, private_key=bytes(pk)).raw_transaction.hex()
        finally:
            _zero_bytes(pk)

    async def get_balance(self, address: str) -> dict:
        """Query ETH, HUA (华币), USDC, and USDT balances."""
        from blockchain.web3_config import get_web3_config
        config = get_web3_config()
        return {
            "address": address.lower(),
            "eth": config.get_eth_balance(address),
            "hua": config.get_hua_balance(address),
            "usdc": config.get_usdc_balance(address),
            "usdt": config.get_usdt_balance(address),
        }

    def _decrypt_private_key(self, wallet) -> bytearray:
        # bytearray（可变）以便用后 _zero_bytes 真正清零明文私钥（bytes 不可变，清零为 no-op）。
        return bytearray(self._encryption.decrypt(
            base64.b64decode(wallet.encrypted_private_key), wallet.public_address,
        ))


# ---------------------------------------------------------------------------
# Web3Auth JWT Verification
# ---------------------------------------------------------------------------

class Web3AuthService:
    """Verifies JWTs issued by Web3Auth (Google, GitHub, email login)."""

    def __init__(self):
        import jwt as _jwt
        from jwt import PyJWKClient
        self._jwt = _jwt
        self._jwks_url = os.getenv(
            "WEB3AUTH_JWKS_URL", "https://api-auth.web3auth.io/.well-known/jwks.json",
        )
        self._client_id = os.getenv("WEB3AUTH_CLIENT_ID", "")
        self._jwks_client = PyJWKClient(self._jwks_url, cache_jwk_set=True, lifespan=3600)

    def verify_token(self, token: str) -> dict:
        """Verify a Web3Auth JWT. Returns wallet_address, email, name, etc."""
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        decoded = self._jwt.decode(
            token, signing_key.key, algorithms=["ES256"],
            audience=self._client_id, options={"verify_iat": True},
        )

        wallets = decoded.get("wallets", [])
        raw_address = wallets[0].get("address", "") if wallets else ""
        wallet_address = raw_address.lower() if raw_address else ""

        if wallet_address and not re.match(r"^0x[a-f0-9]{40}$", wallet_address):
            if re.match(r"^[a-f0-9]{40}$", wallet_address):
                wallet_address = f"0x{wallet_address}"

        return {
            "wallet_address": wallet_address,
            "email": decoded.get("email"),
            "name": decoded.get("name"),
            "provider": decoded.get("verifier"),
            "app_pub_key": decoded.get("app_pub_key"),
            "iat": decoded.get("iat"),
            "exp": decoded.get("exp"),
        }


# ---------------------------------------------------------------------------
# Helpers & Singletons
# ---------------------------------------------------------------------------

def _hash_mnemonic(mnemonic_phrase: str) -> str:
    import bcrypt
    # bcrypt only considers the first 72 bytes; truncate to stay within its hard limit.
    return bcrypt.hashpw(mnemonic_phrase.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


_local_encryption: Optional[LocalKeyEncryptionProvider] = None


def _get_local_encryption() -> LocalKeyEncryptionProvider:
    global _local_encryption
    if _local_encryption is None:
        _local_encryption = LocalKeyEncryptionProvider()
    return _local_encryption


def ensure_user_wallet(db, user, wallet_type: str = "user") -> str:
    """Ensure `user` has a custodial wallet; create one if missing.

    Synchronous counterpart to WalletIssuerService.create_wallet for use in
    sync request handlers (e.g. task publishing). Generates an HD wallet,
    stores the AES-encrypted private key, back-fills user.wallet_address, and
    returns the lower-cased address. Idempotent: returns the existing address
    when the user already has one.
    """
    if user.wallet_address:
        return user.wallet_address

    from mnemonic import Mnemonic
    from eth_account import Account
    from models.database import Wallet
    Account.enable_unaudited_hdwallet_features()

    mnemonic_phrase = Mnemonic("english").generate(strength=128)
    acct = Account.from_mnemonic(mnemonic_phrase, account_path=_DEFAULT_DERIVATION_PATH)
    address = acct.address.lower()

    encrypted_key = _get_local_encryption().encrypt(acct.key, address)
    db.add(Wallet(
        wallet_id=str(uuid.uuid4()), public_address=address,
        encrypted_private_key=base64.b64encode(encrypted_key).decode("utf-8"),
        mnemonic_hash=_hash_mnemonic(mnemonic_phrase),
        derivation_path=_DEFAULT_DERIVATION_PATH, key_version=1,
        user_id=user.id, wallet_type=wallet_type, activation_status="created",
    ))
    user.wallet_address = address
    db.commit()
    logger.info("Custodial wallet provisioned for user_id=%s address=%s", user.id, address)
    return address


def sign_and_broadcast_transaction(
    db, wallet_public_address: str, built_tx: dict, timeout_seconds: int = 60
) -> tuple:
    """Sign and broadcast a transaction using the user's custodial wallet private key.

    Handles:
    - Private key decryption from encrypted storage
    - Transaction signing with nonce lock to prevent race conditions
    - Raw transaction broadcast
    - Receipt polling with timeout
    - Secure private key zeroing

    Args:
        db: Database session
        wallet_public_address: Public address of the custodial wallet
        built_tx: Transaction dict (from web3 contract.functions.xxx.build_transaction())
        timeout_seconds: Receipt polling timeout

    Returns:
        tuple: (tx_hash_hex, receipt) or raises ValueError on failure

    Raises:
        ValueError: If wallet not found, decryption fails, or tx reverts
    """
    from web3 import Web3
    from blockchain.web3_config import get_web3_config
    from models.database import Wallet
    from services.nonce_lock import account_nonce_lock

    config = get_web3_config()
    w3 = config.w3
    from_addr = Web3.to_checksum_address(wallet_public_address)

    wallet = (
        db.query(Wallet)
        .filter(Wallet.public_address == from_addr, Wallet.encrypted_private_key.isnot(None))
        .first()
    )
    if wallet is None:
        raise ValueError(f"Custodial wallet {wallet_public_address} not found or has no encrypted key")

    pk = bytearray(_get_local_encryption().decrypt(
        base64.b64decode(wallet.encrypted_private_key), wallet.public_address
    ))
    try:
        # Serialize nonce acquisition and broadcast for the same account
        with account_nonce_lock(from_addr):
            signed = w3.eth.account.sign_transaction(built_tx, bytes(pk))
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    finally:
        _zero_bytes(pk)  # Zero the plaintext private key

    # Wait for receipt
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    if receipt["status"] != 1:
        raise ValueError(f"Transaction reverted on-chain (status=0, tx={tx_hash.hex() if hasattr(tx_hash, 'hex') else tx_hash})")

    return tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash), receipt


def pay_hua_from_custodial(db, from_user, to_address: str, amount_units: int) -> str:
    """Transfer `amount_units` (HUA smallest units, 18 decimals) from a user's
    custodial wallet to `to_address` on the private chain.

    Used to settle task rewards: the publisher's own custodial key signs an
    ERC-20 transfer (gasPrice 0, no ETH needed). Raises ValueError on any
    failure (no custodial wallet / would revert / tx reverted) so callers can
    surface a clear error and avoid marking a task paid when it wasn't.
    """
    from web3 import Web3
    from blockchain.web3_config import get_web3_config
    from models.database import Wallet

    config = get_web3_config()
    if config.hua_contract is None:
        raise ValueError("华币合约未配置或链不可用")

    wallet = (
        db.query(Wallet)
        .filter(Wallet.user_id == from_user.id, Wallet.encrypted_private_key.isnot(None))
        .order_by(Wallet.created_at.desc())
        .first()
    )
    if wallet is None or not wallet.encrypted_private_key:
        raise ValueError("发布方没有可代签的平台托管钱包，无法支付奖励")

    w3 = config.w3
    from_addr = Web3.to_checksum_address(wallet.public_address)
    to_addr = Web3.to_checksum_address(to_address)

    # Pre-flight via eth_call so an insufficient-balance transfer surfaces as a
    # clear error instead of broadcasting a tx that silently reverts on-chain.
    try:
        config.hua_contract.functions.transfer(to_addr, amount_units).call({"from": from_addr})
    except Exception as exc:
        raise ValueError(f"奖励支付无法完成（余额不足或被合约拒绝）：{exc}") from exc

    # Build transaction and use shared signing/broadcast function
    tx = config.hua_contract.functions.transfer(to_addr, amount_units).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr, "pending"),
        "gas": 100000,
        "gasPrice": 0,
        "chainId": config.chain_id,
    })

    tx_hash, _ = sign_and_broadcast_transaction(db, wallet.public_address, tx, timeout_seconds=60)
    return tx_hash


def _zero_bytes(data: bytes) -> None:
    if isinstance(data, bytearray):
        for i in range(len(data)):
            data[i] = 0


_web3auth_service: Optional[Web3AuthService] = None


def get_web3auth_service() -> Web3AuthService:
    global _web3auth_service
    if _web3auth_service is None:
        _web3auth_service = Web3AuthService()
    return _web3auth_service
