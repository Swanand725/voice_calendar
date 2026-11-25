import json
import urllib.error
from datetime import date

import pytest

from app import parser

THU = date(2026, 11, 5)


def test_parse_runs_the_model_output_through_the_safety_net(monkeypatch):
    reply = json.dumps({"action": "info", "title": "painpoints assessment",
                        "date_text": "October 5th", "time": None})
    monkeypatch.setattr(parser, "ask", lambda model, system, phrase: (reply, 1.5))
    result = parser.parse('I have an assignment due on October 5th called "painpoints assessment"', THU)
    assert result.cmd["action"] == "add" and result.cmd["date"] == "2027-10-05"
    assert result.raw["action"] == "info" and result.notes and result.seconds == 1.5


def test_garbled_model_output_becomes_a_question(monkeypatch):
    monkeypatch.setattr(parser, "ask", lambda model, system, phrase: ("not json at all", 0.5))
    result = parser.parse("whatever", THU)
    assert result.cmd["action"] == "clarify" and result.raw is None


def test_ollama_not_running_is_a_clear_error(monkeypatch):
    def boom(model, system, phrase):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(parser, "ask", boom)
    with pytest.raises(parser.ModelUnavailable, match="Is it running"):
        parser.parse("what do I have today", THU)


def test_model_not_downloaded_is_a_clear_error(monkeypatch):
    def boom(model, system, phrase):
        raise urllib.error.HTTPError("http://x", 404, "not found", {}, None)
    monkeypatch.setattr(parser, "ask", boom)
    with pytest.raises(parser.ModelUnavailable, match="ollama pull"):
        parser.parse("what do I have today", THU, model="nope")


def test_clear_my_schedule_for_today_end_to_end(monkeypatch, tmp_path):
    from datetime import datetime
    from app import storage
    from app.executor import Assistant
    from app.models import Item

    path = tmp_path / "cal.json"
    storage.add_item(Item(title="Call mom", date="2026-11-24"), path)
    storage.add_item(Item(title="Team meeting", date="2026-11-24", time="10:00"), path)
    storage.add_item(Item(title="Exam", date="2026-11-25", time="09:00"), path)

    # the model wrongly says "list", which is what happened in real use
    reply = json.dumps({"action": "list", "title": None, "date_text": "today", "time": None})
    monkeypatch.setattr(parser, "ask", lambda model, system, phrase: (reply, 0.1))
    bot = Assistant(lambda phrase, today: parser.parse(phrase, today), path=path,
                    now_fn=lambda: datetime(2026, 11, 24, 8, 0))

    question = bot.handle("clear my schedule for today")
    assert "delete the following 2 items from today" in question
    assert "Call mom" in question and "Team meeting" in question and "Exam" not in question
    assert len(storage.list_items(path=path)) == 3          # nothing happens before "yes"
    assert bot.handle("yes").startswith("Deleted 2 items from today")
    assert [i.title for i in storage.list_items(path=path)] == ["Exam"]
    assert "Brought back 2 items" in bot.handle("undo")