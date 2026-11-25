import json
from datetime import datetime

from app import scheduler, singleton, storage
from app.models import Item

NOW = datetime(2026, 11, 24, 12, 0)


def make(**kw):
    base = dict(title="Dentist", date="2026-11-24", time="10:00", reminders_min_before=[30],
                updated_at="2026-11-20T12:00:00")
    base.update(kw)
    return Item(**base)


# ------------------------------------------------------ what was missed ---

def test_an_event_that_came_and_went_while_away_is_reported():
    item = make()
    assert scheduler.missed_during_gap([item], datetime(2026, 11, 24, 8, 0), NOW, set()) == [item]


def test_things_that_do_not_belong_in_the_summary():
    away_from = datetime(2026, 11, 24, 8, 0)
    notified = make()
    keys = {f"{notified.id}:2026-11-24:10:00:30"}
    assert scheduler.missed_during_gap([notified], away_from, NOW, keys) == []        # already notified
    assert scheduler.missed_during_gap([make(time="11:58")], away_from, NOW, set()) == []   # in the grace period
    assert scheduler.missed_during_gap([make(time="15:00")], away_from, NOW, set()) == []   # still to come
    assert scheduler.missed_during_gap([make(status="done")], away_from, NOW, set()) == []
    assert scheduler.missed_during_gap([make(time="07:00")], away_from, NOW, set()) == []   # before the gap


def test_all_day_items_from_days_that_passed_are_reported_but_not_today():
    yesterday, today = make(date="2026-11-23", time=None), make(date="2026-11-24", time=None)
    last_seen = datetime(2026, 11, 23, 18, 0)
    assert scheduler.missed_during_gap([yesterday, today], last_seen, NOW, set()) == [yesterday]
    assert scheduler.missed_during_gap([yesterday], last_seen, NOW, {f"{yesterday.id}:2026-11-23:allday"}) == []


def test_the_summary_is_short():
    items = [make(title=f"Thing {n}", time=f"0{n}:00") for n in range(1, 7)]
    lines = scheduler.gap_summary(items).splitlines()
    assert len(lines) == 5 and lines[-1] == "...and 2 more" and "Thing 1" in lines[0]


# --------------------------------------------------------------- a pass ---

def files(tmp_path, calls):
    return dict(path=tmp_path / "cal.json", fired_file=tmp_path / "fired.json",
                state_file=tmp_path / "state.json", notify_fn=lambda t, m: calls.append((t, m)))


def test_a_gap_is_summarized_once(tmp_path):
    calls = []
    storage.add_item(make(), tmp_path / "cal.json")
    scheduler.tick(datetime(2026, 11, 24, 8, 0), **files(tmp_path, calls))       # first ever run
    assert calls == []
    scheduler.tick(datetime(2026, 11, 24, 12, 0), **files(tmp_path, calls))      # off through the 10:00 event
    assert [c[0] for c in calls] == ["While you were away"] and "Dentist" in calls[0][1]
    scheduler.tick(datetime(2026, 11, 24, 12, 1), **files(tmp_path, calls))
    assert len(calls) == 1


def test_a_normal_pass_fires_a_due_reminder_exactly_once(tmp_path):
    calls = []
    storage.add_item(make(), tmp_path / "cal.json")
    scheduler.tick(datetime(2026, 11, 24, 9, 31), **files(tmp_path, calls))
    scheduler.tick(datetime(2026, 11, 24, 9, 32), **files(tmp_path, calls))
    assert calls == [("Dentist", "In 29 min (10:00)")]
    assert len(json.loads((tmp_path / "fired.json").read_text())) == 1


# ------------------------------------------------- one copy, and the log ---

def test_only_one_copy_can_hold_the_lock():
    first = singleton.acquire(47998)
    assert first is not None
    assert singleton.acquire(47998) is None
    first.close()
    again = singleton.acquire(47998)
    assert again is not None
    again.close()


def test_the_log_file_is_written(tmp_path):
    scheduler.setup_logging(tmp_path / "s.log")
    try:
        scheduler.log.info("hello from the test")
        for handler in scheduler.log.handlers:
            handler.flush()
        assert "hello from the test" in (tmp_path / "s.log").read_text(encoding="utf-8")
    finally:
        for handler in list(scheduler.log.handlers):
            scheduler.log.removeHandler(handler)
            handler.close()