"""Savings goals and contributions.

Contribution rows are the single source of truth for the saved amount, which
is always derived by summing them (no separately maintained total). A
contribution is a tracking record: it is never deducted from the balance as an
expense.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
from models import GoalContribution, SavingsGoal
from services.common import get_owned
from utils.dates import add_months
from utils.errors import ValidationError
from utils.money import percentage, to_paise
from utils.validators import validate_date, validate_text

log = logging.getLogger(__name__)

DAYS_PER_MONTH = 30.4375
PACE_WINDOW_DAYS = 90

COMPLETED, ON_TRACK, BEHIND, OVERDUE, NO_DEADLINE, NO_PACE, PAUSED = (
    "completed", "on_track", "behind", "overdue", "no_deadline", "no_pace", "paused")


@dataclass(frozen=True)
class GoalProgress:
    goal_id: int
    name: str
    description: str
    status: str
    target: int
    saved: int
    remaining: int
    pct: float | None            # real percentage, may exceed 100
    display_pct: float           # capped at 100 for progress bars
    over_target: int             # amount saved beyond the target
    start_date: date
    target_date: date | None
    days_left: int | None
    required_monthly: int | None
    required_weekly: int | None
    monthly_pace: int            # recent average monthly contribution
    projected_completion: date | None
    track_status: str
    track_message: str


# ------------------------------------------------------------------ pure calculations
def project_completion(remaining: int, monthly_amount: int, today: date) -> date | None:
    """Estimated finish date if ``monthly_amount`` is saved every month (``None`` if never)."""
    if remaining <= 0:
        return today
    if monthly_amount <= 0:
        return None
    return add_months(today, math.ceil(remaining / monthly_amount))


def required_contributions(remaining: int, days_left: int | None) -> tuple[int | None, int | None]:
    """Approximate (monthly, weekly) saving needed to finish by the deadline."""
    if remaining <= 0 or days_left is None or days_left <= 0:
        return None, None
    months = max(days_left / DAYS_PER_MONTH, 1.0)
    weeks = max(days_left / 7, 1.0)
    return math.ceil(remaining / months), math.ceil(remaining / weeks)


def assess_goal(
    *, target: int, saved: int, start_date: date, target_date: date | None, recent_total: int,
    today: date, paused: bool = False,
) -> dict:
    """Derive progress numbers and an on-track verdict. Pure function (easy to test)."""
    remaining = max(0, target - saved)
    pct = percentage(saved, target) if target > 0 else None
    display_pct = min(100.0, pct) if pct is not None else 0.0
    days_left = (target_date - today).days if target_date else None
    window_days = min(PACE_WINDOW_DAYS, max(30, (today - start_date).days))
    monthly_pace = round(recent_total / (window_days / DAYS_PER_MONTH))
    required_monthly, required_weekly = required_contributions(remaining, days_left)
    projected = project_completion(remaining, monthly_pace, today) if remaining > 0 else today

    if remaining == 0:
        status, msg = COMPLETED, "Goal reached. Well done!"
    elif paused:
        status, msg = PAUSED, "This goal is paused."
    elif target_date is None:
        status, msg = NO_DEADLINE, "No target date set, so there is no deadline to measure against."
    elif days_left is not None and days_left < 0:
        status, msg = OVERDUE, "The target date has passed and the goal is not yet complete."
    elif monthly_pace <= 0:
        status, msg = NO_PACE, "No recent contributions, so progress toward the deadline cannot be projected."
    elif projected is not None and projected <= target_date:
        status, msg = ON_TRACK, "At your recent saving pace you should reach this goal by the target date (an estimate, not a guarantee)."
    else:
        status, msg = BEHIND, "At your recent saving pace you may miss the target date (an estimate, not a guarantee)."
    return dict(remaining=remaining, pct=pct, display_pct=display_pct, over_target=max(0, saved - target),
                days_left=days_left, required_monthly=required_monthly, required_weekly=required_weekly,
                monthly_pace=monthly_pace, projected_completion=projected, track_status=status, track_message=msg)


# ------------------------------------------------------------------ goals
def _sync_notifications(db: Session, user_id: int, goal_id: int | None = None) -> None:
    from services import notification_service
    notification_service.sync_goal_notifications(db, user_id, [goal_id] if goal_id else None)


def get_goal(db: Session, user_id: int, goal_id: int) -> SavingsGoal:
    return get_owned(db, SavingsGoal, goal_id, user_id, "savings goal")


def saved_amount(db: Session, user_id: int, goal_id: int) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(GoalContribution.amount), 0)).where(
        GoalContribution.user_id == user_id, GoalContribution.goal_id == goal_id)) or 0)


def _refresh_status(db: Session, goal: SavingsGoal) -> None:
    """active <-> completed follows the contributions; paused is left to the user."""
    if goal.status == config.GOAL_PAUSED:
        return
    saved = saved_amount(db, goal.user_id, goal.id)
    goal.status = config.GOAL_COMPLETED if saved >= goal.target_amount else config.GOAL_ACTIVE
    db.flush()


def _validate_dates(start: date, target: date | None) -> None:
    validate_date(start, "Start date", allow_future=True)
    if target is not None:
        validate_date(target, "Target date", allow_future=True)
        if target < start:
            raise ValidationError("Target date cannot be before the start date.")


def create_goal(db: Session, user_id: int, *, name: str, target_amount: object, description: str = "",
                start_date: date | None = None, target_date: date | None = None,
                initial_saved: object = 0, today: date | None = None) -> SavingsGoal:
    today = today or date.today()
    title = validate_text(name, "Goal name", max_len=100, required=True)
    target = to_paise(target_amount, "Target amount")
    start = start_date or today
    _validate_dates(start, target_date)
    initial = to_paise(initial_saved or 0, "Amount already saved", allow_zero=True)
    goal = SavingsGoal(user_id=user_id, name=title, description=validate_text(description, "Description", max_len=500),
                       target_amount=target, start_date=start, target_date=target_date)
    db.add(goal)
    db.flush()
    if initial:  # keep contribution rows the single source of truth
        db.add(GoalContribution(user_id=user_id, goal_id=goal.id, amount=initial, contribution_date=min(start, today),
                                note="Amount already saved when the goal was created"))
        db.flush()
    _refresh_status(db, goal)
    _sync_notifications(db, user_id, goal.id)
    return goal


def update_goal(db: Session, user_id: int, goal_id: int, *, name: str, target_amount: object, description: str = "",
                start_date: date, target_date: date | None) -> SavingsGoal:
    goal = get_goal(db, user_id, goal_id)
    _validate_dates(start_date, target_date)
    goal.name = validate_text(name, "Goal name", max_len=100, required=True)
    goal.target_amount = to_paise(target_amount, "Target amount")
    goal.description = validate_text(description, "Description", max_len=500)
    goal.start_date, goal.target_date = start_date, target_date
    db.flush()
    _refresh_status(db, goal)
    _sync_notifications(db, user_id, goal.id)
    return goal


def set_goal_paused(db: Session, user_id: int, goal_id: int, paused: bool) -> SavingsGoal:
    goal = get_goal(db, user_id, goal_id)
    goal.status = config.GOAL_PAUSED if paused else config.GOAL_ACTIVE
    db.flush()
    if not paused:
        _refresh_status(db, goal)
    _sync_notifications(db, user_id, goal.id)
    return goal


def delete_goal(db: Session, user_id: int, goal_id: int) -> None:
    """Delete a goal and (by database cascade) its contribution records."""
    goal = get_goal(db, user_id, goal_id)
    from services import notification_service
    notification_service.remove_for_entity(db, user_id, "goal", goal.id)
    db.delete(goal)
    db.flush()


# ------------------------------------------------------------------ contributions
def add_contribution(db: Session, user_id: int, goal_id: int, amount: object, contribution_date: date,
                     note: str = "", today: date | None = None) -> GoalContribution:
    goal = get_goal(db, user_id, goal_id)
    contribution = GoalContribution(
        user_id=user_id, goal_id=goal.id, amount=to_paise(amount, "Contribution amount"),
        contribution_date=validate_date(contribution_date, "Contribution date", today=today),
        note=validate_text(note, "Note", max_len=255))
    db.add(contribution)
    db.flush()
    _refresh_status(db, goal)
    _sync_notifications(db, user_id, goal.id)
    return contribution


def get_contribution(db: Session, user_id: int, contribution_id: int) -> GoalContribution:
    return get_owned(db, GoalContribution, contribution_id, user_id, "contribution")


def update_contribution(db: Session, user_id: int, contribution_id: int, amount: object, contribution_date: date,
                        note: str = "", today: date | None = None) -> GoalContribution:
    c = get_contribution(db, user_id, contribution_id)
    c.amount = to_paise(amount, "Contribution amount")
    c.contribution_date = validate_date(contribution_date, "Contribution date", today=today)
    c.note = validate_text(note, "Note", max_len=255)
    db.flush()
    goal = get_goal(db, user_id, c.goal_id)
    _refresh_status(db, goal)
    _sync_notifications(db, user_id, goal.id)
    return c


def delete_contribution(db: Session, user_id: int, contribution_id: int) -> None:
    c = get_contribution(db, user_id, contribution_id)
    goal = get_goal(db, user_id, c.goal_id)
    db.delete(c)
    db.flush()
    _refresh_status(db, goal)
    _sync_notifications(db, user_id, goal.id)


def list_contributions(db: Session, user_id: int, goal_id: int) -> list[GoalContribution]:
    get_goal(db, user_id, goal_id)  # ownership check
    return list(db.scalars(select(GoalContribution).where(
        GoalContribution.user_id == user_id, GoalContribution.goal_id == goal_id
    ).order_by(GoalContribution.contribution_date.desc(), GoalContribution.id.desc())))


# ------------------------------------------------------------------ progress
def goal_progress(db: Session, user_id: int, goal: SavingsGoal, today: date | None = None) -> GoalProgress:
    today = today or date.today()
    saved = saved_amount(db, user_id, goal.id)
    recent_total = int(db.scalar(select(func.coalesce(func.sum(GoalContribution.amount), 0)).where(
        GoalContribution.user_id == user_id, GoalContribution.goal_id == goal.id,
        GoalContribution.contribution_date > today - timedelta(days=PACE_WINDOW_DAYS))) or 0)
    info = assess_goal(target=goal.target_amount, saved=saved, start_date=goal.start_date,
                       target_date=goal.target_date, recent_total=recent_total, today=today,
                       paused=goal.status == config.GOAL_PAUSED)
    return GoalProgress(goal_id=goal.id, name=goal.name, description=goal.description, status=goal.status,
                        target=goal.target_amount, saved=saved, start_date=goal.start_date,
                        target_date=goal.target_date, **info)


def list_goals(db: Session, user_id: int, status: str | None = None) -> list[SavingsGoal]:
    stmt = select(SavingsGoal).where(SavingsGoal.user_id == user_id)
    if status:
        stmt = stmt.where(SavingsGoal.status == status)
    return list(db.scalars(stmt.order_by(SavingsGoal.status, SavingsGoal.target_date.is_(None),
                                         SavingsGoal.target_date, SavingsGoal.id)))


def list_progress(db: Session, user_id: int, today: date | None = None, include_completed: bool = True
                  ) -> list[GoalProgress]:
    out = [goal_progress(db, user_id, g, today) for g in list_goals(db, user_id)]
    return out if include_completed else [p for p in out if p.status != config.GOAL_COMPLETED]


def contributions_by_month(db: Session, user_id: int) -> dict[str, int]:
    month_expr = func.strftime("%Y-%m", GoalContribution.contribution_date)
    rows = db.execute(select(month_expr, func.sum(GoalContribution.amount)).where(
        GoalContribution.user_id == user_id).group_by(month_expr).order_by(month_expr))
    return {m: int(t) for m, t in rows}
