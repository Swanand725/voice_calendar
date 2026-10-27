from dataclasses import dataclass, field
from datetime import datetime
import uuid


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Item:
    title: str
    date: str                          # "YYYY-MM-DD"
    time: str | None = None            # "HH:MM" (24h); None = all day
    duration_min: int | None = None
    all_day: bool = False
    reminders_min_before: list[int] = field(default_factory=lambda: [10])
    notes: str = ""
    status: str = "pending"            # "pending" | "done"
    recurrence: str | None = None      # reserved for v2
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def __post_init__(self):
        datetime.strptime(self.date, "%Y-%m-%d")       # raises if malformed
        if self.time is None:
            self.all_day = True
        else:
            datetime.strptime(self.time, "%H:%M")      # raises if malformed
            self.all_day = False
        if self.status not in ("pending", "done"):
            raise ValueError(f"Invalid status: {self.status}")