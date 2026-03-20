"""
Telegram-уведомления для MAX POST.

Отправляет:
  - уведомление о запуске программы
  - уведомление о необработанной ошибке (sys.excepthook)
  - уведомление об ошибке отправки поста

Все запросы выполняются в фоновом потоке — не блокируют UI.
Если TG_BOT_TOKEN или TG_CHAT_ID не заданы, уведомления молча пропускаются.
"""

import html
import os
import platform
import re
import socket
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import requests

from env_utils import get_env_path, load_env_safe

load_env_safe(get_env_path())

# Паттерны для редактирования токенов из сообщений об ошибках
_TOKEN_RE = re.compile(
    r'vk1\.[a-zA-Z0-9_.\-]{20,}'          # VK токены
    r'|\b[a-f0-9]{40,}\b'                  # HEX токены (API ключи)
    r'|\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b'  # Telegram bot токены
    r'|\b[A-Za-z0-9]{32,}\b',             # GREEN-API и прочие длинные токены
    re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    """Заменяет паттерны, похожие на токены, на [REDACTED]."""
    return _TOKEN_RE.sub("[REDACTED]", text)


_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "")
_CHAT_ID: str = os.getenv("TG_CHAT_ID", "")

# Предупреждаем при старте если токены заданы как пустые строки (явно присутствуют, но пусты)
if "TG_BOT_TOKEN" in os.environ and not _BOT_TOKEN:
    import warnings
    warnings.warn("tg_notify: TG_BOT_TOKEN задан, но пуст — уведомления отключены.", stacklevel=1)
if "TG_CHAT_ID" in os.environ and not _CHAT_ID:
    import warnings
    warnings.warn("tg_notify: TG_CHAT_ID задан, но пуст — уведомления отключены.", stacklevel=1)


def _enabled() -> bool:
    return bool(_BOT_TOKEN and _CHAT_ID)


def _version() -> str:
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        lines = (base / "version.txt").read_text(encoding="utf-8").strip().splitlines()
        return lines[0].strip() if lines else "?"
    except Exception:
        return "?"


# Кэшируем версию один раз при импорте модуля
_APP_VERSION: str = _version()


def _public_ip() -> str:
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return "недоступен"


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip
    except Exception:
        return "?"


def _send(text: str) -> None:
    """Отправляет сообщение в Telegram (вызывать из фонового потока)."""
    if not _enabled():
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage",
            data={"chat_id": _CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass  # тихо игнорируем — телеграм не должен влиять на работу программы


def _send_async(text: str) -> None:
    threading.Thread(target=_send, args=(text,), daemon=True).start()


def _sys_info() -> str:
    now = datetime.now().strftime("%H:%M · %d.%m.%Y")
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "?")
    pc = socket.gethostname()
    win = platform.version()
    ver = _APP_VERSION
    pub_ip = _public_ip()
    loc_ip = _local_ip()
    return (
        f"👤 Пользователь: {user}\n"
        f"💻 ПК: {pc}\n"
        f"🪟 Windows: {win}\n"
        f"📦 Версия: {ver}\n"
        f"🌐 IP: {pub_ip}\n"
        f"🔌 Локальный IP: {loc_ip}\n"
        f"🕐 {now}"
    )


# ──────────────────────────────────────────────────────────────────
#  Публичный API
# ──────────────────────────────────────────────────────────────────

def send_startup() -> None:
    """Уведомление о запуске программы."""
    if not _enabled():
        return

    def _task():
        info = _sys_info()
        _send(f"✅ <b>MAX POST запущен</b>\n{info}")

    threading.Thread(target=_task, daemon=True).start()


def send_error(title: str, details: str) -> None:
    """Уведомление об ошибке."""
    if not _enabled():
        return
    now = datetime.now().strftime("%H:%M · %d.%m.%Y")
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "?")
    pc = socket.gethostname()
    ver = _APP_VERSION
    text = (
        f"❌ <b>{html.escape(title)}</b>\n\n"
        f"<pre>{html.escape(_sanitize(details[:3000]))}</pre>\n\n"
        f"👤 {html.escape(user)}  💻 {html.escape(pc)}  📦 {html.escape(ver)}\n"
        f"🕐 {now}"
    )
    _send_async(text)


def send_post_done(addresses: list, send_max: bool, send_vk: bool, text: str) -> None:
    """Уведомление об успешной рассылке."""
    if not _enabled():
        return

    def _task():
        now = datetime.now().strftime("%H:%M · %d.%m.%Y")
        try:
            user = os.getlogin()
        except Exception:
            user = os.environ.get("USERNAME", "?")
        pc = socket.gethostname()
        ver = _APP_VERSION
        platforms = []
        if send_max:
            platforms.append("MAX")
        if send_vk:
            platforms.append("ВКонтакте")
        plat_str = " + ".join(platforms) if platforms else "—"
        n = len(addresses)
        preview = text[:150] + ("…" if len(text) > 150 else "")
        msg = (
            f"📤 <b>Рассылка выполнена</b>\n\n"
            f"👤 {html.escape(user)}  💻 {html.escape(pc)}\n"
            f"📦 Версия: {html.escape(ver)}\n"
            f"📡 Платформы: {plat_str}\n"
            f"👥 Групп: {n}\n"
            f"📝 Текст: <i>{html.escape(preview)}</i>\n"
            f"🕐 {now}"
        )
        _send(msg)

    threading.Thread(target=_task, daemon=True).start()


def install_excepthook() -> None:
    """
    Устанавливает глобальный обработчик необработанных исключений.
    Вызывать один раз при старте программы.
    """
    _orig = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        send_error("Необработанная ошибка", tb_text)
        _orig(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
