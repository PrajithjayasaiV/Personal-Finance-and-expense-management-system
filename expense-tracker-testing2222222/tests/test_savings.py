from datetime import date, timedelta

import pytest

from models import Notification
from services import notification_service as ns
from services import savings_service as sv
from sqlalchemy import select
from tests.helpers import TODAY
from utils.errors import ValidationError


def goal(db, user, target=10000, **kw):
    kw.setdefault("name", "Laptop")
    return sv.create_goal(db, user.id, target_amount=target, today=TODAY, **kw)


def progress(db, user, g, today=TODAY):
    return sv.goal_progress(db, user.id, sv.get_goal(db, user.id, g.id), today)


def test_create_goal_defaults(db, alice):
    g = goal(db, alice, description="Work laptop")
    p = progress(db, alice, g)
    assert (p.target, p.saved, p.remaining, p.pct, p.status) == (1_000_000, 0, 1_000_000, 0.0, "active")
    assert g.start_date == TODAY and g.target_date is None


def test_initial_saved_is_stored_as_a_contribution(db, alice):
    g = goal(db, alice, initial_saved=2500)
    contributions = sv.list_contributions(db, alice.id, g.id)
    assert len(contributions) == 1 and contributions[0].amount == 250_000
    assert progress(db, alice, g).saved == 250_000


def test_contributions_drive_progress(db, alice):
    g = goal(db, alice, target=1000)
    sv.add_contribution(db, alice.id, g.id, 250.25, date(2026, 10, 1), "first", today=TODAY)
    sv.add_contribution(db, alice.id, g.id, 100, date(2026, 10, 2), today=TODAY)
    p = progress(db, alice, g)
    assert (p.saved, p.remaining, p.pct) == (35_025, 64_975, 35.03)
    assert p.display_pct == 35.03


def test_edit_and_delete_contribution_recalculate(db, alice):
    g = goal(db, alice, target=1000)
    c = sv.add_contribution(db, alice.id, g.id, 400, TODAY, today=TODAY)
    sv.update_contribution(db, alice.id, c.id, 600, TODAY, "bigger", today=TODAY)
    assert progress(db, alice, g).saved == 60_000
    sv.delete_contribution(db, alice.id, c.id)
    assert progress(db, alice, g).saved == 0


def test_completed_and_exceeded_goals(db, alice):
    g = goal(db, alice, target=1000)
    c = sv.add_contribution(db, alice.id, g.id, 1000, TODAY, today=TODAY)
    p = progress(db, alice, g)
    assert p.status == "completed" and p.remaining == 0 and p.track_status == sv.COMPLETED
    sv.add_contribution(db, alice.id, g.id, 250, TODAY, today=TODAY)
    p = progress(db, alice, g)
    assert p.saved == 125_000 and p.pct == 125.0 and p.display_pct == 100.0 and p.over_target == 25_000
    sv.delete_contribution(db, alice.id, c.id)              # drops below target -> reopens
    sv.update_contribution(db, alice.id, sv.list_contributions(db, alice.id, g.id)[0].id, 100, TODAY, today=TODAY)
    assert progress(db, alice, g).status == "active"


def test_saving_does_not_touch_the_expense_balance(db, alice):
    from services import analytics_service as an
    g = goal(db, alice)
    sv.add_contribution(db, alice.id, g.id, 500, TODAY, today=TODAY)
    assert an.current_balance(db, alice.id) == 0 and an.period_totals(db, alice.id).expense == 0


@pytest.mark.parametrize("bad", [0, -1, "abc", None])
def test_invalid_amounts_rejected(db, alice, bad):
    with pytest.raises(ValidationError):
        goal(db, alice, target=bad)
    g = goal(db, alice)
    with pytest.raises(ValidationError):
        sv.add_contribution(db, alice.id, g.id, bad, TODAY, today=TODAY)


def test_goal_and_contribution_date_rules(db, alice):
    with pytest.raises(ValidationError):
        goal(db, alice, start_date=date(2026, 10, 10), target_date=date(2026, 10, 1))
    with pytest.raises(ValidationError):
        goal(db, alice, name=" ")
    g = goal(db, alice)
    with pytest.raises(ValidationError, match="future"):
        sv.add_contribution(db, alice.id, g.id, 10, TODAY + timedelta(days=1), today=TODAY)


def test_missing_target_date_and_projection_helpers(db, alice):
    g = goal(db, alice, target=1000)
    p = progress(db, alice, g)
    assert p.track_status == sv.NO_DEADLINE and p.required_monthly is None and p.days_left is None
    assert sv.project_completion(0, 100, TODAY) == TODAY
    assert sv.project_completion(1000, 0, TODAY) is None
    assert sv.project_completion(1000, 300, TODAY) == date(2027, 2, 24)      # 4 months
    assert sv.required_contributions(0, 30) == (None, None)
    assert sv.required_contributions(100_000, 0) == (None, None)
    assert sv.required_contributions(120_000, 365) == (10_007, 2_302)
    assert sv.required_contributions(50_000, 10) == (50_000, 35_000)         # < 1 month: needed this month


def test_on_track_behind_overdue_and_no_pace(db, alice):
    deadline = TODAY + timedelta(days=120)
    g = goal(db, alice, target=1200, start_date=TODAY - timedelta(days=60), target_date=deadline)
    assert progress(db, alice, g).track_status == sv.NO_PACE
    sv.add_contribution(db, alice.id, g.id, 300, TODAY - timedelta(days=30), today=TODAY)
    sv.add_contribution(db, alice.id, g.id, 300, TODAY - timedelta(days=1), today=TODAY)
    p = progress(db, alice, g)
    assert p.track_status == sv.ON_TRACK and p.monthly_pace > 0           # ~Rs 600 / 2 months pace
    assert p.projected_completion is not None and p.projected_completion <= deadline
    behind = goal(db, alice, name="Trip", target=100000, start_date=TODAY - timedelta(days=60), target_date=deadline)
    sv.add_contribution(db, alice.id, behind.id, 100, TODAY - timedelta(days=2), today=TODAY)
    assert progress(db, alice, behind).track_status == sv.BEHIND
    late = goal(db, alice, name="Old", target=1000, start_date=TODAY - timedelta(days=100), target_date=TODAY - timedelta(days=5))
    p = progress(db, alice, late)
    assert p.track_status == sv.OVERDUE and p.required_monthly is None and p.days_left == -5


def test_pause_and_resume(db, alice):
    g = goal(db, alice, target=1000)
    sv.set_goal_paused(db, alice.id, g.id, True)
    assert progress(db, alice, g).status == "paused"
    sv.add_contribution(db, alice.id, g.id, 1000, TODAY, today=TODAY)
    assert sv.get_goal(db, alice.id, g.id).status == "paused"              # user choice is respected
    sv.set_goal_paused(db, alice.id, g.id, False)
    assert sv.get_goal(db, alice.id, g.id).status == "completed"


def test_update_goal_target_reopens_or_completes(db, alice):
    g = goal(db, alice, target=1000, initial_saved=1000)
    assert g.status == "completed"
    sv.update_goal(db, alice.id, g.id, name="Laptop", target_amount=5000, start_date=g.start_date, target_date=None)
    assert sv.get_goal(db, alice.id, g.id).status == "active"


def test_delete_goal_removes_contributions_and_notifications(db, alice):
    g = goal(db, alice, target=1000)
    sv.add_contribution(db, alice.id, g.id, 600, TODAY, today=TODAY)
    ns.sync_goal_notifications(db, alice.id, None, TODAY)
    assert db.scalars(select(Notification).where(Notification.ref_type == "goal")).first()
    sv.delete_goal(db, alice.id, g.id)
    db.expire_all()
    from models import GoalContribution
    assert db.scalars(select(GoalContribution)).all() == []
    assert db.scalars(select(Notification).where(Notification.ref_type == "goal")).all() == []


def test_goal_milestone_and_deadline_notifications(db, alice):
    g = goal(db, alice, target=1000, start_date=TODAY - timedelta(days=30), target_date=TODAY + timedelta(days=10))
    def active():
        return [n for n in db.scalars(select(Notification).where(Notification.user_id == alice.id, Notification.is_active.is_(True)))]
    sv.add_contribution(db, alice.id, g.id, 300, TODAY, today=TODAY)
    ns.sync_goal_notifications(db, alice.id, None, TODAY)
    kinds = sorted(n.type for n in active())
    assert kinds == ["goal_deadline", "goal_milestone"]
    assert any("25%" in n.title for n in active())
    sv.add_contribution(db, alice.id, g.id, 300, TODAY, today=TODAY)       # 60% -> milestone moves to 50%
    ns.sync_goal_notifications(db, alice.id, None, TODAY)
    ms = [n for n in active() if n.type == "goal_milestone"]
    assert len(ms) == 1 and "50%" in ms[0].title
    sv.add_contribution(db, alice.id, g.id, 400, TODAY, today=TODAY)       # complete
    ns.sync_goal_notifications(db, alice.id, None, TODAY)
    assert [n.type for n in active()] == ["goal_milestone"] and "Goal reached" in active()[0].title


def test_contributions_by_month(db, alice):
    g = goal(db, alice, target=1000)
    sv.add_contribution(db, alice.id, g.id, 10, date(2026, 9, 3), today=TODAY)
    sv.add_contribution(db, alice.id, g.id, 20, date(2026, 9, 20), today=TODAY)
    sv.add_contribution(db, alice.id, g.id, 5, date(2026, 10, 1), today=TODAY)
    assert sv.contributions_by_month(db, alice.id) == {"2026-09": 3000, "2026-10": 500}
