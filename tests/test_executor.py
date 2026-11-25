import json
from datetime import datetime
from types import SimpleNamespace

from app import storage
from app.executor import Assistant
from app.logbook import Logbook
from app.models import Item

NOW = datetime(2026, 11, 23, 9, 30)          # a Monday morning


def cmd(action, title=None, date=None, time=None, all_items=False, date_text=None, reason=None):
    return {"action": action, "title": title, "date": date, "date_text": date_text,
            "time": time, "all_items": all_items, "reason": reason}


def make_bot(tmp_path, script, logbook=None):
    """An assistant whose 'model' just looks the sentence up in `script`."""
    def parse_fn(phrase, today):
        return SimpleNamespace(cmd=script[phrase], notes=[], raw={"fake": True}, seconds=0.0)
    return Assistant(parse_fn, path=tmp_path / "cal.json", now_fn=lambda: NOW, logbook=logbook)


def put(tmp_path, title, date, time=None, status="pending"):
    return storage.add_item(Item(title=title, date=date, time=time, status=status), tmp_path / "cal.json")


def items(tmp_path):
    return storage.list_items(path=tmp_path / "cal.json")


# ------------------------------------------------------------------- add ---

def test_add_without_a_day_goes_on_today_all_day(tmp_path):
    bot = make_bot(tmp_path, {"remind me to call mom": cmd("add", "call mom")})
    reply = bot.handle("remind me to call mom")
    assert 'Added "call mom"' in reply and "No day given" in reply
    [item] = items(tmp_path)
    assert (item.date, item.time) == ("2026-11-23", None)


def test_add_with_a_time_lists_the_reminders(tmp_path):
    bot = make_bot(tmp_path, {"x": cmd("add", "Dentist appointment", "2026-11-27", "14:00")})
    reply = bot.handle("x")
    assert "Fri Nov 27, 2:00 PM" in reply
    assert "9:00 AM that day and 30 min before" in reply


def test_morning_reminder_is_not_promised_when_9am_has_passed(tmp_path):
    bot = make_bot(tmp_path, {"x": cmd("add", "Gym", "2026-11-23", "18:00")})     # today, now is 9:30
    reply = bot.handle("x")
    assert "30 min before" in reply and "9:00 AM that day" not in reply


def test_add_in_the_past_asks_first(tmp_path):
    bot = make_bot(tmp_path, {"x": cmd("add", "Dentist", "2026-11-20", "14:00")})
    assert "already passed" in bot.handle("x")
    assert bot.handle("no") == "Okay, I didn't add it." and items(tmp_path) == []
    bot.handle("x")
    assert "Added" in bot.handle("yes") and len(items(tmp_path)) == 1


def test_the_same_item_is_not_added_twice(tmp_path):
    bot = make_bot(tmp_path, {"x": cmd("add", "Dentist", "2026-11-27", "14:00")})
    bot.handle("x")
    assert "already have" in bot.handle("x")
    assert len(items(tmp_path)) == 1


# ---------------------------------------------------------------- delete ---

def test_delete_picks_the_best_match_and_asks_once(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    put(tmp_path, "Team lunch", "2026-11-24", "12:00")
    bot = make_bot(tmp_path, {"cancel the team meeting": cmd("delete", "team meeting")})

    assert bot.handle("cancel the team meeting") == 'Do you want me to delete "Team meeting"?'
    assert len(items(tmp_path)) == 2                      # nothing happens before "yes"
    assert bot.handle("yes").startswith('Deleted "Team meeting".')
    assert [i.title for i in items(tmp_path)] == ["Team lunch"]


def test_saying_no_keeps_the_item(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    bot = make_bot(tmp_path, {"x": cmd("delete", "team meeting")})
    bot.handle("x")
    assert bot.handle("no") == "Okay, I won't delete it."
    assert len(items(tmp_path)) == 1


def test_same_name_twice_shows_the_day_and_offers_the_other(tmp_path):
    put(tmp_path, "Gym", "2026-11-24", "18:00")
    put(tmp_path, "Gym", "2026-11-26", "18:00")
    bot = make_bot(tmp_path, {"x": cmd("delete", "gym")})

    first = bot.handle("x")
    assert 'delete "Gym" (Tue Nov 24, 6:00 PM)?' in first and "1 other" in first
    second = bot.handle("no")
    assert "Which one" in second and "Thu Nov 26" in second
    assert 'delete "Gym" (Thu Nov 26, 6:00 PM)?' in bot.handle("1")
    assert bot.handle("yes").startswith('Deleted "Gym"')
    assert [i.date for i in items(tmp_path)] == ["2026-11-24"]


def test_a_day_named_in_a_delete_narrows_the_search(tmp_path):
    put(tmp_path, "Gym", "2026-11-24", "18:00")
    put(tmp_path, "Gym", "2026-11-26", "18:00")
    bot = make_bot(tmp_path, {"x": cmd("delete", "gym", "2026-11-26")})
    assert bot.handle("x") == 'Do you want me to delete "Gym"?'
    bot.handle("yes")
    assert [i.date for i in items(tmp_path)] == ["2026-11-24"]


def test_delete_not_found_suggests_the_closest(tmp_path):
    put(tmp_path, "Team lunch", "2026-11-24", "12:00")
    bot = make_bot(tmp_path, {"x": cmd("delete", "team meeting")})
    reply = bot.handle("x")
    assert "couldn't find" in reply and '"Team lunch"' in reply


def test_deleting_a_whole_day_shows_the_count_and_needs_yes(tmp_path):
    put(tmp_path, "Dentist", "2026-11-27", "10:00")
    put(tmp_path, "Call mom", "2026-11-27")
    put(tmp_path, "Exam", "2026-11-28", "09:00")
    bot = make_bot(tmp_path, {"x": cmd("delete", all_items=True, date="2026-11-27")})

    reply = bot.handle("x")
    assert "Do you want me to delete the following 2 items from Fri Nov 27?" in reply
    assert "Dentist" in reply and "Call mom" in reply and "Exam" not in reply
    assert len(items(tmp_path)) == 3
    assert "Deleted 2 items" in bot.handle("yes")
    assert [i.title for i in items(tmp_path)] == ["Exam"]
    assert "Brought back 2 items" in bot.handle("undo") and len(items(tmp_path)) == 3


# ------------------------------------------------------ complete / update ---

def test_complete_and_undo(tmp_path):
    put(tmp_path, "Jarvis assignment", "2026-11-25")
    bot = make_bot(tmp_path, {"x": cmd("complete", "Jarvis")})
    assert bot.handle("x").startswith('Marked done: "Jarvis assignment"')
    assert items(tmp_path)[0].status == "done"
    assert "Reversed" in bot.handle("undo")
    assert items(tmp_path)[0].status == "pending"


def test_completing_something_already_done_says_so(tmp_path):
    put(tmp_path, "Jarvis assignment", "2026-11-20", status="done")
    bot = make_bot(tmp_path, {"x": cmd("complete", "Jarvis")})
    assert "already marked done" in bot.handle("x")


def test_update_shows_before_and_after_and_can_be_undone(tmp_path):
    put(tmp_path, "Dentist appointment", "2026-11-27", "14:00")
    bot = make_bot(tmp_path, {"x": cmd("update", "dentist appointment", time="15:30")})
    reply = bot.handle("x")
    assert "Fri Nov 27, 2:00 PM -> Fri Nov 27, 3:30 PM" in reply
    assert items(tmp_path)[0].time == "15:30"
    bot.handle("undo")
    assert items(tmp_path)[0].time == "14:00"


def test_update_date_is_the_new_date_not_a_filter(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    bot = make_bot(tmp_path, {"x": cmd("update", "team meeting", date="2026-11-26")})
    bot.handle("x")
    assert (items(tmp_path)[0].date, items(tmp_path)[0].time) == ("2026-11-26", "10:00")


def test_update_to_a_past_time_asks_first(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    bot = make_bot(tmp_path, {"x": cmd("update", "team meeting", date="2026-11-20")})
    assert "already passed" in bot.handle("x")
    assert items(tmp_path)[0].date == "2026-11-24"
    bot.handle("yes")
    assert items(tmp_path)[0].date == "2026-11-20"


# ------------------------------------------------------------ list / info ---

def test_list_a_day_and_an_empty_day(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    put(tmp_path, "Call mom", "2026-11-24")
    bot = make_bot(tmp_path, {"a": cmd("list", date="2026-11-24"), "b": cmd("list", date="2026-11-30")})
    reply = bot.handle("a")
    assert "Tomorrow, Tue Nov 24:" in reply and "10:00 AM" in reply and "All day" in reply
    assert bot.handle("b") == "Nothing scheduled for Mon Nov 30."


def test_list_without_a_day_shows_overdue_and_upcoming(tmp_path):
    put(tmp_path, "Old assignment", "2026-11-20")
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    put(tmp_path, "Finished thing", "2026-11-25", status="done")
    bot = make_bot(tmp_path, {"x": cmd("list")})
    reply = bot.handle("x")
    assert "Overdue:" in reply and "Old assignment" in reply and "Team meeting" in reply
    assert "Finished thing" not in reply


def test_info_gives_time_remaining(tmp_path):
    put(tmp_path, "Dentist appointment", "2026-11-27", "14:00")
    bot = make_bot(tmp_path, {"x": cmd("info", "dentist")})
    assert bot.handle("x") == '"Dentist appointment" is Fri Nov 27, 2:00 PM (in 4 days 4 hours).'


def test_info_also_finds_done_and_past_items(tmp_path):
    put(tmp_path, "Meeting with Alex", "2026-11-10", "15:00", status="done")
    bot = make_bot(tmp_path, {"x": cmd("info", "Alex")})
    reply = bot.handle("x")
    assert "Meeting with Alex" in reply and "ago" in reply and "done" in reply


# ---------------------------------------------------- safety and plumbing ---

def test_clarify_and_unclear_dates_do_nothing(tmp_path):
    bot = make_bot(tmp_path, {
        "a": cmd("clarify", reason="That sounds like more than one request. Please say them one at a time."),
        "b": cmd("add", "Gym", date="?this weekend", date_text="this weekend"),
    })
    assert "one at a time" in bot.handle("a")
    assert 'didn\'t understand the date "this weekend"' in bot.handle("b")
    assert items(tmp_path) == []


def test_undo_with_nothing_to_undo(tmp_path):
    assert make_bot(tmp_path, {}).handle("undo") == "There's nothing to undo."


def test_a_new_command_drops_an_unanswered_question(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    bot = make_bot(tmp_path, {"d": cmd("delete", "team meeting"), "l": cmd("list", date="2026-11-24"),
                              "yes": cmd("clarify", reason="Yes to what?")})
    bot.handle("d")
    assert "Team meeting" in bot.handle("l")             # a new command replaces the question
    assert bot.pending is None and len(items(tmp_path)) == 1
    assert bot.handle("yes") == "Yes to what?"           # a late "yes" no longer deletes anything
    assert len(items(tmp_path)) == 1


def test_backups_are_saved_before_changes(tmp_path):
    bot = make_bot(tmp_path, {"a": cmd("add", "One", "2026-11-27"), "b": cmd("add", "Two", "2026-11-28")})
    bot.handle("a")
    assert not (tmp_path / "backups").exists()            # nothing to back up yet
    bot.handle("b")
    assert len(list((tmp_path / "backups").glob("cal-*.json"))) == 1


def test_wrong_marks_the_last_command_in_the_log(tmp_path):
    log = Logbook(tmp_path / "log.jsonl")
    bot = make_bot(tmp_path, {"remind me to call mom": cmd("add", "call mom")}, logbook=log)
    bot.handle("remind me to call mom")
    assert "Marked" in bot.handle("wrong: it should have asked for a time")
    entries = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert entries[0]["kind"] == "command" and entries[0]["wrong"] is True
    assert entries[0]["note"] == "it should have asked for a time"
    assert entries[0]["raw"] == {"fake": True} and entries[0]["cmd"]["title"] == "call mom"