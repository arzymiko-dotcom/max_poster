"""
Модуль проверки и загрузки обновлений.

GitHub version.txt — две строки:
  строка 1: версия (например: 1.2.3)
  строка 2: sha256:<hex> — SHA256-хэш установщика (опционально)

Если хэш присутствует, скачанный EXE верифицируется перед запуском.
Установщик лежит на Яндекс.Диске (публичная ссылка).
"""

import hashlib
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
    QApplication,
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
YADISK_PUBLIC_URL = "https://disk.yandex.ru/d/rDx8MZ0BVpc3xw"  # публичная ссылка на папку/файл с установщиком
# ══════════════════════════════════════════════════════════════════

GITHUB_VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/master/version.txt"


def _local_version() -> str:
    """Читает текущую версию из version.txt рядом с exe/скриптом."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    path = base / "version.txt"
    if path.exists():
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return lines[0].strip() if lines else "0.0.0"
    return "0.0.0"


def _fetch_remote_version() -> str | None:
    """Возвращает версию с GitHub или None при ошибке."""
    info = _fetch_remote_info()
    return info[0] if info else None


def _fetch_remote_info() -> tuple[str, str | None] | None:
    """Возвращает (версия, sha256_или_None) с GitHub или None при ошибке."""
    try:
        resp = requests.get(GITHUB_VERSION_URL, timeout=10)
        resp.raise_for_status()
        lines = resp.text.strip().splitlines()
        if not lines:
            return None
        version = lines[0].strip()
        sha256 = None
        for line in lines[1:]:
            line = line.strip()
            if line.lower().startswith("sha256:"):
                sha256 = line[len("sha256:"):].strip().lower()
                break
        return version, sha256
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

    def __init__(self, url: str, expected_sha256: str = "") -> None:
        super().__init__()
        self.url = url
        self.expected_sha256 = expected_sha256.strip().lower()

    def run(self) -> None:
        dest: Path | None = None
        zip_path: Path | None = None
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
                zip_path = dest  # запомним для очистки в finally
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
                    zip_path.unlink(missing_ok=True)
                    zip_path = None
                    dest = exe_path
                else:
                    raise RuntimeError(f"Извлечённый EXE пустой или отсутствует: {exe_path}")

            # Проверяем PE-заголовок: настоящий EXE начинается с "MZ"
            with open(dest, "rb") as _f:
                magic = _f.read(2)
            if magic != b"MZ":
                raise RuntimeError("Скачанный файл не является Windows EXE (неверный заголовок)")

            # Проверяем SHA256, если хэш был передан из version.txt
            if self.expected_sha256:
                actual = hashlib.sha256(dest.read_bytes()).hexdigest().lower()
                if actual != self.expected_sha256:
                    raise RuntimeError(
                        f"SHA256 не совпадает — файл повреждён или подменён.\n"
                        f"Ожидается: {self.expected_sha256}\n"
                        f"Получено:  {actual}"
                    )

            self.download_finished.emit(str(dest))

        except Exception as exc:
            # Удаляем временные файлы при любой ошибке
            if zip_path is not None:
                zip_path.unlink(missing_ok=True)
            if dest is not None and dest.exists():
                dest.unlink(missing_ok=True)
            self.failed.emit(str(exc))


class DownloadDialog(QDialog):
    def __init__(self, remote_ver: str, local_ver: str, url: str, expected_sha256: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Загрузка обновления")
        self.setFixedSize(400, 130)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)

        self._url = url
        self._expected_sha256 = expected_sha256
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
        self._worker = DownloadWorker(self._url, self._expected_sha256)
        self._worker.progress.connect(self._bar.setValue)
        self._worker.download_finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker:
            self._worker.quit()
            if not self._worker.wait(5000):
                self._worker.terminate()
        self.reject()

    def _on_finished(self, path: str) -> None:
        self._label.setText("Загрузка завершена. Запускаем установщик...")
        self._cancel_btn.setEnabled(False)
        # Запускаем установщик и закрываем приложение корректно
        subprocess.Popen([path])
        self.accept()
        QApplication.instance().quit()

    def _on_failed(self, error: str) -> None:
        self._label.setText(f"Ошибка загрузки: {error}")
        self._cancel_btn.setText("Закрыть")


class _CheckWorker(QThread):
    """Проверяет версию на GitHub в фоновом потоке."""
    result_ready = pyqtSignal(str, str, str)  # (local_ver, remote_ver, sha256_or_empty)
    up_to_date   = pyqtSignal(str)            # (local_ver) — версия актуальна

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        local = _local_version()
        info = _fetch_remote_info()
        if info is None:
            return
        remote_ver, remote_sha256 = info
        try:
            if Version(remote_ver) <= Version(local):
                self.up_to_date.emit(local)
                return
        except Exception:
            return
        self.result_ready.emit(local, remote_ver, remote_sha256 or "")


def check_for_updates(parent=None, silent: bool = True) -> None:
    """Проверяет обновления в фоновом потоке.

    silent=True  — при автозапуске: ничего не показывать если версия актуальна.
    silent=False — при ручной проверке: показать сообщение «последняя версия».
    """
    worker = _CheckWorker(parent)

    def _on_result(local: str, remote_ver: str, sha256: str) -> None:
        msg = QMessageBox(parent)
        msg.setWindowTitle("Доступно обновление")
        msg.setText(f"Вы используете версию {local}, вышла {remote_ver}.\n\nОбновить сейчас?")
        msg.setIcon(QMessageBox.Icon.Question)
        btn_yes = msg.addButton("Да", QMessageBox.ButtonRole.YesRole)
        msg.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        msg.exec()
        if msg.clickedButton() is not btn_yes:
            return
        dlg = DownloadDialog(remote_ver, local, YADISK_PUBLIC_URL, sha256, parent)
        dlg.start()
        dlg.exec()

    def _on_up_to_date(local: str) -> None:
        if not silent:
            QMessageBox.information(
                parent, "Обновления",
                f"Вы используете последнюю версию\nVersion {local}"
            )

    worker.result_ready.connect(_on_result)
    worker.up_to_date.connect(_on_up_to_date)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    # Сохраняем ссылку на worker, чтобы GC не удалил его до завершения потока.
    # Если parent не передан — привязываем к экземпляру приложения.
    owner = parent if parent is not None else QApplication.instance()
    if owner is not None:
        worker.setParent(owner)