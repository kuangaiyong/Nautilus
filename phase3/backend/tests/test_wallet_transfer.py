"""
Tests for custodial 华币 transfer signing (backs api.wallets.transfer_hua).

Real eth-account signing + real SQLite + real AES key encryption, no mocks.
The security crux of a custodial transfer is that the platform signs with the
wallet's OWN key, so the recovered sender must equal the wallet address. These
tests pin that property (and the encrypt/decrypt round-trip) without needing a
live chain to sign.
"""
import base64

import pytest
from eth_account import Account
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base, User, Wallet
import models.agent_survival  # noqa: F401  registers Agent mapper relationships
from services.wallet import ensure_user_wallet, _get_local_encryption


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, username="payer"):
    u = User(username=username, email=f"{username}@nautilus.local",
             hashed_password="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _transfer_tx(to_hex: str, amount_wei: int) -> dict:
    """Build a minimal legacy ERC-20 transfer tx dict (no chain needed)."""
    selector = "a9059cbb"
    to_arg = to_hex.lower().replace("0x", "").rjust(64, "0")
    amount_arg = "%064x" % amount_wei
    return {
        "to": "0x" + "11" * 20,          # HUA contract (arbitrary for signing)
        "value": 0,
        "gas": 100000,
        "gasPrice": 0,
        "nonce": 0,
        "chainId": 13370,
        "data": "0x" + selector + to_arg + amount_arg,
    }


def test_transfer_signed_with_wallet_own_key(db):
    """The signed transfer recovers to the wallet's own address (custodial)."""
    user = _user(db)
    address = ensure_user_wallet(db, user)
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).one()

    pk = _get_local_encryption().decrypt(
        base64.b64decode(wallet.encrypted_private_key), wallet.public_address
    )
    tx = _transfer_tx("0x" + "22" * 20, 10 ** 18)
    signed = Account.sign_transaction(tx, pk)

    recovered = Account.recover_transaction(signed.raw_transaction)
    assert recovered.lower() == address


def test_two_users_sign_with_distinct_keys(db):
    """Each user's wallet signs with its own key — no cross-talk."""
    a = _user(db, "alice")
    b = _user(db, "bob")
    addr_a = ensure_user_wallet(db, a)
    addr_b = ensure_user_wallet(db, b)
    assert addr_a != addr_b

    enc = _get_local_encryption()
    wa = db.query(Wallet).filter(Wallet.user_id == a.id).one()
    wb = db.query(Wallet).filter(Wallet.user_id == b.id).one()
    pk_a = enc.decrypt(base64.b64decode(wa.encrypted_private_key), wa.public_address)
    pk_b = enc.decrypt(base64.b64decode(wb.encrypted_private_key), wb.public_address)

    tx = _transfer_tx("0x" + "33" * 20, 5 * 10 ** 18)
    rec_a = Account.recover_transaction(Account.sign_transaction(tx, pk_a).raw_transaction)
    rec_b = Account.recover_transaction(Account.sign_transaction(tx, pk_b).raw_transaction)

    assert rec_a.lower() == addr_a
    assert rec_b.lower() == addr_b
