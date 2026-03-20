"""Утилиты для .env файла MAX POST."""
import os
import sys
from pathlib import Path


def load_env_safe(path: Path, override: bool = False) -> None:
    """load_dotenv с автоопределением кодировки (utf-8 → cp1251 → latin-1)."""
    from dotenv import load_dotenv
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            load_dotenv(path, override=override, encoding=enc)
            return
        except UnicodeDecodeError:
            continue


def get_env_path() -> Path:
    """Путь к .env: %APPDATA%\\MAX POST\\ в exe, рядом с кодом — в dev."""
    if getattr(sys, "frozen", False):
        appdata = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST"
        appdata.mkdir(parents=True, exist_ok=True)
        env_path = appdata / ".env"
        # Миграция: если .env рядом с exe, но нет в AppData — скопировать
        legacy = Path(sys.executable).parent / ".env"
        if not env_path.exists() and legacy.exists():
            import shutil
            shutil.copy2(legacy, env_path)
        return env_path
    return Path(__file__).parent / ".env"
