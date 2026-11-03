"""Display formatting: money, percentages, dates, safe text."""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone

import config


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def currency_symbol(currency: str | None) -> str:
    return config.CURRENCIES.get(currency or config.DEFAULT_CURRENCY, config.CURRENCIES["INR"])


def format_money(paise: int | None, currency: str = config.DEFAULT_CURRENCY, *, signed: bool = False) -> str:
    """Format integer paise, e.g. 12505000 -> '₹1,25,050.00' (Indian grouping for INR)."""
    if paise is None:
        return "—"
    paise = int(paise)
    sign = "-" if paise < 0 else ("+" if signed and paise > 0 else "")
    rupees, cents = divmod(abs(paise), 100)
    digits = str(rupees)
    grouped = _group_indian(digits) if currency == "INR" else f"{rupees:,}"
    return f"{sign}{currency_symbol(currency)}{grouped}.{cents:02d}"


def format_pct(value: float | None, digits: int = 1, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def format_date(d: date | None, fmt: str = config.DEFAULT_DATE_FORMAT) -> str:
    return d.strftime(fmt) if d else "—"


def format_datetime(dt: datetime | None, fmt: str = config.DEFAULT_DATE_FORMAT) -> str:
    """Render a stored (naive UTC) timestamp in local time."""
    if dt is None:
        return "—"
    local = dt.replace(tzinfo=timezone.utc).astimezone()
    return local.strftime(f"{fmt} %H:%M")


def format_range(start: date | None, end: date | None, fmt: str = config.DEFAULT_DATE_FORMAT) -> str:
    if start is None and end is None:
        return "All time"
    return f"{format_date(start, fmt)} to {format_date(end, fmt)}"


def widget_date_format(fmt: str) -> str:
    """Map a strftime format to the formats Streamlit's date_input accepts."""
    return {"%d-%m-%Y": "DD-MM-YYYY", "%Y-%m-%d": "YYYY-MM-DD", "%m/%d/%Y": "MM/DD/YYYY"}.get(fmt, "DD-MM-YYYY")


def escape_html(text: object) -> str:
    return html.escape(str(text), quote=True)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#~$|])")


def escape_md(text: object) -> str:
    """Escape Markdown/LaTeX control characters in user-supplied text."""
    return _MD_SPECIAL.sub(r"\\\1", str(text))
