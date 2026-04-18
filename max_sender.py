import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests
from env_utils import get_env_path, load_env_safe

_log = logging.getLogger(__name__)

load_env_safe(get_env_path())


def _json_or_raise(resp: requests.Response) -> dict | list:
    """Парсит JSON из ответа; бросает RuntimeError если Content-Type не JSON."""
    ct = resp.headers.get("Content-Type", "")
    if "application/json" not in ct:
        raise RuntimeError(
            f"Ожидался JSON-ответ, получен Content-Type={ct!r}. "
            f"Тело ответа: {resp.text[:300]!r}"
        )
    return resp.json()


@dataclass
class SendResult:
    success: bool
    message: str


def _ascii_strip(value: str) -> str:
    """Удаляет невидимые и не-ASCII символы (BOM, ZWSP и т.п.) из строки."""
    return "".join(ch for ch in value if ord(ch) < 128 and ch.isprintable())


class MaxSender:
    def __init__(self) -> None:
        self.api_url   = _ascii_strip(os.getenv("MAX_API_URL",   "https://api.green-api.com"))
        self.id_instance = _ascii_strip(os.getenv("MAX_ID_INSTANCE", ""))
        self.api_token   = _ascii_strip(os.getenv("MAX_API_TOKEN",   ""))
        self.media_url = _ascii_strip(os.getenv("MAX_MEDIA_URL", ""))
        if not self.api_url:
            self.api_url = "https://api.green-api.com"
        # Если MAX_MEDIA_URL не задан — используем тот же хост что и API
        if not self.media_url:
            self.media_url = self.api_url

    def _check_credentials(self) -> str | None:
        """Возвращает сообщение об ошибке если учётные данные не заполнены."""
        if not self.id_instance or self.id_instance == "твой_idInstance":
            return "Не заполнен MAX_ID_INSTANCE в файле .env"
        if not self.api_token or self.api_token == "твой_apiTokenInstance":
            return "Не заполнен MAX_API_TOKEN в файле .env"
        return None

    def is_authorized(self) -> bool:
        """Быстрая проверка: аккаунт авторизован прямо сейчас?"""
        try:
            url = f"{self.api_url}/waInstance{self.id_instance}/getStateInstance/{self.api_token}"
            resp = requests.get(url, timeout=8)
            return resp.ok and resp.json().get("stateInstance") == "authorized"
        except Exception:
            return False

    def open_max_for_login(self) -> SendResult:
        """Проверяет подключение к GREEN-API."""
        err = self._check_credentials()
        if err:
            return SendResult(False, err)

        url = f"{self.api_url}/waInstance{self.id_instance}/getStateInstance/{self.api_token}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            state = _json_or_raise(resp).get("stateInstance", "")
            if state == "authorized":
                return SendResult(True, "Подключение успешно. Аккаунт авторизован.")
            else:
                return SendResult(False, f"Аккаунт не авторизован. Статус: {state}\nОтсканируйте QR-код в личном кабинете GREEN-API.")
        except Exception as exc:
            return SendResult(False, f"Ошибка проверки подключения: {exc}")

    def get_chats(self) -> tuple[list[dict], str | None]:
        """Возвращает (список чатов, ошибка или None)."""
        err = self._check_credentials()
        if err:
            return [], err

        url = f"{self.api_url}/waInstance{self.id_instance}/getChats/{self.api_token}"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return _json_or_raise(resp), None
        except Exception as exc:
            return [], f"Ошибка получения чатов: {exc}"

    @staticmethod
    def _resolve_chat_id(raw: str) -> str:
        """Извлекает числовой chat_id из URL или возвращает строку как есть.

        https://web.max.ru/-69098384919255 → -69098384919255
        """
        raw = raw.strip()
        if raw.startswith("http"):
            m = re.search(r"(-\d+)/?$", raw)
            if m:
                return m.group(1)
        return raw

    def send_post(
        self,
        chat_link: str,
        text: str,
        image_path: str | None = None,
        _file_bytes: bytes | None = None,
    ) -> SendResult:
        err = self._check_credentials()
        if err:
            return SendResult(False, err)

        chat_id = self._resolve_chat_id(chat_link)

        try:
            if image_path and Path(image_path).exists():
                return self._send_with_image(chat_id, text, image_path, _file_bytes)
            else:
                return self._send_text(chat_id, text)
        except Exception as exc:
            return SendResult(False, f"Ошибка отправки: {exc}")

    def _send_text(self, chat_id: str, text: str) -> SendResult:
        url = f"{self.api_url}/waInstance{self.id_instance}/sendMessage/{self.api_token}"
        payload = {"chatId": chat_id, "message": text}

        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()

        msg_id = _json_or_raise(resp).get("idMessage", "")
        return SendResult(True, f"Сообщение отправлено. ID: {msg_id}")

    _CAPTION_LIMIT = 4000
    _ALLOWED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    def _send_with_image(self, chat_id: str, text: str, image_path: str,
                         _file_bytes: bytes | None = None) -> SendResult:
        caption = text if len(text) <= self._CAPTION_LIMIT else ""
        file_name = Path(image_path).name

        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or mime_type not in self._ALLOWED_MIME:
            ext = Path(image_path).suffix.lower()
            raise RuntimeError(
                f"Неподдерживаемый тип файла '{ext}'. Разрешены: JPEG, PNG, GIF, WEBP"
            )

        file_data = _file_bytes if _file_bytes is not None else Path(image_path).read_bytes()

        send_url = f"{self.media_url}/waInstance{self.id_instance}/sendFileByUpload/{self.api_token}"
        _log.debug("sendFileByUpload → .../sendFileByUpload/***  chatId=%s  file=%s  mime=%s",
                   chat_id, file_name, mime_type)
        resp = requests.post(
            send_url,
            data={"chatId": chat_id, "fileName": file_name, "caption": caption},
            files={"file": (file_name, file_data, mime_type)},
            timeout=60,
        )

        _log.debug("sendFileByUpload ответ: status=%s  body=%s",
                   resp.status_code, resp.text[:500])

        if not resp.ok:
            return SendResult(False,
                f"chatId={chat_id}\n"
                f"Ошибка загрузки фото ({resp.status_code}): {resp.text[:300]}")

        try:
            msg_id = _json_or_raise(resp).get("idMessage", "")
        except RuntimeError as exc:
            return SendResult(False, f"chatId={chat_id}\nФото отправлено, но ответ не JSON: {exc}")

        if not caption and text:
            text_result = self._send_text(chat_id, text)
            if not text_result.success:
                return SendResult(True,
                    f"Фото отправлено (ID: {msg_id}), "
                    f"текст отдельным сообщением не удалось: {text_result.message}")

        return SendResult(True, f"Сообщение с фото отправлено. ID: {msg_id}")

    def close(self) -> None:
        pass  # Ничего закрывать не нужно
