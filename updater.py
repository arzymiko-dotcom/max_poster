"""
Модуль проверки и загрузки обновлений.

GitHub version.txt — одна строка с версией (например: 1.1.0)
Установщик лежит на Яндекс.Диске (публичная ссылка).
"""

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from packaging.version import Version
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

# ══════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ (ЗАМЕНИ НА СВОИ!)
# ══════════════════════════════════════════════════════════════════
GITHUB_USER = "arzymiko-dotcom"                     # твой логин на GitHub
GITHUB_REPO = "max_poster"                          # название репозитория
YADISK_PUBLIC_URL = "https://disk.yandex.ru/d/0kgzjsURZllkpw"  # публичная ссылка на папку/файл с установщиком
# ══════════════════════════════════════════════════════════════════

GITHUB_VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/version.txt"


def _local_version() -> str:
    """Читает текущую версию из version.txt рядом с exe/скриптом."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    path = base / "version.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return "0.0.0"


def _fetch_remote_version() -> str | None:
    """Возвращает версию с GitHub или None при ошибке."""
    try:
        resp = requests.get(GITHUB_VERSION_URL, timeout=10)
        resp.raise_for_status()
        return resp.text.strip().splitlines()[0].strip()
    except Exception:
        return None


def _get_yadisk_direct_link(public_url: str) -> str:
    """
    Преобразует публичную ссылку Яндекс.Диска в прямую ссылку на скачивание.
    """
    api_url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
    resp = requests.get(api_url, params={"public_key": public_url}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or "href" not in data:
        raise RuntimeError(f"Яндекс.Диск не вернул ссылку: {data!r}")
    return data["href"]


class DownloadWorker(QThread):
    progress = pyqtSignal(int)
    download_finished = pyqtSignal(str)   # путь к скачанному файлу
    failed = pyqtSignal(str)              # сообщение об ошибке

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def run(self) -> None:
        try:
            # Получаем прямую ссылку, если передана публичная ссылка Яндекс.Диска
            if "disk.yandex.ru" in self.url:
                download_url = _get_yadisk_direct_link(self.url)
            else:
                download_url = self.url

            # Определяем временное имя файла
            parsed = urlparse(download_url)
            filename = Path(parsed.path).name or "update_setup.exe"
            # Если имя не содержит .exe, добавляем .exe (для безопасности)
            if not filename.lower().endswith('.exe'):
                filename += ".exe"
            dest = Path(tempfile.gettempdir()) / filename

            # Скачиваем с прогрессом
            resp = requests.get(download_url, stream=True, timeout=60)
            resp.raise_for_status()

            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0

            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(int(downloaded / total * 100))

            self.progress.emit(100)

            # Проверяем, не является ли скачанный файл ZIP-архивом (Яндекс.Диск иногда отдаёт zip)
            if zipfile.is_zipfile(dest):
                with zipfile.ZipFile(dest, 'r') as z:
                    # Ищем внутри архива .exe файл
                    exe_names = [n for n in z.namelist() if n.lower().endswith('.exe')]
                    if not exe_names:
                        raise RuntimeError("Внутри ZIP-архива не найден EXE-файл")
                    # Извлекаем первый найденный .exe во временную папку
                    exe_path = Path(tempfile.gettempdir()) / Path(exe_names[0]).name
                    with open(exe_path, 'wb') as out:
                        out.write(z.read(exe_names[0]))
                # Убеждаемся, что EXE успешно извлечён, и только потом удаляем zip
                if exe_path.exists() and exe_path.stat().st_size > 0:
                    dest.unlink(missing_ok=True)
                    dest = exe_path
                else:
                    raise RuntimeError(f"Извлечённый EXE пустой или отсутствует: {exe_path}")

            self.download_finished.emit(str(dest))

        except Exception as exc:
            self.failed.emit(str(exc))


class DownloadDialog(QDialog):
    def __init__(self, remote_ver: str, local_ver: str, url: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Загрузка обновления")
        self.setFixedSize(400, 130)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)

        self._url = url
        self._worker: DownloadWorker | None = None

        layout = QVBoxLayout(self)

        self._label = QLabel(f"Загрузка версии {remote_ver}...")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        layout.addWidget(self._bar)

        self._cancel_btn = QPushButton("Отмена")
        self._cancel_btn.clicked.connect(self._cancel)
        layout.addWidget(self._cancel_btn)

    def start(self) -> None:
        self._worker = DownloadWorker(self._url)
        self._worker.progress.connect(self._bar.setValue)
        self._worker.download_finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker:
            self._worker.quit()
            self._worker.wait(2000)
        self.reject()

    def _on_finished(self, path: str) -> None:
        self._label.setText("Загрузка завершена. Запускаем установщик...")
        self._cancel_btn.setEnabled(False)
        # Запускаем установщик и закрываем приложение
        subprocess.Popen([path])
        self.accept()
        sys.exit(0)

    def _on_failed(self, error: str) -> None:
        self._label.setText(f"Ошибка загрузки: {error}")
        self._cancel_btn.setText("Закрыть")


def check_for_updates(parent=None) -> None:
    """Проверяет обновления и показывает диалог если доступна новая версия."""
    local = _local_version()
    remote_ver = _fetch_remote_version()

    if remote_ver is None:
        return  # нет интернета или GitHub недоступен — молча пропускаем

    try:
        if Version(remote_ver) <= Version(local):
            return  # версия актуальна
    except Exception:
        return

    msg = QMessageBox(parent)
    msg.setWindowTitle("Доступно обновление")
    msg.setText(f"Вы используете версию {local}, вышла {remote_ver}.\n\nОбновить сейчас?")
    msg.setIcon(QMessageBox.Icon.Question)
    btn_yes = msg.addButton("Да", QMessageBox.ButtonRole.YesRole)
    msg.addButton("Нет", QMessageBox.ButtonRole.NoRole)
    msg.exec()

    if msg.clickedButton() is not btn_yes:
        return

    dlg = DownloadDialog(remote_ver, local, YADISK_PUBLIC_URL, parent)
    dlg.start()
    dlg.exec()