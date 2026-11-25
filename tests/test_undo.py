import json
from dataclasses import asdict
from datetime import datetime, timedelta
from types import SimpleNamespace

from app import storage
from app.executor import Assistant
from app.models import Item

NOW = datetime(2026, 11, 23, 9, 30)


def cmd(action, title=None, date=None, time=None, all_items=False):
    return {"action": action, "title": title, "date": date, "date_text": None,
            "time": time, "all_items": all_items, "reason": None}


def bot(tmp_path, script=None, now=NOW):
    """A brand-new assistant on the same files, like opening the window again."""
    script = script or {}
    return Assistant(lambda p, today: SimpleNamespace(cmd=script[p], notes=[], raw=None, seconds=0.0),
                     path=tmp_path / "cal.json", now_fn=lambda: now)


def items(tmp_path):
    return storage.list_items(path=tmp_path / "cal.json")


def put(tmp_path, title, date, time=None):
    return storage.add_item(Item(title=title, date=date, time=time), tmp_path / "cal.json")


def test_undoing_a_delete_after_the_window_was_closed(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    first = bot(tmp_path, {"x": cmd("delete", "team meeting")})
    first.handle("x")
    first.handle("yes")
    assert items(tmp_path) == []
    assert "Brought back" in bot(tmp_path).handle("undo")
    assert [i.title for i in items(tmp_path)] == ["Team meeting"]


def test_undoing_an_add_after_the_window_was_closed(tmp_path):
    bot(tmp_path, {"x": cmd("add", "Gym", "2026-11-25", "18:00")}).handle("x")
    assert "Removed" in bot(tmp_path).handle("undo")
    assert items(tmp_path) == []


def test_undoing_an_update_and_a_completion_after_the_window_was_closed(tmp_path):
    put(tmp_path, "Dentist", "2026-11-27", "14:00")
    first = bot(tmp_path, {"u": cmd("update", "dentist", time="15:30"), "c": cmd("complete", "dentist")})
    first.handle("u")
    first.handle("c")
    second = bot(tmp_path)
    second.handle("undo")                       # the completion
    assert items(tmp_path)[0].status == "pending" and items(tmp_path)[0].time == "15:30"
    second.handle("undo")                       # the time change
    assert items(tmp_path)[0].time == "14:00"


def test_undoing_a_whole_day_delete_after_the_window_was_closed(tmp_path):
    put(tmp_path, "One", "2026-11-27", "10:00")
    put(tmp_path, "Two", "2026-11-27")
    first = bot(tmp_path, {"x": cmd("delete", all_items=True, date="2026-11-27")})
    first.handle("x")
    first.handle("yes")
    assert "Brought back 2 items" in bot(tmp_path).handle("undo") and len(items(tmp_path)) == 2


def test_undo_only_works_once_and_never_duplicates(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    first = bot(tmp_path, {"x": cmd("delete", "team meeting")})
    first.handle("x")
    first.handle("yes")
    again = bot(tmp_path)
    again.handle("undo")
    assert again.handle("undo") == "There's nothing to undo." and len(items(tmp_path)) == 1


def test_an_undo_entry_for_an_item_that_already_exists_adds_nothing(tmp_path):
    item = put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    (tmp_path / "cal.undo.json").write_text(json.dumps(
        [{"op": "readd", "items": [asdict(item)], "ts": NOW.isoformat()}]))
    bot(tmp_path).handle("undo")
    assert len(items(tmp_path)) == 1


def test_changes_older_than_a_day_are_not_undone(tmp_path):
    put(tmp_path, "Team meeting", "2026-11-24", "10:00")
    first = bot(tmp_path, {"x": cmd("delete", "team meeting")})
    first.handle("x")
    first.handle("yes")
    later = bot(tmp_path, now=NOW + timedelta(hours=25))
    assert "more than a day ago" in later.handle("undo")
    assert items(tmp_path) == []
    assert later.handle("undo") == "There's nothing to undo."


def test_a_damaged_undo_file_is_ignored(tmp_path):
    (tmp_path / "cal.undo.json").write_text("this is not json")
    assert bot(tmp_path).handle("undo") == "There's nothing to undo."


def test_only_the_newest_30_changes_are_kept(tmp_path):
    script = {f"a{n}": cmd("add", f"Thing {n}", "2026-11-25") for n in range(35)}
    first = bot(tmp_path, script)
    for n in range(35):
        first.handle(f"a{n}")
    saved = json.loads((tmp_path / "cal.undo.json").read_text())
    assert len(saved) == 30 and saved[-1]["title"] == "Thing 34"