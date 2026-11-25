"""Benchmark local Ollama models on parsing spoken calendar commands.

Usage (from the project root, with Ollama running):
    python benchmark.py llama3.2:3b
    python benchmark.py llama3.2:3b llama3            # compare models side by side
    python benchmark.py llama3.2:3b --limit 10        # quick smoke test
    python benchmark.py llama3.2:3b --today 2026-11-05   # repeatable dates
    python benchmark.py llama3.2:3b --no-checks       # raw model only, safety net off
    python benchmark.py llama3.2:3b --phrases tests/heldout_phrases.json

The model returns the date as spoken ("Sunday", "October 5th"); app/dates.py turns it into a
real date and app/checks.py catches wrong actions and invented values, so the benchmark
measures the whole pipeline. Every phrase is a fresh request (no shared chat history), with
the reply forced into a JSON schema and temperature 0. Standard library only.

Phrase files: each entry has "phrase" plus the expected "action", "title", "date", "time".
  - "title" may be a list of acceptable answers; null means "no title" (not checked for list).
  - An entry with a "gap" text instead of expected values is a feature the app doesn't have
    yet: it is run and shown, but not scored.
  - Expected dates are tokens, resolved at run time:
      today | tomorrow | +N          days from today
      next:sunday                    first Sunday AFTER today
      this:friday                    first Friday on or after today
      nextweek:friday                Friday of next calendar week (weeks start Monday)
      md:10-05                       next time October 5th occurs
"""
import argparse
import json
import statistics
import urllib.error
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from app.checks import finalize
from app.parser import OLLAMA_URL, ask, build_system_prompt

PHRASES_FILE = Path(__file__).resolve().parent / "tests" / "benchmark_phrases.json"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
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
    """Turn the expected-date tokens in the phrase files into real dates for this run."""
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
    if token.startswith("this:"):
        return (today + timedelta(days=(WEEKDAYS.index(token[5:]) - today.weekday()) % 7)).isoformat()
    if token.startswith("nextweek:"):
        monday_next = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return (monday_next + timedelta(days=WEEKDAYS.index(token[9:]))).isoformat()
    if token.startswith("md:"):
        month, day = (int(x) for x in token[3:].split("-"))
        return next_month_day(today, month, day).isoformat()
    return token


# --------------------------------------------------------------- scoring ---

def title_ok(expected, got, action) -> bool:
    if expected is None:                  # list: title isn't checked; otherwise it must be empty
        return True if action == "list" else got is None
    if not got:
        return False
    g = got.lower().strip()
    for option in (expected if isinstance(expected, list) else [expected]):
        e = option.lower().strip()
        if e in g or (len(g) >= 3 and g in e) or SequenceMatcher(None, e, g).ratio() >= 0.8:
            return True
    return False


def show(d) -> str:
    return " | ".join(str(d.get(k)) if d.get(k) else "-" for k in ("action", "title", "date", "time"))


def run_model(model, system, cases, today, use_checks=True):
    scored_cases = [c for c in cases if "gap" not in c]
    mode = "safety net on" if use_checks else "RAW model, safety net off"
    print(f"\n=== {model} ===  {len(scored_cases)} scored + {len(cases) - len(scored_cases)} "
          f"known-gap phrases, today = {today.isoformat()}, {mode}")
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
    times, failures, observed = [], [], []
    print("  ", end="", flush=True)

    for i, case in enumerate(cases, 1):
        gap = case.get("gap")
        expected = None
        if not gap:
            expected = {
                "action": case["action"],
                "title": case.get("title"),
                "date": resolve_date_token(case.get("date"), today),
                "time": case.get("time"),
            }
        try:
            content, secs = ask(model, system, case["phrase"])
        except Exception as e:                      # timeout etc.
            (observed if gap else failures).append(
                (i, case["phrase"], gap, f"ERROR: {e}") if gap else (i, case["phrase"], expected, f"ERROR: {e}", []))
            print("E", end="", flush=True)
            continue
        if not gap:
            times.append(secs)

        try:
            raw = json.loads(content)
            got, notes = finalize(raw, case["phrase"], today, use_checks)
        except (json.JSONDecodeError, AttributeError):
            if gap:
                observed.append((i, case["phrase"], gap, f"INVALID JSON: {content[:80]}"))
            else:
                bad_json += 1
                failures.append((i, case["phrase"], expected, f"INVALID JSON: {content[:80]}", []))
            print("-" if gap else "x", end="", flush=True)
            continue

        if gap:
            observed.append((i, case["phrase"], gap, got))
            print("-", end="", flush=True)
            continue

        corrected += bool(notes)
        ok = {
            "action": got["action"] == expected["action"],
            "title": title_ok(expected["title"], got["title"], expected["action"]),
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

    n = len(scored_cases)
    pct = lambda c: f"{c}/{n} ({100 * c / n:.0f}%)"
    print("\n   (. correct   x wrong   - known gap, not scored)\n")
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
                if got.get("reason"):
                    print(f"       asks:     {got['reason']}")
                for note in notes:
                    print(f"       checks:   {note}")

    if observed:
        print(f"\nKnown gaps, not scored ({len(observed)}) -- what the pipeline produced   [action | title | date | time]")
        for i, phrase, reason, got in observed:
            print(f"  #{i}  \"{phrase}\"")
            print(f"       gap: {reason}")
            print(f"       got: {got if isinstance(got, str) else show(got)}")
            if not isinstance(got, str) and got.get("reason"):
                print(f"       asks: {got['reason']}")

    return {"model": model, "n": n, "avg": statistics.mean(times) if times else 0, **counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+", help="Ollama model names, e.g. llama3.2:3b")
    ap.add_argument("--phrases", default=str(PHRASES_FILE), help="phrase file to use")
    ap.add_argument("--limit", type=int, help="only run the first N phrases")
    ap.add_argument("--today", help="pretend today is this date (YYYY-MM-DD)")
    ap.add_argument("--no-checks", action="store_true", help="score the raw model, without the safety net")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    cases = json.loads(Path(args.phrases).read_text(encoding="utf-8"))
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