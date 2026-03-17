"""Вспомогательные функции для поиска путей к ресурсам приложения."""

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon


_twemoji_dir_cache: "Path | None" = None


def _twemoji_dir() -> Path:
    """Папка с PNG-иконками Twemoji (работает и в dev, и в exe)."""
    global _twemoji_dir_cache
    if _twemoji_dir_cache is None:
        if getattr(sys, "frozen", False):
            _twemoji_dir_cache = Path(sys._MEIPASS) / "twemoji"  # type: ignore[attr-defined]
        else:
            _twemoji_dir_cache = Path(__file__).parent.parent / "twemoji"
    return _twemoji_dir_cache


def _assets_dir() -> Path:
    """Папка assets (работает и в dev, и в exe)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent.parent
    return base / "assets"


def _fonts_dir() -> Path:
    """Папка fonts (работает и в dev, и в exe)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent.parent
    return base / "fonts"


def _emoji_icon(emoji: str) -> "QIcon | None":
    """Возвращает QIcon из Twemoji PNG или None если файл не найден."""
    codepoints = "-".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
    path = _twemoji_dir() / f"{codepoints}.png"
    if path.exists():
        return QIcon(str(path))
    return None
