import json
from datetime import date
from pathlib import Path

from app.checks import (count_commands, fallback_title, finalize, fix_am_pm, has_correction,
                        needs_clarification, rule_action, time_mentioned, title_grounded)

THU = date(2026, 11, 5)
HERE = Path(__file__).resolve().parent


def _phrases(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------ action rules ---

def test_rule_action_catches_clear_verbs():
    assert rule_action("Delete the painpoints task.") == "delete"
    assert rule_action("Get rid of the gym session.") == "delete"
    assert rule_action("Ya get rid of the appointment tomorrow") == "delete"
    assert rule_action("Actually, ditch that gym thing I had planned for tomorrow.") == "delete"
    assert rule_action("Change the gym session to tomorrow at 7 am.") == "update"
    assert rule_action("Push my 3 pm back an hour.") == "update"
    assert rule_action("Check the Jarvis assignment.") == "complete"
    assert rule_action("Mark the doctor's appointment as done.") == "complete"
    assert rule_action("I finished my homework.") == "complete"
    assert rule_action("I\u2019m done with the AI assignment") == "complete"      # curly apostrophe
    assert rule_action("um can you like add a haircut for friday at four") == "add"
    assert rule_action("Oh, and add a quick reminder to call mom.") == "add"
    assert rule_action("I have an assignment due on October 5th.") == "add"
    assert rule_action("Remind me to call mom.") == "add"


def test_rule_action_stays_quiet_when_unsure():
    assert rule_action("What's on my schedule for today?") is None
    assert rule_action("Check the schedule for tomorrow.") is None
    assert rule_action("Schedule for tomorrow, please.") is None
    assert rule_action("When is my exam?") is None
    assert rule_action("Do I have anything tomorrow?") is None
    assert rule_action("Cancel my 10 am tomorrow, actually wait, no, don't cancel it, just push it to noon.") is None


# ------------------------------------------------- corrections / several requests ---

def test_corrections_are_detected_but_normal_speech_is_not():
    assert has_correction("Cancel my 10 am tomorrow, actually wait, no, don't cancel it, just push it to noon.")
    assert has_correction("Add dentist tomorrow, never mind, make it Friday.")
    assert not has_correction("Oh, and add a quick reminder to call mom. Don't really have a time for that yet.")
    assert not has_correction("Remind me to wait for the plumber at 3.")


def test_several_requests_in_one_sentence_are_counted():
    assert count_commands("I need to call the bank sometime this week, move my Friday study session to "
                          "Saturday, and delete that old grocery reminder while you're at it.") >= 2
    assert count_commands("Ignore the assignment for tonight and push the 2nd task for today to tomorrow at like 7") == 2
    assert count_commands("Add lunch with Sam and Alex on Saturday.") == 1
    assert needs_clarification("Cancel my dentist appointment and move the team meeting to Friday.")
    assert needs_clarification("Add gym tomorrow at 6.") is None


def test_no_ordinary_phrase_is_mistaken_for_several_requests():
    """Every scored phrase in both phrase files is a single request."""
    for name in ("benchmark_phrases.json", "heldout_phrases.json"):
        for case in _phrases(name):
            if "gap" in case:
                continue
            assert needs_clarification(case["phrase"]) is None, case["phrase"]


# ---------------------------------------------------------- grounding checks ---

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


def test_fallback_title_reads_the_name_from_what_was_said():
    assert fallback_title("Ya get rid of the appointment tomorrow") == "appointment"
    assert fallback_title("I'm done with the AI assignment") == "AI assignment"     # keeps capitals
    assert fallback_title("Mark the project deadline as done.") == "project deadline"
    assert fallback_title("Reschedule the team meeting to Wednesday.") == "team meeting"
    assert fallback_title("Cancel everything I've got scheduled for this Friday.") is None
    assert fallback_title("Delete it") is None
    assert fallback_title("What's on my schedule for today?") is None


# ------------------------- real mistakes from the benchmark runs, replayed ---

def test_wrong_action_is_fixed():
    raw = {"action": "info", "title": "painpoints assessment", "date_text": "October 5th", "time": None}
    cmd, notes = finalize(raw, 'I have an assignment due on October 5th called "painpoints assessment"', THU)
    assert cmd["action"] == "add" and cmd["date"] == "2027-10-05" and notes


def test_next_friday_means_friday_of_next_week():
    raw = {"action": "add", "title": "dentist appointment", "date_text": "next Friday", "time": "14:00"}
    cmd, _ = finalize(raw, "Hey, throw a dentist appointment on my calendar for next Friday at like 2 in the afternoon.", THU)
    assert (cmd["action"], cmd["date"], cmd["time"]) == ("add", "2026-11-13", "14:00")


def test_copied_example_values_are_dropped():
    raw = {"action": "add", "title": "Jarvis assignment", "date_text": "tomorrow", "time": "16:00"}
    cmd, _ = finalize(raw, "Check the Jarvis assignment.", THU)
    assert (cmd["action"], cmd["date"], cmd["time"]) == ("complete", None, None)
    assert cmd["title"] == "Jarvis assignment"


def test_invented_title_is_replaced_by_the_real_one():
    raw = {"action": "complete", "title": "piano lesson", "date_text": None, "time": None}
    cmd, _ = finalize(raw, "Check the Jarvis assignment.", THU)
    assert cmd["title"] == "Jarvis assignment"


def test_invented_time_is_dropped():
    raw = {"action": "update", "title": "team meeting", "date_text": "Wednesday", "time": "14:00"}
    cmd, _ = finalize(raw, "Reschedule the team meeting to Wednesday.", THU)
    assert cmd["time"] is None and cmd["date"] == "2026-11-11"


def test_invented_date_is_dropped():
    raw = {"action": "list", "title": None, "date_text": "today", "time": None}
    cmd, _ = finalize(raw, "List my tasks.", THU)
    assert cmd["date"] is None


def test_date_mistaken_for_a_title_is_fixed():
    raw = {"action": "delete", "title": "tomorrow", "date_text": None, "time": None}
    cmd, notes = finalize(raw, "Ya get rid of the appointment tomorrow", THU)
    assert (cmd["title"], cmd["date"]) == ("appointment", "2026-11-06") and notes


def test_missing_title_is_recovered_when_the_wording_is_clear():
    raw = {"action": "list", "title": None, "date_text": None, "time": None}
    cmd, _ = finalize(raw, "I'm done with the AI assignment", THU)
    assert (cmd["action"], cmd["title"]) == ("complete", "AI assignment")


# ------------------------------------------------------------ safety rules ---

def test_deleting_a_whole_day_needs_the_word_everything_and_a_day():
    raw = {"action": "delete", "title": None, "date_text": "this Friday", "time": None}
    cmd, _ = finalize(raw, "Cancel everything I've got scheduled for this Friday. I'm not going anywhere.", THU)
    assert cmd["action"] == "delete" and cmd["all_items"] is True and cmd["date"] == "2026-11-06"

    raw = {"action": "delete", "title": None, "date_text": None, "time": None}
    cmd, _ = finalize(raw, "Delete everything.", THU)
    assert cmd["action"] == "clarify"

    raw = {"action": "delete", "title": None, "date_text": "tomorrow", "time": None}
    cmd, _ = finalize(raw, "Delete it tomorrow.", THU)                  # no item, no "everything"
    assert cmd["action"] == "clarify" and cmd["all_items"] is False


def test_update_with_nothing_to_change_asks():
    raw = {"action": "update", "title": "study session", "date_text": None, "time": None}
    cmd, _ = finalize(raw, "Change the study session.", THU)
    assert cmd["action"] == "clarify"


def test_several_requests_or_a_change_of_mind_ask_instead_of_acting():
    raw = {"action": "delete", "title": "dentist appointment", "date_text": None, "time": None}
    cmd, _ = finalize(raw, "Cancel my dentist appointment and move the team meeting to Friday.", THU)
    assert cmd["action"] == "clarify" and "one at a time" in cmd["reason"]

    cmd, _ = finalize(raw, "Cancel my 10 am tomorrow, actually wait, no, don't cancel it, just push it to noon.", THU)
    assert cmd["action"] == "clarify"


def test_unclear_date_is_flagged_not_guessed():
    raw = {"action": "add", "title": "gym", "date_text": "sometime soon", "time": None}
    cmd, _ = finalize(raw, "Add gym sometime soon.", THU)
    assert cmd["date"].startswith("?")


def test_good_answers_pass_through_untouched():
    raw = {"action": "add", "title": "doctor's appointment", "date_text": "tomorrow", "time": "17:00:00"}
    cmd, notes = finalize(raw, "I have a doctor's appointment at 5 tomorrow.", THU)
    assert cmd == {"action": "add", "title": "doctor's appointment", "date_text": "tomorrow",
                   "time": "17:00", "date": "2026-11-06", "all_items": False}
    assert notes == []


def test_checks_can_be_switched_off():
    raw = {"action": "info", "title": "x", "date_text": "tomorrow", "time": None}
    cmd, notes = finalize(raw, "I have an assignment.", THU, use_checks=False)
    assert cmd["action"] == "info" and notes == []


def test_am_pm_rule_is_applied_in_code():
    def fix(raw, phrase):
        return fix_am_pm(raw, phrase)[0]
    assert fix("07:00", "Add a task for 7 on Monday") == "19:00"          # bare 7 -> evening
    assert fix("06:30", "Schedule it for Wednesday, maybe around 6:30") == "18:30"
    assert fix("03:00", "Add dentist tomorrow at 3 pm") == "15:00"          # model forgot the pm
    assert fix("07:00", "Call mom at 7 tonight") == "19:00"
    assert fix("06:00", "I am free, add gym at 6") == "18:00"               # the word "am" isn't a time
    assert fix("07:00", "Add yoga at 7 in the morning on Thursday") == "07:00"
    assert fix("09:00", "Call my advisor at 9 am") == "09:00"
    assert fix("08:00", "Meeting at 8") == "08:00"                          # bare 8-11 stay morning
    assert fix("12:00", "Lunch at noon") == "12:00"
    assert fix("19:00", "Add gym at 7") == "19:00"                          # already right
    assert fix("07:00", "at 7 in the morning or the evening") == "07:00"    # contradictory: leave it
    assert fix(None, "anything") is None


def test_bare_seven_becomes_evening_in_the_full_pipeline():
    raw = {"action": "add", "title": "AI assignment", "date_text": "Monday", "time": "07:00"}
    cmd, notes = finalize(raw, 'Add a task for 7 on Monday, call it "AI assignment"', THU)
    assert (cmd["time"], cmd["date"]) == ("19:00", "2026-11-09") and notes


def test_clearing_a_day_is_a_bulk_delete():
    assert rule_action("Clear my schedule for today.") == "delete"
    assert rule_action("Wipe my calendar for tomorrow.") == "delete"
    # the model called these a "list"; the rules and the bulk check turn them into a whole-day delete
    for phrase, said in [("Clear my schedule for today.", "today"), ("Wipe my calendar for tomorrow.", "tomorrow"),
                         ("Empty my schedule for Saturday.", "Saturday"), ("Clear today.", "today"),
                         ("Clear everything on Friday.", "Friday")]:
        raw = {"action": "list", "title": None, "date_text": said, "time": None}
        cmd, _ = finalize(raw, phrase, THU)
        assert (cmd["action"], cmd["title"], cmd["all_items"]) == ("delete", None, True), phrase
    raw = {"action": "delete", "title": "schedule", "date_text": "today", "time": None}   # "schedule" is not an item name
    cmd, _ = finalize(raw, "Clear my schedule for today.", THU)
    assert cmd["all_items"] is True and cmd["title"] is None


def test_clearing_one_item_is_not_a_bulk_delete():
    raw = {"action": "delete", "title": None, "date_text": None, "time": None}
    cmd, _ = finalize(raw, "Clear the dentist appointment.", THU)
    assert (cmd["action"], cmd["title"], cmd["all_items"]) == ("delete", "dentist appointment", False)


def test_clearing_without_a_day_asks_which_day():
    raw = {"action": "delete", "title": None, "date_text": None, "time": None}
    cmd, _ = finalize(raw, "Clear my schedule.", THU)
    assert cmd["action"] == "clarify" and "Which day" in cmd["reason"]