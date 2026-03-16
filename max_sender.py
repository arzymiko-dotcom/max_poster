import mimetypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv


def _json_or_raise(resp: requests.Response) -> dict | list:
    """Парсит JSON из ответа; бросает RuntimeError если Content-Type не JSON."""
    ct = resp.headers.get("Content-Type", "")
    if "application/json" not in ct:
        raise RuntimeError(
            f"Ожидался JSON-ответ, получен Content-Type={ct!r}. "
            f"Тело ответа: {resp.text[:300]!r}"
        )
    return resp.json()

_env_path = Path(sys.executable).parent / '.env' if getattr(sys, 'frozen', False) else Path(__file__).parent / '.env'
load_dotenv(_env_path)


@dataclass
class SendResult:
    success: bool
    message: str


class MaxSender:
    def __init__(self) -> None:
        self.api_url = os.getenv("MAX_API_URL", "https://api.green-api.com")
        self.media_url = os.getenv("MAX_MEDIA_URL", "https://media.green-api.com")
        self.id_instance = os.getenv("MAX_ID_INSTANCE", "")
        self.api_token = os.getenv("MAX_API_TOKEN", "")

    def _check_credentials(self) -> str | None:
        """Возвращает сообщение об ошибке если учётные данные не заполнены."""
        if not self.id_instance or self.id_instance == "твой_idInstance":
            return "Не заполнен MAX_ID_INSTANCE в файле .env"
        if not self.api_token or self.api_token == "твой_apiTokenInstance":
            return "Не заполнен MAX_API_TOKEN в файле .env"
        return None

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

    def send_post(
        self,
        chat_link: str,
        text: str,
        image_path: str | None = None,
    ) -> SendResult:
        err = self._check_credentials()
        if err:
            return SendResult(False, err)

        chat_id = chat_link.strip()

        try:
            if image_path and Path(image_path).exists():
                return self._send_with_image(chat_id, text, image_path)
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

    def _upload_file(self, image_path: str) -> str:
        """Загружает файл на сервер, возвращает urlFile."""
        upload_url = f"{self.media_url}/waInstance{self.id_instance}/uploadFile/{self.api_token}"
        file_name = Path(image_path).name
        _ALLOWED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or mime_type not in _ALLOWED_MIME:
            ext = Path(image_path).suffix.lower()
            raise RuntimeError(
                f"Неподдерживаемый тип файла '{ext}'. Разрешены: JPEG, PNG, GIF, WEBP"
            )
        with open(image_path, "rb") as f:
            resp = requests.post(
                upload_url,
                headers={"Content-Type": mime_type, "GA-Filename": file_name},
                data=f,
                timeout=60,
            )
        if not resp.ok:
            raise RuntimeError(f"Ошибка загрузки файла ({resp.status_code}): {resp.text}")
        url_file = _json_or_raise(resp).get("urlFile")
        if not url_file:
            raise RuntimeError(f"API не вернул urlFile. Ответ: {resp.text}")
        return url_file

    def _send_with_image(self, chat_id: str, text: str, image_path: str) -> SendResult:
        caption = text if len(text) <= self._CAPTION_LIMIT else ""
        file_name = Path(image_path).name

        url_file = self._upload_file(image_path)

        send_url = f"{self.api_url}/waInstance{self.id_instance}/sendFileByUrl/{self.api_token}"
        payload = {"chatId": chat_id, "urlFile": url_file, "fileName": file_name, "caption": caption}
        resp = requests.post(send_url, json=payload, timeout=30)

        if not resp.ok:
            return SendResult(False, f"chatId={chat_id}\nОшибка отправки ({resp.status_code}): {resp.text}")

        msg_id = _json_or_raise(resp).get("idMessage", "")

        if not caption and text:
            text_result = self._send_text(chat_id, text)
            if not text_result.success:
                return SendResult(False, f"Фото отправлено (ID: {msg_id}), но текст не удалось отправить: {text_result.message}")

        return SendResult(True, f"Сообщение с фото отправлено. ID: {msg_id}")

    def close(self) -> None:
        pass  # Ничего закрывать не нужно
