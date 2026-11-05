from datetime import date

from app.checks import finalize, rule_action, time_mentioned, title_grounded

THU = date(2026, 11, 5)


def test_rule_action_catches_clear_verbs():
    assert rule_action("Delete the painpoints task.") == "delete"
    assert rule_action("Get rid of the gym session.") == "delete"
    assert rule_action("Change the gym session to tomorrow at 7 am.") == "update"
    assert rule_action("Check the Jarvis assignment.") == "complete"
    assert rule_action("Mark the doctor's appointment as done.") == "complete"
    assert rule_action("I finished my homework.") == "complete"
    assert rule_action("um can you like add a haircut for friday at four") == "add"
    assert rule_action("I have an assignment due on October 5th.") == "add"
    assert rule_action("Remind me to call mom.") == "add"


def test_rule_action_stays_quiet_when_unsure():
    assert rule_action("What's on my schedule for today?") is None
    assert rule_action("Check the schedule for tomorrow.") is None
    assert rule_action("Schedule for tomorrow, please.") is None
    assert rule_action("When is my exam?") is None
    assert rule_action("Do I have anything tomorrow?") is None


def test_time_mentioned():
    assert time_mentioned("I have a doctor's appointment at 5 tomorrow.")
    assert time_mentioned("Add lunch on Saturday at noon.")
    assert time_mentioned("add a haircut for friday at four")
    assert not time_mentioned("Reschedule the team meeting to Wednesday.")
    assert not time_mentioned("I have a deadline on November 15th.")      # digits belong to the date
    assert not time_mentioned("Add pick up package in 2 days.")
    assert time_mentioned("Add pick up package in 2 days at 4.")


def test_title_grounded():
    assert title_grounded("Do Homework", "Remind me to do my homework tomorrow")
    assert title_grounded("painpoints assessment", "When is the Painpoints assement assignment due?")
    assert not title_grounded("piano lesson", "Check the Jarvis assignment.")


# The cases below are real mistakes the models made in the benchmark.

def test_wrong_action_is_fixed():
    raw = {"action": "info", "title": "painpoints assessment", "date_text": "October 5th", "time": None}
    cmd, notes = finalize(raw, 'I have an assignment due on October 5th called "painpoints assessment"', THU)
    assert cmd["action"] == "add" and cmd["date"] == "2027-10-05" and notes


def test_copied_example_values_are_dropped():
    raw = {"action": "add", "title": "Jarvis assignment", "date_text": "tomorrow", "time": "16:00"}
    cmd, _ = finalize(raw, "Check the Jarvis assignment.", THU)
    assert (cmd["action"], cmd["date"], cmd["time"]) == ("complete", None, None)
    assert cmd["title"] == "Jarvis assignment"


def test_invented_title_is_dropped():
    raw = {"action": "complete", "title": "piano lesson", "date_text": None, "time": None}
    cmd, _ = finalize(raw, "Check the Jarvis assignment.", THU)
    assert cmd["title"] is None


def test_invented_time_is_dropped():
    raw = {"action": "update", "title": "team meeting", "date_text": "Wednesday", "time": "14:00"}
    cmd, _ = finalize(raw, "Reschedule the team meeting to Wednesday.", THU)
    assert cmd["time"] is None and cmd["date"] == "2026-11-11"


def test_invented_date_is_dropped():
    raw = {"action": "list", "title": None, "date_text": "today", "time": None}
    cmd, _ = finalize(raw, "List my tasks.", THU)
    assert cmd["date"] is None


def test_unclear_date_is_flagged_not_guessed():
    raw = {"action": "add", "title": "gym", "date_text": "sometime soon", "time": None}
    cmd, _ = finalize(raw, "Add gym sometime soon.", THU)
    assert cmd["date"].startswith("?")


def test_good_answers_pass_through_untouched():
    raw = {"action": "add", "title": "doctor's appointment", "date_text": "tomorrow", "time": "17:00:00"}
    cmd, notes = finalize(raw, "I have a doctor's appointment at 5 tomorrow.", THU)
    assert cmd == {"action": "add", "title": "doctor's appointment", "date_text": "tomorrow",
                   "time": "17:00", "date": "2026-11-06"}
    assert notes == []


def test_checks_can_be_switched_off():
    raw = {"action": "info", "title": "x", "date_text": "tomorrow", "time": None}
    cmd, notes = finalize(raw, "I have an assignment.", THU, use_checks=False)
    assert cmd["action"] == "info" and notes == []