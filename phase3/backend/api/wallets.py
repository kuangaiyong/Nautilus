"""Wallet management API endpoints."""
import base64
import logging
import os
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from sqlalchemy import select

from models.database import User, Wallet
from utils.database import get_db
from utils.auth import get_current_user, get_current_admin_user
from services.key_encryption_provider import LocalKeyEncryptionProvider

logger = logging.getLogger(__name__)

router = APIRouter()

TESTING = os.getenv("TESTING", "false").lower() == "true"
limiter = Limiter(key_func=get_remote_address, enabled=not TESTING)

# Module-level encryption provider (singleton)
_encryption_provider = LocalKeyEncryptionProvider()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CreateWalletRequest(BaseModel):
    """Request to create a new custodial wallet."""
    wallet_type: str = Field(default="agent", pattern="^(agent|user)$")


class CreateWalletResponse(BaseModel):
    """Response after wallet creation (mnemonic shown once)."""
    wallet_id: str
    address: str
    mnemonic: str
    derivation_path: str


class ImportWalletRequest(BaseModel):
    """Request to import wallet from mnemonic."""
    mnemonic: str = Field(..., min_length=10)
    derivation_path: str = Field(default="m/44'/60'/0'/0/0")


class ImportWalletResponse(BaseModel):
    """Response after wallet import."""
    wallet_id: str
    address: str
    derivation_path: str


class WalletBalanceResponse(BaseModel):
    """Wallet balance across token types."""
    address: str
    eth: float
    usdc: float
    usdt: float


class SignMessageRequest(BaseModel):
    """Request to sign a message with wallet key."""
    message: str = Field(..., min_length=1, max_length=4096)


class SignMessageResponse(BaseModel):
    """Signed message result."""
    wallet_id: str
    signature: str


class WalletSummary(BaseModel):
    """Wallet info without sensitive fields."""
    wallet_id: str
    address: str
    wallet_type: str
    activation_status: str
    derivation_path: str


class WalletOverviewResponse(BaseModel):
    """Current user's primary custodial wallet with on-chain balances."""
    wallet_id: str
    address: str
    eth: float
    hua: float
    nau: float


class TransferRequest(BaseModel):
    """Request to transfer 华币 (HUA) to another address."""
    to_address: str = Field(..., min_length=42, max_length=42)
    amount: float = Field(..., gt=0)


class TransferResponse(BaseModel):
    """Result of a 华币 transfer."""
    tx_hash: str
    from_address: str
    to_address: str
    amount: float


class MintRequest(BaseModel):
    """Admin request to mint 华币. target = username or 0x-address."""
    target: str = Field(..., min_length=3)
    amount: float = Field(..., gt=0)


class MintResponse(BaseModel):
    """Result of an admin 华币 mint."""
    tx_hash: str
    to_address: str
    amount: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_wallet_service(db: Session):
    """
    Create a WalletIssuerService backed by a sync session.

    WalletIssuerService was designed for AsyncSession but the rest of the
    app uses synchronous SQLAlchemy.  We import it lazily and wrap the sync
    session so callers can still ``await`` its methods (SQLAlchemy sync
    sessions silently support this when running inside ``asyncio``).
    """
    from services.wallet_issuer_service import WalletIssuerService
    return WalletIssuerService(db, _encryption_provider)  # type: ignore[arg-type]


def _assert_wallet_owner(wallet: Wallet, current_user: User) -> None:
    """Raise 403 if the wallet does not belong to the current user."""
    if wallet.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "WALLET_ACCESS_DENIED",
                    "message": "You do not own this wallet",
                }
            },
        )


def _sign_and_send_local(w3, tx: dict, private_key) -> str:
    """Sign a tx with a local key and broadcast it; return the tx hash hex.

    Distinct from blockchain_service._sign_and_send, which always uses the
    platform key — here the caller supplies the signing key (a user's custodial
    key, or the contract owner key for mint).
    """
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/create",
    response_model=CreateWalletResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("3/hour")
async def create_wallet(
    request: Request,
    body: CreateWalletRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new HD wallet for the current user.

    The mnemonic is returned **once** and never stored in plaintext.
    Rate limit: 3 per hour.
    """
    svc = _get_wallet_service(db)
    try:
        result = await svc.create_wallet(
            wallet_type=body.wallet_type,
            user_id=current_user.id,
        )
    except Exception as exc:
        logger.error("Wallet creation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "WALLET_CREATION_FAILED",
                    "message": "Failed to create wallet",
                    "details": {"reason": str(exc)},
                }
            },
        )

    return CreateWalletResponse(
        wallet_id=result["wallet_id"],
        address=result["address"],
        mnemonic=result["mnemonic"],
        derivation_path=result["derivation_path"],
    )


@router.get(
    "/{wallet_id}/balance",
    response_model=WalletBalanceResponse,
)
@limiter.limit("30/minute")
async def get_wallet_balance(
    request: Request,
    wallet_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get ETH, USDC and USDT balances for a wallet.

    Only the wallet owner can query the balance.
    """
    svc = _get_wallet_service(db)
    wallet = await svc.get_wallet(wallet_id)

    if wallet is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "WALLET_NOT_FOUND",
                    "message": "Wallet not found",
                    "details": {"wallet_id": wallet_id},
                }
            },
        )

    _assert_wallet_owner(wallet, current_user)

    try:
        balance = await svc.get_balance(wallet.public_address)
    except Exception as exc:
        logger.error("Balance query failed for %s: %s", wallet_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": {
                    "code": "BALANCE_QUERY_FAILED",
                    "message": "Failed to query on-chain balance",
                    "details": {"reason": str(exc)},
                }
            },
        )

    return WalletBalanceResponse(
        address=balance["address"],
        eth=balance["eth"],
        usdc=balance["usdc"],
        usdt=balance["usdt"],
    )


@router.post(
    "/import",
    response_model=ImportWalletResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("3/hour")
async def import_wallet(
    request: Request,
    body: ImportWalletRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Import a wallet from an existing BIP39 mnemonic.

    Rate limit: 3 per hour.
    """
    svc = _get_wallet_service(db)
    try:
        result = await svc.import_wallet(
            mnemonic_phrase=body.mnemonic,
            derivation_path=body.derivation_path,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "WALLET_IMPORT_FAILED",
                    "message": str(exc),
                    "details": {},
                }
            },
        )
    except Exception as exc:
        logger.error("Wallet import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "WALLET_IMPORT_ERROR",
                    "message": "Failed to import wallet",
                    "details": {"reason": str(exc)},
                }
            },
        )

    return ImportWalletResponse(
        wallet_id=result["wallet_id"],
        address=result["address"],
        derivation_path=result["derivation_path"],
    )


@router.post(
    "/{wallet_id}/sign",
    response_model=SignMessageResponse,
)
@limiter.limit("10/minute")
async def sign_message(
    request: Request,
    wallet_id: str,
    body: SignMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Sign a plaintext message with the wallet's private key.

    Only the wallet owner can sign. Rate limit: 10 per minute.
    """
    svc = _get_wallet_service(db)
    wallet = await svc.get_wallet(wallet_id)

    if wallet is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "WALLET_NOT_FOUND",
                    "message": "Wallet not found",
                    "details": {"wallet_id": wallet_id},
                }
            },
        )

    _assert_wallet_owner(wallet, current_user)

    try:
        signature = await svc.sign_message(wallet_id, body.message)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "SIGNING_FAILED",
                    "message": str(exc),
                    "details": {},
                }
            },
        )
    except Exception as exc:
        logger.error("Signing failed for wallet %s: %s", wallet_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "SIGNING_ERROR",
                    "message": "Failed to sign message",
                    "details": {"reason": str(exc)},
                }
            },
        )

    return SignMessageResponse(wallet_id=wallet_id, signature=signature)


@router.get(
    "/my",
    response_model=List[WalletSummary],
)
@limiter.limit("30/minute")
async def list_my_wallets(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all wallets owned by the current user.

    Private keys and mnemonic hashes are never included.
    """
    wallets = (
        db.query(Wallet)
        .filter(Wallet.user_id == current_user.id)
        .order_by(Wallet.created_at.desc())
        .all()
    )

    return [
        WalletSummary(
            wallet_id=w.wallet_id,
            address=w.public_address,
            wallet_type=w.wallet_type,
            activation_status=w.activation_status,
            derivation_path=w.derivation_path,
        )
        for w in wallets
    ]


@router.get(
    "/me",
    response_model=WalletOverviewResponse,
)
@limiter.limit("30/minute")
def get_my_wallet(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the current user's primary custodial wallet with ETH (gas) and 华币
    balances. Provisions a wallet on first access (idempotent) so every
    logged-in user has one. Balances default to 0 if the chain is unreachable.
    """
    from services.wallet import ensure_user_wallet
    from blockchain.web3_config import get_web3_config

    ensure_user_wallet(db, current_user)
    # Resolve by user_id (not address): a user whose wallet_address was set
    # externally (e.g. legacy wallet-login) has no custodial row, and
    # ensure_user_wallet returns early without creating one.
    wallet = (
        db.query(Wallet)
        .filter(Wallet.user_id == current_user.id)
        .order_by(Wallet.created_at.desc())
        .first()
    )
    if wallet is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "NO_CUSTODIAL_WALLET",
                              "message": "当前账户没有平台托管钱包，无法展示托管余额"}},
        )

    address = wallet.public_address
    eth = hua = nau = 0.0
    try:
        config = get_web3_config()
        eth = config.get_eth_balance(address)
        hua = config.get_hua_balance(address)
        nau = config.get_nau_balance(address)
    except Exception as exc:
        logger.warning("Balance query failed for %s: %s", address, exc)

    return WalletOverviewResponse(
        wallet_id=wallet.wallet_id, address=address, eth=eth, hua=hua, nau=nau
    )


@router.post(
    "/{wallet_id}/transfer",
    response_model=TransferResponse,
)
@limiter.limit("10/minute")
def transfer_hua(
    request: Request,
    wallet_id: str,
    body: TransferRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Transfer 华币 (HUA) from the owner's custodial wallet to another address.

    The platform decrypts the wallet's own key, signs an ERC-20 ``transfer``
    and broadcasts it to the private chain (gasPrice 0, no ETH needed).
    Owner-only. Rate limit: 10 per minute.
    """
    from web3 import Web3
    from blockchain.web3_config import get_web3_config
    from services.wallet import _get_local_encryption

    wallet = db.query(Wallet).filter(Wallet.wallet_id == wallet_id).first()
    if wallet is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "WALLET_NOT_FOUND",
                              "message": "Wallet not found",
                              "details": {"wallet_id": wallet_id}}},
        )
    _assert_wallet_owner(wallet, current_user)

    if not Web3.is_address(body.to_address):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_ADDRESS",
                              "message": "Invalid recipient address"}},
        )

    config = get_web3_config()
    if config.hua_contract is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "HUA_UNAVAILABLE",
                              "message": "华币合约未配置或链不可用"}},
        )

    if not wallet.encrypted_private_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "NON_CUSTODIAL_WALLET",
                              "message": "该钱包非平台托管，无法代签转账"}},
        )

    w3 = config.w3
    from_addr = Web3.to_checksum_address(wallet.public_address)
    to_addr = Web3.to_checksum_address(body.to_address)
    # 华币为 18 位精度，与 ether 同量级，可直接用 to_wei 换算最小单位。
    amount_units = w3.to_wei(Decimal(str(body.amount)), "ether")

    # Pre-flight via eth_call so a transfer that would revert (e.g. insufficient
    # balance) surfaces as 400 — otherwise the tx is broadcast, silently reverts
    # on-chain (status 0), and the API would falsely report success.
    try:
        config.hua_contract.functions.transfer(to_addr, amount_units).call({"from": from_addr})
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "TRANSFER_WOULD_REVERT",
                              "message": "转账无法完成（余额不足或被合约拒绝）",
                              "details": {"reason": str(exc)}}},
        )

    try:
        tx = config.hua_contract.functions.transfer(to_addr, amount_units).build_transaction({
            "from": from_addr,
            "nonce": w3.eth.get_transaction_count(from_addr, "pending"),
            "gas": 100000,
            "gasPrice": 0,
            "chainId": config.chain_id,
        })
        # Decrypt with the same provider singleton that ensure_user_wallet used
        # to encrypt, so this works even when WALLET_MASTER_KEY is unset (dev).
        pk = _get_local_encryption().decrypt(
            base64.b64decode(wallet.encrypted_private_key), wallet.public_address
        )
        tx_hash_hex = _sign_and_send_local(w3, tx, pk)
    except Exception as exc:
        logger.error("HUA transfer failed for wallet %s: %s", wallet_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "TRANSFER_FAILED",
                              "message": "华币转账失败",
                              "details": {"reason": str(exc)}}},
        )

    return TransferResponse(
        tx_hash=tx_hash_hex,
        from_address=from_addr.lower(),
        to_address=to_addr.lower(),
        amount=body.amount,
    )


# Minimal ABI for the onlyOwner mint(to, amount) entrypoint on HuaCoin.
_MINT_ABI = [{
    "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
    "name": "mint", "outputs": [], "stateMutability": "nonpayable", "type": "function",
}]


@router.post(
    "/mint",
    response_model=MintResponse,
)
@limiter.limit("20/minute")
def mint_hua(
    request: Request,
    body: MintRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """
    Admin-only: mint 华币 (HUA) to a user. 华币 is onlyOwner-minted (the
    company is the issuer), so this is the canonical way users acquire HUA.

    ``target`` is a username (resolved to the user's custodial wallet,
    provisioning one if needed) or a raw 0x address. Signed with the contract
    owner key and broadcast to the private chain. Requires is_admin.
    """
    from web3 import Web3
    from blockchain.web3_config import get_web3_config

    config = get_web3_config()
    if not config.hua_address:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "HUA_UNAVAILABLE",
                              "message": "华币合约未配置或链不可用"}},
        )

    target = body.target.strip()
    if Web3.is_address(target):
        to_addr = Web3.to_checksum_address(target)
    else:
        user = db.query(User).filter(User.username == target).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "USER_NOT_FOUND",
                                  "message": f"用户不存在：{target}"}},
            )
        from services.wallet import ensure_user_wallet
        to_addr = Web3.to_checksum_address(ensure_user_wallet(db, user))

    owner_pk = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("BLOCKCHAIN_PRIVATE_KEY")
    if not owner_pk:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "OWNER_KEY_MISSING",
                              "message": "铸币所需的合约 owner 私钥未配置"}},
        )

    w3 = config.w3
    hua = w3.eth.contract(address=Web3.to_checksum_address(config.hua_address), abi=_MINT_ABI)
    owner = w3.eth.account.from_key(owner_pk)
    units = w3.to_wei(Decimal(str(body.amount)), "ether")

    try:
        tx = hua.functions.mint(to_addr, units).build_transaction({
            "from": owner.address,
            "nonce": w3.eth.get_transaction_count(owner.address, "pending"),
            "gas": 120000, "gasPrice": 0, "chainId": config.chain_id,
        })
        tx_hash_hex = _sign_and_send_local(w3, tx, owner_pk)
    except Exception as exc:
        logger.error("HUA mint to %s failed: %s", to_addr, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "MINT_FAILED", "message": "华币铸造失败",
                              "details": {"reason": str(exc)}}},
        )

    logger.info("admin %s minted %s HUA to %s", current_admin.username, body.amount, to_addr)
    return MintResponse(
        tx_hash=tx_hash_hex,
        to_address=to_addr.lower(),
        amount=body.amount,
    )
