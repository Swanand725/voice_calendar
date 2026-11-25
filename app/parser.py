"""Turns a spoken sentence into a command: Ollama model -> safety net -> date resolver.

    result = parse("cancel the team meeting", today)
    result.cmd      # the finished command (fields are described in app/checks.py)
    result.notes    # what the safety net changed
    result.raw      # the model's untouched JSON

The benchmark imports its prompt and schema from here, so it always tests exactly what the
app runs.
"""
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date

from . import config
from .checks import finalize

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = config.get("model")

ACTIONS = ["add", "delete", "update", "complete", "list", "info"]

_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "title": _NULLABLE_STR,
        "date_text": _NULLABLE_STR,
        "time": _NULLABLE_STR,
    },
    "required": ["action", "title", "date_text", "time"],
    "additionalProperties": False,
}

RULES = """You convert spoken calendar requests into one JSON object with the keys action, title, date_text, time.

Actions:
- add: create a new event, task, assignment, or reminder
- delete: remove one (also: cancel, get rid of); "clear" or "wipe" a whole day is also delete
- update: change an existing one (also: move, reschedule, change); date_text and time hold the NEW values
- complete: mark one as done (also: check, check off, finished, done with). "check the X" means complete X
- list: show what is scheduled on a day (or everything if no day is given)
- info: ask about one existing item (when is it, what time, how long until it)

Rules:
- title: the name of the item, without filler words. Keep quoted names as spoken. Use null for list.
- date_text: the date words exactly as spoken (for example "tomorrow", "Sunday", "October 5th", "in 2 days"), or null if no day was named. Do NOT convert it into a numeric date.
- time: 24-hour HH:MM, or null if no time was said. Without am/pm, hours 1-7 mean PM and 8-11 mean AM; noon is 12:00.
- Never invent a date or a time that was not said.
- delete, complete, and info: date_text is only the day the item is on, if the person named one (it narrows which item); time is normally null.
- If the request is about everything or all items on a day, title is null.
- Reply with the JSON object only."""


# ---------------------------------------------------------------- prompt ---

def build_system_prompt() -> str:
    def ex(phrase, action, title, date_text, time_):
        obj = {"action": action, "title": title, "date_text": date_text, "time": time_}
        return f'"{phrase}" -> {json.dumps(obj)}'

    examples = [
        ex("put piano lesson on my calendar tomorrow at 4", "add", "piano lesson", "tomorrow", "16:00"),
        ex("cancel the car wash tomorrow", "delete", "car wash", "tomorrow", None),
        ex("I finished the laundry task", "complete", "laundry", None, None),
        ex("I'm done with the science project", "complete", "science project", None, None),
        ex("check the taxes assignment", "complete", "taxes", None, None),
        ex("what do I have today", "list", None, "today", None),
        ex("show me everything I have coming up", "list", None, None, None),
        ex("when is my piano lesson", "info", "piano lesson", None, None),
        ex("move the piano lesson to 5", "update", "piano lesson", None, "17:00"),
        ex("change the car wash to Saturday", "update", "car wash", "Saturday", None),
    ]
    return RULES + "\n\nExamples:\n" + "\n".join(examples)


# ---------------------------------------------------------------- ollama ---

def ask(model: str, system: str, phrase: str):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": phrase},
        ],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0},
        "keep_alive": config.keep_alive_value(),
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return data["message"]["content"], time.perf_counter() - start



# ------------------------------------------------------------------ parse ---

class ModelUnavailable(Exception):
    """Ollama isn't running, or the model isn't downloaded."""


@dataclass
class ParseResult:
    cmd: dict
    notes: list = field(default_factory=list)
    raw: dict = None
    seconds: float = 0.0


_SYSTEM = None


def parse(phrase, today=None, model=DEFAULT_MODEL, system=None, use_checks=True) -> ParseResult:
    global _SYSTEM
    if system is None:
        _SYSTEM = _SYSTEM or build_system_prompt()
        system = _SYSTEM
    try:
        content, seconds = ask(model, system, phrase)
    except urllib.error.HTTPError as e:
        raise ModelUnavailable(
            f"Ollama rejected the request (HTTP {e.code}). "
            f"Is '{model}' downloaded? Try: ollama pull {model}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise ModelUnavailable(
            f"I can't reach Ollama at {OLLAMA_URL}. Is it running? ({e})") from e

    try:
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("not an object")
    except ValueError:
        cmd = {"action": "clarify", "title": None, "date_text": None, "date": None,
               "time": None, "all_items": False,
               "reason": "I couldn't understand that. Could you say it another way?"}
        return ParseResult(cmd, ["model returned invalid JSON"], None, seconds)

    cmd, notes = finalize(raw, phrase, today or date.today(), use_checks)
    return ParseResult(cmd, notes, raw, seconds)