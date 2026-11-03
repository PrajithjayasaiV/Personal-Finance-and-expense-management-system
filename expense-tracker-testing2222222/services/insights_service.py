"""Transparent, rule-based spending insights, budget recommendations and Safe-to-Spend.

No machine learning, no external service: every insight is a documented rule
over the user's own transactions, and carries the evidence that produced it.
Rules stay silent when the data cannot support them.

Thresholds (all amounts in paise):

* MIN_BASELINE        previous-period spending needed before a category can be compared (Rs 500)
* MIN_CHANGE/_PCT     smallest change worth mentioning (Rs 200 AND 20%)
* STRONG_CHANGE/_PCT  change flagged as an "unusual increase" (Rs 1,000 AND 50%)
* COVERAGE_GRACE_DAYS the user's first record must be within 7 days of a period's start for
                      that period to count as fully observed
* TOP_CATEGORY_SHARE_PCT / MIN_TXNS_FOR_SHARE  largest-category rule (>= 25% share, >= 3 expenses)
* REPEAT_MIN_BUDGETED_MONTHS / REPEAT_MIN_EXCEEDED  repeated overspending (over budget in >= 2 of the last 3 budgeted months)
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import config
from models import Budget, Category, InsightFeedback, Transaction, User
from services import analytics_service as an
from services import budget_service as bs
from services import savings_service as sv
from utils.dates import (
    days_in_month, month_bounds, month_key, month_label, parse_month, period_key,
    previous_comparable_period, shift_month,
)
from utils.errors import ValidationError
from utils.formatting import format_money, format_pct, format_range
from utils.money import to_paise

log = logging.getLogger(__name__)

MIN_BASELINE = 50_000
MIN_CHANGE = 20_000
MIN_CHANGE_PCT = 20.0
STRONG_CHANGE = 100_000
STRONG_CHANGE_PCT = 50.0
COVERAGE_GRACE_DAYS = 7
TOP_CATEGORY_SHARE_PCT = 25.0
MIN_TXNS_FOR_SHARE = 3
REPEAT_MIN_BUDGETED_MONTHS = 3
REPEAT_MIN_EXCEEDED = 2
VOLATILITY_CV = 0.5           # coefficient of variation above which spending is "irregular"
MAX_REDUCTION_PCT = 40        # discretionary suggestions may be trimmed by at most this much
ROUND_TO = 1_000              # suggestions are rounded up to the next Rs 10

SEVERITY_PRIORITY = {"critical": 100, "warning": 80, "info": 40, "success": 30}


@dataclass
class Insight:
    key: str
    kind: str
    severity: str             # critical | warning | info | success
    title: str
    message: str
    evidence: str = ""
    priority: int = 0
    notify: bool = False
    hidden: bool = False


# ====================================================================== feedback (dismiss / acknowledge)
def set_feedback(db: Session, user_id: int, key: str, state: str) -> None:
    if state not in ("acknowledged", "dismissed"):
        raise ValidationError("Unknown feedback state.")
    row = db.scalar(select(InsightFeedback).where(InsightFeedback.user_id == user_id, InsightFeedback.insight_key == key))
    if row:
        row.state = state
    else:
        db.add(InsightFeedback(user_id=user_id, insight_key=key[:160], state=state))
    db.flush()


def hidden_keys(db: Session, user_id: int) -> set[str]:
    return set(db.scalars(select(InsightFeedback.insight_key).where(InsightFeedback.user_id == user_id)))


def clear_feedback(db: Session, user_id: int) -> int:
    result = db.execute(delete(InsightFeedback).where(InsightFeedback.user_id == user_id))
    db.flush()
    return result.rowcount or 0


# ====================================================================== insight rules
def is_period_covered(first: date | None, period_start: date) -> bool:
    """True if the user was already recording by the start of the period."""
    return first is not None and first <= period_start + timedelta(days=COVERAGE_GRACE_DAYS)


def _spending_change_insights(db, user_id, start, end, first, cur, fmt) -> list[Insight]:
    """Rules 12.1 + 12.4: category and overall changes versus the comparable previous period."""
    prev_start, prev_end = previous_comparable_period(start, end)
    if not is_period_covered(first, prev_start):
        return []
    pk = period_key(start, end)
    now_r, prev_r = format_range(start, end, fmt), format_range(prev_start, prev_end, fmt)
    now = {c.category_id: c for c in an.category_totals(db, user_id, start, end)}
    before = {c.category_id: c for c in an.category_totals(db, user_id, prev_start, prev_end)}
    increases: list[tuple[int, Insight]] = []
    decreases: list[tuple[int, Insight]] = []
    for cid in set(now) | set(before):
        base = before[cid].total if cid in before else 0
        if base < MIN_BASELINE:
            continue
        name = (now.get(cid) or before[cid]).name
        value = now[cid].total if cid in now else 0
        change = value - base
        pct = an.signed_pct(change, base)
        evidence = f"{name}: {format_money(value, cur)} for {now_r} versus {format_money(base, cur)} for {prev_r}."
        if change >= MIN_CHANGE and pct is not None and pct >= MIN_CHANGE_PCT:
            strong = change >= STRONG_CHANGE and pct >= STRONG_CHANGE_PCT
            msg = (f"Your {name} spending increased from {format_money(base, cur)} to {format_money(value, cur)}, "
                   f"an increase of {format_money(change, cur)} or approximately {format_pct(pct, 2)}.")
            if strong:
                msg += " This is much higher than the previous period. It may be planned, but it is worth a quick look."
                insight = Insight(f"cat_change:{cid}:{pk}", "unusual_increase", "warning", f"{name} spending is much higher",
                                  msg, evidence, SEVERITY_PRIORITY["warning"] + min(int(pct) // 10, 9), notify=True)
            else:
                insight = Insight(f"cat_change:{cid}:{pk}", "category_increase", "info", f"{name} spending increased",
                                  msg, evidence, SEVERITY_PRIORITY["info"] + min(int(pct) // 10, 9))
            increases.append((change, insight))
        elif change <= -MIN_CHANGE and pct is not None and pct <= -MIN_CHANGE_PCT:
            msg = (f"Your {name} spending fell from {format_money(base, cur)} to {format_money(value, cur)}, "
                   f"a decrease of {format_money(-change, cur)} or approximately {format_pct(abs(pct), 2)}.")
            decreases.append((-change, Insight(f"cat_change:{cid}:{pk}", "category_decrease", "success",
                                               f"{name} spending decreased", msg, evidence, SEVERITY_PRIORITY["success"])))
    out = [i for _, i in sorted(increases, key=lambda t: -t[0])[:3]] + [i for _, i in sorted(decreases, key=lambda t: -t[0])[:1]]

    total_now = sum(c.total for c in now.values())
    total_before = sum(c.total for c in before.values())
    change = total_now - total_before
    pct = an.signed_pct(change, total_before)
    if total_before >= MIN_BASELINE and pct is not None and abs(change) >= MIN_CHANGE and abs(pct) >= 10:
        lower = change < 0
        out.append(Insight(
            f"total_change:{pk}", "spending_change", "success" if lower else "info",
            "Spending is lower than the previous period" if lower else "Spending is higher than the previous period",
            f"Recorded spending is {format_money(abs(change), cur)} ({format_pct(abs(pct), 2)}) {'lower' if lower else 'higher'} "
            f"than the previous comparable period.",
            f"{format_money(total_now, cur)} for {now_r} versus {format_money(total_before, cur)} for {prev_r}.",
            SEVERITY_PRIORITY["success" if lower else "info"] - 5))
    return out


def _top_category_insight(db, user_id, start, end, cur, fmt) -> list[Insight]:
    """Rule 12.2: the category with the largest share of expenses."""
    cats = an.category_totals(db, user_id, start, end)
    count = db.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.user_id == user_id, Transaction.type == config.EXPENSE,
        Transaction.txn_date >= start, Transaction.txn_date <= end)) or 0
    if not cats or count < MIN_TXNS_FOR_SHARE:
        return []
    top, grand = cats[0], sum(c.total for c in cats)
    if top.share_pct is None or top.share_pct < TOP_CATEGORY_SHARE_PCT:
        return []
    return [Insight(
        f"top_category:{top.category_id}:{period_key(start, end)}", "top_category", "info",
        f"{top.name} is your biggest expense",
        f"{top.name} accounts for {format_pct(top.share_pct, 1)} of your recorded expenses for this period.",
        f"{format_money(top.total, cur)} of {format_money(grand, cur)} total expenses ({format_range(start, end, fmt)}, {count} expense records).",
        SEVERITY_PRIORITY["info"] + 5)]


def _cash_flow_insight(db, user_id, start, end, cur, fmt) -> list[Insight]:
    totals = an.period_totals(db, user_id, start, end)
    if totals.income <= 0 or totals.expense <= totals.income:
        return []
    return [Insight(
        f"negative_flow:{period_key(start, end)}", "negative_cash_flow", "warning",
        "Spending is higher than income",
        f"Recorded expenses are {format_money(totals.expense - totals.income, cur)} higher than recorded income "
        f"(savings rate {format_pct(totals.savings_rate, 1)}).",
        f"Income {format_money(totals.income, cur)} and expenses {format_money(totals.expense, cur)} for {format_range(start, end, fmt)}. "
        "If some income is not recorded here, this may look worse than it is.",
        SEVERITY_PRIORITY["warning"] + 5)]


def _budget_insights(db, user_id, today, cur) -> list[Insight]:
    month = month_key(today)
    out = []
    for s in bs.month_statuses(db, user_id, month):
        if s.level == bs.OK:
            continue
        exceeded = s.level == bs.EXCEEDED
        detail = (f"over by {format_money(s.exceeded_by, cur)}" if s.exceeded_by else "fully used") if exceeded \
            else f"{format_money(s.remaining, cur)} remaining"
        scope = "overall" if s.is_overall else s.label
        out.append(Insight(
            f"budget:{s.budget_id}:{s.level}", "budget_exceeded" if exceeded else "budget_warning",
            "critical" if exceeded else "warning",
            f"{'Overall' if s.is_overall else s.label} budget {'reached or exceeded' if exceeded else 'is nearly used'}",
            f"You have used {format_pct(s.utilization, 1)} of your {scope} budget for {month_label(month)} ({detail}).",
            f"Spent {format_money(s.used, cur)} of {format_money(s.limit, cur)}.",
            SEVERITY_PRIORITY["critical" if exceeded else "warning"] + (5 if s.is_overall else 0)))
    return out


def _overspend_insights(db, user_id, today, cur) -> list[Insight]:
    """Rule 12.3: over budget in at least 2 of the last 3 budgeted months (needs >= 3 budgeted months)."""
    month = month_key(today)
    cat_ids = db.scalars(select(Budget.category_id).where(
        Budget.user_id == user_id, Budget.category_id.is_not(None), Budget.month <= month).distinct())
    names = {c.id: c.name for c in db.scalars(select(Category).where(Category.user_id == user_id))}
    out = []
    for cid in cat_ids:
        history = bs.category_budget_history(db, user_id, cid, month, REPEAT_MIN_BUDGETED_MONTHS)
        if len(history) < REPEAT_MIN_BUDGETED_MONTHS:
            continue
        over = [(m, s) for m, s in history if s.used > s.limit]
        if len(over) < REPEAT_MIN_EXCEEDED:
            continue
        avg_used = round(sum(s.used for _, s in history) / len(history))
        months_txt = ", ".join(month_label(m, short=True) for m, _ in over)
        out.append(Insight(
            f"overspend:{cid}:{month}", "repeated_overspending", "warning",
            f"{names.get(cid, 'A category')} keeps going over budget",
            f"{names.get(cid, 'This category')} exceeded its budget in {len(over)} of your last {len(history)} budgeted months ({months_txt}). "
            f"Review recent purchases in this category, or set a budget closer to what you really spend (average {format_money(avg_used, cur)}).",
            "; ".join(f"{month_label(m, short=True)}: {format_money(s.used, cur)} of {format_money(s.limit, cur)}" for m, s in history),
            SEVERITY_PRIORITY["warning"] + 8, notify=True))
    return out


def _goal_insights(db, user_id, today, cur) -> list[Insight]:
    out = []
    for p in sv.list_progress(db, user_id, today, include_completed=False):
        if p.status != config.GOAL_ACTIVE or p.track_status not in (sv.BEHIND, sv.OVERDUE, sv.NO_PACE):
            continue
        if p.track_status == sv.OVERDUE:
            msg = f"The target date for '{p.name}' has passed and {format_money(p.remaining, cur)} is still to be saved."
        else:
            need = format_money(p.required_monthly, cur) if p.required_monthly else "more"
            pace = format_money(p.monthly_pace, cur)
            msg = (f"To reach '{p.name}' by its target date you would need to save about {need} per month. "
                   f"Your recent pace is about {pace} per month.")
        out.append(Insight(f"goal:{p.goal_id}:{p.track_status}:{month_key(today)}", "goal_behind", "warning",
                           f"Savings goal '{p.name}' needs attention", msg,
                           f"{format_money(p.saved, cur)} saved of {format_money(p.target, cur)}. Projections are estimates only.",
                           SEVERITY_PRIORITY["warning"] - 5))
    return out


def generate_insights(db: Session, user_id: int, start: date, end: date, *, today: date | None = None,
                      include_hidden: bool = False, limit: int | None = 6) -> list[Insight]:
    """The most relevant insights for a period, highest priority first."""
    today = today or date.today()
    user = db.get(User, user_id)
    if user is None:
        return []
    first = an.first_transaction_date(db, user_id)
    if first is None:
        return []
    cur, fmt = user.currency, user.date_format
    found: list[Insight] = []
    found += _budget_insights(db, user_id, today, cur)
    found += _overspend_insights(db, user_id, today, cur)
    found += _spending_change_insights(db, user_id, start, end, first, cur, fmt)
    found += _top_category_insight(db, user_id, start, end, cur, fmt)
    found += _cash_flow_insight(db, user_id, start, end, cur, fmt)
    found += _goal_insights(db, user_id, today, cur)
    hidden = hidden_keys(db, user_id)
    result = []
    for ins in sorted(found, key=lambda i: (-i.priority, i.key)):
        ins.hidden = ins.key in hidden
        if ins.hidden and not include_hidden:
            continue
        result.append(ins)
    return result[:limit] if limit else result


# ====================================================================== Safe-to-Spend
@dataclass(frozen=True)
class SafeToSpend:
    balance: int
    reserve: int
    commitments: int
    available: int                 # max(0, balance - reserve - commitments)
    monthly_budget: int | None
    budget_used: int | None
    remaining_budget: int | None   # may be negative
    estimate: int
    basis: str                     # "budget_limited" | "balance_limited" | "balance_only"
    explanation: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def safe_to_spend(db: Session, user_id: int, today: date | None = None) -> SafeToSpend:
    """Configurable, transparent estimate of money that can be spent without breaking the reserve or budget.

    1. available = max(0, recorded balance - reserve - upcoming commitments)
    2. With an overall budget for the current month: estimate = min(available, max(0, remaining budget))
    3. Without one: estimate = available (clearly labelled as not budget-checked)

    Expenses already recorded are inside the balance, so nothing is deducted twice.
    """
    today = today or date.today()
    user = db.get(User, user_id)
    if user is None:
        raise ValidationError("Account not found.")
    cur = user.currency
    balance = an.current_balance(db, user_id)
    reserve, commitments = user.min_reserve, user.upcoming_commitments
    available = max(0, balance - reserve - commitments)
    status = bs.overall_status(db, user_id, month_key(today))
    lines = [f"Current recorded balance: {format_money(balance, cur)} (opening balance + recorded income - recorded expenses)."]
    lines.append(f"Minus your reserve of {format_money(reserve, cur)}" + (
        f" and {format_money(commitments, cur)} of upcoming commitments you entered" if commitments else "") +
        f" leaves {format_money(available, cur)} (never below zero).")
    warnings = ["This is an estimate based only on what you have recorded here. Unrecorded income or expenses, pending payments "
                "and your real bank balance are not known."]
    if balance < 0:
        warnings.append("Your recorded balance is below zero, so nothing is considered safe to spend.")
    if status is None:
        basis, estimate, budget, used, remaining = "balance_only", available, None, None, None
        lines.append("No overall budget exists for this month, so this figure is based on balance and reserve only. "
                     "It is not checked against a monthly budget.")
    else:
        budget, used, remaining = status.limit, status.used, status.remaining
        lines.append(f"Remaining overall budget this month: {format_money(budget, cur)} - {format_money(used, cur)} spent = "
                     f"{format_money(remaining, cur)}.")
        if max(0, remaining) < available:
            basis, estimate = "budget_limited", max(0, remaining)
            lines.append("Your remaining budget is lower than the available balance, so the budget limits the estimate.")
        else:
            basis, estimate = "balance_limited", available
            lines.append("Your available balance is lower than the remaining budget, so the balance limits the estimate.")
        if remaining < 0:
            warnings.append(f"You are {format_money(-remaining, cur)} over this month's overall budget.")
    return SafeToSpend(balance, reserve, commitments, available, budget, used, remaining, estimate, basis, lines, warnings)


# ====================================================================== budget recommendations
@dataclass
class CategoryRecommendation:
    category_id: int
    category_name: str
    history: list[tuple[str, int]]    # (month label, amount) actually used
    weighted_average: int
    buffer_pct: int
    base_suggestion: int
    suggested: int
    current_budget: int | None
    confidence: str                   # high | medium | low
    protected: bool                   # essential category - not trimmed to fit an overall budget
    explanation: str
    adjustment: str = ""


@dataclass
class BudgetRecommendation:
    target_month: str
    basis_months: list[str]
    basis_label: str
    items: list[CategoryRecommendation]
    suggested_total: int
    overall_limit: int | None
    unresolved_gap: int               # how far suggestions still exceed the overall budget
    unallocated: int | None           # overall budget not assigned to any category
    confidence: str
    is_starting_estimate: bool
    average_income: int | None
    notes: list[str]
    assumptions: list[str]


def _round_up(value: int, step: int = ROUND_TO) -> int:
    return max(step, -(-value // step) * step)


def _build_item(cid: int, name: str, labels: list[str], series: list[int], cur: str, existing: int | None) -> CategoryRecommendation:
    n = len(series)
    weights = list(range(1, n + 1))                         # newest month weighs most
    wavg = round(sum(w * s for w, s in zip(weights, series)) / sum(weights))
    active = sum(1 for s in series if s > 0)
    mean = sum(series) / n
    cv = (statistics.pstdev(series) / mean) if n >= 2 and mean > 0 else None
    volatile = cv is not None and cv > VOLATILITY_CV
    sparse = n < 2 or active < 2
    buffer = 10 if (volatile or sparse) else 5
    reason = ("only one month of evidence" if sparse else "spending varies a lot between months") if buffer == 10 else "spending is fairly regular"
    raw = -(-wavg * (100 + buffer) // 100)
    base = _round_up(raw)
    conf = "high" if n >= 3 and active >= 3 else ("medium" if n >= 2 and active >= 2 else "low")
    history_txt = ", ".join(f"{lbl} {format_money(v, cur)}" for lbl, v in zip(labels, series))
    text = (f"Monthly spending used: {history_txt}. More recent months count more (weighted average {format_money(wavg, cur)}). "
            f"A {buffer}% buffer was added because {reason}, giving {format_money(raw, cur)}, rounded up to {format_money(base, cur)}.")
    return CategoryRecommendation(cid, name, list(zip(labels, series)), wavg, buffer, base, base, existing, conf,
                                  name.casefold() in config.ESSENTIAL_CATEGORY_NAMES, text)


def _fit_to_overall(items: list[CategoryRecommendation], cap: int, cur: str) -> tuple[int, list[str]]:
    """Trim discretionary suggestions (never essentials, never below 60%) so the total fits ``cap``."""
    notes: list[str] = []
    total = sum(i.suggested for i in items)
    if total <= cap:
        return 0, notes
    shortfall = total - cap
    flexible = [i for i in items if not i.protected]
    flex_total = sum(i.base_suggestion for i in flexible)
    max_cut = flex_total * MAX_REDUCTION_PCT // 100
    cut = min(shortfall, max_cut)
    if flex_total > 0 and cut > 0:
        for i in flexible:   # integer arithmetic only: scale by (flex_total - cut) / flex_total
            new = max(ROUND_TO, (i.base_suggestion * (flex_total - cut) // flex_total) // ROUND_TO * ROUND_TO)
            if new < i.suggested:
                i.adjustment = f"Reduced from {format_money(i.suggested, cur)} to fit your overall budget."
                i.suggested = new
    total = sum(i.suggested for i in items)
    gap = max(0, total - cap)
    if gap:
        notes.append(f"Even after trimming non-essential categories by up to {MAX_REDUCTION_PCT}%, the suggested total "
                     f"({format_money(total, cur)}) is {format_money(gap, cur)} above your overall budget of {format_money(cap, cur)}. "
                     "Essential categories (rent, utilities, education, healthcare, groceries, family) were not reduced. "
                     "Raise the overall budget, or lower individual category budgets yourself.")
    else:
        notes.append(f"Suggestions were first {format_money(shortfall, cur)} above your overall budget; non-essential categories "
                     "were reduced so the total now fits.")
    return gap, notes


def recommend_budgets(db: Session, user_id: int, target_month: str, *, lookback_months: int = 3,
                      overall_limit: object = None, today: date | None = None) -> BudgetRecommendation:
    """Suggest category budgets for ``target_month`` from real spending history (nothing is saved)."""
    parse_month(target_month)
    today = today or date.today()
    user = db.get(User, user_id)
    if user is None:
        raise ValidationError("Account not found.")
    cur = user.currency
    lookback = max(1, min(int(lookback_months), 12))
    cap = to_paise(overall_limit, "Overall budget") if overall_limit not in (None, "", 0, 0.0) else None
    cur_month = month_key(today)
    anchor = min(target_month, cur_month)
    first = an.first_transaction_date(db, user_id)
    names = {c.id: c.name for c in db.scalars(select(Category).where(Category.user_id == user_id, Category.type == config.EXPENSE))}
    existing = {b.category_id: b.limit_amount for b, _ in bs.list_budgets(db, user_id, target_month) if b.category_id is not None}

    candidates = [shift_month(anchor, -i) for i in range(lookback, 0, -1)]
    observed = [m for m in candidates if first is not None and is_period_covered(first, month_bounds(m)[0])]
    notes: list[str] = []
    series_by_cat: dict[int, list[int]] = {}
    labels: list[str] = []
    basis_months: list[str] = []
    basis_label = ""

    if observed:
        spend = an.monthly_category_spending(db, user_id, observed[0], observed[-1])
        labels = [month_label(m, short=True) for m in observed]
        basis_months = observed
        for cid, by_month in spend.items():
            series = [by_month.get(m, 0) for m in observed]
            if sum(series) > 0:
                series_by_cat[cid] = series
        basis_label = f"{len(observed)} completed month(s): " + ", ".join(labels)
        skipped = [m for m in candidates if m not in observed]
        if skipped:
            notes.append("Months before you started recording were ignored: " + ", ".join(month_label(m, short=True) for m in skipped) + ".")
    elif first is not None and today.day >= COVERAGE_GRACE_DAYS and is_period_covered(first, month_bounds(cur_month)[0]):
        start, _ = month_bounds(cur_month)
        spent = an.category_totals(db, user_id, start, today)
        factor_n, factor_d = days_in_month(today.year, today.month), today.day
        for c in spent:
            series_by_cat[c.category_id] = [round(c.total * factor_n / factor_d)]
        labels, basis_months = [f"{month_label(cur_month, short=True)} (projected)"], [cur_month]
        basis_label = f"this month so far (day {today.day} of {factor_n}), projected to month end"
        notes.append("There is no completed month of history yet, so this is a starting estimate projected from the current month.")
    if not series_by_cat:
        notes.append("There is not enough expense history to suggest budgets. Record expenses for a few weeks and try again.")

    items = [_build_item(cid, names.get(cid, "Unknown"), labels, s, cur, existing.get(cid)) for cid, s in series_by_cat.items()]
    items.sort(key=lambda i: -i.suggested)
    gap, unallocated = 0, None
    if cap and items:
        gap, fit_notes = _fit_to_overall(items, cap, cur)
        notes += fit_notes
    total = sum(i.suggested for i in items)
    if cap:
        unallocated = max(0, cap - total)
        if unallocated and items:
            notes.append(f"{format_money(unallocated, cur)} of your overall budget is not assigned to any category (available as a buffer or for saving).")

    avg_income = None
    if observed:
        income = an.period_totals(db, user_id, month_bounds(observed[0])[0], month_bounds(observed[-1])[1]).income
        avg_income = round(income / len(observed))
        if avg_income and total > avg_income:
            notes.append(f"The suggested total ({format_money(total, cur)}) is higher than your average recorded monthly income "
                         f"({format_money(avg_income, cur)}).")
    n_obs = len(observed)
    starting = n_obs < 2
    confidence = "high" if n_obs >= 3 else ("medium" if n_obs == 2 else "low")
    if starting and items:
        notes.append("Starting estimate: fewer than two months of history, so treat these numbers as a rough guide rather than a pattern.")
    assumptions = [
        f"History: up to {lookback} completed month(s) before {month_label(min(target_month, cur_month))}; the unfinished current month is not used.",
        "Months before your first recorded transaction are ignored; months with no spending after you started count as zero.",
        "Recent months are weighted more heavily (weights 1, 2, 3 ... from oldest to newest).",
        "A 5% buffer is added for regular spending, or 10% when spending is irregular or based on a single month, then rounded up to the next Rs 10.",
        "If you enter an overall budget, only non-essential categories are reduced (by at most 40%) to fit it.",
        "Suggestions are not saved until you review and apply them.",
    ]
    return BudgetRecommendation(target_month, basis_months, basis_label, items, total, cap, gap, unallocated,
                                confidence, starting, avg_income, notes, assumptions)
