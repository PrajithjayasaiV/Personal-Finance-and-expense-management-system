"""Monthly budgets (overall and per category) and their live utilisation.

Spending is always read from the transactions table; nothing is cached or
counted separately, so editing/deleting a transaction immediately changes the
budget status.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from models import Budget, Category
from services import analytics_service
from services.common import get_owned
from utils.dates import month_bounds, parse_month
from utils.errors import AuthorizationError, ConflictError, ValidationError
from utils.money import percentage, to_paise
from utils.validators import validate_text

log = logging.getLogger(__name__)

OK, WARNING, EXCEEDED = "ok", "warning", "exceeded"
LEVEL_LABELS = {
    OK: "On track (below 80%)",
    WARNING: "Approaching limit (80% or more)",
    EXCEEDED: "Limit reached or exceeded (100% or more)",
}


@dataclass(frozen=True)
class BudgetStatus:
    budget_id: int
    month: str
    category_id: int | None
    label: str                # "Overall budget" or the category name
    limit: int
    used: int
    remaining: int            # negative when exceeded
    exceeded_by: int          # 0 unless exceeded
    utilization: float | None
    level: str
    description: str

    @property
    def is_overall(self) -> bool:
        return self.category_id is None


def classify(used: int, limit: int) -> str:
    """Threshold status using exact integer arithmetic (80% and 100% are inclusive)."""
    if limit <= 0:
        return OK
    if used * 100 >= limit * config.BUDGET_EXCEEDED_PCT:
        return EXCEEDED
    if used * 100 >= limit * config.BUDGET_WARNING_PCT:
        return WARNING
    return OK


def make_status(budget: Budget, label: str, used: int) -> BudgetStatus:
    return BudgetStatus(
        budget_id=budget.id, month=budget.month, category_id=budget.category_id, label=label,
        limit=budget.limit_amount, used=used, remaining=budget.limit_amount - used,
        exceeded_by=max(0, used - budget.limit_amount),
        utilization=percentage(used, budget.limit_amount), level=classify(used, budget.limit_amount),
        description=budget.description,
    )


def _sync(db: Session, user_id: int, months: list[str]) -> None:
    from services import notification_service
    notification_service.sync_budget_notifications(db, user_id, months)


def _validate_scope(db: Session, user_id: int, month: str, category_id: int | None) -> None:
    parse_month(month)
    if category_id is not None:
        cat = db.get(Category, int(category_id))
        if cat is None:
            raise ValidationError("The selected category does not exist.")
        if cat.user_id != user_id:
            raise AuthorizationError("You do not have access to the selected category.")
        if cat.type != config.EXPENSE:
            raise ValidationError("Budgets can only be set for expense categories.")


def _find(db: Session, user_id: int, month: str, category_id: int | None) -> Budget | None:
    stmt = select(Budget).where(Budget.user_id == user_id, Budget.month == month)
    stmt = stmt.where(Budget.category_id.is_(None) if category_id is None else Budget.category_id == int(category_id))
    return db.scalar(stmt)


def create_budget(db: Session, user_id: int, month: str, limit: object, category_id: int | None = None,
                  description: str = "") -> Budget:
    _validate_scope(db, user_id, month, category_id)
    amount = to_paise(limit, "Budget limit")
    note = validate_text(description, "Description", max_len=255)
    if _find(db, user_id, month, category_id):
        raise ConflictError("A budget for this month and scope already exists. Edit the existing budget instead.")
    budget = Budget(user_id=user_id, month=month, category_id=category_id, limit_amount=amount, description=note)
    try:
        with db.begin_nested():
            db.add(budget)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError("A budget for this month and scope already exists.") from exc
    _sync(db, user_id, [month])
    return budget


def get_budget(db: Session, user_id: int, budget_id: int) -> Budget:
    return get_owned(db, Budget, budget_id, user_id, "budget")


def update_budget(db: Session, user_id: int, budget_id: int, limit: object | None = None,
                  description: str | None = None) -> Budget:
    budget = get_budget(db, user_id, budget_id)
    if limit is not None:
        budget.limit_amount = to_paise(limit, "Budget limit")
    if description is not None:
        budget.description = validate_text(description, "Description", max_len=255)
    db.flush()
    _sync(db, user_id, [budget.month])
    return budget


def upsert_budget(db: Session, user_id: int, month: str, limit: object, category_id: int | None = None,
                  description: str = "") -> tuple[Budget, bool]:
    """Create or update the budget for a scope. Returns ``(budget, created)``.

    Only call this after the user has confirmed (e.g. accepted a recommendation).
    """
    _validate_scope(db, user_id, month, category_id)
    existing = _find(db, user_id, month, category_id)
    if existing:
        return update_budget(db, user_id, existing.id, limit, description or None), False
    return create_budget(db, user_id, month, limit, category_id, description), True


def delete_budget(db: Session, user_id: int, budget_id: int) -> None:
    budget = get_budget(db, user_id, budget_id)
    from services import notification_service
    notification_service.remove_for_entity(db, user_id, "budget", budget.id)
    db.delete(budget)
    db.flush()


def list_budgets(db: Session, user_id: int, month: str) -> list[tuple[Budget, str]]:
    """Budgets for a month as ``(budget, label)``; overall first, then by category name."""
    parse_month(month)
    rows = db.execute(
        select(Budget, Category.name).outerjoin(Category, Category.id == Budget.category_id)
        .where(Budget.user_id == user_id, Budget.month == month)
    ).all()
    out = [(b, name if name else "Overall budget") for b, name in rows]
    return sorted(out, key=lambda item: (item[0].category_id is not None, item[1].casefold()))


def month_statuses(db: Session, user_id: int, month: str) -> list[BudgetStatus]:
    """Status of every budget in ``month`` (overall first, then highest utilisation)."""
    start, end = month_bounds(month)
    total, by_cat = analytics_service.expenses_by_category(db, user_id, start, end)
    statuses = [make_status(b, label, total if b.category_id is None else by_cat.get(b.category_id, 0))
                for b, label in list_budgets(db, user_id, month)]
    return sorted(statuses, key=lambda s: (not s.is_overall, -(s.utilization or 0), s.label.casefold()))


def overall_status(db: Session, user_id: int, month: str) -> BudgetStatus | None:
    return next((s for s in month_statuses(db, user_id, month) if s.is_overall), None)


def category_budget_total(db: Session, user_id: int, month: str) -> int:
    return sum(b.limit_amount for b, _ in list_budgets(db, user_id, month) if b.category_id is not None)


def overall_history(db: Session, user_id: int, months: list[str]) -> list[tuple[str, BudgetStatus | None]]:
    return [(m, overall_status(db, user_id, m)) for m in months]


def category_budget_history(db: Session, user_id: int, category_id: int, last_month: str, count: int = 6
                            ) -> list[tuple[str, BudgetStatus]]:
    """Statuses of one category's budgets for up to ``count`` months ending at ``last_month`` (oldest first)."""
    rows = db.scalars(
        select(Budget).where(Budget.user_id == user_id, Budget.category_id == category_id, Budget.month <= last_month)
        .order_by(Budget.month.desc()).limit(count)
    ).all()
    out = []
    for b in reversed(rows):
        start, end = month_bounds(b.month)
        used = analytics_service.expenses_by_category(db, user_id, start, end)[1].get(category_id, 0)
        out.append((b.month, make_status(b, "", used)))
    return out
