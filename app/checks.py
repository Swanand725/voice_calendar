"""A safety net around the language model: small deterministic checks on what it returns.

The model is good at understanding messy wording, but it sometimes picks the wrong action,
invents a date or time you never said, mixes up the title and the date, or copies words from
the prompt's examples. It also can't handle several requests in one sentence. These checks are
plain code, so they are instant and always behave the same way.

    cmd, notes = finalize(raw_model_json, "what the person said", today)

`notes` lists anything the checks changed, which is handy for logging and debugging.
If the person said several things at once, or corrected themselves mid-sentence, the command
comes back with action "clarify" so the app can ask instead of guessing.
"""
import re
from datetime import date
from difflib import SequenceMatcher

from .dates import MONTHS, WEEKDAYS, resolve_date

# ------------------------------------------------------------ text helpers ---

_FILLER = re.compile(
    r"^(?:(?:um+|uh+|er+|oh|ok|okay|hey|so|like|please|just|well|actually|yeah|yep|ya|"
    r"alright|all right|and|can you|could you|would you|will you|i need you to|"
    r"i want you to|go ahead and)\b[,\s]*)+")


def _norm(text) -> str:
    return str(text).lower().replace("\u2019", "'").strip()


def _strip_fillers(text: str) -> str:
    return _FILLER.sub("", text.strip()).strip(" ,.!?")


# ------------------------------------------------------------ action rules ---
# Obvious command verbs decide the action without asking the model.

_ACTION_RULES = [
    ("delete", r"(?:delete|remove|cancel|scratch|get rid of|ditch|clear|wipe(?: out)?|empty)\b"),
    ("update", r"(?:move|reschedule|change|postpone|push)\b"),
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

# Phrases that mean "I'm changing my mind": the first verb is no longer the real request.
_CORRECTION = re.compile(
    r"\b(?:never ?mind|scratch that|on second thought)\b"
    r"|\bno,? (?:don't|do not|wait)\b|\bactually,? (?:no|wait)\b|\bwait,? no\b")

# Command verbs that start a request, used to spot several requests in one sentence.
_CMD_VERBS = re.compile(
    r"(?:add|schedule|put|create|book|set up|remind me|delete|remove|cancel|get rid of|ditch|clear|wipe|empty|"
    r"ignore|move|reschedule|push|change|make|mark|check off|tick off|cross off)\b")
_CLAUSE_SPLIT = re.compile(r"[.;!?,]|\band\b|\bthen\b|\balso\b|\bbut\b")


def has_correction(phrase: str) -> bool:
    return bool(_CORRECTION.search(_norm(phrase)))


def count_commands(phrase: str) -> int:
    """How many clauses start with a command verb ("move X to Y, and delete Z" -> 2)."""
    return sum(1 for clause in _CLAUSE_SPLIT.split(_norm(phrase))
               if _CMD_VERBS.match(_strip_fillers(clause)))


def needs_clarification(phrase: str):
    """Return a question for the person if the sentence can't safely be turned into one command."""
    if has_correction(phrase):
        return "I heard you change your mind. Please say only the final request."
    if count_commands(phrase) >= 2:
        return "That sounds like more than one request. Please say them one at a time."
    return None


def rule_action(phrase: str):
    """Return the action if the phrase starts with an unmistakable command, else None."""
    if has_correction(phrase):
        return None
    s = _strip_fillers(_norm(phrase))
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
    s = _norm(phrase)
    for pattern in _DATE_PATTERNS:
        s = pattern.sub(" ", s)
    return bool(_TIME_CUE.search(s))


_AMPM = re.compile(rf"(?:\d|\b(?:{_NUM_WORDS}))\s*([ap])\.?m\b")
_PM_WORDS = re.compile(r"\b(?:afternoon|evening|night|tonight)\b")
_AM_WORDS = re.compile(r"\bmorning\b")


def fix_am_pm(time_value, phrase: str):
    """Apply the AM/PM rule in code, because the model doesn't follow it reliably.

    - Said "pm", "afternoon", "evening", "night": an hour from 1 to 11 is PM.
    - Said "am" or "morning": left alone.
    - Said nothing (a bare "at 7"): hours 1 to 7 mean PM, 8 to 11 mean AM, 12 is noon.
    Returns (time, note); note is None when nothing changed.
    """
    m = re.fullmatch(r"(\d{2}):(\d{2})", time_value or "")
    if not m:
        return time_value, None
    hour, minute = int(m[1]), m[2]
    s = _norm(phrase)
    ampm = _AMPM.search(s)
    if ampm:
        pm, am = ampm[1] == "p", ampm[1] == "a"
    else:
        pm, am = bool(_PM_WORDS.search(s)), bool(_AM_WORDS.search(s))
    if pm and am:                                   # contradictory wording: leave it
        return time_value, None
    if pm and 1 <= hour <= 11:
        why = "said pm/afternoon/evening"
    elif not pm and not am and 1 <= hour <= 7:
        why = "a bare hour from 1 to 7 means PM"
    else:
        return time_value, None
    fixed = f"{hour + 12:02d}:{minute}"
    return fixed, f"time {time_value} -> {fixed} ({why})"


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


# ------------------------------------------------------------------ titles ---

_GENERIC_TITLES = {"everything", "all", "anything", "it", "that", "this", "them", "stuff", "things",
                   "schedule", "calendar", "agenda", "day", "plans", "tasks", "events", "items"}
_BULK = re.compile(r"\b(?:everything|all|every)\b")
_BULK_VERB = r"(?:clear|wipe(?: out)?|empty)"
_BULK_OBJECT = re.compile(rf"^{_BULK_VERB}\b.*\b(?:schedule|calendar|agenda|day|plans)\b")
_BULK_BARE_DAY = re.compile(rf"^{_BULK_VERB}\s+(?:out\s+)?(?:today|tomorrow|tonight|{'|'.join(WEEKDAYS)})\b")


def is_bulk_request(phrase: str) -> bool:
    """True for "cancel everything on Friday" and "clear my schedule for today"."""
    s = _norm(phrase)
    lead = _strip_fillers(s)
    return bool(_BULK.search(s) or _BULK_OBJECT.match(lead) or _BULK_BARE_DAY.match(lead))
_TIMEISH = re.compile(r"(?:at )?\d{1,2}(?::\d{2})? ?(?:am|pm|a\.m\.|p\.m\.)?|noon|midnight")
_VERB_PREFIX = re.compile(
    r"^(?:delete|remove|cancel|scratch|get rid of|ditch|clear|wipe out|wipe|empty|"
    r"move|reschedule|change|postpone|push|"
    r"check off|tick off|cross off|check|mark|i(?:'ve| have)? (?:finished|completed)|"
    r"i(?:'m| am) done with|finished|completed)\b")
_ARTICLES = {"the", "my", "that", "this", "those", "a", "an", "our", "your"}
_TRAILING = set(WEEKDAYS) | {"today", "tomorrow", "tonight", "yesterday", "morning", "afternoon",
                             "evening", "am", "pm", "at", "on", "in", "this", "next"}
_CUT = re.compile(r"[.;!?,]| (?:to|from|on|at|for|by|until|as|because|since)\b")


def _is_date_or_time(text: str, today: date) -> bool:
    t = _norm(text)
    return bool(_TIMEISH.fullmatch(t)) or resolve_date(t, today) is not None


def fallback_title(phrase: str):
    """Pull the item name out of "cancel the dentist appointment tomorrow" without the model.
    Only works for phrases that start with a command verb; returns None when unsure."""
    s = _strip_fillers(_norm(phrase))
    m = _VERB_PREFIX.match(s)
    if not m:
        return None
    words = _CUT.split(s[m.end():])[0].split()
    while words and words[0] in _ARTICLES:
        words.pop(0)
    while words and (words[-1] in _TRAILING or re.fullmatch(r"\d{1,2}(?::\d{2})?(?:am|pm)?", words[-1])):
        words.pop()
    found = " ".join(words)
    if not words or words[0] in ("everything", "all", "anything") or found in _GENERIC_TITLES:
        return None
    original = re.search(re.escape(found), str(phrase).replace("\u2019", "'"), re.I)
    return original.group(0) if original else found


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


def _clarify(reason: str):
    cmd = {"action": "clarify", "title": None, "date_text": None, "date": None,
           "time": None, "all_items": False, "reason": reason}
    return cmd, [f"needs clarification: {reason}"]


def finalize(raw: dict, phrase: str, today: date = None, use_checks: bool = True):
    """Model JSON + what the person said -> (clean command, notes about what was fixed).

    The returned command has: action, title, date_text, date (resolved ISO date, or
    '?text' if the words could not be understood), time (HH:MM), and all_items (True only
    for "cancel everything on Friday"-style requests; never delete a whole day without it).
    Its action is "clarify" (with a `reason`) when the sentence can't safely become a single
    command, and the app should ask the person that question instead of doing anything.
    """
    today = today or date.today()
    cmd = {
        "action": _clean(raw.get("action")),
        "title": _clean(raw.get("title")),
        "date_text": _clean(raw.get("date_text")),
        "time": norm_time(raw.get("time")),
        "all_items": False,
    }
    notes = []

    if use_checks:
        reason = needs_clarification(phrase)
        if reason:
            return _clarify(reason)

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
        if cmd["time"]:
            cmd["time"], why = fix_am_pm(cmd["time"], phrase)
            if why:
                notes.append(why)

        if cmd["action"] != "list":
            title = cmd["title"]
            if title:
                if _is_date_or_time(title, today):
                    if (not cmd["date_text"] and resolve_date(title, today)
                            and date_text_grounded(title, phrase)):
                        cmd["date_text"] = title
                    notes.append(f"title '{title}' was really a date or time")
                    title = None
                elif _norm(title) in _GENERIC_TITLES:
                    notes.append(f"dropped title '{title}' (means everything, not a name)")
                    title = None
                elif not title_grounded(title, phrase):
                    notes.append(f"dropped title '{title}' (not in what was said)")
                    title = None
            if title is None and cmd["action"] in ("delete", "update", "complete"):
                found = fallback_title(phrase)
                if found:
                    notes.append(f"title '{found}' taken from what was said")
                    title = found
            cmd["title"] = title

        if cmd["action"] in ("delete", "update", "complete", "info") and cmd["title"] is None:
            if cmd["action"] == "delete" and is_bulk_request(phrase):
                if not cmd["date_text"]:
                    return _clarify("Which day should I clear? I won't delete everything without one.")
                cmd["all_items"] = True
            else:
                return _clarify("Which item do you mean?")
        elif cmd["action"] == "add" and cmd["title"] is None:
            return _clarify("What should I call it?")

    resolved = resolve_date(cmd["date_text"], today) if cmd["date_text"] else None
    if cmd["date_text"] and resolved is None:
        resolved = f"?{cmd['date_text']}"      # unclear: the app should ask the user
    cmd["date"] = resolved
    if use_checks and cmd["action"] == "update" and cmd["date"] is None and cmd["time"] is None:
        return _clarify("What should I change it to?")
    return cmd, notes