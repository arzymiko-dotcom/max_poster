"""Централизованная конфигурация логирования."""
import logging
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
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    # Silence noisy third-party loggers
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
