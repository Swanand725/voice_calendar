"""A plain-text log of every command, so real use becomes the next test set.

Each line of data/command_log.jsonl is one JSON object: what you said, what the model
answered, what the safety net changed, the final command, and the reply. Typing "wrong"
after a reply marks the last command as a mistake (with an optional reason).
"""
import json
from pathlib import Path


class Logbook:
    def __init__(self, path):
        self.path = Path(path)

    def record(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def mark_last_wrong(self, note: str = "") -> bool:
        """Flag the most recent command as a mistake. Returns False if there is none."""
        if not self.path.exists():
            return False
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for i in range(len(lines) - 1, -1, -1):
            try:
                entry = json.loads(lines[i])
            except json.JSONDecodeError:
                continue
            if entry.get("kind") == "command":
                entry["wrong"] = True
                if note:
                    entry["note"] = note
                lines[i] = json.dumps(entry, ensure_ascii=False)
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
        return False