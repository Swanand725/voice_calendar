"""Turn spoken date phrases into ISO dates: "tomorrow", "Sunday", "October 5th", "in 2 days".

Plain Python, no dependencies. Returns None when it can't understand a phrase, so the
app can ask the user instead of silently guessing.

Rules:
- A weekday name means the first such day AFTER today ("Thursday" said on a Thursday = next
  week; say "today" for today).
- A month and day with no year means its next occurrence (today counts).
- An ISO date (2026-11-08) passes through unchanged.
"""
import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY_ALIASES = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
                    "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
                 "twelve": 12}
_ORD = r"(?:st|nd|rd|th)?"


def _weekday_index(word):
    if word in WEEKDAYS:
        return WEEKDAYS.index(word)
    return _WEEKDAY_ALIASES.get(word)


def _month_index(word):
    for i, name in enumerate(MONTHS, 1):
        if word == name or word == name[:3] or (i == 9 and word == "sept"):
            return i
    return None


def next_weekday(today: date, index: int) -> date:
    return today + timedelta(days=(index - today.weekday()) % 7 or 7)


def _month_day(today: date, month: int, day: int, year=None) -> date:
    if year is not None:
        return date(year, month, day)
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def _year(text):
    if text is None:
        return None
    return 2000 + int(text) if len(text) == 2 else int(text)


def _resolve(s: str, today: date):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s).isoformat()
    if s in ("today", "tonight"):
        return today.isoformat()
    if s in ("tomorrow", "tmrw"):
        return (today + timedelta(days=1)).isoformat()
    if s == "day after tomorrow":
        return (today + timedelta(days=2)).isoformat()

    m = re.fullmatch(r"(?:in )?(\d+|[a-z]+) (day|days|week|weeks)", s)
    if m:
        n = int(m[1]) if m[1].isdigit() else _NUMBER_WORDS.get(m[1])
        if n is None:
            return None
        return (today + timedelta(days=n * (7 if m[2].startswith("week") else 1))).isoformat()

    m = re.fullmatch(r"(?:next )?([a-z]+)", s)
    if m and _weekday_index(m[1]) is not None:
        return next_weekday(today, _weekday_index(m[1])).isoformat()

    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s)
    if m:
        return _month_day(today, int(m[1]), int(m[2]), _year(m[3])).isoformat()

    m = re.fullmatch(rf"([a-z]+) (\d{{1,2}}){_ORD}(?: (\d{{4}}))?", s)      # october 5th [2027]
    if m and _month_index(m[1]):
        return _month_day(today, _month_index(m[1]), int(m[2]), _year(m[3])).isoformat()

    m = re.fullmatch(rf"(\d{{1,2}}){_ORD}(?: of)? ([a-z]+)(?: (\d{{4}}))?", s)  # 5th of october
    if m and _month_index(m[2]):
        return _month_day(today, _month_index(m[2]), int(m[1]), _year(m[3])).isoformat()

    return None


def resolve_date(text, today=None):
    """Return 'YYYY-MM-DD' for a spoken date phrase, or None if it can't be understood."""
    if text is None:
        return None
    today = today or date.today()
    s = re.sub(r"[,.!?]", " ", str(text).lower())
    s = re.sub(r"\b(on|the|this|coming)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    try:
        return _resolve(s, today)
    except ValueError:            # impossible dates such as "february 30th"
        return None