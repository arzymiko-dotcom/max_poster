import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests
from env_utils import get_env_path, load_env_safe
from constants import VK_API_URL, VK_API_VERSION, VK_MAX_PHOTO_MB, VK_RETRY_DELAYS

_log = logging.getLogger(__name__)

load_env_safe(get_env_path())


@dataclass
class SendResult:
    success: bool
    message: str
    post_id: int | None = None   # ID опубликованного поста (для последующего редактирования)


class VkSender:
    def __init__(self) -> None:
        self.group_token = os.getenv("VK_GROUP_TOKEN", "")
        self.user_token  = os.getenv("VK_USER_TOKEN", "")
        self.group_id    = os.getenv("VK_GROUP_ID", "")  # только цифры, без минуса

    def _check_credentials(self, need_user_token: bool = False) -> str | None:
        if not self.group_token:
            return "Не заполнен VK_GROUP_TOKEN в файле .env"
        if not self.group_id:
            return "Не заполнен VK_GROUP_ID в файле .env"
        if need_user_token and not self.user_token:
            return (
                "Для загрузки фото нужен VK_USER_TOKEN в файле .env\n"
                "Получить: oauth.vk.com → client_id=2685278 (Kate Mobile) → scope=wall,photos,offline"
            )
        return None

    def _call(self, method: str, token: str, **params) -> dict:
        params["access_token"] = token
        params["v"] = VK_API_VERSION
        url = f"{VK_API_URL}/{method}"
        last_exc: Exception | None = None
        for attempt, delay in enumerate(VK_RETRY_DELAYS + (None,)):
            try:
                resp = requests.post(url, data=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except requests.RequestException as e:
                last_exc = e
                _log.warning("VK API %s: сетевая ошибка (попытка %d): %s", method, attempt + 1, e)
                if delay is not None:
                    time.sleep(delay)
        else:
            raise RuntimeError(f"Сеть: {last_exc}")
        if not isinstance(data, dict):
            raise RuntimeError(f"Неожиданный формат ответа ВК: {data!r}")
        if "error" in data:
            error = data["error"]
            msg = error.get("error_msg", str(error)) if isinstance(error, dict) else str(error)
            raise RuntimeError(msg)
        if "response" not in data:
            raise RuntimeError(f"Ответ ВК не содержит 'response': {data!r}")
        return data["response"]

    def _upload_photo(self, image_path: str, progress: Callable[[str], None] | None = None) -> str:
        """Загружает фото на стену группы и возвращает строку вложения photo{owner_id}_{id}.
        Используется пользовательский токен — group token не поддерживает методы photos.*
        """
        def _step(msg: str) -> None:
            if progress:
                progress(msg)

        # 0. Проверка размера файла
        file_mb = Path(image_path).stat().st_size / (1024 * 1024)
        if file_mb > VK_MAX_PHOTO_MB:
            raise RuntimeError(
                f"Файл слишком большой: {file_mb:.1f} МБ (лимит ВК — {VK_MAX_PHOTO_MB} МБ)"
            )

        # 1. Получаем upload URL (user token + group_id)
        _step("Подготовка загрузки…")
        upload_server = self._call(
            "photos.getWallUploadServer",
            token=self.user_token,
            group_id=self.group_id,
        )
        upload_url = upload_server["upload_url"]

        # 2. Загружаем файл
        _step("Загрузка фото на сервер…")
        with open(image_path, "rb") as f:
            resp = requests.post(upload_url, files={"photo": f}, timeout=60)
        resp.raise_for_status()
        uploaded = resp.json()

        photo_str = uploaded.get("photo", "")
        if not photo_str or photo_str == "[]":
            raise RuntimeError(
                f"ВК не принял файл (photo пустой).\n"
                f"Ответ: {uploaded}\n\n"
                f"Попробуйте обновить VK_USER_TOKEN в файле .env"
            )

        # 3. Сохраняем фото (user token + group_id)
        _step("Сохранение фото…")
        saved = self._call(
            "photos.saveWallPhoto",
            token=self.user_token,
            group_id=self.group_id,
            server=uploaded["server"],
            photo=photo_str,
            hash=uploaded["hash"],
        )
        if not isinstance(saved, list) or not saved:
            raise RuntimeError(f"ВК не вернул сохранённое фото (photos.saveWallPhoto): {saved!r}")
        photo = saved[0]
        return f"photo{photo['owner_id']}_{photo['id']}"

    def send_post(
        self,
        text: str,
        image_path: str | None = None,
        progress: Callable[[str], None] | None = None,
        publish_date: int | None = None,
    ) -> SendResult:
        """Публикует пост в группе ВКонтакте.

        publish_date — Unix-timestamp для отложенной публикации (ВК сам опубликует в нужное время,
        программа может быть выключена). Если None — публикуется немедленно.
        """
        has_photo = bool(image_path and Path(image_path).exists())
        err = self._check_credentials(need_user_token=has_photo)
        if err:
            return SendResult(False, err)

        try:
            attachment = ""
            if has_photo:
                attachment = self._upload_photo(image_path, progress=progress)

            if progress:
                progress("Публикация поста…" if not publish_date else "Регистрация отложенного поста в ВК…")

            params: dict = {
                "owner_id": f"-{self.group_id}",
                "message": text,
                "from_group": 1,
            }
            if attachment:
                params["attachments"] = attachment
            if publish_date:
                params["publish_date"] = publish_date

            resp = self._call("wall.post", token=self.group_token, **params)
            post_id: int | None = resp.get("post_id") if isinstance(resp, dict) else None

            if publish_date:
                from datetime import datetime
                dt_local = datetime.fromtimestamp(publish_date)
                return SendResult(True, f"Запланировано в ВКонтакте на {dt_local.strftime('%d.%m.%Y  %H:%M')}", post_id=post_id)
            return SendResult(True, "Опубликовано в ВКонтакте", post_id=post_id)

        except Exception as exc:
            _log.exception("Ошибка при публикации в ВК: %s", exc)
            return SendResult(False, f"Ошибка ВК: {exc}")

    def get_post_text(self, post_id: int) -> str:
        """Загружает текст поста из ВКонтакте по post_id. Возвращает пустую строку при ошибке."""
        err = self._check_credentials()
        if err:
            return ""
        try:
            posts_key = f"-{self.group_id}_{post_id}"
            resp = self._call("wall.getById", token=self.group_token, posts=posts_key)
            items = resp.get("items", resp) if isinstance(resp, dict) else resp
            if isinstance(items, list) and items:
                return items[0].get("text", "")
        except Exception as exc:
            _log.warning("get_post_text failed: %s", exc)
        return ""

    def edit_post(
        self,
        post_id: int,
        text: str,
        image_path: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> SendResult:
        """Редактирует существующий пост группы.
        Если image_path задан — загружает новое фото и заменяет вложения.
        Если image_path не задан — вложения не трогаются (остаются прежними).
        """
        has_photo = bool(image_path and Path(image_path).exists())
        err = self._check_credentials(need_user_token=has_photo)
        if err:
            return SendResult(False, err)

        try:
            params: dict = {
                "owner_id": f"-{self.group_id}",
                "post_id": post_id,
                "message": text,
            }
            if has_photo:
                attachment = self._upload_photo(image_path, progress=progress)
                params["attachments"] = attachment
            elif progress:
                progress("Сохранение изменений…")

            self._call("wall.edit", token=self.group_token, **params)
            return SendResult(True, "Пост обновлён в ВКонтакте")

        except Exception as exc:
            _log.exception("Ошибка редактирования поста ВК: %s", exc)
            return SendResult(False, f"Ошибка ВК: {exc}")
