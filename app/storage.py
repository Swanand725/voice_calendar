import json
import os
import tempfile
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path

from .models import Item, now_iso

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "calendar.json"


def _load(path: Path) -> list[Item]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [Item(**d) for d in json.load(f)]


def _save(items: list[Item], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file, then swap it in, so a crash can't corrupt the calendar
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump([asdict(i) for i in items], f, indent=2)
    os.replace(tmp, path)


def add_item(item: Item, path: Path = DATA_FILE) -> Item:
    items = _load(path)
    items.append(item)
    _save(items, path)
    return item


def list_items(date: str | None = None, status: str | None = None,
               path: Path = DATA_FILE) -> list[Item]:
    items = _load(path)
    if date:
        items = [i for i in items if i.date == date]
    if status:
        items = [i for i in items if i.status == status]
    return sorted(items, key=lambda i: (i.date, i.time or "00:00"))


def get_item(item_id: str, path: Path = DATA_FILE) -> Item | None:
    return next((i for i in _load(path) if i.id == item_id), None)


def update_item(item_id: str, path: Path = DATA_FILE, **changes) -> Item | None:
    items = _load(path)
    for i, item in enumerate(items):
        if item.id == item_id:
            data = {**item.__dict__, **changes, "updated_at": now_iso()}
            items[i] = Item(**data)        # re-validates the new values
            _save(items, path)
            return items[i]
    return None


def delete_item(item_id: str, path: Path = DATA_FILE) -> bool:
    items = _load(path)
    kept = [i for i in items if i.id != item_id]
    if len(kept) == len(items):
        return False
    _save(kept, path)
    return True


def search(text: str, threshold: float = 0.5, path: Path = DATA_FILE) -> list[Item]:
    """Fuzzy title match. Returns best matches first; caller decides what to do
    if there's more than one."""
    text = text.lower().strip()
    scored = []
    for item in _load(path):
        title = item.title.lower()
        score = 1.0 if text in title else SequenceMatcher(None, text, title).ratio()
        if score >= threshold:
            scored.append((score, item))
    return [item for _, item in sorted(scored, key=lambda s: -s[0])]