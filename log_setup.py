"""Централизованная конфигурация логирования."""
import logging
import logging.handlers
import os
import sys
from pathlib import Path


def get_log_path() -> Path:
    if getattr(sys, "frozen", False):
        appdata = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST"
    else:
        appdata = Path(__file__).parent
    appdata.mkdir(parents=True, exist_ok=True)
    return appdata / "max_poster.log"


def setup_logging() -> None:
    log_path = get_log_path()
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    handlers: list[logging.Handler] = [
        file_handler,
        logging.StreamHandler(sys.stderr),
    ]
    # В production-сборке (frozen) используем INFO, в dev — DEBUG
    level = logging.INFO if getattr(sys, "frozen", False) else logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    # basicConfig игнорирует level если логгер уже настроен — устанавливаем явно
    logging.getLogger().setLevel(level)
    # Silence noisy third-party loggers
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
