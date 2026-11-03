"""SQLAlchemy ORM models.

Money is stored as integer paise. Ownership is enforced twice: in the service
layer, and in the database through composite foreign keys such as
``(category_id, user_id) -> categories(id, user_id)`` so a row can never point
at another user's category even if application code has a bug.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import config


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no timezone type)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # normalised (lower-case)
    password_hash: Mapped[str] = mapped_column(String(255))
    opening_balance: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default=config.DEFAULT_CURRENCY)
    date_format: Mapped[str] = mapped_column(String(20), default=config.DEFAULT_DATE_FORMAT)
    min_reserve: Mapped[int] = mapped_column(BigInteger, default=0)
    upcoming_commitments: Mapped[int] = mapped_column(BigInteger, default=0)
    default_range: Mapped[str] = mapped_column(String(20), default="current_month")
    notify_budget: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_goals: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_insights: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("opening_balance >= 0", name="ck_user_opening_nonneg"),
        CheckConstraint("min_reserve >= 0", name="ck_user_reserve_nonneg"),
        CheckConstraint("upcoming_commitments >= 0", name="ck_user_commitments_nonneg"),
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    name_key: Mapped[str] = mapped_column(String(50))  # casefolded name for case-insensitive uniqueness
    type: Mapped[str] = mapped_column(String(10))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "type", "name_key", name="uq_category_user_type_name"),
        UniqueConstraint("id", "user_id", name="uq_category_id_user"),
        UniqueConstraint("id", "user_id", "type", name="uq_category_id_user_type"),
        CheckConstraint("type IN ('income','expense')", name="ck_category_type"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(10))
    amount: Mapped[int] = mapped_column(BigInteger)  # paise, always > 0
    txn_date: Mapped[date] = mapped_column(Date)
    payment_method: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        # The category must belong to the same user AND have the same type.
        ForeignKeyConstraint(
            ["category_id", "user_id", "type"],
            ["categories.id", "categories.user_id", "categories.type"],
            name="fk_txn_category_owner_type", ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0", name="ck_txn_amount_positive"),
        CheckConstraint("type IN ('income','expense')", name="ck_txn_type"),
        Index("ix_txn_user_date", "user_id", "txn_date"),
        Index("ix_txn_user_category", "user_id", "category_id"),
    )


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))  # 'YYYY-MM'
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL = overall budget
    limit_amount: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "user_id"], ["categories.id", "categories.user_id"],
            name="fk_budget_category_owner", ondelete="CASCADE",
        ),
        CheckConstraint("limit_amount > 0", name="ck_budget_limit_positive"),
        # NULLs are distinct in SQLite unique indexes, so overall budgets need their own partial index.
        Index("uq_budget_user_month_category", "user_id", "month", "category_id", unique=True,
              sqlite_where=text("category_id IS NOT NULL")),
        Index("uq_budget_user_month_overall", "user_id", "month", unique=True,
              sqlite_where=text("category_id IS NULL")),
    )


class SavingsGoal(Base):
    __tablename__ = "savings_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    target_amount: Mapped[int] = mapped_column(BigInteger)
    start_date: Mapped[date] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=config.GOAL_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_goal_id_user"),
        CheckConstraint("target_amount > 0", name="ck_goal_target_positive"),
        CheckConstraint("status IN ('active','completed','paused')", name="ck_goal_status"),
    )


class GoalContribution(Base):
    __tablename__ = "goal_contributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    goal_id: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    contribution_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        ForeignKeyConstraint(
            ["goal_id", "user_id"], ["savings_goals.id", "savings_goals.user_id"],
            name="fk_contribution_goal_owner", ondelete="CASCADE",
        ),
        CheckConstraint("amount > 0", name="ck_contribution_amount_positive"),
        Index("ix_contribution_goal", "goal_id", "contribution_date"),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(30))  # budget_warning, budget_exceeded, goal_milestone, ...
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(1000))
    severity: Mapped[str] = mapped_column(String(10), default="info")
    ref_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # budget | goal | transaction | insight
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False once the condition no longer holds
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notification_user_key"),
        Index("ix_notification_user_read", "user_id", "is_active", "is_read"),
    )


class InsightFeedback(Base):
    """Remembers insights the user acknowledged or dismissed."""

    __tablename__ = "insight_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    insight_key: Mapped[str] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(15))  # acknowledged | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "insight_key", name="uq_feedback_user_key"),
        CheckConstraint("state IN ('acknowledged','dismissed')", name="ck_feedback_state"),
    )
