"""Хранение истории публикаций в history.json."""

import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()

# Переопределяемые в тестах переменные
_HISTORY_FILE: "Path | None" = None   # None = вычислять через _path()
_MAX_ENTRIES: int = 200


def _data_dir() -> Path:
    """Папка для пользовательских данных — APPDATA в exe, рядом со скриптом в dev."""
    if getattr(sys, "frozen", False):
        d = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST"
        _old_d = Path(os.environ.get("APPDATA", Path.home())) / "max_poster"
        if not d.exists() and _old_d.exists():
            try:
                _old_d.rename(d)
            except OSError:
                pass  # если переименовать не удалось — просто создадим новую папку ниже
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).parent


def _path() -> Path:
    if _HISTORY_FILE is not None:
        return _HISTORY_FILE
    return _data_dir() / "history.json"


def load() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def add_scheduled_entry(
    entry_id: str,
    addresses: list[str],
    sent_max: bool,
    sent_vk: bool,
    text: str,
    scheduled_at: str,
) -> None:
    """Добавляет отложенную запись в начало истории."""
    entry: dict = {
        "id": entry_id,
        "ts": datetime.now().strftime("%d.%m.%Y  %H:%M"),
        "scheduled_at": scheduled_at,
        "status": "scheduled",
    }
    if text:
        snippet = text.strip().replace("\n", " ")
        encoded = snippet.encode("utf-32-le")
        cp_count = len(encoded) // 4
        if cp_count > 60:
            entry["text"] = encoded[: 60 * 4].decode("utf-32-le") + "…"
        else:
            entry["text"] = snippet
    if sent_max:
        entry["max"] = addresses if addresses else []
    if sent_vk:
        entry["vk"] = True

    with _lock:
        history = load()
        history = [entry] + history[:_MAX_ENTRIES - 1]
        _atomic_write(_path(), json.dumps(history, ensure_ascii=False, indent=2))


def update_entry_status(entry_id: str, new_status: str) -> None:
    """Обновляет статус записи по id."""
    with _lock:
        history = load()
        for entry in history:
            if entry.get("id") == entry_id:
                entry["status"] = new_status
                break
        _atomic_write(_path(), json.dumps(history, ensure_ascii=False, indent=2))


def add_entry(
    addresses: list[str],
    sent_max: bool,
    sent_vk: bool,
    text: str = "",
    vk_post_id: int | None = None,
) -> None:
    """Добавляет запись в начало истории."""
    entry: dict = {
        "ts": datetime.now().strftime("%d.%m.%Y  %H:%M"),
    }
    if text:
        snippet = text.strip().replace("\n", " ")
        # Emoji-safe truncation: encode to UTF-32 code-points, slice, then decode
        encoded = snippet.encode("utf-32-le")
        cp_count = len(encoded) // 4
        if cp_count > 60:
            truncated = encoded[: 60 * 4].decode("utf-32-le")
            entry["text"] = truncated + "…"
        else:
            entry["text"] = snippet
    if sent_max:
        entry["max"] = addresses if addresses else []
    if sent_vk:
        entry["vk"] = True
    if vk_post_id:
        entry["vk_post_id"] = vk_post_id

    with _lock:
        history = load()
        history = [entry] + history[:_MAX_ENTRIES - 1]
        _atomic_write(_path(), json.dumps(history, ensure_ascii=False, indent=2))


def _atomic_write(path: Path, text: str) -> None:
    """Записывает text во временный файл, затем атомарно переименовывает его в path."""
    dir_ = path.parent
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    try:
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def clear() -> None:
    _atomic_write(_path(), "[]")
