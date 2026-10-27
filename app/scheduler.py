import json
import time
import math
from datetime import datetime, timedelta
from pathlib import Path

from . import storage
from .notifier import notify

FIRED_FILE = Path(__file__).resolve().parent.parent / "data" / "reminders_fired.json"
POLL_SECONDS = 60
GRACE_MIN = 5          # still fire up to 5 min after the event starts
MORNING_HOUR = 9       # morning-of reminder time (also used for all-day items)


def due_reminders(items, now, fired):
    """Pure function: returns [(key, item, message), ...] that should fire now."""
    due = []
    for item in items:
        if item.status != "pending":
            continue

        if item.time is None:                       # all-day item
            trigger = datetime.strptime(item.date, "%Y-%m-%d").replace(hour=MORNING_HOUR)
            end = trigger.replace(hour=0) + timedelta(days=1)
            key = f"{item.id}:{item.date}:allday"
            if trigger <= now < end and key not in fired:
                due.append((key, item, "Today"))
            continue

        start = datetime.strptime(f"{item.date} {item.time}", "%Y-%m-%d %H:%M")

        # Morning-of reminder
        if item.morning_reminder:
            morning = start.replace(hour=MORNING_HOUR, minute=0)
            modified = datetime.fromisoformat(item.updated_at)
            # Once the earliest lead-up reminder is due, a late morning one is redundant
            first_lead = start - timedelta(minutes=max(item.reminders_min_before, default=0))
            key = f"{item.id}:{item.date}:{item.time}:morning"
            if (morning < start               # skip if the event starts at/before 9:00
                    and modified <= morning   # skip if added/changed after 9:00 that day
                    and morning <= now < first_lead
                    and key not in fired):
                due.append((key, item, f"Today at {item.time}"))

        # Lead-up reminders
        for m in item.reminders_min_before:
            remind_at = start - timedelta(minutes=m)
            key = f"{item.id}:{item.date}:{item.time}:{m}"
            if remind_at <= now < start + timedelta(minutes=GRACE_MIN) and key not in fired:
                left_sec = (start - now).total_seconds()
                left = math.ceil(left_sec / 60)
                msg = "Starting now" if left_sec <= 0 else f"In {left} min ({item.time})"
                due.append((key, item, msg))
    return due


def _load_fired() -> set:
    if FIRED_FILE.exists():
        return set(json.loads(FIRED_FILE.read_text(encoding="utf-8")))
    return set()


def _save_fired(fired: set) -> None:
    FIRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    FIRED_FILE.write_text(json.dumps(sorted(fired)), encoding="utf-8")


def check_once(now=None, path=storage.DATA_FILE) -> None:
    now = now or datetime.now()
    items = storage.list_items(path=path)
    fired = _load_fired()
    for key, item, text in due_reminders(items, now, fired):
        notify(item.title, text)
        fired.add(key)
    # Drop records for items that no longer exist so the file stays small
    ids = {i.id for i in items}
    _save_fired({k for k in fired if k.split(":")[0] in ids})


def run() -> None:
    print(f"Reminder scheduler running (checking every {POLL_SECONDS}s). Ctrl+C to stop.")
    while True:
        try:
            check_once()
        except Exception as e:           # one bad tick shouldn't kill the loop
            print("Scheduler error:", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()