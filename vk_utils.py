"""vk_utils.py — единая обёртка над VK API с retry при сетевых ошибках.

Используется из vk_sender.py, vk_messages_panel.py, shared_files_panel.py.
"""
from __future__ import annotations

import logging
import time

import requests

from constants import VK_API_URL, VK_API_VERSION, VK_RETRY_DELAYS

_log = logging.getLogger(__name__)


def vk_api_call(method: str, token: str, post: bool = False, **params) -> dict | list:
    """Выполняет запрос к VK API с автоматическим retry при сетевых ошибках.

    :param method:  имя метода, например «messages.send»
    :param token:   access token пользователя или группы
    :param post:    True → HTTP POST, False → HTTP GET
    :param params:  параметры запроса (без access_token и v)
    :raises RuntimeError: при сетевой ошибке или ошибке от API
    """
    params["access_token"] = token
    params["v"] = VK_API_VERSION
    url = f"{VK_API_URL}/{method}"
    last_exc: Exception | None = None
    for attempt, delay in enumerate(VK_RETRY_DELAYS + (None,)):
        try:
            if post:
                r = requests.post(url, data=params, timeout=30)
            else:
                r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as e:
            last_exc = e
            _log.warning("VK API %s: сетевая ошибка (попытка %d): %s", method, attempt + 1, e)
            if delay is not None:
                time.sleep(delay)
    else:
        raise RuntimeError(f"Сеть: {last_exc}")
    if not isinstance(data, dict):
        raise RuntimeError(f"Неожиданный ответ ВК: {data!r}")
    if "error" in data:
        err = data["error"]
        raise RuntimeError(err.get("error_msg", str(err)) if isinstance(err, dict) else str(err))
    return data.get("response", data)
