"""
Tests for services.payment_service (credit ledger).

Real in-memory SQLite + real ORM models (CreditAccount / CreditTransaction),
no mocks. Backs the restored billing service layer imported by
api/payments.py and api/academic_tasks.py.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.testdb import TEST_DATABASE_URL
from models.database import Base
import models.payment  # noqa: F401  registers CreditAccount/CreditTransaction
import models.agent_survival  # noqa: F401  registers AgentSurvival for Agent mapper config
from services.payment_service import PaymentService, InsufficientBalanceError


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


def test_get_or_create_is_idempotent(db):
    a1 = PaymentService.get_or_create_account(db, user_id=1)
    a2 = PaymentService.get_or_create_account(db, user_id=1)
    assert a1.id == a2.id
    assert a1.balance == 0.0


def test_deposit_increases_balance(db):
    tx = PaymentService.deposit(db, user_id=1, amount=1000.0, description="top up")
    assert tx.amount == 1000.0
    assert tx.balance_after == 1000.0
    assert tx.transaction_type == "deposit"
    acc = PaymentService.get_or_create_account(db, user_id=1)
    assert acc.balance == 1000.0
    assert acc.total_deposited == 1000.0


def test_deposit_rejects_non_positive(db):
    with pytest.raises(ValueError):
        PaymentService.deposit(db, user_id=1, amount=0)


def test_charge_deducts_and_records_negative(db):
    PaymentService.deposit(db, user_id=1, amount=1000.0)
    tx = PaymentService.charge(db, user_id=1, amount=200.0, task_id="acad_x", description="task")
    assert tx.amount == -200.0
    assert tx.balance_after == 800.0
    assert tx.task_id == "acad_x"
    acc = PaymentService.get_or_create_account(db, user_id=1)
    assert acc.balance == 800.0
    assert acc.total_spent == 200.0


def test_charge_insufficient_balance_raises(db):
    PaymentService.deposit(db, user_id=1, amount=100.0)
    with pytest.raises(InsufficientBalanceError):
        PaymentService.charge(db, user_id=1, amount=500.0, task_id="acad_y")
    # balance untouched
    assert PaymentService.get_or_create_account(db, user_id=1).balance == 100.0


def test_get_transactions_newest_first(db):
    PaymentService.deposit(db, user_id=1, amount=1000.0)
    PaymentService.charge(db, user_id=1, amount=200.0, task_id="acad_z")
    txs = PaymentService.get_transactions(db, user_id=1)
    assert len(txs) == 2
    assert txs[0]["transaction_type"] == "task_charge"
    assert txs[1]["transaction_type"] == "deposit"


def test_invoice_summary(db):
    PaymentService.deposit(db, user_id=1, amount=1000.0)
    PaymentService.charge(db, user_id=1, amount=200.0, task_id="acad_w")
    now = datetime.utcnow()
    summary = PaymentService.get_invoice_summary(db, user_id=1, year=now.year, month=now.month)
    assert summary["deposits"] == 1000.0
    assert summary["charges"] == 200.0
    assert summary["net_spend"] == 200.0
    assert summary["transaction_count"] == 2
    assert summary["current_balance"] == 800.0
