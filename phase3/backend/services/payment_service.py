"""
Credit-based billing service.

Thin service layer over the CreditAccount / CreditTransaction ledger
(models/payment.py) for prepaid B2B clients. 1 credit = 1 RMB. Distinct from
the on-chain HUA/NAU settlement used to reward agents.

Consumed by api/payments.py (balance/deposit/transactions/invoice) and
api/academic_tasks.py (per-task charge).
"""
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import extract
from sqlalchemy.orm import Session

from models.payment import CreditAccount, CreditTransaction, TransactionType

logger = logging.getLogger(__name__)


class InsufficientBalanceError(Exception):
    """Raised when a charge would exceed the account's available balance."""


class PaymentService:
    """Credit ledger operations. All methods are synchronous and commit on success."""

    @staticmethod
    def get_or_create_account(db: Session, user_id: int) -> CreditAccount:
        account = (
            db.query(CreditAccount)
            .filter(CreditAccount.user_id == user_id)
            .first()
        )
        if account is None:
            account = CreditAccount(user_id=user_id)
            db.add(account)
            db.commit()
            db.refresh(account)
        return account

    @staticmethod
    def deposit(db: Session, user_id: int, amount: float, description: str = "") -> CreditTransaction:
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")

        account = PaymentService.get_or_create_account(db, user_id)
        account.balance += amount
        account.total_deposited += amount
        account.updated_at = datetime.utcnow()

        tx = CreditTransaction(
            account_id=account.id,
            transaction_type=TransactionType.DEPOSIT.value,
            amount=amount,
            balance_after=account.balance,
            description=description or "Deposit",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        logger.info("Deposit %.2f for user %s -> balance %.2f", amount, user_id, account.balance)
        return tx

    @staticmethod
    def charge(db: Session, user_id: int, amount: float, task_id: str, description: str = "") -> CreditTransaction:
        if amount <= 0:
            raise ValueError("Charge amount must be positive")

        account = PaymentService.get_or_create_account(db, user_id)
        if account.balance < amount:
            raise InsufficientBalanceError(
                f"Insufficient balance: have {account.balance}, need {amount}"
            )

        account.balance -= amount
        account.total_spent += amount
        account.updated_at = datetime.utcnow()

        tx = CreditTransaction(
            account_id=account.id,
            transaction_type=TransactionType.TASK_CHARGE.value,
            amount=-amount,  # negative for charges
            balance_after=account.balance,
            description=description or f"Task charge: {task_id}",
            task_id=task_id,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        logger.info("Charged %.2f for user %s task %s -> balance %.2f", amount, user_id, task_id, account.balance)
        return tx

    @staticmethod
    def get_transactions(
        db: Session,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        transaction_type: Optional[str] = None,
    ) -> List[dict]:
        account = PaymentService.get_or_create_account(db, user_id)
        query = db.query(CreditTransaction).filter(CreditTransaction.account_id == account.id)
        if transaction_type:
            query = query.filter(CreditTransaction.transaction_type == transaction_type)
        rows = (
            query.order_by(CreditTransaction.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "transaction_type": r.transaction_type,
                "amount": r.amount,
                "balance_after": r.balance_after,
                "description": r.description,
                "task_id": r.task_id,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    @staticmethod
    def get_invoice_summary(db: Session, user_id: int, year: int, month: int) -> dict:
        account = PaymentService.get_or_create_account(db, user_id)
        rows = (
            db.query(CreditTransaction)
            .filter(
                CreditTransaction.account_id == account.id,
                extract("year", CreditTransaction.created_at) == year,
                extract("month", CreditTransaction.created_at) == month,
            )
            .all()
        )
        deposits = sum(r.amount for r in rows if r.transaction_type == TransactionType.DEPOSIT.value)
        charges = sum(-r.amount for r in rows if r.transaction_type == TransactionType.TASK_CHARGE.value)
        refunds = sum(r.amount for r in rows if r.transaction_type == TransactionType.REFUND.value)
        return {
            "year": year,
            "month": month,
            "deposits": round(deposits, 2),
            "charges": round(charges, 2),
            "refunds": round(refunds, 2),
            "net_spend": round(charges - refunds, 2),
            "transaction_count": len(rows),
            "current_balance": account.balance,
        }
