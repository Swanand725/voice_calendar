"""A safety net around the language model: small deterministic checks on what it returns.

The model is good at understanding messy wording, but it sometimes picks the wrong action,
invents a date or time you never said, or copies words from the prompt's examples. These
checks are plain code, so they are instant and always behave the same way.

    cmd, notes = finalize(raw_model_json, "what the person said", today)

`notes` lists anything the checks changed, which is handy for logging and debugging.
"""
import re
from datetime import date
from difflib import SequenceMatcher

from .dates import MONTHS, resolve_date

# ------------------------------------------------------------ action rules ---
# Obvious command verbs decide the action without asking the model.

_FILLER = re.compile(
    r"^(?:(?:um+|uh+|er+|ok|okay|hey|so|like|please|can you|could you|would you|will you|"
    r"i need you to|i want you to|go ahead and)\b[,\s]*)+")

_ACTION_RULES = [
    ("delete", r"(?:delete|remove|cancel|scratch|get rid of)\b"),
    ("update", r"(?:move|reschedule|change|postpone|push back)\b"),
    ("complete", r"(?:check off|tick off|cross off)\b"),
    ("complete", r"check the (?!(?:schedule|calendar|agenda|weather|time|date)\b)"),
    ("complete", r"mark\b.*\b(?:done|complete|completed|finished)\b"),
    ("complete", r"i(?:'ve| have)? (?:finished|completed)\b"),
    ("complete", r"i(?:'m| am) done with\b"),
    ("add", r"(?:add|create|book|put|remind me)\b"),
    ("add", r"schedule (?!(?:for|on|today|tomorrow)\b)"),
    ("add", r"i have an? "),
]
_ACTION_RULES = [(action, re.compile(pattern)) for action, pattern in _ACTION_RULES]


def rule_action(phrase: str):
    """Return the action if the phrase starts with an unmistakable command, else None."""
    s = _FILLER.sub("", phrase.lower().strip()).strip(" ,.!?")
    for action, pattern in _ACTION_RULES:
        if pattern.match(s):
            return action
    return None


# ---------------------------------------------------------- grounding checks ---

_NUM_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_MONTH_NAMES = "|".join(MONTHS + ["jan", "feb", "mar", "apr", "jun", "jul", "aug", "sept", "sep",
                                  "oct", "nov", "dec"])
_DATE_PATTERNS = [
    re.compile(rf"\b(?:{_MONTH_NAMES})\b\.? \d{{1,2}}(?:st|nd|rd|th)?(?:,? \d{{4}})?"),   # october 5th
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?(?: of)? (?:{_MONTH_NAMES})\b"),             # 5th of october
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?"),                                         # 10/5
    re.compile(r"\b\d{4}-\d{2}-\d{2}"),                                                    # 2026-10-05
    re.compile(r"\b(?:in )?\d+ (?:day|days|week|weeks)\b"),                                # in 2 days
]
_TIME_CUE = re.compile(
    rf"\d|\bnoon\b|\bmidnight\b|o'?clock|\bat (?:{_NUM_WORDS})\b|\b(?:{_NUM_WORDS}) (?:am|pm)\b")
_STOP = {"the", "and", "for", "with", "my", "to", "of", "on", "in", "at"}


def _words(text: str):
    return re.findall(r"[a-z]+|\d+", text.lower())


def time_mentioned(phrase: str) -> bool:
    """True if the phrase contains something that sounds like a time of day.
    Digits that are part of a date ("October 5th", "in 2 days") don't count."""
    s = phrase.lower()
    for pattern in _DATE_PATTERNS:
        s = pattern.sub(" ", s)
    return bool(_TIME_CUE.search(s))


def date_text_grounded(text: str, phrase: str) -> bool:
    """True if at least one word of the date text really appears in what was said."""
    said = _words(phrase)
    for w in _words(text):
        if w.isdigit():
            if w in said:
                return True
        elif len(w) >= 3 and any(len(p) >= 3 and p[:3] == w[:3] for p in said):
            return True
    return False


def title_grounded(title: str, phrase: str) -> bool:
    """True if the title shares a meaningful word (typos allowed) with what was said."""
    said = [w for w in _words(phrase) if w.isalpha()]
    title_words = [w for w in _words(title) if w.isalpha() and len(w) >= 3 and w not in _STOP]
    if not title_words:
        return True                       # nothing to judge
    return any(w == p or SequenceMatcher(None, w, p).ratio() >= 0.8
               for w in title_words for p in said)


# --------------------------------------------------------------- cleaning ---

def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return None if value.lower() in ("", "null", "none") else value


def norm_time(value):
    """Accept 19:00:00 or 9:00 and turn them into HH:MM."""
    value = _clean(value)
    if value is None:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", value)
    return f"{int(m[1]):02d}:{m[2]}" if m else value


def finalize(raw: dict, phrase: str, today: date = None, use_checks: bool = True):
    """Model JSON + what the person said -> (clean command, notes about what was fixed).

    The returned command has: action, title, date_text, date (resolved ISO date, or
    '?text' if the words could not be understood), time (HH:MM).
    """
    cmd = {
        "action": _clean(raw.get("action")),
        "title": _clean(raw.get("title")),
        "date_text": _clean(raw.get("date_text")),
        "time": norm_time(raw.get("time")),
    }
    notes = []

    if use_checks:
        forced = rule_action(phrase)
        if forced and forced != cmd["action"]:
            notes.append(f"action {cmd['action']} -> {forced} (keyword rule)")
            cmd["action"] = forced
        if cmd["date_text"] and not date_text_grounded(cmd["date_text"], phrase):
            notes.append(f"dropped date '{cmd['date_text']}' (not in what was said)")
            cmd["date_text"] = None
        if cmd["time"] and not time_mentioned(phrase):
            notes.append(f"dropped time {cmd['time']} (no time in what was said)")
            cmd["time"] = None
        if cmd["title"] and cmd["action"] != "list" and not title_grounded(cmd["title"], phrase):
            notes.append(f"dropped title '{cmd['title']}' (not in what was said)")
            cmd["title"] = None

    resolved = resolve_date(cmd["date_text"], today) if cmd["date_text"] else None
    if cmd["date_text"] and resolved is None:
        resolved = f"?{cmd['date_text']}"      # unclear: the app should ask the user
    cmd["date"] = resolved
    return cmd, notes