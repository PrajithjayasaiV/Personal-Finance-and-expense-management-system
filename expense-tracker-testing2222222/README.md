# Personal Finance and Expense Management System with Smart Budget Recommendations and Spending Insights

A local, privacy-friendly personal finance application built with **Python, Streamlit, SQLite and SQLAlchemy**.
Record income and expenses, set monthly budgets, track savings goals, explore interactive charts and get
transparent, rule-based insights, budget suggestions and a "Safe to spend" estimate. Everything runs on your own
computer. No cloud service, paid API, AI API key or internet connection is needed.

- Default currency: **Indian Rupees (₹, INR)** with Indian digit grouping (₹1,25,000.00)
- Runs on **Windows 10/11** (and macOS/Linux) with Python 3.11 or newer
- 207 automated tests (see [Test results](#14-running-the-automated-tests))

---

## 1. Problem statement

Most people know how much money they receive, but not exactly where it goes, which categories
are exceeding their budget, whether they spend more than last month, or how much they can safely spend.
A plain expense tracker only stores transactions. This project adds accurate bookkeeping, budgets, savings
goals, analytics and explainable recommendations in one tool, free to run.

## 2. Objectives

1. Accurate record keeping with exact money arithmetic (integer paise, never floats).
2. Practical monthly budgeting with clear 80% / 100% alerts.
3. Savings goals driven by real contribution records.
4. Interactive analytics and exportable reports.
5. Honest, explainable insights: every message shows the data behind it, and rules stay silent when data is insufficient.
6. Strict per-user data isolation and sound security practice.

## 3. Features

| Area | What you can do |
|---|---|
| Accounts | Register, sign in, sign out, change password, edit profile. Passwords are hashed (PBKDF2-SHA256). |
| Transactions | Add / edit / delete income and expenses; search; filter by type, category, date range and payment method; sort by date or amount; paginate. |
| Categories | Defaults seeded on registration; add, rename and delete custom categories. Used categories can only be deleted by moving their transactions elsewhere. |
| Budgets | Overall and per-category monthly budgets, live utilisation, alerts at 80% and 100%, history, copy last month, suggestions for next month. |
| Savings goals | Targets, optional deadlines, contributions (add/edit/delete), progress, required monthly saving, "what if" projections. |
| Dashboard | Summary cards, spending-by-category doughnut, income vs expenses, monthly trend, month-over-month comparison, budget status, goals, insights, recent transactions, Safe-to-Spend. |
| Analytics | Category analysis, trends, savings trend, period comparison, budget performance, payment-method breakdown. |
| Reports | Filtered CSV export of transactions, period summary (week/month/custom) as CSV, budget and contribution exports. |
| Insights | Category changes, largest category, repeated overspending, budget alerts, goal pacing, negative cash flow. Dismiss/acknowledge. |
| Notifications | In-app alerts for budgets, goal milestones, goal deadlines and notable spending changes; read/unread; no duplicates. |
| Settings | Name, currency symbol, date format, default dashboard range, reserve, upcoming commitments, opening balance, alert preferences. |

## 4. Technology stack

| Purpose | Technology |
|---|---|
| Language | Python 3.11+ |
| UI | Streamlit (multi-page via `st.navigation`) |
| Database | SQLite through SQLAlchemy 2.x ORM |
| Analysis / charts | pandas, Plotly |
| Security | Werkzeug password hashing, parameterised ORM queries |
| Configuration | python-dotenv (optional `.env` file) |
| Testing | pytest (+ Streamlit's `AppTest` for UI flows) |

## 5. Architecture and folder structure

```text
expense-tracker/
├── app.py                  Entry point: page config, database init, navigation
├── config.py               Paths, constants, defaults (no database/UI code)
├── database.py             Engine, SQLite pragmas, init_db, session_scope, migrations hook
├── models.py               SQLAlchemy ORM models and database constraints
├── requirements.txt
├── README.md
├── .gitignore
├── .env.example            Optional settings (database path, log level)
├── .streamlit/config.toml  Theme colour, usage-stats off
│
├── services/               ALL business rules. Take a Session and a user_id; never import Streamlit.
│   ├── common.py             Ownership check (get_owned)
│   ├── auth_service.py       Register, login, session helpers, settings, password change
│   ├── transaction_service.py Categories and transactions (validation, filters, sorting)
│   ├── analytics_service.py  Totals, balance, category/period/series calculations
│   ├── budget_service.py     Budgets, utilisation, thresholds
│   ├── savings_service.py    Goals, contributions, progress, projections
│   ├── insights_service.py   Insight rules, Safe-to-Spend, budget recommendations
│   ├── notification_service.py Deduplicated in-app notifications
│   └── report_service.py     Summaries and safe CSV exports
│
├── components/             Reusable UI pieces
│   ├── navigation.py         Auth gate, menu, sidebar
│   ├── auth_pages.py         Sign-in and registration pages
│   ├── layout.py             Header, metric cards, bars, empty states, flash messages
│   ├── forms.py              Date-range selector, month picker, transaction form
│   └── charts.py             Plotly figure builders
│
├── views/                  The seven application pages + notifications (run through st.navigation)
│   ├── 1_Dashboard.py  2_Transactions.py  3_Budgets.py  4_Savings_Goals.py
│   └── 5_Analytics.py  6_Reports.py  7_Settings.py  8_Notifications.py
│
├── utils/                  money.py (paise), dates.py, formatting.py, validators.py, errors.py
├── tests/                  pytest suite (see section 14)
└── data/                   Runtime files (database, log). Contents are git-ignored.
```

Design rules followed throughout:

- UI code contains no financial formulas. Pages call services; services hold every rule and calculation.
- Services receive a SQLAlchemy `Session` and **never commit**; `session_scope()` commits or rolls back the whole unit of work, so related changes succeed or fail together.
- Every service function takes the acting `user_id` and checks ownership itself. The UI is never trusted as the only gate.
- The pages folder is called `views/` instead of `pages/` on purpose. With `st.navigation`, an extra `pages/` folder triggers warnings on some Streamlit versions, and hiding a link would not be access control anyway.

## 6. Prerequisites

- **Python 3.11 or newer** (3.12 recommended). Check with `py --version` in PowerShell.
  Download from <https://www.python.org/downloads/>; tick "Add python.exe to PATH" in the installer.
- Windows 10/11 (or macOS/Linux). Several hundred MB of disk for the dependencies.
- A web browser. No Docker, paid API, cloud database or external AI service.

## 7. Installation (Windows PowerShell)

```powershell
cd expense-tracker

# 1. Create a virtual environment (use a specific version if you have several)
py -m venv .venv
#   py -3.12 -m venv .venv

# 2. Activate it
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**If PowerShell says "running scripts is disabled on this system"**, you do not need to change system-wide security.
Pick one of these:

```powershell
# Option A - allow scripts for this PowerShell window only (reverts when you close it)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# Option B - skip activation and call the virtual environment's Python directly
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Option C: use Command Prompt (`cmd`) and run `.venv\Scripts\activate.bat` instead.

## 8. Configuration

Everything has a working default. To override, copy `.env.example` to `.env` and edit it:

| Variable | Default | Meaning |
|---|---|---|
| `EXPENSE_TRACKER_DB_PATH` | `data/expense_tracker.db` | SQLite file location. Relative paths are resolved from the **project folder**, not from where you launch Streamlit, so the database never silently changes. |
| `EXPENSE_TRACKER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Logs are written to `data/app.log`. |

There are no API keys or secrets. `.env` is git-ignored.

## 9. Database initialisation

You do not create the database yourself. On first launch the app creates the `data/` folder, the SQLite file,
all tables, indexes and constraints. On later launches it only creates what is missing; it **never drops or
resets data**, and your records persist across logout, restarts and reboots. The schema version is stored in
SQLite's `user_version`, and `database.MIGRATIONS` is the place for future, non-destructive upgrades.

To back up your data, copy the database file (default `data/expense_tracker.db`) while the app is stopped.

## 10. Starting, stopping and reopening

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

Streamlit opens <http://localhost:8501>. If it does not, open that address yourself.

- **Stop:** press `Ctrl+C` in the terminal window running the server.
- **Reopen later:** open PowerShell in the project folder, activate the environment, run `streamlit run app.py` again.
- Another port: `streamlit run app.py --server.port 8600`.

## 11. Main workflows

1. **Create an account** (Create account page). Optionally enter an opening balance, which is counted once and is *not* income.
2. **Record transactions** (Transactions > Add transaction). Pick Income or Expense first, so the category list matches.
3. **Set budgets** (Budgets > Set a budget): one overall limit per month and/or limits per expense category.
4. **Watch alerts**: 80% shows "Approaching limit", 100% shows "Limit reached or exceeded". See the bell page, Notifications.
5. **Create savings goals** (Savings goals > New goal) and record contributions. Progress comes only from contribution records.
6. **Review** the Dashboard and Analytics; choose date ranges with the selector.
7. **Plan next month** (Budgets > Suggestions): generate, review/edit the amounts, tick the confirmation box, then apply. Nothing is saved before that.
8. **Export** (Reports): filtered transactions CSV, period summary CSV, budgets and contributions CSV.
9. **Tune Safe-to-Spend** (Settings): set your reserve and any known upcoming commitments.

## 12. Financial calculation definitions

All money is stored as **integer paise** (₹1,250.50 = 125,050). Input is converted with `Decimal` and rounded half-up to two places. Percentages return "not available" instead of dividing by zero.

| Metric | Definition |
|---|---|
| Total income / expenses | Sum of income / expense transactions in the selected range (inclusive of both dates). |
| Net cash flow | Total income − total expenses, for the **selected period**. |
| Current recorded balance | Opening balance + **all** recorded income − **all** recorded expenses. Opening balance counted once. Not a bank balance. |
| Recorded monthly surplus | Monthly income − monthly expenses. A calculation, not money moved to savings. |
| Savings rate | (income − expenses) / income × 100; negative when overspending; "not available" when income is zero. |
| Budget used | Sum of expense transactions in the budget's scope and month (overall = all expenses; category = that category). Always read from transactions. |
| Remaining budget | Limit − used (negative when exceeded; the exceeded amount is shown as a positive number). |
| Utilisation | Used / limit × 100. Thresholds use exact integer math: ≥ 80% warning, ≥ 100% reached/exceeded. |
| Goal saved | Sum of that goal's contribution records. |
| Goal remaining / progress | max(0, target − saved); saved / target × 100 (bar capped at 100%, real percentage still shown). |
| Required saving | Remaining ÷ months (or weeks) left until the target date; if under one month remains, the whole remaining amount is needed. |
| Goal pace and projection | Contributions in the last 90 days, converted to a monthly average; completion date = remaining ÷ monthly amount, rounded up to whole months. Estimates only. |
| Safe to spend | `available = max(0, balance − reserve − upcoming commitments)`. With an overall budget this month: `min(available, max(0, remaining budget))`. Without one it is labelled "balance and reserve only". Recorded expenses are already inside the balance, so nothing is deducted twice. |

Month-over-month comparisons compare a month-to-date range with the **same number of days** of the previous month, never a half month with a full one.

## 13. Insight and recommendation rules (all documented, no AI service)

Defined as named constants at the top of `services/insights_service.py`.

- **Category change:** needs previous-period spending of at least ₹500. Change of at least ₹200 and 20% → informational insight; at least ₹1,000 and 50% → "much higher" warning (also raises a notification). Wording never calls anything fraud or unnecessary.
- **Valid comparisons only:** the previous period must be covered, meaning your first record is within 7 days of its start. Otherwise no comparison insight is shown.
- **Largest category:** share of at least 25%, with at least 3 expense records.
- **Repeated overspending:** needs 3 budgeted months for a category; flagged when over budget in at least 2 of the last 3.
- **Budget suggestions:** up to N completed months of history (default 3), weighted so recent months count more, + 5% buffer (10% when irregular or based on one month), rounded up to the next ₹10. With fewer than two months the result is labelled a **starting estimate**. If you enter an overall budget, only non-essential categories are trimmed (by at most 40%); any remaining gap is explained, never hidden.
- Each insight shows its evidence; **Got it** / **Dismiss** hide it (restore in Settings).

## 14. Running the automated tests

```powershell
python -m pytest -q
```

Tests use temporary databases and never touch your real data.

| File | Covers |
|---|---|
| `test_auth.py` | Registration, duplicates, validation, hashing, login, lockout, session handling |
| `test_transactions.py` | Create/edit/delete, validation, ownership, filters, search, sort, pagination, categories |
| `test_calculations.py` | Exact balance/net/savings-rate figures, paise precision, date helpers, formatting |
| `test_budgets.py` | Creation, duplicates, 79.99/80/99.99/100/100.01% thresholds, recalculation, notification de-duplication |
| `test_savings.py` | Goals, contributions, progress, completion, over-target, projections, notifications |
| `test_analytics.py` | Category totals, series, comparisons, payment methods |
| `test_insights.py` | Every insight rule (with and without enough data), dismissal, Safe-to-Spend, recommendations |
| `test_authorization.py` | User A cannot read/change/delete/export User B's data through any service |
| `test_reports.py` | CSV content, filters, empty export, formula-injection protection, no credentials |
| `test_database.py` | Fresh creation, persistence across restart, safe re-initialisation, constraints |
| `test_pages.py` / `test_flows.py` | Every page runs for empty/populated/signed-out users; real form submissions persist |

### Test results (what was actually run)

- **207 tests passed** on Linux with Python 3.12 using the exact versions in a fresh virtual environment built only from `requirements.txt`
  (Streamlit 1.66.1, SQLAlchemy 2.1.6, pandas 3.1.0, Plotly 7.1.0, Werkzeug 3.1.9).
- The suite also passed on the oldest declared versions: Streamlit 1.46.0, SQLAlchemy 2.0.30, pandas 2.2.2, Plotly 5.20.0, Werkzeug 3.0.0, and on Streamlit 1.52.0.
  Streamlit 1.43 and older were *not* accepted (a navigation flow failed in their test harness), which is why `requirements.txt` requires `streamlit>=1.46`.
- The real `streamlit run app.py` server started from a different working directory and reported healthy.
- **Not verified:** I did not run this on Windows, and I did not look at the pages in a browser. The UI is exercised through Streamlit's headless `AppTest`
  (forms are filled and submitted against a real database), but visual layout, charts' appearance and responsiveness have not been inspected by eye.

## 15. Security considerations

- Passwords are hashed with PBKDF2-SHA256 (600,000 iterations, per-password salt) via Werkzeug. Plaintext passwords are never stored or logged; hashes never appear in the UI, logs or exports.
- Sign-in errors are identical for unknown email and wrong password; a dummy hash is checked so timing does not reveal accounts. Five failed attempts per email lock sign-in for 15 minutes (in memory, resets on restart).
- All database access uses the SQLAlchemy ORM with bound parameters. No SQL is built by string concatenation (search wildcards `%` and `_` are escaped).
- **Authorization is enforced in every service call** (`get_owned` raises `AuthorizationError` for another user's record) and again by the database through composite foreign keys, for example a transaction cannot reference another user's category even if code is wrong.
- Every page re-validates the signed-in user against the database on each run; a forged or stale session id shows the sign-in page. Logout (and each login) clears all session state.
- CSV exports contain only the requesting user's rows and neutralise cells beginning with `= + - @` to prevent spreadsheet formula injection.
- User text shown in the UI is HTML-escaped / Markdown-escaped.
- Unexpected errors show a generic message; details go to `data/app.log` without passwords or amounts.
- `.gitignore` excludes the database, logs and `.env`.

This is a local personal application. It has not been professionally audited and is not meant to be exposed to the internet (no HTTPS, no account recovery, database file not encrypted).

## 16. Known limitations

- **Refreshing the browser tab signs you out** (Streamlit session state lives in the tab session). Persistent "remember me" sign-in is not implemented.
- **No password reset.** There is no email service. If you forget a password, you must create a new account (or restore a backup).
- Transactions and contributions cannot be dated in the future. Dates use your computer's clock.
- Balances reflect only what you record; no bank, card or UPI integration exists (payment methods are labels).
- Recurring transactions are not automated; record regular items manually.
- Currency is display-only; changing it does not convert amounts.
- Insight thresholds are fixed constants (reserve, commitments and budget limits are yours to set). Insights are guidance, not financial advice.
- The failed-login limiter is per running process, not stored in the database.
- Notifications exist only inside the app (no email, SMS or push).
- Tested on Linux only; see "Not verified" above.

## 17. Future improvements

Persistent sign-in; password reset through a recovery code; recurring transactions; per-user configurable insight thresholds;
CSV import; multi-currency with exchange rates; database encryption; PDF reports; Alembic-based migrations; shared household accounts.
