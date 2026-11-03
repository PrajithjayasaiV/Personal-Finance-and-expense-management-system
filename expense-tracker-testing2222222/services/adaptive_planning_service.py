"""History-based salary-change plans and five-month automatic budgets.

All amounts remain integer paise. Suggestions are transparent rules over a
user's own transactions; no bank connection or machine learning is involved.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
from models import Category, Transaction
from utils.dates import month_bounds, shift_month


def _is_essential_category(name: str) -> bool:
    """Treat the default Food category as essential alongside configured essentials."""
    return name.casefold() in config.ESSENTIAL_CATEGORY_NAMES or name.casefold() == "food"


@dataclass(frozen=True)
class CategoryAverage:
    category_id: int
    name: str
    average_monthly: int
    total: int
    essential: bool


@dataclass(frozen=True)
class FiveMonthHistory:
    months: tuple[str, ...]
    income_by_month: dict[str, int]
    expense_by_month: dict[str, int]
    income_months_recorded: int
    average_income: int
    expense_months_recorded: int
    category_averages: tuple[CategoryAverage, ...]
    average_expenses: int


@dataclass(frozen=True)
class AutoBudgetRecommendation:
    target_month: str
    basis_months: tuple[str, ...]
    required_months: int
    items: tuple[CategoryAverage, ...]
    suggested_total: int

    @property
    def ready(self) -> bool:
        return len(self.basis_months) >= self.required_months


@dataclass(frozen=True)
class SalaryCategoryPlan:
    category_id: int
    name: str
    essential: bool
    historical_average: int
    recommended_limit: int
    suggested_reduction: int


@dataclass(frozen=True)
class SalaryAdjustmentPlan:
    baseline_income: int
    new_income: int
    income_change: int
    income_change_pct: float | None
    reserve: int
    commitments: int
    spending_capacity: int
    essential_average: int
    flexible_average: int
    recommended_expenses: int
    total_reduction: int
    remaining_after_plan: int
    essential_shortfall: int
    categories: tuple[SalaryCategoryPlan, ...]

    @property
    def income_decreased(self) -> bool:
        return self.baseline_income > 0 and self.new_income < self.baseline_income


def five_month_history(db: Session, user_id: int, effective_month: str) -> FiveMonthHistory:
    """Summarise the five complete calendar months before ``effective_month``."""
    months = tuple(shift_month(effective_month, offset) for offset in range(-5, 0))
    start = month_bounds(months[0])[0]
    end = month_bounds(months[-1])[1]
    income_by_month = {m: 0 for m in months}
    expense_by_month = {m: 0 for m in months}
    expense_by_category: dict[int, dict[str, object]] = {}
    expense_months: set[str] = set()
    month_expr = func.strftime("%Y-%m", Transaction.txn_date)
    stmt = (
        select(Transaction.type, month_expr, Transaction.category_id, Category.name, func.sum(Transaction.amount))
        .join(Category, Category.id == Transaction.category_id)
        .where(Transaction.user_id == user_id, Transaction.txn_date >= start, Transaction.txn_date <= end)
        .group_by(Transaction.type, month_expr, Transaction.category_id, Category.name)
    )
    for kind, month, category_id, category_name, amount in db.execute(stmt):
        amount = int(amount or 0)
        if kind == config.INCOME:
            income_by_month[month] += amount
        else:
            expense_months.add(month)
            expense_by_month[month] += amount
            item = expense_by_category.setdefault(category_id, {"name": category_name, "total": 0})
            item["total"] = int(item["total"]) + amount

    income_months = sum(1 for amount in income_by_month.values() if amount > 0)
    average_income = round(sum(income_by_month.values()) / income_months) if income_months else 0
    category_averages = tuple(sorted((
        CategoryAverage(
            category_id=cid,
            name=str(item["name"]),
            average_monthly=round(int(item["total"]) / len(months)),
            total=int(item["total"]),
            essential=_is_essential_category(str(item["name"])),
        )
        for cid, item in expense_by_category.items()
    ), key=lambda item: (-item.average_monthly, item.name.casefold())))
    average_expenses = sum(item.average_monthly for item in category_averages)
    return FiveMonthHistory(
        months=months,
        income_by_month=income_by_month,
        expense_by_month=expense_by_month,
        income_months_recorded=income_months,
        average_income=average_income,
        expense_months_recorded=len(expense_months),
        category_averages=category_averages,
        average_expenses=average_expenses,
    )


def auto_budget_recommendations(db: Session, user_id: int, target_month: str,
                                required_months: int = 5) -> AutoBudgetRecommendation:
    """Average spending over the latest five distinct completed expense months."""
    target_start = month_bounds(target_month)[0]
    month_expr = func.strftime("%Y-%m", Transaction.txn_date)
    month_stmt = (
        select(month_expr).where(
            Transaction.user_id == user_id,
            Transaction.type == config.EXPENSE,
            Transaction.txn_date < target_start,
        ).distinct().order_by(month_expr.desc()).limit(required_months)
    )
    basis_months = tuple(reversed([m for m in db.scalars(month_stmt).all() if m]))
    if len(basis_months) < required_months:
        return AutoBudgetRecommendation(target_month, basis_months, required_months, (), 0)

    stmt = (
        select(Transaction.category_id, Category.name, func.sum(Transaction.amount))
        .join(Category, Category.id == Transaction.category_id)
        .where(
            Transaction.user_id == user_id,
            Transaction.type == config.EXPENSE,
            month_expr.in_(basis_months),
        )
        .group_by(Transaction.category_id, Category.name)
    )
    items = tuple(sorted((
        CategoryAverage(
            category_id=int(cid), name=name, average_monthly=round(int(total) / required_months),
            total=int(total), essential=_is_essential_category(name),
        )
        for cid, name, total in db.execute(stmt)
    ), key=lambda item: (-item.average_monthly, item.name.casefold())))
    return AutoBudgetRecommendation(target_month, basis_months, required_months, items,
                                     sum(item.average_monthly for item in items))


def make_salary_adjustment_plan(history: FiveMonthHistory, new_income: int,
                                reserve: int = 0, commitments: int = 0) -> SalaryAdjustmentPlan:
    """Keep historical essential averages first, then scale flexible categories to fit."""
    new_income = max(0, int(new_income))
    reserve = max(0, int(reserve))
    commitments = max(0, int(commitments))
    capacity = max(0, new_income - reserve - commitments)
    essentials = sum(item.average_monthly for item in history.category_averages if item.essential)
    flexible = sum(item.average_monthly for item in history.category_averages if not item.essential)
    essential_shortfall = max(0, essentials - capacity)
    flexible_capacity = max(0, capacity - essentials)
    planned = []
    for item in history.category_averages:
        if item.essential or not flexible or flexible_capacity >= flexible:
            recommended = item.average_monthly
        else:
            # Integer arithmetic keeps allocations in paise and never exceeds the available cap.
            recommended = item.average_monthly * flexible_capacity // flexible
        planned.append(SalaryCategoryPlan(
            category_id=item.category_id,
            name=item.name,
            essential=item.essential,
            historical_average=item.average_monthly,
            recommended_limit=recommended,
            suggested_reduction=max(0, item.average_monthly - recommended),
        ))

    planned_total = sum(item.recommended_limit for item in planned)
    income_change = new_income - history.average_income
    pct = (income_change / history.average_income * 100) if history.average_income > 0 else None
    return SalaryAdjustmentPlan(
        baseline_income=history.average_income,
        new_income=new_income,
        income_change=income_change,
        income_change_pct=pct,
        reserve=reserve,
        commitments=commitments,
        spending_capacity=capacity,
        essential_average=essentials,
        flexible_average=flexible,
        recommended_expenses=planned_total,
        total_reduction=sum(item.suggested_reduction for item in planned),
        remaining_after_plan=max(0, capacity - planned_total),
        essential_shortfall=essential_shortfall,
        categories=tuple(planned),
    )


def possible_income_drop(history: FiveMonthHistory, current_month_income: int, threshold_pct: float = 5.0) -> float | None:
    """Return a drop percentage only with five recorded baseline income months."""
    if history.income_months_recorded < 5 or history.average_income <= 0 or current_month_income <= 0:
        return None
    change_pct = (history.average_income - current_month_income) / history.average_income * 100
    return change_pct if change_pct >= threshold_pct else None
