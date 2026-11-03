"""In-app notifications.

Each notification has a stable ``dedupe_key`` (for example ``budget_exceeded:12``)
and a unique constraint on ``(user_id, dedupe_key)``. Re-running the sync on
every Streamlit rerun therefore never creates duplicates: it updates the
existing row, switches it off (``is_active = False``) when the condition no
longer holds, and switches it back on (unread) if the condition returns.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from models import Notification, SavingsGoal, User, utcnow
from services import budget_service as bs
from services import insights_service
from services import savings_service as sv
from services.common import get_owned
from utils.dates import month_bounds, month_key, month_label
from utils.formatting import format_date, format_money, format_pct

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ low level
def _upsert(db: Session, user_id: int, key: str, *, type: str, title: str, message: str, severity: str,
            ref_type: str | None, ref_id: int | None) -> Notification:
    row = db.scalar(select(Notification).where(Notification.user_id == user_id, Notification.dedupe_key == key))
    if row is None:
        row = Notification(user_id=user_id, type=type, title=title[:200], message=message[:1000], severity=severity,
                           ref_type=ref_type, ref_id=ref_id, dedupe_key=key)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return row
        except IntegrityError:  # another request created it first - fall through and update it
            row = db.scalar(select(Notification).where(Notification.user_id == user_id, Notification.dedupe_key == key))
            if row is None:
                raise
    if not row.is_active:                       # condition came back: show it again as unread
        row.is_active, row.is_read, row.created_at = True, False, utcnow()
    row.title, row.message, row.severity = title[:200], message[:1000], severity
    row.ref_type, row.ref_id = ref_type, ref_id
    db.flush()
    return row


def _deactivate(db: Session, user_id: int, *keys: str) -> None:
    for row in db.scalars(select(Notification).where(
            Notification.user_id == user_id, Notification.dedupe_key.in_(keys), Notification.is_active.is_(True))):
        row.is_active = False
    db.flush()


def remove_for_entity(db: Session, user_id: int, ref_type: str, ref_id: int) -> None:
    """Delete notifications that point at an entity that no longer exists."""
    db.execute(delete(Notification).where(
        Notification.user_id == user_id, Notification.ref_type == ref_type, Notification.ref_id == ref_id))
    db.flush()


# ------------------------------------------------------------------ budgets
def sync_budget_notifications(db: Session, user_id: int, months: list[str] | None = None) -> None:
    """Create/update/deactivate budget alerts for the given months (default: current month)."""
    user = db.get(User, user_id)
    if user is None:
        return
    months = months or [month_key(date.today())]
    for month in months:
        for b, _label in bs.list_budgets(db, user_id, month):
            warn_key, exceed_key = f"budget_warning:{b.id}", f"budget_exceeded:{b.id}"
            status = next((s for s in bs.month_statuses(db, user_id, month) if s.budget_id == b.id), None)
            if status is None or status.level == bs.OK or not user.notify_budget:
                _deactivate(db, user_id, warn_key, exceed_key)
                continue
            cur = user.currency
            scope = "overall" if status.is_overall else status.label
            base = (f"{month_label(month)} {scope} budget: spent {format_money(status.used, cur)} of "
                    f"{format_money(status.limit, cur)} ({format_pct(status.utilization, 1)}).")
            if status.level == bs.WARNING:
                _upsert(db, user_id, warn_key, type="budget_warning", severity="warning", ref_type="budget", ref_id=b.id,
                        title=f"Approaching budget limit: {status.label}",
                        message=f"{base} Remaining: {format_money(status.remaining, cur)}.")
                _deactivate(db, user_id, exceed_key)
            else:
                tail = f"Over by {format_money(status.exceeded_by, cur)}." if status.exceeded_by else "You have used the full budget."
                _upsert(db, user_id, exceed_key, type="budget_exceeded", severity="critical", ref_type="budget", ref_id=b.id,
                        title=f"Budget reached or exceeded: {status.label}", message=f"{base} {tail}")
                _deactivate(db, user_id, warn_key)


# ------------------------------------------------------------------ goals
def sync_goal_notifications(db: Session, user_id: int, goal_ids: list[int] | None = None,
                            today: date | None = None) -> None:
    user = db.get(User, user_id)
    if user is None:
        return
    today = today or date.today()
    stmt = select(SavingsGoal).where(SavingsGoal.user_id == user_id)
    if goal_ids:
        stmt = stmt.where(SavingsGoal.id.in_(goal_ids))
    cur = user.currency
    for goal in db.scalars(stmt):
        p = sv.goal_progress(db, user_id, goal, today)
        milestone_keys = {m: f"goal_milestone:{goal.id}:{m}" for m in config.GOAL_MILESTONES}
        deadline_key = f"goal_deadline:{goal.id}"
        if not user.notify_goals:
            _deactivate(db, user_id, deadline_key, *milestone_keys.values())
            continue
        reached = max((m for m in config.GOAL_MILESTONES if (p.pct or 0) >= m), default=None)
        for m, key in milestone_keys.items():
            if m == reached:
                done = m == 100
                if done:
                    title = f"Goal reached: {goal.name}"
                    message = f"You have saved {format_money(p.saved, cur)} of {format_money(p.target, cur)} - goal complete!"
                else:
                    title = f"{m}% of '{goal.name}' saved"
                    message = (f"You have saved {format_money(p.saved, cur)} of {format_money(p.target, cur)} "
                               f"({format_pct(p.pct, 1)}). {format_money(p.remaining, cur)} to go.")
                _upsert(db, user_id, key, type="goal_milestone", severity="success", ref_type="goal", ref_id=goal.id,
                        title=title, message=message)
            else:
                _deactivate(db, user_id, key)
        near = (p.remaining > 0 and p.status == config.GOAL_ACTIVE and p.days_left is not None
                and p.days_left <= config.GOAL_DEADLINE_WARNING_DAYS)
        if near:
            when = format_date(p.target_date, user.date_format)
            msg = (f"'{goal.name}' is due on {when} ({p.days_left} day(s) left) with {format_money(p.remaining, cur)} still to save."
                   if p.days_left >= 0 else
                   f"The target date ({when}) for '{goal.name}' has passed with {format_money(p.remaining, cur)} still to save.")
            _upsert(db, user_id, deadline_key, type="goal_deadline", severity="warning" if p.days_left >= 0 else "critical",
                    ref_type="goal", ref_id=goal.id, title=f"Goal deadline: {goal.name}", message=msg)
        else:
            _deactivate(db, user_id, deadline_key)


# ------------------------------------------------------------------ insights
def sync_insight_notifications(db: Session, user_id: int, today: date | None = None) -> None:
    """Notify about the few insights flagged ``notify`` (unusual increases, repeated overspending)."""
    user = db.get(User, user_id)
    if user is None or not user.notify_insights:
        return
    today = today or date.today()
    start, _ = month_bounds(month_key(today))
    for ins in insights_service.generate_insights(db, user_id, start, today, today=today, include_hidden=False, limit=None):
        if ins.notify:
            _upsert(db, user_id, f"insight:{ins.key}", type="insight", severity=ins.severity, ref_type="insight",
                    ref_id=None, title=ins.title, message=ins.message)


def refresh_all(db: Session, user_id: int, today: date | None = None) -> None:
    """Bring every notification up to date with the current data (safe to call on every page load)."""
    today = today or date.today()
    sync_budget_notifications(db, user_id, [month_key(today)])
    sync_goal_notifications(db, user_id, None, today)
    sync_insight_notifications(db, user_id, today)


# ------------------------------------------------------------------ reading / marking
def list_notifications(db: Session, user_id: int, *, unread_only: bool = False, include_inactive: bool = False,
                       limit: int = 100) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if not include_inactive:
        stmt = stmt.where(Notification.is_active.is_(True))
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    return list(db.scalars(stmt.order_by(Notification.is_read, Notification.created_at.desc(), Notification.id.desc()).limit(limit)))


def unread_count(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user_id, Notification.is_active.is_(True), Notification.is_read.is_(False))) or 0


def set_read(db: Session, user_id: int, notification_id: int, read: bool = True) -> Notification:
    row = get_owned(db, Notification, notification_id, user_id, "notification")
    row.is_read = read
    db.flush()
    return row


def mark_all_read(db: Session, user_id: int) -> int:
    rows = list(db.scalars(select(Notification).where(
        Notification.user_id == user_id, Notification.is_active.is_(True), Notification.is_read.is_(False))))
    for r in rows:
        r.is_read = True
    db.flush()
    return len(rows)
