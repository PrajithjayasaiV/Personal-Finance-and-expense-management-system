"""Categories and income/expense transactions.

All business rules (ownership, category/type match, amount and date checks)
live here, not in the UI widgets.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from models import Budget, Category, Transaction, utcnow
from services.common import get_owned
from utils.dates import month_key
from utils.errors import AuthorizationError, ConflictError, ValidationError
from utils.money import to_paise
from utils.validators import validate_choice, validate_date, validate_text

log = logging.getLogger(__name__)


# ====================================================================== categories
def seed_default_categories(db: Session, user_id: int) -> None:
    """Insert the default categories a user does not have yet (idempotent)."""
    existing = {(c.type, c.name_key) for c in db.scalars(select(Category).where(Category.user_id == user_id))}
    for txn_type, names in ((config.INCOME, config.DEFAULT_INCOME_CATEGORIES), (config.EXPENSE, config.DEFAULT_EXPENSE_CATEGORIES)):
        for name in names:
            if (txn_type, name.casefold()) not in existing:
                db.add(Category(user_id=user_id, name=name, name_key=name.casefold(), type=txn_type, is_default=True))
    db.flush()


def list_categories(db: Session, user_id: int, txn_type: str | None = None) -> list[Category]:
    stmt = select(Category).where(Category.user_id == user_id)
    if txn_type:
        validate_choice(txn_type, config.TRANSACTION_TYPES, "Category type")
        stmt = stmt.where(Category.type == txn_type)
    return list(db.scalars(stmt.order_by(Category.type, Category.name_key)))


def get_category(db: Session, user_id: int, category_id: int) -> Category:
    return get_owned(db, Category, category_id, user_id, "category")


def _clean_category_name(name: str) -> str:
    return validate_text(name, "Category name", max_len=50, required=True)


def create_category(db: Session, user_id: int, name: str, txn_type: str) -> Category:
    name = _clean_category_name(name)
    validate_choice(txn_type, config.TRANSACTION_TYPES, "Category type")
    cat = Category(user_id=user_id, name=name, name_key=name.casefold(), type=txn_type)
    try:
        with db.begin_nested():
            db.add(cat)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError(f"You already have an {txn_type} category named '{name}'.") from exc
    return cat


def rename_category(db: Session, user_id: int, category_id: int, new_name: str) -> Category:
    cat = get_category(db, user_id, category_id)
    name = _clean_category_name(new_name)
    cat.name, cat.name_key = name, name.casefold()
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        raise ConflictError(f"You already have an {cat.type} category named '{name}'.") from exc
    return cat


def category_usage_counts(db: Session, user_id: int) -> dict[int, int]:
    rows = db.execute(
        select(Transaction.category_id, func.count()).where(Transaction.user_id == user_id).group_by(Transaction.category_id)
    )
    return {cid: n for cid, n in rows}


def delete_category(db: Session, user_id: int, category_id: int, reassign_to_id: int | None = None) -> None:
    """Delete a category without ever silently erasing transactions.

    If the category is used, the caller must name another category of the same
    type to receive the transactions. Budgets set on the deleted category are
    removed with it (they have no meaning without it).
    """
    cat = get_category(db, user_id, category_id)
    used = db.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.user_id == user_id, Transaction.category_id == cat.id)) or 0
    months: set[str] = set()
    if used:
        if reassign_to_id is None:
            raise ValidationError(
                f"'{cat.name}' is used by {used} transaction(s). Choose another category to move them to before deleting.")
        target = get_category(db, user_id, reassign_to_id)
        if target.id == cat.id:
            raise ValidationError("Choose a different category to move the transactions to.")
        if target.type != cat.type:
            raise ValidationError(f"Transactions can only be moved to another {cat.type} category.")
        months = {month_key(d) for d in db.scalars(select(Transaction.txn_date).where(
            Transaction.user_id == user_id, Transaction.category_id == cat.id))}
        db.execute(update(Transaction).where(Transaction.user_id == user_id, Transaction.category_id == cat.id)
                   .values(category_id=target.id, updated_at=utcnow()).execution_options(synchronize_session=False))
    budget_ids = list(db.scalars(select(Budget.id).where(Budget.user_id == user_id, Budget.category_id == cat.id)))
    from services import notification_service  # local import avoids an import cycle
    for bid in budget_ids:
        notification_service.remove_for_entity(db, user_id, "budget", bid)
    db.expire_all()
    db.delete(cat)
    db.flush()
    if months:
        notification_service.sync_budget_notifications(db, user_id, sorted(months))


# ====================================================================== transactions
@dataclass(frozen=True)
class TransactionRow:
    id: int
    txn_date: date
    type: str
    category_id: int
    category_name: str
    amount: int
    payment_method: str
    description: str


@dataclass(frozen=True)
class TransactionFilters:
    start: date | None = None
    end: date | None = None
    type: str | None = None
    category_id: int | None = None
    payment_method: str | None = None
    search: str | None = None


def _validated_fields(
    db: Session, user_id: int, txn_type: str, amount: object, category_id: int, txn_date: date,
    payment_method: str, description: str, today: date | None,
) -> tuple[int, Category, date, str]:
    validate_choice(txn_type, config.TRANSACTION_TYPES, "Transaction type")
    paise = to_paise(amount, "Amount")
    when = validate_date(txn_date, "Transaction date", today=today)
    validate_choice(payment_method, config.PAYMENT_METHODS, "Payment method")
    note = validate_text(description, "Description", max_len=255)
    if category_id is None:
        raise ValidationError("Choose a category.")
    cat = db.get(Category, int(category_id))
    if cat is None:
        raise ValidationError("The selected category does not exist.")
    if cat.user_id != user_id:
        raise AuthorizationError("You do not have access to the selected category.")
    if cat.type != txn_type:
        raise ValidationError(f"An {txn_type} transaction needs an {txn_type} category "
                              f"('{cat.name}' is a {cat.type} category).")
    return paise, cat, when, note


def _sync_budgets(db: Session, user_id: int, months: set[str]) -> None:
    from services import notification_service
    notification_service.sync_budget_notifications(db, user_id, sorted(months))


def create_transaction(
    db: Session, user_id: int, *, txn_type: str, amount: object, category_id: int, txn_date: date,
    payment_method: str, description: str = "", today: date | None = None,
) -> Transaction:
    paise, cat, when, note = _validated_fields(db, user_id, txn_type, amount, category_id, txn_date, payment_method, description, today)
    txn = Transaction(user_id=user_id, category_id=cat.id, type=txn_type, amount=paise, txn_date=when,
                      payment_method=payment_method, description=note)
    db.add(txn)
    db.flush()
    if txn_type == config.EXPENSE:
        _sync_budgets(db, user_id, {month_key(when)})
    return txn


def get_transaction(db: Session, user_id: int, txn_id: int) -> Transaction:
    return get_owned(db, Transaction, txn_id, user_id, "transaction")


def update_transaction(
    db: Session, user_id: int, txn_id: int, *, txn_type: str, amount: object, category_id: int,
    txn_date: date, payment_method: str, description: str = "", today: date | None = None,
) -> Transaction:
    txn = get_transaction(db, user_id, txn_id)
    paise, cat, when, note = _validated_fields(db, user_id, txn_type, amount, category_id, txn_date, payment_method, description, today)
    months = {month_key(txn.txn_date), month_key(when)}
    txn.type, txn.category_id, txn.amount = txn_type, cat.id, paise
    txn.txn_date, txn.payment_method, txn.description = when, payment_method, note
    db.flush()
    _sync_budgets(db, user_id, months)
    return txn


def delete_transaction(db: Session, user_id: int, txn_id: int) -> None:
    txn = get_transaction(db, user_id, txn_id)
    month = month_key(txn.txn_date)
    db.delete(txn)
    db.flush()
    _sync_budgets(db, user_id, {month})


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_filters(stmt, user_id: int, f: TransactionFilters):
    stmt = stmt.where(Transaction.user_id == user_id)
    if f.start:
        stmt = stmt.where(Transaction.txn_date >= f.start)
    if f.end:
        stmt = stmt.where(Transaction.txn_date <= f.end)
    if f.type:
        stmt = stmt.where(Transaction.type == f.type)
    if f.category_id:
        stmt = stmt.where(Transaction.category_id == f.category_id)
    if f.payment_method:
        stmt = stmt.where(Transaction.payment_method == f.payment_method)
    if f.search and f.search.strip():
        pattern = f"%{_escape_like(f.search.strip())}%"
        stmt = stmt.where(Transaction.description.ilike(pattern, escape="\\") | Category.name.ilike(pattern, escape="\\"))
    return stmt


def query_transactions(
    db: Session, user_id: int, filters: TransactionFilters | None = None, *,
    sort_by: str = "date", descending: bool = True, limit: int | None = None, offset: int = 0,
) -> tuple[list[TransactionRow], int]:
    """Return ``(rows, total_matching)`` for the user's transactions."""
    f = filters or TransactionFilters()
    if f.start and f.end and f.end < f.start:
        raise ValidationError("End date must not be before the start date.")
    count_stmt = _apply_filters(
        select(func.count()).select_from(Transaction).join(Category, Category.id == Transaction.category_id), user_id, f)
    total = db.scalar(count_stmt) or 0
    stmt = _apply_filters(
        select(Transaction, Category.name).join(Category, Category.id == Transaction.category_id), user_id, f)
    if sort_by == "amount":
        order = [Transaction.amount.desc() if descending else Transaction.amount.asc(), Transaction.id.desc()]
    else:
        order = [Transaction.txn_date.desc() if descending else Transaction.txn_date.asc(),
                 Transaction.id.desc() if descending else Transaction.id.asc()]
    stmt = stmt.order_by(*order)
    if limit is not None:
        stmt = stmt.limit(limit).offset(max(0, offset))
    rows = [
        TransactionRow(t.id, t.txn_date, t.type, t.category_id, name, t.amount, t.payment_method, t.description)
        for t, name in db.execute(stmt)
    ]
    return rows, total


def recent_transactions(db: Session, user_id: int, limit: int = 10) -> list[TransactionRow]:
    return query_transactions(db, user_id, limit=limit)[0]


def count_transactions(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Transaction).where(Transaction.user_id == user_id)) or 0
