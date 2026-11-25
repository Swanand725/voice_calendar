"""Small user settings, read from data/config.json (optional; every key has a default).

Example data/config.json:
    {"model": "llama3.2:3b", "keep_alive": "10m"}

keep_alive is how long Ollama keeps the model in memory after your last command:
    "10m"  = ten minutes (default)    "24h" = a day    "-1" = never unload    "0" = unload right away
"""
import json
import re
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "data" / "config.json"
DEFAULTS = {"model": "llama3.2:3b", "keep_alive": "10m"}


def load(path=CONFIG_FILE) -> dict:
    settings = dict(DEFAULTS)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            settings.update({k: v for k, v in data.items() if v is not None})
    except (OSError, ValueError):
        pass                                   # no file, or unreadable: use the defaults
    return settings


def get(key, path=CONFIG_FILE):
    return load(path).get(key)


def keep_alive_value(path=CONFIG_FILE):
    """The keep_alive setting in the form Ollama expects (numbers as ints, durations as text)."""
    value = get("keep_alive", path)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    return value