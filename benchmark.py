"""Benchmark local Ollama models on parsing spoken calendar commands.

Usage (from the project root, with Ollama running):
    python benchmark.py llama3.2:3b
    python benchmark.py llama3.2:3b llama3            # compare models side by side
    python benchmark.py llama3.2:3b --limit 10        # quick smoke test
    python benchmark.py llama3.2:3b --today 2026-11-05   # repeatable dates
    python benchmark.py llama3.2:3b --no-checks       # raw model only, safety net off

The model returns the date as spoken ("Sunday", "October 5th"); app/dates.py turns it into a
real date and app/checks.py catches wrong actions and invented values, so the benchmark
measures the whole pipeline. Every phrase is a fresh request (no shared chat history), with
the reply forced into a JSON schema and temperature 0. Standard library only.
"""
import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from app.checks import finalize

OLLAMA_URL = "http://localhost:11434/api/chat"
PHRASES_FILE = Path(__file__).resolve().parent / "tests" / "benchmark_phrases.json"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
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
- delete: remove one (also: cancel, get rid of)
- update: change an existing one (also: move, reschedule, change); date_text and time hold the NEW values
- complete: mark one as done (also: check, check off, finished). "check the X" means complete X
- list: show what is scheduled on a day (or everything if no day is given)
- info: ask about one existing item (when is it, what time, how long until it)

Rules:
- title: the name of the item, without filler words. Keep quoted names as spoken. Use null for list.
- date_text: the date words exactly as spoken (for example "tomorrow", "Sunday", "October 5th", "in 2 days"), or null if no day was named. Do NOT convert it into a numeric date.
- time: 24-hour HH:MM, or null if no time was said. Without am/pm, hours 1-6 mean PM and 7-11 mean AM; noon is 12:00.
- Never invent a date or a time that was not said.
- delete, complete, and info normally have date_text and time null.
- Reply with the JSON object only."""


# -------------------------------------------- expected dates (independent logic) ---
# Kept separate from app/dates.py on purpose, so the resolver is checked against a
# second implementation instead of against itself.

def next_weekday(today: date, name: str) -> date:
    delta = (WEEKDAYS.index(name) - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


def next_month_day(today: date, month: int, day: int) -> date:
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def resolve_date_token(token, today: date):
    """Turn the expected-date tokens in the phrase file into real dates for this run."""
    if token is None:
        return None
    if token == "today":
        return today.isoformat()
    if token == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if token.startswith("+"):
        return (today + timedelta(days=int(token[1:]))).isoformat()
    if token.startswith("next:"):
        return next_weekday(today, token[5:]).isoformat()
    if token.startswith("md:"):
        month, day = (int(x) for x in token[3:].split("-"))
        return next_month_day(today, month, day).isoformat()
    return token


# ---------------------------------------------------------------- prompt ---

def build_system_prompt() -> str:
    def ex(phrase, action, title, date_text, time_):
        obj = {"action": action, "title": title, "date_text": date_text, "time": time_}
        return f'"{phrase}" -> {json.dumps(obj)}'

    examples = [
        ex("put piano lesson on my calendar tomorrow at 4", "add", "piano lesson", "tomorrow", "16:00"),
        ex("cancel the car wash", "delete", "car wash", None, None),
        ex("I finished the laundry task", "complete", "laundry", None, None),
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
        "keep_alive": "30m",
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return data["message"]["content"], time.perf_counter() - start


# --------------------------------------------------------------- scoring ---

def title_ok(expected, got) -> bool:
    if expected is None:                  # e.g. list: title isn't checked
        return True
    if not got:
        return False
    e, g = expected.lower().strip(), got.lower().strip()
    return (e in g or (len(g) >= 3 and g in e)
            or SequenceMatcher(None, e, g).ratio() >= 0.8)


def show(d) -> str:
    return " | ".join(str(d.get(k)) if d.get(k) else "-" for k in ("action", "title", "date", "time"))


def run_model(model, system, cases, today, use_checks=True):
    mode = "safety net on" if use_checks else "RAW model, safety net off"
    print(f"\n=== {model} ===  {len(cases)} phrases, today = {today.isoformat()}, {mode}")
    print("Warming up (loads the model and caches the prompt)...")
    try:
        ask(model, system, "what do I have today")
    except urllib.error.HTTPError as e:
        print(f"  Ollama rejected the request (HTTP {e.code}). Is '{model}' downloaded? Try: ollama pull {model}")
        return None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        print(f"  Could not reach Ollama at {OLLAMA_URL}: {e}\n  Is Ollama running?")
        return None

    counts = {"action": 0, "title": 0, "date": 0, "time": 0, "overall": 0}
    bad_json = 0
    corrected = 0
    times, failures = [], []
    print("  ", end="", flush=True)

    for i, case in enumerate(cases, 1):
        expected = {
            "action": case["action"],
            "title": case.get("title"),
            "date": resolve_date_token(case.get("date"), today),
            "time": case.get("time"),
        }
        try:
            content, secs = ask(model, system, case["phrase"])
        except Exception as e:                      # timeout etc.
            failures.append((i, case["phrase"], expected, f"ERROR: {e}", []))
            print("E", end="", flush=True)
            continue
        times.append(secs)

        try:
            raw = json.loads(content)
            got, notes = finalize(raw, case["phrase"], today, use_checks)
        except (json.JSONDecodeError, AttributeError):
            bad_json += 1
            failures.append((i, case["phrase"], expected, f"INVALID JSON: {content[:80]}", []))
            print("x", end="", flush=True)
            continue
        corrected += bool(notes)

        ok = {
            "action": got["action"] == expected["action"],
            "title": title_ok(expected["title"], got["title"]),
            "date": got["date"] == expected["date"],
            "time": got["time"] == expected["time"],
        }
        for k, v in ok.items():
            counts[k] += v
        if all(ok.values()):
            counts["overall"] += 1
            print(".", end="", flush=True)
        else:
            failures.append((i, case["phrase"], expected, got, notes))
            print("x", end="", flush=True)

    n = len(cases)
    pct = lambda c: f"{c}/{n} ({100 * c / n:.0f}%)"
    print("\n")
    print(f"All fields correct : {pct(counts['overall'])}")
    print(f"  action           : {pct(counts['action'])}")
    print(f"  title            : {pct(counts['title'])}")
    print(f"  date             : {pct(counts['date'])}")
    print(f"  time             : {pct(counts['time'])}")
    print(f"Invalid JSON       : {bad_json}")
    if use_checks:
        print(f"Safety-net changes : {corrected} phrases had something corrected")
    if times:
        print(f"Time per command   : avg {statistics.mean(times):.1f}s, "
              f"median {statistics.median(times):.1f}s, slowest {max(times):.1f}s")

    if failures:
        print(f"\nFailures ({len(failures)})   [action | title | date | time]")
        for i, phrase, exp, got, notes in failures:
            print(f"  #{i}  \"{phrase}\"")
            print(f"       expected: {show(exp)}")
            if isinstance(got, str):
                print(f"       got:      {got}")
            else:
                said = f'   (date said: "{got["date_text"]}")' if got.get("date_text") else ""
                print(f"       got:      {show(got)}{said}")
                for note in notes:
                    print(f"       checks:   {note}")

    return {"model": model, "n": n, "avg": statistics.mean(times) if times else 0, **counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+", help="Ollama model names, e.g. llama3.2:3b")
    ap.add_argument("--limit", type=int, help="only run the first N phrases")
    ap.add_argument("--today", help="pretend today is this date (YYYY-MM-DD)")
    ap.add_argument("--no-checks", action="store_true", help="score the raw model, without the safety net")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    cases = json.loads(PHRASES_FILE.read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[:args.limit]
    system = build_system_prompt()

    results = [r for r in (run_model(m, system, cases, today, not args.no_checks) for m in args.models) if r]

    if len(results) > 1:
        print("\n=== Summary ===")
        print(f"{'model':<20}{'overall':>9}{'action':>8}{'title':>7}{'date':>6}{'time':>6}{'avg s':>8}")
        for r in results:
            p = lambda k: f"{100 * r[k] / r['n']:.0f}%"
            print(f"{r['model']:<20}{p('overall'):>9}{p('action'):>8}{p('title'):>7}"
                  f"{p('date'):>6}{p('time'):>6}{r['avg']:>8.1f}")


if __name__ == "__main__":
    main()