"""The typed assistant.  Run it from the project root with:

    python -m app.assistant
    python -m app.assistant --model llama3
"""
import argparse
import sys

from . import storage
from .executor import Assistant
from .logbook import Logbook
from .parser import DEFAULT_MODEL, ModelUnavailable, ask, build_system_prompt, parse


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Typed voice-calendar assistant")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default: {DEFAULT_MODEL})")
    args = ap.parse_args(argv)

    system = build_system_prompt()

    def parse_fn(phrase, today):
        print("  thinking...", end="\r", flush=True)
        try:
            return parse(phrase, today, model=args.model, system=system)
        finally:
            print(" " * 20, end="\r", flush=True)

    bot = Assistant(parse_fn, logbook=Logbook(storage.DATA_FILE.parent / "command_log.jsonl"))

    print(f"Loading {args.model} (the first time can take about 20 seconds)...")
    try:
        ask(args.model, system, "what do I have today")
    except Exception as e:                       # the loop still works for local replies
        print(f"Warning: could not load the model yet: {e}")
    print('Ready. Say what you want to do. Type "help" for ideas or "quit" to leave.\n')

    while True:
        try:
            text = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.strip().lower() in ("quit", "exit", "bye"):
            break
        try:
            reply = bot.handle(text)
        except ModelUnavailable as e:
            reply = str(e)
        if reply:
            print(reply + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])