"""Input validation shared by the service layer."""
from __future__ import annotations

import re
from datetime import date, datetime

import config
from utils.errors import ValidationError

_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    """Return the normalised e-mail or raise ``ValidationError``."""
    value = normalize_email(email)
    if not value:
        raise ValidationError("Email address is required.")
    if len(value) > 254 or ".." in value or not _EMAIL_RE.match(value):
        raise ValidationError("Enter a valid email address, for example name@example.com.")
    return value


def validate_password(password: str) -> str:
    if not password:
        raise ValidationError("Password is required.")
    if len(password) < config.PASSWORD_MIN_LENGTH:
        raise ValidationError(f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters long.")
    if len(password) > config.PASSWORD_MAX_LENGTH:
        raise ValidationError(f"Password must be at most {config.PASSWORD_MAX_LENGTH} characters long.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValidationError("Password must contain at least one letter and one number.")
    return password


def validate_text(value: str | None, field: str, *, max_len: int, required: bool = False) -> str:
    text = " ".join((value or "").split())  # trims and collapses whitespace/newlines
    if required and not text:
        raise ValidationError(f"{field} is required.")
    if len(text) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return text


def validate_full_name(name: str) -> str:
    return validate_text(name, "Full name", max_len=100, required=True)


def validate_choice(value: str | None, allowed: tuple[str, ...] | list[str] | frozenset[str], field: str) -> str:
    if value not in allowed:
        raise ValidationError(f"{field} is not valid.")
    return value  # type: ignore[return-value]


def validate_date(value: object, field: str = "Date", *, allow_future: bool = False, today: date | None = None) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValidationError(f"{field} is required and must be a valid date.")
    if not allow_future and value > (today or date.today()):
        raise ValidationError(f"{field} cannot be in the future.")
    if value.year < 2000:
        raise ValidationError(f"{field} is too far in the past.")
    return value
