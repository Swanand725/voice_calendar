import argparse
import sys
from datetime import date, timedelta

from .models import Item
from . import storage


def fmt(item: Item) -> str:
    when = item.time or "all day"
    mark = "x" if item.status == "done" else " "
    return f"[{mark}] {item.id}  {item.date}  {when:<8}  {item.title}"


def pick_one(matches: list[Item], action: str) -> Item | None:
    """If a search matches several items, ask which one. Never guess."""
    if not matches:
        print("No matching items.")
        return None
    if len(matches) == 1:
        return matches[0]
    print(f"Several items match. Which one do you want to {action}?")
    for n, item in enumerate(matches, 1):
        print(f"  {n}. {fmt(item)}")
    choice = input("Number (or Enter to cancel): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(matches):
        return matches[int(choice) - 1]
    print("Cancelled.")
    return None


def resolve_date(text: str) -> str:
    if text == "today":
        return date.today().isoformat()
    if text == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    return text   # expects YYYY-MM-DD


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="calendar")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="add an item")
    a.add_argument("title")
    a.add_argument("--date", default="today", help="YYYY-MM-DD, today, or tomorrow")
    a.add_argument("--time", help="HH:MM (24h); leave out for all day")
    a.add_argument("--remind", type=int, nargs="*", default=[10],
                   help="minutes before to remind (default: 10)")

    l = sub.add_parser("list", help="list items")
    l.add_argument("--date", help="YYYY-MM-DD, today, or tomorrow")
    l.add_argument("--all", action="store_true", help="include completed items")

    d = sub.add_parser("delete", help="delete an item by title search")
    d.add_argument("search")

    c = sub.add_parser("done", help="mark an item complete")
    c.add_argument("search")

    args = p.parse_args(argv)

    try:
        if args.cmd == "add":
            item = storage.add_item(Item(
                title=args.title,
                date=resolve_date(args.date),
                time=args.time,
                reminders_min_before=args.remind,
            ))
            print("Added:", fmt(item))

        elif args.cmd == "list":
            day = resolve_date(args.date) if args.date else None
            items = storage.list_items(date=day, status=None if args.all else "pending")
            if not items:
                print("Nothing scheduled.")
            for item in items:
                print(fmt(item))

        elif args.cmd == "delete":
            item = pick_one(storage.search(args.search), "delete")
            if item and input(f"Delete '{item.title}'? (y/n): ").lower() == "y":
                storage.delete_item(item.id)
                print("Deleted.")

        elif args.cmd == "done":
            item = pick_one(storage.search(args.search), "complete")
            if item:
                storage.update_item(item.id, status="done")
                print("Marked done:", item.title)

    except ValueError as e:
        print(f"Invalid input: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()