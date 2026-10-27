from datetime import datetime
from app.models import Item
from app.scheduler import due_reminders

LONG_AGO = "2026-10-20T12:00:00"


def make(**kw):
    base = dict(title="Dentist", date="2026-10-27", time="15:00",
                reminders_min_before=[30], updated_at=LONG_AGO)
    base.update(kw)
    return Item(**base)


def test_lead_up_fires_in_window_and_only_once():
    item = make(morning_reminder=False)
    assert due_reminders([item], datetime(2026, 10, 27, 14, 20), set()) == []   # too early
    due = due_reminders([item], datetime(2026, 10, 27, 14, 31), set())
    assert len(due) == 1
    key = due[0][0]
    assert due_reminders([item], datetime(2026, 10, 27, 14, 32), {key}) == []   # not twice
    assert due_reminders([item], datetime(2026, 10, 27, 15, 10), set()) == []   # too late


def test_morning_reminder_fires_at_9am_once():
    item = make(reminders_min_before=[])
    assert due_reminders([item], datetime(2026, 10, 27, 8, 59), set()) == []
    due = due_reminders([item], datetime(2026, 10, 27, 9, 1), set())
    assert len(due) == 1
    assert due_reminders([item], datetime(2026, 10, 27, 9, 2), {due[0][0]}) == []


def test_morning_reminder_skipped_if_event_before_9am():
    item = make(time="07:30", reminders_min_before=[])
    assert due_reminders([item], datetime(2026, 10, 27, 9, 1), set()) == []


def test_morning_reminder_skipped_if_added_after_9am():
    item = make(updated_at="2026-10-27T10:00:00", reminders_min_before=[])
    assert due_reminders([item], datetime(2026, 10, 27, 10, 1), set()) == []


def test_both_reminders_for_one_event():
    item = make()
    morning = due_reminders([item], datetime(2026, 10, 27, 9, 1), set())
    lead = due_reminders([item], datetime(2026, 10, 27, 14, 31), set())
    assert len(morning) == 1 and len(lead) == 1


def test_done_items_are_ignored():
    item = make(status="done")
    assert due_reminders([item], datetime(2026, 10, 27, 14, 35), set()) == []


def test_all_day_item_fires_at_9am():
    item = make(time=None)
    assert due_reminders([item], datetime(2026, 10, 27, 8, 59), set()) == []
    assert len(due_reminders([item], datetime(2026, 10, 27, 9, 1), set())) == 1