import csv
import io
from datetime import date

from services import budget_service as bs
from services import report_service as rs
from services import savings_service as sv
from services import transaction_service as ts
from tests.helpers import TODAY, cat_id, expense, income, make_user


def parse(blob: bytes):
    return list(csv.reader(io.StringIO(blob.decode("utf-8-sig"))))


def test_transaction_export_columns_and_values(db):
    u = make_user(db)
    income(db, u, 42000.5, "Salary", date(2026, 10, 1), method="Bank Transfer", desc="October pay")
    expense(db, u, 1234.56, "Food", date(2026, 10, 2), method="UPI", desc="Dinner, with \"friends\"")
    blob, count = rs.export_transactions_csv(db, u.id)
    rows = parse(blob)
    assert count == 2
    assert rows[0] == ["Transaction ID", "Date", "Type", "Category", "Amount (INR)", "Payment Method", "Description"]
    assert rows[1][1:] == ["2026-10-02", "Expense", "Food", "1234.56", "UPI", "Dinner, with \"friends\""]
    assert rows[2][1:5] == ["2026-10-01", "Income", "Salary", "42000.50"]
    assert blob.startswith(b"\xef\xbb\xbf")                              # BOM so Excel reads UTF-8


def test_export_respects_filters_and_empty_result(db):
    u = make_user(db)
    expense(db, u, 10, "Food", date(2026, 9, 1))
    expense(db, u, 20, "Rent", date(2026, 10, 1))
    blob, count = rs.export_transactions_csv(db, u.id, ts.TransactionFilters(start=date(2026, 10, 1), category_id=cat_id(db, u, "Rent")))
    assert count == 1 and len(parse(blob)) == 2
    blob, count = rs.export_transactions_csv(db, u.id, ts.TransactionFilters(search="no-such-text"))
    assert count == 0 and len(parse(blob)) == 1                          # header only, no crash


def test_formula_injection_is_neutralised(db):
    u = make_user(db)
    expense(db, u, 5, "Food", TODAY, desc="=HYPERLINK(\"http://evil\",\"x\")")
    expense(db, u, 5, "Food", TODAY, desc="@SUM(A1)")
    expense(db, u, 5, "Food", TODAY, desc="+1+1")
    expense(db, u, 5, "Food", TODAY, desc="-2")
    ts.create_category(db, u.id, "=EVIL", "expense")
    expense(db, u, 5, "=EVIL", TODAY, desc="plain")
    descriptions = {r[6] for r in parse(rs.export_transactions_csv(db, u.id)[0])[1:]}
    assert all(d[0] == "'" for d in descriptions if d != "plain")
    categories = {r[3] for r in parse(rs.export_transactions_csv(db, u.id)[0])[1:]}
    assert "'=EVIL" in categories
    assert rs.safe_cell("normal") == "normal" and rs.safe_cell(None) == ""


def test_export_never_contains_credentials(db):
    u = make_user(db, email="secret@example.com")
    expense(db, u, 5, "Food", TODAY)
    for blob in (rs.export_transactions_csv(db, u.id)[0], rs.export_contributions_csv(db, u.id)[0],
                 rs.summary_csv(rs.build_summary(db, u.id, date(2026, 10, 1), TODAY))):
        text = blob.decode("utf-8-sig")
        assert u.password_hash not in text and "pbkdf2" not in text and "secret@example.com" not in text


def test_summary_report_contents(db):
    u = make_user(db)
    income(db, u, 1000, "Salary", date(2026, 10, 1))
    expense(db, u, 1500, "Food", date(2026, 10, 2))
    expense(db, u, 250, "Rent", date(2026, 10, 3))
    bs.create_budget(db, u.id, "2026-10", 2000)
    s = rs.build_summary(db, u.id, date(2026, 10, 1), TODAY)
    assert (s.income, s.expense, s.net, s.transaction_count) == (100_000, 175_000, -75_000, 3)
    assert s.savings_rate == -75.0 and [c.name for c in s.expense_categories] == ["Food", "Rent"]
    rows = {(r[0], r[1]): r for r in parse(rs.summary_csv(s))[1:]}
    assert rows[("Totals", "Net cash flow")][2] == "-750.00"
    assert rows[("Totals", "Savings rate")][3] == "-75.00%"
    assert rows[("Expenses by category", "Food")][2] == "1500.00"
    assert "Overall budget" in rows[("Budgets", "2026-10 Overall budget")][1]
    assert rows[("Budgets", "2026-10 Overall budget")][3].startswith("Spent 1750.00; remaining 250.00")


def test_summary_with_no_data_and_zero_income(db):
    u = make_user(db)
    s = rs.build_summary(db, u.id, date(2026, 10, 1), TODAY)
    assert s.savings_rate is None and s.expense_categories == []
    rows = {(r[0], r[1]): r for r in parse(rs.summary_csv(s))[1:]}
    assert rows[("Totals", "Savings rate")][3].startswith("Not available")


def test_budget_and_contribution_exports(db):
    u = make_user(db)
    bs.create_budget(db, u.id, "2026-10", 1000, cat_id(db, u, "Food"))
    expense(db, u, 850, "Food", date(2026, 10, 2))
    rows = parse(rs.export_budgets_csv(db, u.id, "2026-09", "2026-10")[0])
    assert rows[1][:6] == ["2026-10", "Food", "1000.00", "850.00", "150.00", "85.00"]
    g = sv.create_goal(db, u.id, name="=Trip", target_amount=500, today=TODAY)
    sv.add_contribution(db, u.id, g.id, 99.5, TODAY, "first", today=TODAY)
    blob, count = rs.export_contributions_csv(db, u.id)
    rows = parse(blob)
    assert count == 1 and rows[1][1:] == ["'=Trip", "2026-10-24", "99.50", "first"]


def test_invalid_summary_range(db):
    import pytest
    from utils.errors import ValidationError
    u = make_user(db)
    with pytest.raises(ValidationError):
        rs.build_summary(db, u.id, date(2026, 10, 5), date(2026, 10, 1))
