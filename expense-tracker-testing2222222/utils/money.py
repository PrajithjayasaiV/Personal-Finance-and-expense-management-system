"""Exact money handling.

All amounts are stored and calculated as integer *paise* (1 rupee = 100 paise).
Floats are only used at the very edge: parsing user input and drawing charts.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import config
from utils.errors import ValidationError

PAISE_PER_RUPEE = 100
_CENT = Decimal("0.01")


def to_decimal(value: object, field: str = "Amount") -> Decimal:
    """Convert user/UI input into a finite ``Decimal`` (no float artefacts)."""
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field} is required.")
    try:
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, int):
            result = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValidationError(f"{field} must be a valid number.")
            result = Decimal(str(value))  # shortest repr: 0.1 -> "0.1", not 0.1000000000000000055
        elif isinstance(value, str):
            cleaned = value.strip().replace(",", "")
            for symbol in config.CURRENCIES.values():
                cleaned = cleaned.replace(symbol, "")
            if not cleaned:
                raise ValidationError(f"{field} is required.")
            result = Decimal(cleaned.strip())
        else:
            raise ValidationError(f"{field} must be a number.")
    except InvalidOperation as exc:
        raise ValidationError(f"{field} must be a valid number.") from exc
    if not result.is_finite():
        raise ValidationError(f"{field} must be a valid number.")
    return result


def to_paise(value: object, field: str = "Amount", *, allow_zero: bool = False) -> int:
    """Validate an amount in rupees and return it as integer paise.

    Rounds half-up to two decimal places. Rejects zero (unless ``allow_zero``),
    negative numbers and absurdly large values.
    """
    amount = to_decimal(value, field).quantize(_CENT, rounding=ROUND_HALF_UP)
    if amount < 0:
        raise ValidationError(f"{field} cannot be negative.")
    if amount == 0 and not allow_zero:
        raise ValidationError(f"{field} must be greater than zero.")
    paise = int(amount * PAISE_PER_RUPEE)
    if paise > config.MAX_AMOUNT_PAISE:
        raise ValidationError(f"{field} is too large.")
    return paise


def paise_to_decimal(paise: int) -> Decimal:
    """Exact rupee value of an integer paise amount."""
    return (Decimal(int(paise)) / PAISE_PER_RUPEE).quantize(_CENT)


def paise_to_float(paise: int) -> float:
    """Rupee value as float - for charts and widgets only, never for totals."""
    return float(paise_to_decimal(paise))


def percentage(numerator: int, denominator: int, digits: int = 2) -> float | None:
    """``numerator / denominator * 100`` rounded half-up; ``None`` if undefined.

    Returns ``None`` instead of raising or producing NaN/Infinity when the
    denominator is zero or negative.
    """
    if denominator is None or numerator is None or denominator <= 0:
        return None
    ratio = Decimal(int(numerator)) * 100 / Decimal(int(denominator))
    return float(ratio.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
