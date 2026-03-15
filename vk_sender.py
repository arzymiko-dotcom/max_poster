import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

_env_path = Path(sys.executable).parent / '.env' if getattr(sys, 'frozen', False) else Path(__file__).parent / '.env'
load_dotenv(_env_path)

VK_API = "https://api.vk.com/method"
VK_VER = "5.199"


@dataclass
class SendResult:
    success: bool
    message: str


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
                "Получить: https://vkhost.github.io/ → Kate Mobile → права photos + wall"
            )
        return None

    def _call(self, method: str, token: str, **params) -> dict:
        params["access_token"] = token
        params["v"] = VK_VER
        resp = requests.post(f"{VK_API}/{method}", data=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"]["error_msg"])
        return data["response"]

    def _upload_photo(self, image_path: str, progress: Callable[[str], None] | None = None) -> str:
        """Загружает фото на стену группы и возвращает строку вложения photo{owner_id}_{id}.
        Используется пользовательский токен — group token не поддерживает методы photos.*
        """
        def _step(msg: str) -> None:
            if progress:
                progress(msg)

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
        photo = saved[0]
        return f"photo{photo['owner_id']}_{photo['id']}"

    def send_post(self, text: str, image_path: str | None = None, progress: Callable[[str], None] | None = None) -> SendResult:
        has_photo = bool(image_path and Path(image_path).exists())
        err = self._check_credentials(need_user_token=has_photo)
        if err:
            return SendResult(False, err)

        try:
            attachment = ""
            if has_photo:
                attachment = self._upload_photo(image_path, progress=progress)

            if progress:
                progress("Публикация поста…")

            params: dict = {
                "owner_id": f"-{self.group_id}",
                "message": text,
                "from_group": 1,
            }
            if attachment:
                params["attachments"] = attachment

            # Публикуем от имени группы — group token
            result = self._call("wall.post", token=self.group_token, **params)
            post_id = result.get("post_id", "")
            return SendResult(True, f"Опубликовано в ВК. ID поста: {post_id}")

        except Exception as exc:
            return SendResult(False, f"Ошибка ВК: {exc}")
