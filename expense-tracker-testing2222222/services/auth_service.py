"""Registration, login, session helpers and user settings."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import MutableMapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

import config
from models import User
from services import transaction_service
from utils.dates import DASHBOARD_PRESETS
from utils.errors import AuthenticationError, ConflictError, ValidationError
from utils.money import to_paise
from utils.validators import (
    normalize_email, validate_choice, validate_email, validate_full_name, validate_password,
)

log = logging.getLogger(__name__)

SESSION_USER_KEY = "auth_user_id"


@dataclass(frozen=True)
class UserProfile:
    """Detached, read-only snapshot of a user for the UI (never contains the hash)."""

    id: int
    full_name: str
    email: str
    opening_balance: int
    currency: str
    date_format: str
    min_reserve: int
    upcoming_commitments: int
    default_range: str
    notify_budget: bool
    notify_goals: bool
    notify_insights: bool


def to_profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id, full_name=user.full_name, email=user.email, opening_balance=user.opening_balance,
        currency=user.currency, date_format=user.date_format, min_reserve=user.min_reserve,
        upcoming_commitments=user.upcoming_commitments, default_range=user.default_range,
        notify_budget=user.notify_budget, notify_goals=user.notify_goals, notify_insights=user.notify_insights,
    )


# ---------------------------------------------------------------- hashing / throttle
def hash_password(password: str) -> str:
    return generate_password_hash(password, method=config.PASSWORD_HASH_METHOD)


_failures: dict[str, list[float]] = {}
_dummy_hash: str | None = None


def reset_login_throttle() -> None:
    _failures.clear()


def _recent_failures(email_key: str) -> list[float]:
    cutoff = time.time() - config.LOGIN_LOCKOUT_SECONDS
    recent = [t for t in _failures.get(email_key, []) if t > cutoff]
    _failures[email_key] = recent
    return recent


def _check_throttle(email_key: str) -> None:
    recent = _recent_failures(email_key)
    if len(recent) >= config.LOGIN_MAX_FAILURES:
        wait = max(1, int((recent[0] + config.LOGIN_LOCKOUT_SECONDS - time.time()) / 60) + 1)
        raise AuthenticationError(f"Too many failed sign-in attempts. Please try again in about {wait} minute(s).")


# ---------------------------------------------------------------- registration / login
def register_user(
    db: Session, full_name: str, email: str, password: str, confirm_password: str,
    opening_balance: object = 0, currency: str = config.DEFAULT_CURRENCY,
) -> User:
    """Create an account and seed default categories. Raises ``ServiceError`` subclasses."""
    name = validate_full_name(full_name)
    email_key = validate_email(email)
    validate_password(password)
    if password != confirm_password:
        raise ValidationError("Passwords do not match.")
    validate_choice(currency, tuple(config.CURRENCIES), "Currency")
    opening = to_paise(opening_balance or 0, "Opening balance", allow_zero=True)

    user = User(
        full_name=name, email=email_key, password_hash=hash_password(password),
        opening_balance=opening, currency=currency,
    )
    try:
        with db.begin_nested():  # savepoint: a duplicate does not poison the caller's transaction
            db.add(user)
            db.flush()
    except IntegrityError as exc:  # unique index also protects against simultaneous registrations
        raise ConflictError("An account with this email address already exists.") from exc

    transaction_service.seed_default_categories(db, user.id)
    log.info("User registered (id=%s)", user.id)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    """Verify credentials. The error message never reveals whether the email exists."""
    global _dummy_hash
    email_key = normalize_email(email)
    _check_throttle(email_key)
    user = db.scalar(select(User).where(User.email == email_key)) if email_key else None
    if _dummy_hash is None:
        _dummy_hash = hash_password("not-a-real-password-1")
    # Always verify a hash so response time does not reveal which emails exist.
    valid = check_password_hash(user.password_hash if user else _dummy_hash, password or "")
    if user is None or not valid:
        _failures.setdefault(email_key, []).append(time.time())
        log.info("Failed sign-in attempt")
        raise AuthenticationError("Invalid email or password.")
    _failures.pop(email_key, None)
    return user


# ---------------------------------------------------------------- session helpers
def end_session(state: MutableMapping) -> None:
    """Remove every key (identity, filters, cached widgets) from the session."""
    for key in list(state.keys()):
        del state[key]


def start_session(state: MutableMapping, user_id: int) -> None:
    end_session(state)  # never carry over anything from a previous user
    state[SESSION_USER_KEY] = int(user_id)


def session_user_id(state: MutableMapping) -> int | None:
    uid = state.get(SESSION_USER_KEY)
    return uid if isinstance(uid, int) and not isinstance(uid, bool) else None


def get_authenticated_user(db: Session, state: MutableMapping) -> User:
    """Re-validate the session against the database on every call."""
    uid = session_user_id(state)
    user = db.get(User, uid) if uid is not None else None
    if user is None:
        if uid is not None:
            end_session(state)
        raise AuthenticationError("Please sign in to continue.")
    return user


# ---------------------------------------------------------------- profile & settings
def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AuthenticationError("Account not found. Please sign in again.")
    return user


def get_user(db: Session, user_id: int) -> User:
    return _get_user(db, user_id)


def update_settings(
    db: Session, user_id: int, *, full_name: str | None = None, currency: str | None = None,
    date_format: str | None = None, min_reserve: object = None, upcoming_commitments: object = None,
    default_range: str | None = None, notify_budget: bool | None = None,
    notify_goals: bool | None = None, notify_insights: bool | None = None,
) -> User:
    user = _get_user(db, user_id)
    if full_name is not None:
        user.full_name = validate_full_name(full_name)
    if currency is not None:
        user.currency = validate_choice(currency, tuple(config.CURRENCIES), "Currency")
    if date_format is not None:
        user.date_format = validate_choice(date_format, tuple(config.DATE_FORMATS.values()), "Date format")
    if min_reserve is not None:
        user.min_reserve = to_paise(min_reserve, "Reserve amount", allow_zero=True)
    if upcoming_commitments is not None:
        user.upcoming_commitments = to_paise(upcoming_commitments, "Upcoming commitments", allow_zero=True)
    if default_range is not None:
        user.default_range = validate_choice(default_range, DASHBOARD_PRESETS[:-1], "Default date range")
    if notify_budget is not None:
        user.notify_budget = bool(notify_budget)
    if notify_goals is not None:
        user.notify_goals = bool(notify_goals)
    if notify_insights is not None:
        user.notify_insights = bool(notify_insights)
    db.flush()
    return user


def update_opening_balance(db: Session, user_id: int, amount: object) -> User:
    user = _get_user(db, user_id)
    user.opening_balance = to_paise(amount, "Opening balance", allow_zero=True)
    db.flush()
    return user


def change_password(db: Session, user_id: int, current_password: str, new_password: str, confirm: str) -> None:
    user = _get_user(db, user_id)
    if not check_password_hash(user.password_hash, current_password or ""):
        raise AuthenticationError("Your current password is incorrect.")
    validate_password(new_password)
    if new_password != confirm:
        raise ValidationError("New passwords do not match.")
    user.password_hash = hash_password(new_password)
    db.flush()
