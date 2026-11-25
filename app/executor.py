"""The executor: turns a parsed command into a safe action on the calendar.

Rules it follows:
- Nothing is deleted without a "yes". Deleting a whole day needs the word "everything",
  a day, and a "yes" after seeing the count.
- It picks the best-matching item by itself. It only offers a choice when several items
  have the same name, and then it starts with the soonest one.
- Everything it changes can be undone with "undo", and a backup copy of the calendar is
  saved before every change (data/backups/, newest 20 kept).
- Short answers ("yes", "no", a number, "undo") are handled here, without the model.
"""
import json
import os
import re
import shutil
from dataclasses import asdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from . import storage
from .models import Item
from .scheduler import MORNING_HOUR

MATCH_MIN = 0.6          # how similar a title must be to count as a match
CLOSE_MIN = 0.4          # ...to be offered as "closest"
TIE_MARGIN = 0.05        # items this close to the best score count as the same match
BACKUPS_KEPT = 20
UNDO_KEEP = 30                  # how many changes can be undone
UNDO_MAX_AGE = timedelta(hours=24)   # older changes are left alone

YES = {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "go ahead",
       "please do", "confirm"}
NO = {"no", "n", "nope", "nah", "cancel", "stop", "never mind", "nevermind", "dont",
      "dont do it"}
UNDO = {"undo", "undo that", "undo it", "undo last", "go back"}
HELP = {"help", "what can you do", "commands"}

HELP_TEXT = """Things you can say:
  "I have a dentist appointment next Friday at 2"   add something
  "What's on my schedule tomorrow?"                  see a day (or "what's coming up")
  "Move the dentist appointment to 3:30"             change one
  "Cancel the team meeting"                          delete one (I'll ask first)
  "Mark the AI assignment as done"                   complete one
  "When is my exam?"                                 look one up
Also: "undo" reverses my last change, "wrong" marks my last answer as a mistake
(add a reason if you like), "quit" leaves."""


# ----------------------------------------------------------------- helpers ---

def _plain(text) -> str:
    text = str(text).lower().replace("-", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", text)).strip()


def _plain_title(text) -> str:
    return re.sub(r"^(?:the|my|a|an) ", "", _plain(text))


def _score(query, title) -> float:
    q, t = _plain_title(query or ""), _plain_title(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    qw, tw = set(q.split()), set(t.split())
    if qw <= tw or tw <= qw:
        return 0.9
    return SequenceMatcher(None, q, t).ratio()


def _short_day(iso) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a %b} {d.day}"


def _clock(hhmm) -> str:
    hour, minute = int(hhmm[:2]), hhmm[3:5]
    return f"{hour % 12 or 12}:{minute} {'AM' if hour < 12 else 'PM'}"


def _when(item) -> str:
    if item.time:
        return f"{_short_day(item.date)}, {_clock(item.time)}"
    return f"{_short_day(item.date)} (all day)"


def _day_phrase(iso, today) -> str:
    d = date.fromisoformat(iso)
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return _short_day(iso)


def _day_heading(iso, today) -> str:
    d = date.fromisoformat(iso)
    if d == today:
        return f"Today, {_short_day(iso)}"
    if d == today + timedelta(days=1):
        return f"Tomorrow, {_short_day(iso)}"
    return _short_day(iso)


def _start(item) -> datetime:
    d = date.fromisoformat(item.date)
    if item.time:
        return datetime.combine(d, datetime.strptime(item.time, "%H:%M").time())
    return datetime.combine(d, datetime.min.time())


def _is_past(item, now) -> bool:
    if item.time is None:
        return date.fromisoformat(item.date) < now.date()
    return _start(item) < now


def _span(delta) -> str:
    minutes = int(delta.total_seconds() // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins and not days:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts) or "less than a minute"


def _relative(item, now) -> str:
    if item.time:
        delta = _start(item) - now
        return f"in {_span(delta)}" if delta >= timedelta(0) else f"{_span(-delta)} ago"
    days = (date.fromisoformat(item.date) - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    return f"in {days} days" if days > 0 else f"{-days} days ago"


def _reminder_summary(item, now) -> str:
    parts = []
    if item.time is None:
        parts.append(f"{_clock(f'{MORNING_HOUR:02d}:00')} that day")
    else:
        start = _start(item)
        morning = start.replace(hour=MORNING_HOUR, minute=0)
        if item.morning_reminder and morning < start and now <= morning:
            parts.append(f"{_clock(f'{MORNING_HOUR:02d}:00')} that day")
        parts += [f"{m} min before" for m in sorted(item.reminders_min_before, reverse=True)]
    return " and ".join(parts) or "none"


def _plural(n, word) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# --------------------------------------------------------------- assistant ---

class Assistant:
    def __init__(self, parse_fn, path=None, now_fn=datetime.now, logbook=None):
        """parse_fn(phrase, today) -> object with .cmd, .notes, .raw, .seconds"""
        self.parse_fn = parse_fn
        self.path = Path(path) if path else storage.DATA_FILE
        self.now_fn = now_fn
        self.logbook = logbook
        self.pending = None          # a question we're waiting for an answer to
        self.undo_path = self.path.with_name(f"{self.path.stem}.undo.json")
        self.undo_stack = self._load_undo()   # saved to disk, so undo works after the window closes
        self._result = None

    # ----------------------------------------------------------- entry point

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        self._result = None
        local = self._local(text)
        if local is not None:
            reply, kind = local
        else:
            self.pending = None      # a new command replaces any unanswered question
            kind = "command"
            reply = self._command(text)
        self._log(kind, text, reply)
        return reply

    def _log(self, kind, text, reply):
        if not self.logbook:
            return
        entry = {"ts": self.now_fn().isoformat(timespec="seconds"),
                 "kind": kind, "phrase": text, "reply": reply}
        if self._result is not None:
            entry.update(raw=self._result.raw, notes=self._result.notes,
                         cmd=self._result.cmd, seconds=round(self._result.seconds, 2))
        self.logbook.record(entry)

    # ------------------------------------------- answers handled without the model

    def _local(self, text):
        word = _plain(text)
        if word in UNDO:
            return self._undo(), "local"
        if word in HELP:
            return HELP_TEXT, "local"
        if word == "wrong" or word.startswith("wrong "):
            return self._mark_wrong(text), "local"

        p = self.pending
        if p is None:
            return None
        if p["kind"] == "confirm":
            if word in YES:
                self.pending = None
                return p["yes"](), "reply"
            if word in NO:
                self.pending = None
                return p["no"](), "reply"
        elif p["kind"] == "choose":
            if word.isdigit() and 1 <= int(word) <= len(p["items"]):
                self.pending = None
                return p["pick"](p["items"][int(word) - 1]), "reply"
            if word in NO:
                self.pending = None
                return "Okay, never mind.", "reply"
            if word in YES:
                return 'Which number? (or say "no" to skip)', "reply"
        return None

    def _mark_wrong(self, text) -> str:
        if not self.logbook:
            return "There's no log to mark."
        note = text.strip()[5:].strip(" :,-")
        if self.logbook.mark_last_wrong(note):
            return "Marked the last command as a mistake. Thanks, that helps improve the tests."
        return "There's nothing in the log to mark yet."

    # ------------------------------------------------------------ commands

    def _command(self, text):
        now = self.now_fn()
        result = self.parse_fn(text, now.date())
        self._result = result
        cmd = result.cmd
        action = cmd["action"]

        if action == "clarify":
            return cmd.get("reason") or "Could you say that another way?"
        if cmd.get("date") and str(cmd["date"]).startswith("?"):
            return (f'I didn\'t understand the date "{cmd.get("date_text")}". '
                    'Try a day like "Friday" or a date like "November 30".')

        handler = {"add": self._add, "delete": self._delete, "complete": self._complete,
                   "update": self._update, "list": self._list, "info": self._info}.get(action)
        if handler is None:
            return "I'm not sure what to do with that."
        try:
            return handler(cmd, now)
        except ValueError as e:
            return f"Something in that didn't look right ({e}). Could you say it again?"

    # ------------------------------------------------------- finding items

    def _matches(self, cmd, *, use_day, statuses):
        scored = []
        for item in storage.list_items(path=self.path):
            score = _score(cmd.get("title"), item.title)
            if score < MATCH_MIN:
                continue
            if statuses and item.status not in statuses:
                continue
            if use_day and cmd.get("date") and item.date != cmd["date"]:
                continue
            scored.append((score, item))
        if not scored:
            return []
        top = max(score for score, _ in scored)
        group = [item for score, item in scored if score >= top - TIE_MARGIN]
        return sorted(group, key=lambda i: (i.date, i.time or ""))

    def _find(self, cmd, *, use_day, pending_first=True):
        for statuses in ((("pending",), None) if pending_first else (None,)):
            group = self._matches(cmd, use_day=use_day, statuses=statuses)
            if group:
                return group
        return []

    @staticmethod
    def _choose(group, now):
        """Pick the soonest upcoming item (or the latest past one)."""
        today = now.date().isoformat()
        upcoming = [i for i in group if i.date >= today]
        chosen = upcoming[0] if upcoming else group[-1]
        return chosen, [i for i in group if i is not chosen]

    def _not_found(self, cmd, now, *, use_day):
        title = cmd.get("title")
        where = f" on {_day_phrase(cmd['date'], now.date())}" if use_day and cmd.get("date") else ""
        reply = f'I couldn\'t find anything called "{title}"{where}.'
        close = []
        for item in storage.list_items(path=self.path):
            if _score(title, item.title) >= CLOSE_MIN and item.title not in close:
                close.append(item.title)
        if close:
            reply += " Closest: " + ", ".join(f'"{t}"' for t in close[:3]) + "."
        return reply

    # ------------------------------------------------------ safety plumbing

    def _backup(self):
        if not self.path.exists():
            return
        folder = self.path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = self.now_fn().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(self.path, folder / f"{self.path.stem}-{stamp}.json")
        for old in sorted(folder.glob(f"{self.path.stem}-*.json"))[:-BACKUPS_KEPT]:
            old.unlink()

    def _load_undo(self):
        try:
            data = json.loads(self.undo_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_undo(self):
        self.undo_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.undo_path.with_name(self.undo_path.name + ".tmp")
        tmp.write_text(json.dumps(self.undo_stack[-UNDO_KEEP:]), encoding="utf-8")
        os.replace(tmp, self.undo_path)

    def _push_undo(self, entry):
        entry["ts"] = self.now_fn().isoformat(timespec="seconds")
        self.undo_stack.append(entry)
        self._save_undo()

    def _undo(self) -> str:
        if not self.undo_stack:
            return "There's nothing to undo."
        entry = self.undo_stack.pop()
        try:
            too_old = self.now_fn() - datetime.fromisoformat(entry["ts"]) > UNDO_MAX_AGE
        except (KeyError, ValueError, TypeError):
            too_old = True
        if too_old:
            self.undo_stack.clear()          # everything below it is older still
            self._save_undo()
            return "That change was made more than a day ago, so I won't undo it."
        self._save_undo()
        self._backup()
        try:
            return self._apply_undo(entry)
        except (KeyError, TypeError, ValueError):
            return "I couldn't undo that."

    def _apply_undo(self, entry):
        op = entry["op"]
        if op == "remove":
            return self._undo_added(entry["id"], entry["title"])
        if op == "readd":
            return self._undo_deleted([Item(**d) for d in entry["items"]])
        if op == "restore":
            return self._undo_fields(entry["id"], entry["old"], entry["title"])
        return "I couldn't undo that."

    def _undo_added(self, item_id, title):
        storage.delete_item(item_id, self.path)
        return f'Removed "{title}" again.'

    def _undo_deleted(self, items):
        for item in items:
            if storage.get_item(item.id, self.path) is None:      # never duplicate an item
                storage.add_item(item, self.path)
        if len(items) == 1:
            return f'Brought back "{items[0].title}".'
        return f"Brought back {len(items)} items."

    def _undo_fields(self, item_id, old, title):
        if storage.update_item(item_id, path=self.path, **old) is None:
            return f'I couldn\'t undo that: "{title}" is gone.'
        return f'Reversed the change to "{title}".'

    # ------------------------------------------------------------------ add

    def _add(self, cmd, now):
        day = cmd.get("date") or now.date().isoformat()
        item = Item(title=cmd["title"], date=day, time=cmd.get("time"))
        note = "" if cmd.get("date") else "\nNo day given, so I put it on today."

        for other in storage.list_items(path=self.path):
            if (other.status == "pending" and _plain(other.title) == _plain(item.title)
                    and other.date == item.date and other.time == item.time):
                return f'You already have "{other.title}" on {_when(other)}.'

        if _is_past(item, now):
            self.pending = {"kind": "confirm",
                            "yes": lambda: self._do_add(item, note),
                            "no": lambda: "Okay, I didn't add it."}
            return f"That time has already passed ({_when(item)}). Add it anyway? (yes/no)"
        return self._do_add(item, note)

    def _do_add(self, item, note=""):
        self._backup()
        storage.add_item(item, self.path)
        self._push_undo({"op": "remove", "id": item.id, "title": item.title})
        return (f'Added "{item.title}": {_when(item)}.{note}\n'
                f'Reminders: {_reminder_summary(item, self.now_fn())}. (say "undo" to remove it)')

    # --------------------------------------------------------------- delete

    def _delete(self, cmd, now):
        if cmd.get("all_items"):
            return self._delete_all(cmd, now)
        group = self._find(cmd, use_day=True)
        if not group:
            return self._not_found(cmd, now, use_day=True)
        chosen, others = self._choose(group, now)
        return self._confirm_delete(chosen, others)

    def _confirm_delete(self, chosen, others, show_when=False):
        label = f'"{chosen.title}"' + (f" ({_when(chosen)})" if others or show_when else "")
        question = f"Do you want me to delete {label}?"
        if others:
            question += (f'\n(I found {_plural(len(others), "other")} with that name. '
                         'Say "no" to pick a different one.)')

        def no():
            if not others:
                return "Okay, I won't delete it."
            return self._offer_choice(others)

        self.pending = {"kind": "confirm", "yes": lambda: self._do_delete(chosen), "no": no}
        return question

    def _offer_choice(self, items):
        lines = ["Which one do you want to delete?"]
        lines += [f"  {n}. {i.title} - {_when(i)}" for n, i in enumerate(items, 1)]
        lines.append('(Say a number, or "no" to cancel.)')
        self.pending = {"kind": "choose", "items": items,
                        "pick": lambda item: self._confirm_delete(item, [], show_when=True)}
        return "\n".join(lines)

    def _do_delete(self, item):
        self._backup()
        if not storage.delete_item(item.id, self.path):
            return "That item is already gone."
        self._push_undo({"op": "readd", "items": [asdict(item)]})
        return f'Deleted "{item.title}". (say "undo" to bring it back)'

    def _delete_all(self, cmd, now):
        day = cmd.get("date")
        items = [i for i in storage.list_items(date=day, path=self.path) if i.status == "pending"]
        if not items:
            return f"Nothing is scheduled for {_day_phrase(day, now.date())}."
        what = "the following item" if len(items) == 1 else f"the following {len(items)} items"
        lines = [f"Do you want me to delete {what} from {_day_phrase(day, now.date())}?"]
        lines += [f"  - {_clock(i.time) if i.time else 'all day'}: {i.title}" for i in items]
        lines.append('(yes/no)')
        self.pending = {"kind": "confirm",
                        "yes": lambda: self._do_delete_all(items, day, now),
                        "no": lambda: "Okay, I left them alone."}
        return "\n".join(lines)

    def _do_delete_all(self, items, day, now):
        self._backup()
        for item in items:
            storage.delete_item(item.id, self.path)
        self._push_undo({"op": "readd", "items": [asdict(i) for i in items]})
        return (f"Deleted {_plural(len(items), 'item')} from {_day_phrase(day, now.date())}. "
                '(say "undo" to bring them back)')

    # ------------------------------------------------------------- complete

    def _complete(self, cmd, now):
        group = self._find(cmd, use_day=True)
        if not group:
            return self._not_found(cmd, now, use_day=True)
        chosen, others = self._choose(group, now)
        if chosen.status == "done":
            return f'"{chosen.title}" is already marked done.'
        self._backup()
        storage.update_item(chosen.id, path=self.path, status="done")
        self._push_undo({"op": "restore", "id": chosen.id, "old": {"status": "pending"}, "title": chosen.title})
        reply = f'Marked done: "{chosen.title}". (say "undo" to reverse it)'
        if others:
            reply += (f"\n(I found {_plural(len(others), 'other')} with that name; "
                      "this was the soonest.)")
        return reply

    # --------------------------------------------------------------- update

    def _update(self, cmd, now):
        group = self._find(cmd, use_day=False)      # the date in an update is the NEW date
        if not group:
            return self._not_found(cmd, now, use_day=False)
        chosen, others = self._choose(group, now)

        changes = {}
        if cmd.get("date"):
            changes["date"] = cmd["date"]
        if cmd.get("time"):
            changes["time"] = cmd["time"]
        new = Item(**{**chosen.__dict__, **changes})
        if (new.date, new.time) == (chosen.date, chosen.time):
            return f'"{chosen.title}" is already on {_when(chosen)}.'

        if _is_past(new, now):
            self.pending = {"kind": "confirm",
                            "yes": lambda: self._do_update(chosen, new, changes, others),
                            "no": lambda: "Okay, I left it as it was."}
            return (f'That time has already passed ({_when(new)}). '
                    f'Move "{chosen.title}" there anyway? (yes/no)')
        return self._do_update(chosen, new, changes, others)

    def _do_update(self, chosen, new, changes, others):
        self._backup()
        storage.update_item(chosen.id, path=self.path, **changes)
        old = {key: getattr(chosen, key) for key in changes}
        self._push_undo({"op": "restore", "id": chosen.id, "old": old, "title": chosen.title})
        reply = (f'Updated "{chosen.title}": {_when(chosen)} -> {_when(new)}. '
                 '(say "undo" to reverse it)')
        if others:
            reply += (f"\n(I found {_plural(len(others), 'other')} with that name; "
                      "I changed the soonest.)")
        return reply

    # ----------------------------------------------------------------- list

    def _list(self, cmd, now):
        today = now.date()
        pending = [i for i in storage.list_items(path=self.path) if i.status == "pending"]

        def line(item):
            return f"  {(_clock(item.time) if item.time else 'All day'):<9} {item.title}"

        day = cmd.get("date")
        if day:
            items = [i for i in pending if i.date == day]
            if not items:
                return f"Nothing scheduled for {_day_phrase(day, today)}."
            return "\n".join([f"{_day_heading(day, today)}:"] + [line(i) for i in items])

        overdue = [i for i in pending if i.date < today.isoformat()]
        upcoming = [i for i in pending if i.date >= today.isoformat()]
        if not overdue and not upcoming:
            return "Nothing coming up."
        limit, shown, lines = 20, 0, []
        if overdue:
            lines.append("Overdue:")
            for i in overdue[:limit]:
                lines.append(f"  {_short_day(i.date)}: {i.title}")
                shown += 1
        current = None
        for i in upcoming:
            if shown >= limit:
                break
            if i.date != current:
                current = i.date
                lines.append(f"{_day_heading(i.date, today)}:")
            lines.append(line(i))
            shown += 1
        if shown < len(overdue) + len(upcoming):
            lines.append(f"...and {len(overdue) + len(upcoming) - shown} more.")
        return "\n".join(lines)

    # ----------------------------------------------------------------- info

    def _info(self, cmd, now):
        group = self._find(cmd, use_day=True, pending_first=False)   # includes done and past items
        if not group:
            return self._not_found(cmd, now, use_day=True)

        def status(item):
            return " - done" if item.status == "done" else ""

        if len(group) == 1:
            i = group[0]
            return f'"{i.title}" is {_when(i)} ({_relative(i, now)}){status(i)}.'
        lines = [f'I found {len(group)} matching "{cmd.get("title")}":']
        for i in group[:5]:
            lines.append(f"  {i.title}: {_when(i)} ({_relative(i, now)}){status(i)}")
        return "\n".join(lines)