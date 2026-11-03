import pytest

import config
from models import User
from services import auth_service as auth
from services import transaction_service as ts
from tests.helpers import PASSWORD, make_user
from utils.errors import AuthenticationError, ConflictError, ValidationError


def test_register_success_seeds_categories_and_stores_paise(db):
    user = auth.register_user(db, "  Asha   Menon ", "Asha@Example.COM ", PASSWORD, PASSWORD, opening_balance=1250.50)
    assert user.full_name == "Asha Menon"
    assert user.email == "asha@example.com"
    assert user.opening_balance == 125_050
    assert user.currency == "INR"
    names = {(c.type, c.name) for c in ts.list_categories(db, user.id)}
    assert ("income", "Salary") in names and ("expense", "Food") in names
    assert len(names) == len(config.DEFAULT_INCOME_CATEGORIES) + len(config.DEFAULT_EXPENSE_CATEGORIES)


def test_password_is_hashed_not_stored(db):
    user = make_user(db)
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("pbkdf2:")


def test_duplicate_email_rejected_case_insensitively(db):
    make_user(db, email="dup@example.com")
    with pytest.raises(ConflictError):
        make_user(db, email="  DUP@example.com ")
    assert db.query(User).count() == 1   # the failed attempt left the session usable and added nothing


@pytest.mark.parametrize("email", ["", "plainaddress", "a@b", "a@@b.com", "a b@c.com", "a@b..com", "@example.com", "x" * 260 + "@a.com"])
def test_invalid_email_rejected(db, email):
    with pytest.raises(ValidationError):
        auth.register_user(db, "Name", email, PASSWORD, PASSWORD)


def test_password_confirmation_mismatch(db):
    with pytest.raises(ValidationError, match="do not match"):
        auth.register_user(db, "Name", "a@example.com", PASSWORD, "Different123")


@pytest.mark.parametrize("pw", ["", "short1", "alllettersonly", "12345678901"])
def test_weak_passwords_rejected(db, pw):
    with pytest.raises(ValidationError):
        auth.register_user(db, "Name", "a@example.com", pw, pw)


def test_required_name_and_invalid_opening_balance(db):
    with pytest.raises(ValidationError):
        auth.register_user(db, "   ", "a@example.com", PASSWORD, PASSWORD)
    with pytest.raises(ValidationError):
        auth.register_user(db, "Name", "a@example.com", PASSWORD, PASSWORD, opening_balance=-5)


def test_authenticate_success_and_failure(db):
    user = make_user(db, email="login@example.com")
    assert auth.authenticate(db, " LOGIN@example.com", PASSWORD).id == user.id
    with pytest.raises(AuthenticationError) as wrong_pw:
        auth.authenticate(db, "login@example.com", "WrongPass1")
    with pytest.raises(AuthenticationError) as unknown:
        auth.authenticate(db, "nobody@example.com", "WrongPass1")
    assert str(wrong_pw.value) == str(unknown.value) == "Invalid email or password."   # no account enumeration


def test_login_lockout_after_repeated_failures(db):
    make_user(db, email="lock@example.com")
    for _ in range(config.LOGIN_MAX_FAILURES):
        with pytest.raises(AuthenticationError):
            auth.authenticate(db, "lock@example.com", "WrongPass1")
    with pytest.raises(AuthenticationError, match="Too many"):
        auth.authenticate(db, "lock@example.com", PASSWORD)   # even the right password is refused while locked


def test_logout_clears_all_session_state(db):
    user = make_user(db)
    state: dict = {"leftover_filter": 3}
    auth.start_session(state, user.id)
    assert state == {auth.SESSION_USER_KEY: user.id}      # nothing carried over from before login
    state["dash_range"] = "last_30_days"
    auth.end_session(state)
    assert state == {}
    with pytest.raises(AuthenticationError):
        auth.get_authenticated_user(db, state)


def test_session_is_revalidated_against_database(db):
    user = make_user(db)
    state: dict = {}
    auth.start_session(state, user.id)
    assert auth.get_authenticated_user(db, state).id == user.id
    state[auth.SESSION_USER_KEY] = 99999                    # forged / stale id
    with pytest.raises(AuthenticationError):
        auth.get_authenticated_user(db, state)
    assert state == {}                                      # stale session wiped
    state[auth.SESSION_USER_KEY] = "1"                      # wrong type is not accepted
    with pytest.raises(AuthenticationError):
        auth.get_authenticated_user(db, state)


def test_switching_users_does_not_leak_state(db, alice, bob):
    state: dict = {}
    auth.start_session(state, alice.id)
    state["txn_page"] = 4
    auth.start_session(state, bob.id)
    assert state == {auth.SESSION_USER_KEY: bob.id}


def test_update_settings_and_validation(db, alice):
    auth.update_settings(db, alice.id, full_name="Alice R", currency="USD", date_format="%Y-%m-%d",
                         min_reserve=2000, upcoming_commitments=500.5, default_range="last_30_days",
                         notify_budget=False)
    assert (alice.full_name, alice.currency, alice.min_reserve, alice.upcoming_commitments) == ("Alice R", "USD", 200_000, 50_050)
    assert alice.notify_budget is False
    for bad in ({"currency": "XYZ"}, {"date_format": "%x"}, {"min_reserve": -1}, {"default_range": "custom"}, {"full_name": " "}):
        with pytest.raises(ValidationError):
            auth.update_settings(db, alice.id, **bad)


def test_opening_balance_update_and_password_change(db, alice):
    auth.update_opening_balance(db, alice.id, 750)
    assert alice.opening_balance == 75_000
    with pytest.raises(AuthenticationError):
        auth.change_password(db, alice.id, "WrongPass1", "NewSecret456", "NewSecret456")
    with pytest.raises(ValidationError):
        auth.change_password(db, alice.id, PASSWORD, "NewSecret456", "Mismatch456")
    auth.change_password(db, alice.id, PASSWORD, "NewSecret456", "NewSecret456")
    assert auth.authenticate(db, "alice@example.com", "NewSecret456").id == alice.id
    with pytest.raises(AuthenticationError):
        auth.authenticate(db, "alice@example.com", PASSWORD)


def test_profile_snapshot_never_exposes_hash(db, alice):
    profile = auth.to_profile(alice)
    assert not hasattr(profile, "password_hash")
    assert "password" not in repr(profile).lower()
