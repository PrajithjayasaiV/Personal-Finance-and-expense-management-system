"""Helpers shared by all services."""
from __future__ import annotations

from typing import TypeVar

from sqlalchemy.orm import Session

from utils.errors import AuthorizationError, NotFoundError

T = TypeVar("T")


def get_owned(db: Session, model: type[T], obj_id: object, user_id: int, label: str = "record") -> T:
    """Load ``model`` row ``obj_id`` and verify it belongs to ``user_id``.

    * Unknown id        -> ``NotFoundError``
    * Another user's id -> ``AuthorizationError`` (the row is never returned)
    """
    try:
        key = int(obj_id)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise NotFoundError(f"{label.capitalize()} not found.") from exc
    obj = db.get(model, key)
    if obj is None:
        raise NotFoundError(f"{label.capitalize()} not found.")
    if getattr(obj, "user_id", None) != user_id:
        raise AuthorizationError(f"You do not have access to this {label}.")
    return obj
