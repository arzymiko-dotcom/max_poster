"""Хранение шаблонов текста в templates.json."""

import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

_MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

_lock = threading.Lock()


def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        d = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).parent


def _path() -> Path:
    return _data_dir() / "templates.json"


def load() -> list[dict]:
    """Возвращает список шаблонов [{name, text, created}, ...]."""
    p = _path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_template(name: str, text: str) -> None:
    """Добавляет или заменяет шаблон с данным именем."""
    templates = load()
    for t in templates:
        if t.get("name") == name:
            t["text"] = text
            t["created"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _write(templates)
            return
    templates.append({
        "name": name,
        "text": text,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    _write(templates)


def delete_template(name: str) -> None:
    templates = [t for t in load() if t.get("name") != name]
    _write(templates)


def apply_variables(text: str, address: str | None = None) -> str:
    """Подставляет переменные {{адрес}}, {{дата}}, {{месяц}}, {{год}} в текст шаблона."""
    now = datetime.now()
    replacements = {
        "{{адрес}}":  address or "",
        "{{дата}}":   now.strftime("%d.%m.%Y"),
        "{{месяц}}":  _MONTHS_GEN[now.month],
        "{{год}}":    str(now.year),
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def _write(templates: list[dict]) -> None:
    p = _path()
    with _lock:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=p.parent, delete=False, suffix=".tmp"
            ) as tmp:
                json.dump(templates, tmp, ensure_ascii=False, indent=2)
                tmp_path = Path(tmp.name)
            tmp_path.replace(p)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
