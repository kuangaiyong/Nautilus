"""
Tests for services.wallet.ensure_user_wallet.

Real in-memory SQLite + real User/Wallet ORM rows, no mocks. Verifies a
custodial wallet is provisioned for users without one (account/password
signups), the address is back-filled, the encrypted key round-trips to the
same address, and the operation is idempotent. Backs the fix that lets such
users publish tasks (Task.publisher is NOT NULL).
"""
import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.testdb import TEST_DATABASE_URL
from models.database import Base, User, Wallet
import models.agent_survival  # noqa: F401  registers Agent mapper relationships
from services.wallet import ensure_user_wallet, _get_local_encryption


@pytest.fixture
def db():
    engine = create_engine(
        TEST_DATABASE_URL
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_user(db, username="alice", email="alice@nautilus.local", wallet_address=None):
    user = User(username=username, email=email,
                hashed_password="x", wallet_address=wallet_address)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_provisions_wallet_for_user_without_one(db):
    user = _make_user(db)
    assert user.wallet_address is None

    addr = ensure_user_wallet(db, user)

    assert addr.startswith("0x") and len(addr) == 42
    assert user.wallet_address == addr

    wallets = db.query(Wallet).filter(Wallet.user_id == user.id).all()
    assert len(wallets) == 1
    w = wallets[0]
    assert w.public_address == addr
    assert w.wallet_type == "user"
    assert w.encrypted_private_key
    assert w.mnemonic_hash


def test_is_idempotent(db):
    user = _make_user(db)
    addr1 = ensure_user_wallet(db, user)
    addr2 = ensure_user_wallet(db, user)

    assert addr1 == addr2
    assert db.query(Wallet).filter(Wallet.user_id == user.id).count() == 1


def test_encrypted_key_roundtrips_to_address(db):
    from eth_account import Account
    user = _make_user(db)

    addr = ensure_user_wallet(db, user)
    w = db.query(Wallet).filter(Wallet.user_id == user.id).one()
    pk = _get_local_encryption().decrypt(
        base64.b64decode(w.encrypted_private_key), addr
    )

    assert Account.from_key(pk).address.lower() == addr


def test_returns_existing_address_without_creating_wallet(db):
    existing = "0x" + "a" * 40
    user = _make_user(db, wallet_address=existing)

    addr = ensure_user_wallet(db, user)

    assert addr == existing
    assert db.query(Wallet).filter(Wallet.user_id == user.id).count() == 0
