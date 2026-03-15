"""Хранение истории публикаций в history.json."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _data_dir() -> Path:
    """Папка для пользовательских данных — APPDATA в exe, рядом со скриптом в dev."""
    if getattr(sys, "frozen", False):
        d = Path(os.environ.get("APPDATA", Path.home())) / "max_poster"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).parent


def _path() -> Path:
    return _data_dir() / "history.json"


def load() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return []


def add_entry(addresses: list[str], sent_max: bool, sent_vk: bool) -> None:
    """Добавляет запись в начало истории."""
    entry: dict = {
        "ts": datetime.now().strftime("%d.%m.%Y  %H:%M"),
    }
    if sent_max:
        entry["max"] = addresses if addresses else ["—"]
    if sent_vk:
        entry["vk"] = True

    history = load()
    history.insert(0, entry)
    _path().write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear() -> None:
    _path().write_text("[]", encoding="utf-8")
