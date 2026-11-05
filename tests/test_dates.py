from datetime import date

from app.dates import resolve_date

THU = date(2026, 11, 5)     # a Thursday
TUE = date(2026, 10, 27)    # a Tuesday


def test_relative_words():
    assert resolve_date("today", THU) == "2026-11-05"
    assert resolve_date("tonight", THU) == "2026-11-05"
    assert resolve_date("tomorrow", THU) == "2026-11-06"
    assert resolve_date("Tomorrow.", THU) == "2026-11-06"
    assert resolve_date("day after tomorrow", THU) == "2026-11-07"


def test_in_n_days_and_weeks():
    assert resolve_date("in 2 days", THU) == "2026-11-07"
    assert resolve_date("in a week", THU) == "2026-11-12"
    assert resolve_date("in two weeks", THU) == "2026-11-19"
    assert resolve_date("2 days", THU) == "2026-11-07"         # "in" left out
    assert resolve_date("a week", THU) == "2026-11-12"


def test_weekdays_mean_the_next_one_after_today():
    assert resolve_date("Sunday", THU) == "2026-11-08"
    assert resolve_date("on Monday", THU) == "2026-11-09"
    assert resolve_date("next friday", THU) == "2026-11-06"
    assert resolve_date("thursday", THU) == "2026-11-12"      # today is Thursday -> next week
    assert resolve_date("tue", TUE) == "2026-11-03"


def test_month_and_day_use_next_occurrence():
    assert resolve_date("October 5th", THU) == "2027-10-05"   # already passed this year
    assert resolve_date("oct 5", THU) == "2027-10-05"
    assert resolve_date("5th of October", THU) == "2027-10-05"
    assert resolve_date("November 15th", THU) == "2026-11-15"
    assert resolve_date("November 5th", THU) == "2026-11-05"  # today counts
    assert resolve_date("december 3rd", THU) == "2026-12-03"
    assert resolve_date("10/5", THU) == "2027-10-05"


def test_explicit_years_and_iso():
    assert resolve_date("October 5th 2026", THU) == "2026-10-05"
    assert resolve_date("10/5/2026", THU) == "2026-10-05"
    assert resolve_date("2026-11-08", THU) == "2026-11-08"


def test_unclear_input_returns_none_instead_of_guessing():
    for text in [None, "", "whenever", "next week", "february 30th", "13/45", "soon"]:
        assert resolve_date(text, THU) is None, text