"""Central configuration: paths, constants and shared defaults.

Nothing in this module touches the database or Streamlit, so it can be
imported safely from anywhere (services, tests, UI).
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

APP_NAME = "Personal Finance and Expense Management System"
APP_BRAND = "Smart Finance"

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
DATA_DIR = BASE_DIR / "data"


def _resolve_db_path() -> Path:
    raw = os.getenv("EXPENSE_TRACKER_DB_PATH", "").strip()
    if not raw:
        return DATA_DIR / "expense_tracker.db"
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


DB_PATH = _resolve_db_path()
LOG_PATH = DATA_DIR / "app.log"
LOG_LEVEL = os.getenv("EXPENSE_TRACKER_LOG_LEVEL", "INFO").upper()

# --------------------------------------------------------------------------
# Money / currency
# --------------------------------------------------------------------------
DEFAULT_CURRENCY = "INR"
# Display symbols only. Changing the currency never converts stored amounts.
CURRENCIES = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
# Upper bound for a single amount (₹10,00,00,00,000.00) to keep arithmetic sane.
MAX_AMOUNT_PAISE = 10**12

# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------
# Werkzeug hash method string. Tests lower the iteration count for speed.
PASSWORD_HASH_METHOD = "pbkdf2:sha256:600000"
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60

# --------------------------------------------------------------------------
# Domain constants
# --------------------------------------------------------------------------
INCOME = "income"
EXPENSE = "expense"
TRANSACTION_TYPES = (INCOME, EXPENSE)

PAYMENT_METHODS = ("Cash", "UPI", "Debit Card", "Credit Card", "Bank Transfer", "Other")

DEFAULT_INCOME_CATEGORIES = (
    "Salary", "Allowance", "Freelance Income", "Scholarship",
    "Business Income", "Investment Income", "Gift", "Other Income",
)
DEFAULT_EXPENSE_CATEGORIES = (
    "Food", "Groceries", "Transportation", "Education", "Rent", "Utilities",
    "Shopping", "Healthcare", "Entertainment", "Subscriptions",
    "Personal Care", "Travel", "Family", "Miscellaneous",
)
# Categories treated as "essential" when budget suggestions must be trimmed.
ESSENTIAL_CATEGORY_NAMES = frozenset(
    {"rent", "utilities", "education", "healthcare", "groceries", "family"}
)

DATE_FORMATS = {
    "DD-MM-YYYY": "%d-%m-%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD Mon YYYY": "%d %b %Y",
    "MM/DD/YYYY": "%m/%d/%Y",
}
DEFAULT_DATE_FORMAT = "%d-%m-%Y"

# Budget thresholds (percent of the limit that has been used).
BUDGET_WARNING_PCT = 80
BUDGET_EXCEEDED_PCT = 100

# Savings goals
GOAL_ACTIVE, GOAL_COMPLETED, GOAL_PAUSED = "active", "completed", "paused"
GOAL_STATUSES = (GOAL_ACTIVE, GOAL_COMPLETED, GOAL_PAUSED)
GOAL_MILESTONES = (25, 50, 75, 100)
GOAL_DEADLINE_WARNING_DAYS = 14

PAGE_SIZE_OPTIONS = (10, 25, 50, 100)


def setup_logging() -> None:
    """Configure application logging once (console + rotating file)."""
    root = logging.getLogger()
    if getattr(root, "_expense_tracker_configured", False):
        return
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(level)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:  # read-only folder etc. - fall back to console only
        pass
    root._expense_tracker_configured = True  # type: ignore[attr-defined]
