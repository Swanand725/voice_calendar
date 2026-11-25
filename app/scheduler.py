"""The reminder scheduler: runs quietly in the background and sends notifications.

Every minute it re-reads the calendar and fires reminders that are due. New in this version:
- a log file (data/scheduler.log) so a hidden background run is never a mystery,
- only one copy can run at a time,
- a "While you were away" summary when the laptop slept or was off through an event.
"""
import json
import logging
import math
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import singleton, storage
from .notifier import notify

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FIRED_FILE = DATA_DIR / "reminders_fired.json"
STATE_FILE = DATA_DIR / "scheduler_state.json"
LOG_FILE = DATA_DIR / "scheduler.log"

POLL_SECONDS = 60
GAP_SECONDS = POLL_SECONDS * 3     # a longer silence than this means the laptop slept or was off
GRACE_MIN = 5                      # still fire up to 5 min after the event starts
MORNING_HOUR = 9                   # morning-of reminder time (also used for all-day items)
SUMMARY_LINES = 4

log = logging.getLogger("atlas.scheduler")


# ------------------------------------------------------------ what is due ---

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


def _reminder_keys(item):
    base = f"{item.id}:{item.date}:{item.time}"
    return [f"{base}:morning"] + [f"{base}:{m}" for m in item.reminders_min_before]


def missed_during_gap(items, last_seen, now, fired):
    """Pending items that came and went while the scheduler wasn't running, unnoticed.

    Events that are still coming up, or began within the grace period, are left to the normal
    reminders, which fire late on their own."""
    cutoff = now - timedelta(minutes=GRACE_MIN)
    missed = []
    for item in items:
        if item.status != "pending":
            continue
        if item.time is None:
            day = datetime.strptime(item.date, "%Y-%m-%d").date()
            if last_seen.date() <= day < now.date() and f"{item.id}:{item.date}:allday" not in fired:
                missed.append(item)
            continue
        start = datetime.strptime(f"{item.date} {item.time}", "%Y-%m-%d %H:%M")
        if last_seen <= start < cutoff and not any(k in fired for k in _reminder_keys(item)):
            missed.append(item)
    return sorted(missed, key=lambda i: (i.date, i.time or ""))


def gap_summary(missed):
    def line(item):
        day = datetime.strptime(item.date, "%Y-%m-%d").date()
        when = f"{day:%a %b} {day.day}" + (f" {item.time}" if item.time else "")
        return f"{when}: {item.title}"
    lines = [line(i) for i in missed[:SUMMARY_LINES]]
    if len(missed) > SUMMARY_LINES:
        lines.append(f"...and {len(missed) - SUMMARY_LINES} more")
    return "\n".join(lines)


# ------------------------------------------------------------ small files ---

def _load_fired(path=FIRED_FILE) -> set:
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_fired(fired: set, path=FIRED_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(fired)), encoding="utf-8")


def _load_last_seen(path=STATE_FILE):
    try:
        return datetime.fromisoformat(json.loads(Path(path).read_text(encoding="utf-8"))["last_seen"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_last_seen(now, path=STATE_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_seen": now.isoformat(timespec="seconds")}), encoding="utf-8")


# ----------------------------------------------------------------- a pass ---

def tick(now=None, path=storage.DATA_FILE, fired_file=FIRED_FILE, state_file=STATE_FILE, notify_fn=notify):
    """One scheduler pass: summarize anything missed during a gap, then fire what is due."""
    now = now or datetime.now()
    items = storage.list_items(path=path)
    fired = _load_fired(fired_file)
    last_seen = _load_last_seen(state_file)

    if last_seen and (now - last_seen).total_seconds() > GAP_SECONDS:
        missed = missed_during_gap(items, last_seen, now, fired)
        log.info("Back after %s away; %d missed item(s)", now - last_seen, len(missed))
        if missed:
            notify_fn("While you were away", gap_summary(missed))
            fired.update(f"{i.id}:{i.date}:{i.time or 'allday'}:missed" for i in missed)

    for key, item, text in due_reminders(items, now, fired):
        log.info("Reminder: %s (%s)", item.title, text)
        notify_fn(item.title, text)
        fired.add(key)

    ids = {i.id for i in items}                     # forget records of items that no longer exist
    _save_fired({k for k in fired if k.split(":")[0] in ids}, fired_file)
    _save_last_seen(now, state_file)


def setup_logging(path=LOG_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=200_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    if sys.stderr is not None:                      # no console when run with pythonw
        log.addHandler(logging.StreamHandler())


def run() -> None:
    setup_logging()
    lock = singleton.acquire()
    if lock is None:
        log.info("The scheduler is already running; this copy will exit.")
        return
    log.info("Scheduler started (checking every %ss)", POLL_SECONDS)
    while True:
        try:
            tick()
        except Exception:                           # one bad pass shouldn't kill the loop
            log.exception("Scheduler pass failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()