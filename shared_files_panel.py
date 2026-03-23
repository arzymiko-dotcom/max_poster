"""
shared_files_panel.py — Общие файлы через ВК-альбом.

Env vars (добавить в .env):
    SHARED_VK_GROUP_ID=236573184
    SHARED_VK_ALBUM_ID=308743880
    VK_USER_TOKEN — уже должен быть
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests
from PyQt6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from env_utils import get_env_path, load_env_safe
from constants import VK_API_URL, VK_API_VERSION, VK_RETRY_DELAYS

_log = logging.getLogger(__name__)
load_env_safe(get_env_path())


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b} Б"
    if b < 1024 ** 2:
        return f"{b // 1024} КБ"
    return f"{b / 1024 ** 2:.1f} МБ"


def _fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


# ── VK API ────────────────────────────────────────────────────────

def _vk_call(method: str, token: str, **params) -> dict | list:
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
            _log.warning("VK %s попытка %d: %s", method, attempt + 1, e)
            if delay is not None:
                time.sleep(delay)
    else:
        raise RuntimeError(f"Сеть: {last_exc}")
    if not isinstance(data, dict):
        raise RuntimeError(f"Неожиданный ответ ВК: {data!r}")
    if "error" in data:
        err = data["error"]
        raise RuntimeError(err.get("error_msg", str(err)) if isinstance(err, dict) else str(err))
    if "response" not in data:
        raise RuntimeError(f"Нет 'response': {data!r}")
    return data["response"]


def _best_thumb_url(sizes: list[dict], target: int = 130) -> str:
    best_url, best_diff = "", 99999
    for s in sizes:
        diff = abs(s.get("width", 0) - target)
        if diff < best_diff:
            best_diff = diff
            best_url = s.get("url", "")
    return best_url


def _max_photo_url(sizes: list[dict]) -> str:
    """URL максимального размера фото."""
    for t in "wzyrqpoxms":
        for s in sizes:
            if s.get("type") == t:
                return s.get("url", "")
    return sizes[-1]["url"] if sizes else ""


# ── Workers ───────────────────────────────────────────────────────

class _Signals(QObject):
    done     = pyqtSignal(object)
    error    = pyqtSignal(str)
    progress = pyqtSignal(str)


class _FetchPhotosWorker(QRunnable):
    def __init__(self, token: str, group_id: str, album_id: str):
        super().__init__()
        self.signals = _Signals()
        self._token, self._group_id, self._album_id = token, group_id, album_id
        self.setAutoDelete(True)

    def run(self):
        try:
            resp = _vk_call(
                "photos.get",
                token=self._token,
                owner_id=f"-{self._group_id}",
                album_id=self._album_id,
                count=200,
                rev=1,
                photo_sizes=1,
            )
            items = resp.get("items", []) if isinstance(resp, dict) else resp
            self.signals.done.emit(items)
        except Exception as e:
            self.signals.error.emit(str(e))


class _FetchDocsWorker(QRunnable):
    def __init__(self, token: str, group_id: str):
        super().__init__()
        self.signals = _Signals()
        self._token, self._group_id = token, group_id
        self.setAutoDelete(True)

    def run(self):
        try:
            resp = _vk_call(
                "docs.get",
                token=self._token,
                owner_id=f"-{self._group_id}",
                count=200,
            )
            items = resp.get("items", []) if isinstance(resp, dict) else resp
            self.signals.done.emit(items)
        except Exception as e:
            self.signals.error.emit(str(e))


class _UploadPhotoWorker(QRunnable):
    def __init__(self, token: str, group_id: str, album_id: str, file_path: str):
        super().__init__()
        self.signals = _Signals()
        self._token = token
        self._group_id = group_id
        self._album_id = album_id
        self._file_path = file_path
        self.setAutoDelete(True)

    def run(self):
        try:
            # 1. Получаем URL для загрузки в альбом группы
            self.signals.progress.emit("Получение адреса загрузки…")
            srv = _vk_call(
                "photos.getUploadServer",
                token=self._token,
                album_id=self._album_id,
                group_id=self._group_id,
            )
            upload_url = srv["upload_url"]

            # 2. Загружаем файл
            self.signals.progress.emit("Загрузка фото…")
            fname = Path(self._file_path).name
            ext = Path(self._file_path).suffix.lower().lstrip(".")
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
            with open(self._file_path, "rb") as f:
                resp = requests.post(
                    upload_url,
                    files={"file1": (fname, f, mime)},
                    timeout=120,
                )
            resp.raise_for_status()
            uploaded = resp.json()
            _log.debug("VK album upload response: %s", uploaded)

            photos_list = uploaded.get("photos_list", "")
            if not photos_list or photos_list in ("[]", ""):
                raise RuntimeError(
                    f"ВК не принял файл (photos_list пустой).\n"
                    f"Ответ: {uploaded}\n\n"
                    "Проверьте VK_USER_TOKEN — нужны права: photos, offline"
                )

            # Новый VK API (5.1xx+) автоматически сохраняет фото в альбом при загрузке.
            # photos.save больше не нужен — фото уже в альбоме.
            self.signals.progress.emit("Сохранение…")
            self.signals.done.emit(uploaded)
        except Exception as e:
            self.signals.error.emit(str(e))


class _UploadDocWorker(QRunnable):
    def __init__(self, token: str, group_id: str, file_path: str):
        super().__init__()
        self.signals = _Signals()
        self._token = token
        self._group_id = group_id
        self._file_path = file_path
        self.setAutoDelete(True)

    def run(self):
        try:
            self.signals.progress.emit("Получение адреса загрузки…")
            srv = _vk_call(
                "docs.getUploadServer",
                token=self._token,
                group_id=self._group_id,
            )
            upload_url = srv["upload_url"]

            self.signals.progress.emit("Загрузка файла…")
            fname = Path(self._file_path).name
            with open(self._file_path, "rb") as f:
                resp = requests.post(upload_url, files={"file": (fname, f)}, timeout=120)
            resp.raise_for_status()
            uploaded = resp.json()

            self.signals.progress.emit("Сохранение…")
            saved = _vk_call(
                "docs.save",
                token=self._token,
                file=uploaded.get("file", ""),
                title=fname,
            )
            self.signals.done.emit(saved)
        except Exception as e:
            self.signals.error.emit(str(e))


class _DownloadWorker(QRunnable):
    def __init__(self, url: str, save_path: str):
        super().__init__()
        self.signals = _Signals()
        self._url = url
        self._save_path = save_path
        self.setAutoDelete(True)

    def run(self):
        try:
            resp = requests.get(self._url, timeout=120, stream=True)
            resp.raise_for_status()
            with open(self._save_path, "wb") as f:
                for chunk in resp.iter_content(65536):
                    if chunk:
                        f.write(chunk)
            self.signals.done.emit(self._save_path)
        except Exception as e:
            self.signals.error.emit(str(e))


class _DeletePhotoWorker(QRunnable):
    def __init__(self, token: str, group_id: str, photo_id: int):
        super().__init__()
        self.signals = _Signals()
        self._token, self._group_id, self._photo_id = token, group_id, photo_id
        self.setAutoDelete(True)

    def run(self):
        try:
            _vk_call(
                "photos.delete",
                token=self._token,
                owner_id=f"-{self._group_id}",
                photo_id=self._photo_id,
            )
            self.signals.done.emit(self._photo_id)
        except Exception as e:
            self.signals.error.emit(str(e))


class _DeleteDocWorker(QRunnable):
    def __init__(self, token: str, group_id: str, doc_id: int):
        super().__init__()
        self.signals = _Signals()
        self._token, self._group_id, self._doc_id = token, group_id, doc_id
        self.setAutoDelete(True)

    def run(self):
        try:
            _vk_call(
                "docs.delete",
                token=self._token,
                owner_id=f"-{self._group_id}",
                doc_id=self._doc_id,
            )
            self.signals.done.emit(self._doc_id)
        except Exception as e:
            self.signals.error.emit(str(e))


class _LoadThumbWorker(QRunnable):
    def __init__(self, url: str, photo_id: int):
        super().__init__()
        self.signals = _Signals()
        self._url = url
        self._photo_id = photo_id
        self.setAutoDelete(True)

    def run(self):
        try:
            resp = requests.get(self._url, timeout=20)
            resp.raise_for_status()
            px = QPixmap()
            px.loadFromData(resp.content)
            self.signals.done.emit((self._photo_id, px))
        except Exception as e:
            _log.debug("Thumb load failed: %s", e)
            self.signals.error.emit(str(e))


# ── Photo Card ────────────────────────────────────────────────────

class _PhotoCard(QFrame):
    clicked_post     = pyqtSignal(int, list)
    clicked_download = pyqtSignal(int, list)
    clicked_delete   = pyqtSignal(int)

    CARD_W = 144
    CARD_H = 178
    THUMB_W = 130
    THUMB_H = 118

    def __init__(self, photo: dict, parent=None):
        super().__init__(parent)
        self._photo_id = photo.get("id", 0)
        self._sizes = photo.get("sizes", [])

        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setObjectName("photoCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(self.THUMB_W, self.THUMB_H)
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setStyleSheet("background:#1a1a2e; border-radius:4px; color:#6e6e9e; font-size:18px;")
        self._thumb_lbl.setText("⏳")
        layout.addWidget(self._thumb_lbl)

        date_lbl = QLabel(_fmt_date(photo.get("date", 0)))
        date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        date_lbl.setStyleSheet("font-size:10px; color:#6e6e9e; background:transparent;")
        layout.addWidget(date_lbl)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(3)

        self._btn_post = QPushButton("→ В пост")
        self._btn_post.setFixedHeight(22)
        self._btn_post.setStyleSheet(
            "QPushButton{background:#4a6cf7;color:#fff;border:none;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#5a7cff;}"
        )
        self._btn_post.setToolTip("Вставить фото в пост")
        self._btn_post.clicked.connect(lambda: self.clicked_post.emit(self._photo_id, self._sizes))

        self._btn_dl = QPushButton("💾")
        self._btn_dl.setFixedSize(24, 22)
        self._btn_dl.setStyleSheet(
            "QPushButton{background:#2d2d3f;color:#aaa;border:none;border-radius:3px;font-size:12px;}"
            "QPushButton:hover{background:#3a3a5f;color:#fff;}"
        )
        self._btn_dl.setToolTip("Скачать фото на ПК")
        self._btn_dl.clicked.connect(lambda: self.clicked_download.emit(self._photo_id, self._sizes))

        self._btn_del = QPushButton("🗑")
        self._btn_del.setFixedSize(24, 22)
        self._btn_del.setStyleSheet(
            "QPushButton{background:#2d2d3f;color:#aaa;border:none;border-radius:3px;font-size:12px;}"
            "QPushButton:hover{background:#8b2020;color:#fff;}"
        )
        self._btn_del.setToolTip("Удалить фото")
        self._btn_del.clicked.connect(lambda: self.clicked_delete.emit(self._photo_id))

        btn_row.addWidget(self._btn_post)
        btn_row.addWidget(self._btn_dl)
        btn_row.addWidget(self._btn_del)
        layout.addLayout(btn_row)

    def set_thumb(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            self.THUMB_W, self.THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumb_lbl.clear()
        self._thumb_lbl.setPixmap(scaled)

    def set_dark(self, dark: bool) -> None:
        bg = "#1a1a2e" if dark else "#e4e4f0"
        self._thumb_lbl.setStyleSheet(
            f"background:{bg}; border-radius:4px; color:#6e6e9e; font-size:18px;"
        )


# ── Doc Item ──────────────────────────────────────────────────────

class _DocItem(QFrame):
    clicked_download = pyqtSignal(str, str)
    clicked_delete   = pyqtSignal(int)

    def __init__(self, doc: dict, parent=None):
        super().__init__(parent)
        self._url = doc.get("url", "")
        self._fname = doc.get("title", "файл")
        self._doc_id = doc.get("id", 0)
        ext = doc.get("ext", "")

        self.setObjectName("docItem")
        self.setFixedHeight(54)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(10)

        ext_lbl = QLabel(ext.upper()[:4] or "FILE")
        ext_lbl.setFixedSize(40, 28)
        ext_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ext_lbl.setStyleSheet(
            f"background:{self._ext_color(ext)}; color:#fff; border-radius:4px;"
            "font-size:10px; font-weight:bold;"
        )
        layout.addWidget(ext_lbl)

        info = QVBoxLayout()
        info.setSpacing(1)
        info.setContentsMargins(0, 0, 0, 0)

        name = self._fname if len(self._fname) <= 50 else self._fname[:47] + "…"
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size:12px; background:transparent;")
        info.addWidget(name_lbl)

        meta_lbl = QLabel(f"{_fmt_size(doc.get('size', 0))}  ·  {_fmt_date(doc.get('date', 0))}")
        meta_lbl.setStyleSheet("font-size:10px; color:#6e6e9e; background:transparent;")
        info.addWidget(meta_lbl)

        layout.addLayout(info)
        layout.addStretch()

        btn_dl = QPushButton("💾 Скачать")
        btn_dl.setFixedHeight(26)
        btn_dl.setStyleSheet(
            "QPushButton{background:#2d2d3f;color:#aaa;border:none;border-radius:4px;"
            "font-size:11px;padding:0 10px;}"
            "QPushButton:hover{background:#3a3a5f;color:#fff;}"
        )
        btn_dl.clicked.connect(lambda: self.clicked_download.emit(self._url, self._fname))
        layout.addWidget(btn_dl)

        btn_del = QPushButton("🗑")
        btn_del.setFixedSize(26, 26)
        btn_del.setStyleSheet(
            "QPushButton{background:#2d2d3f;color:#aaa;border:none;border-radius:4px;font-size:13px;}"
            "QPushButton:hover{background:#8b2020;color:#fff;}"
        )
        btn_del.setToolTip("Удалить файл")
        btn_del.clicked.connect(lambda: self.clicked_delete.emit(self._doc_id))
        layout.addWidget(btn_del)

    @staticmethod
    def _ext_color(ext: str) -> str:
        colors = {
            "xlsx": "#217346", "xls": "#217346",
            "docx": "#2b579a", "doc": "#2b579a",
            "pdf": "#e74c3c",
            "zip": "#f39c12", "rar": "#f39c12", "7z": "#f39c12",
            "mp4": "#9b59b6", "avi": "#9b59b6", "mov": "#9b59b6",
            "png": "#3498db", "jpg": "#3498db", "jpeg": "#3498db",
        }
        return colors.get(ext.lower(), "#555577")


# ── Progress Button ───────────────────────────────────────────────

class _ProgressButton(QPushButton):
    """Кнопка с встроенным прогресс-баром (зелёная полоса снизу)."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._progress = 0.0
        self._base_text = text

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(100.0, value))
        pct = int(self._progress)
        if pct > 0:
            self.setText(f"⬆  {pct}%")
        self.update()

    def reset_progress(self) -> None:
        self._progress = 0.0
        self.setText(self._base_text)
        self.setEnabled(True)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._progress <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar_h = 5
        y = self.height() - bar_h
        w = self.width()
        # Track (фон полосы)
        p.fillRect(0, y, w, bar_h, QColor(255, 255, 255, 25))
        # Fill (прогресс)
        fill_w = int(w * self._progress / 100)
        if fill_w > 0:
            p.fillRect(0, y, fill_w, bar_h, QColor("#4ade80"))
        p.end()


# ── Цветовые схемы ────────────────────────────────────────────────

_DARK = dict(
    bg="#1e1e2e", header_bg="#16162a", border="#2d2d3f",
    text="#e0e0f0", text_muted="#6e6e9e",
    card_bg="#252535", tab_active="#4a6cf7", tab_inactive="#2d2d3f",
    sep="#2d2d3f", scroll="#2d2d3f",
)
_LIGHT = dict(
    bg="#f3f4f6", header_bg="#ededf8", border="#d0d0e8",
    text="#1a1a3a", text_muted="#6060a0",
    card_bg="#ffffff", tab_active="#4a6cf7", tab_inactive="#e0e0ef",
    sep="#d0d0e8", scroll="#c0c0d8",
)


# ── Main Panel ────────────────────────────────────────────────────

class SharedFilesPanel(QWidget):
    """Панель общих файлов через ВК-альбом (фото + документы)."""

    photo_for_post = pyqtSignal(str)   # local temp path — для вставки в пост

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._dark = True
        self._c = dict(_DARK)

        self._token    = os.getenv("VK_USER_TOKEN", "")
        self._group_id = os.getenv("SHARED_VK_GROUP_ID", "")
        self._album_id = os.getenv("SHARED_VK_ALBUM_ID", "")

        self._photos: list[dict] = []
        self._photo_cards: dict[int, _PhotoCard] = {}
        self._worker_signals: list[_Signals] = []   # держим ссылки чтобы GC не удалил

        self._spin_frame = 0
        self._spin_msg = ""
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(80)
        self._spinner_timer.timeout.connect(self._spin_step)

        self._upload_progress = 0.0   # текущее значение (0-100)
        self._upload_target   = 0.0   # цель анимации
        self._upload_timer = QTimer(self)
        self._upload_timer.setInterval(40)
        self._upload_timer.timeout.connect(self._animate_upload)

        self._build_ui()
        self._apply_theme()

    # ── UI ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QFrame()
        header.setObjectName("sfHeader")
        header.setFixedHeight(50)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(8)

        title = QLabel("📁  Общие файлы")
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        hl.addWidget(title)
        hl.addStretch()

        self._btn_tab_photos = QPushButton("Фото")
        self._btn_tab_photos.setCheckable(True)
        self._btn_tab_photos.setChecked(True)
        self._btn_tab_photos.setFixedHeight(28)

        self._btn_tab_docs = QPushButton("Файлы")
        self._btn_tab_docs.setCheckable(True)
        self._btn_tab_docs.setChecked(False)
        self._btn_tab_docs.setFixedHeight(28)

        self._btn_tab_photos.clicked.connect(lambda: self._switch_tab(0))
        self._btn_tab_docs.clicked.connect(lambda: self._switch_tab(1))

        hl.addWidget(self._btn_tab_photos)
        hl.addWidget(self._btn_tab_docs)

        self._btn_refresh = QPushButton("⟳")
        self._btn_refresh.setFixedSize(30, 28)
        self._btn_refresh.setToolTip("Обновить список")
        self._btn_refresh.clicked.connect(self._refresh)
        hl.addWidget(self._btn_refresh)

        root.addWidget(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setObjectName("sfSep")
        root.addWidget(sep)

        # Status bar
        self._status_lbl = QLabel("")
        self._status_lbl.setFixedHeight(22)
        self._status_lbl.setContentsMargins(14, 0, 0, 0)
        self._status_lbl.setVisible(False)
        root.addWidget(self._status_lbl)

        # Tab stack
        self._tab_stack = QStackedWidget()
        root.addWidget(self._tab_stack)

        # Photos tab
        self._photos_scroll = QScrollArea()
        self._photos_scroll.setWidgetResizable(True)
        self._photos_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._photos_container = QWidget()
        self._photos_grid = QGridLayout(self._photos_container)
        self._photos_grid.setContentsMargins(12, 12, 12, 12)
        self._photos_grid.setSpacing(8)
        self._photos_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._photos_scroll.setWidget(self._photos_container)
        self._tab_stack.addWidget(self._photos_scroll)

        # Docs tab
        self._docs_scroll = QScrollArea()
        self._docs_scroll.setWidgetResizable(True)
        self._docs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._docs_container = QWidget()
        self._docs_layout = QVBoxLayout(self._docs_container)
        self._docs_layout.setContentsMargins(12, 12, 12, 12)
        self._docs_layout.setSpacing(4)
        self._docs_layout.addStretch()
        self._docs_scroll.setWidget(self._docs_container)
        self._tab_stack.addWidget(self._docs_scroll)

        # ── Bottom upload button ────────────────────────────────
        bot_sep = QFrame()
        bot_sep.setFrameShape(QFrame.Shape.HLine)
        bot_sep.setFixedHeight(1)
        bot_sep.setObjectName("sfSep")
        root.addWidget(bot_sep)

        bot = QFrame()
        bot.setObjectName("sfBottom")
        bot.setFixedHeight(66)
        bl = QVBoxLayout(bot)
        bl.setContentsMargins(12, 10, 12, 10)

        self._btn_upload = _ProgressButton("⬆   Загрузить фото или файл")
        self._btn_upload.setFixedHeight(44)
        self._btn_upload.setToolTip("Загрузить фото в альбом или файл в документы группы ВК")
        self._btn_upload.clicked.connect(self._upload)
        bl.addWidget(self._btn_upload)

        root.addWidget(bot)

    # ── Tab switching ────────────────────────────────────────────

    def _switch_tab(self, index: int) -> None:
        self._tab_stack.setCurrentIndex(index)
        self._btn_tab_photos.setChecked(index == 0)
        self._btn_tab_docs.setChecked(index == 1)
        self._apply_tab_style()
        if index == 0 and not self._photos:
            self._refresh_photos()
        elif index == 1:
            self._refresh_docs()

    def _refresh(self) -> None:
        if self._tab_stack.currentIndex() == 0:
            self._refresh_photos()
        else:
            self._refresh_docs()

    # ── Fetch photos ─────────────────────────────────────────────

    def _refresh_photos(self) -> None:
        if not self._check_config(need_album=True):
            return
        self._set_status("Загрузка фото…")
        self._btn_refresh.setEnabled(False)
        w = _FetchPhotosWorker(self._token, self._group_id, self._album_id)
        self._worker_signals.append(w.signals)
        w.signals.done.connect(self._on_photos_fetched)
        w.signals.error.connect(self._on_fetch_error)
        self._pool.start(w)

    def _on_photos_fetched(self, items: object) -> None:
        photos = items if isinstance(items, list) else []
        self._photos = photos
        self._btn_refresh.setEnabled(True)
        self._clear_status()
        self._render_photos(photos)

    # ── Fetch docs ───────────────────────────────────────────────

    def _refresh_docs(self) -> None:
        if not self._check_config():
            return
        self._set_status("Загрузка файлов…")
        self._btn_refresh.setEnabled(False)
        w = _FetchDocsWorker(self._token, self._group_id)
        self._worker_signals.append(w.signals)
        w.signals.done.connect(self._on_docs_fetched)
        w.signals.error.connect(self._on_fetch_error)
        self._pool.start(w)

    def _on_docs_fetched(self, items: object) -> None:
        docs = items if isinstance(items, list) else []
        self._btn_refresh.setEnabled(True)
        self._clear_status()
        self._render_docs(docs)

    def _on_fetch_error(self, msg: str) -> None:
        self._btn_refresh.setEnabled(True)
        self._set_status(f"Ошибка: {msg}", error=True)

    # ── Render photos ────────────────────────────────────────────

    def _render_photos(self, photos: list) -> None:
        while self._photos_grid.count():
            item = self._photos_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._photo_cards.clear()

        if not photos:
            lbl = QLabel("Нет фото\nНажмите ⬆ Загрузить чтобы добавить")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color:{self._c['text_muted']}; font-size:13px; background:transparent;")
            self._photos_grid.addWidget(lbl, 0, 0, 1, 3)
            return

        cols = 3
        for i, photo in enumerate(photos):
            card = _PhotoCard(photo, self._photos_container)
            card.set_dark(self._dark)
            card.clicked_post.connect(self._on_photo_for_post)
            card.clicked_download.connect(self._on_photo_download)
            card.clicked_delete.connect(self._on_photo_delete)
            self._photo_cards[photo["id"]] = card
            self._photos_grid.addWidget(card, i // cols, i % cols)

            thumb_url = _best_thumb_url(photo.get("sizes", []), 130)
            if thumb_url:
                tw = _LoadThumbWorker(thumb_url, photo["id"])
                self._worker_signals.append(tw.signals)
                tw.signals.done.connect(self._on_thumb_loaded)
                self._pool.start(tw)

    def _on_thumb_loaded(self, data: object) -> None:
        if not isinstance(data, tuple):
            return
        photo_id, pixmap = data
        if photo_id in self._photo_cards and not pixmap.isNull():
            self._photo_cards[photo_id].set_thumb(pixmap)

    # ── Render docs ──────────────────────────────────────────────

    def _render_docs(self, docs: list) -> None:
        while self._docs_layout.count() > 1:
            item = self._docs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not docs:
            lbl = QLabel("Нет файлов\nНажмите ⬆ Загрузить чтобы добавить")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color:{self._c['text_muted']}; font-size:13px; background:transparent;")
            self._docs_layout.insertWidget(0, lbl)
            return

        for doc in docs:
            item = _DocItem(doc, self._docs_container)
            item.clicked_download.connect(self._on_doc_download)
            item.clicked_delete.connect(self._on_doc_delete)
            self._docs_layout.insertWidget(self._docs_layout.count() - 1, item)

    # ── Actions: photo → post ────────────────────────────────────

    def _on_photo_for_post(self, photo_id: int, sizes: list) -> None:
        url = _max_photo_url(sizes)
        if not url:
            return
        tmp = tempfile.mktemp(suffix=".jpg", prefix="maxpost_shared_")
        self._set_status("Загрузка фото для поста…")
        w = _DownloadWorker(url, tmp)
        self._worker_signals.append(w.signals)
        w.signals.done.connect(self._on_post_photo_ready)
        w.signals.error.connect(lambda e: self._set_status(f"Ошибка: {e}", error=True))
        self._pool.start(w)

    def _on_post_photo_ready(self, path: object) -> None:
        self._clear_status()
        self.photo_for_post.emit(str(path))

    # ── Actions: download ────────────────────────────────────────

    def _on_photo_download(self, photo_id: int, sizes: list) -> None:
        url = _max_photo_url(sizes)
        if not url:
            return
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить фото", f"photo_{photo_id}.jpg",
            "Изображения (*.jpg *.jpeg *.png);;Все файлы (*.*)"
        )
        if not save_path:
            return
        self._start_download(url, save_path)

    def _on_doc_download(self, url: str, fname: str) -> None:
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить файл", fname, "Все файлы (*.*)"
        )
        if not save_path:
            return
        self._start_download(url, save_path)

    def _start_download(self, url: str, save_path: str) -> None:
        self._set_status("Скачивание…")
        w = _DownloadWorker(url, save_path)
        self._worker_signals.append(w.signals)
        w.signals.done.connect(lambda _: self._set_status("✓ Сохранено"))
        w.signals.error.connect(lambda e: self._set_status(f"Ошибка: {e}", error=True))
        self._pool.start(w)

    # ── Actions: upload ──────────────────────────────────────────

    # ── Actions: delete ──────────────────────────────────────────

    def _on_photo_delete(self, photo_id: int) -> None:
        self._set_status("Удаление…")
        w = _DeletePhotoWorker(self._token, self._group_id, photo_id)
        self._worker_signals.append(w.signals)
        w.signals.done.connect(lambda _: (self._clear_status(), self._refresh_photos()))
        w.signals.error.connect(lambda e: self._set_status(f"Ошибка: {e}", error=True))
        self._pool.start(w)

    def _on_doc_delete(self, doc_id: int) -> None:
        self._set_status("Удаление…")
        w = _DeleteDocWorker(self._token, self._group_id, doc_id)
        self._worker_signals.append(w.signals)
        w.signals.done.connect(lambda _: (self._clear_status(), self._refresh_docs()))
        w.signals.error.connect(lambda e: self._set_status(f"Ошибка: {e}", error=True))
        self._pool.start(w)

    # ── Progress animation ───────────────────────────────────────

    def _start_upload_progress(self) -> None:
        self._upload_progress = 0.0
        self._upload_target = 0.0
        self._btn_upload.setEnabled(False)
        self._btn_upload.set_progress(0)
        self._upload_timer.start()

    def _set_upload_target(self, target: float) -> None:
        self._upload_target = target

    def _animate_upload(self) -> None:
        diff = self._upload_target - self._upload_progress
        if abs(diff) < 0.3:
            self._upload_progress = self._upload_target
        else:
            self._upload_progress += diff * 0.06
        self._btn_upload.set_progress(self._upload_progress)
        if self._upload_progress >= 100.0:
            self._upload_timer.stop()

    def _finish_upload_progress(self) -> None:
        self._upload_target = 100.0

    def _reset_upload_progress(self) -> None:
        self._upload_timer.stop()
        self._btn_upload.reset_progress()

    # ── Upload phase → target mapping ────────────────────────────

    _PHASE_TARGETS = {
        "Получение адреса загрузки…": 12.0,
        "Загрузка фото…": 70.0,
        "Загрузка файла…": 70.0,
        "Сохранение…": 88.0,
        "Перемещение в альбом…": 95.0,
    }

    def _on_upload_progress(self, msg: str) -> None:
        target = self._PHASE_TARGETS.get(msg, self._upload_target)
        self._set_upload_target(target)
        self._set_status(msg)

    # ── Actions: upload ──────────────────────────────────────────

    def _upload(self) -> None:
        if self._tab_stack.currentIndex() == 0:
            self._upload_photo()
        else:
            self._upload_doc()

    def _upload_photo(self) -> None:
        if not self._check_config(need_album=True):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать фото", "",
            "Изображения (*.jpg *.jpeg *.png *.webp);;Все файлы (*.*)"
        )
        if not path:
            return
        self._start_upload_progress()
        w = _UploadPhotoWorker(self._token, self._group_id, self._album_id, path)
        self._worker_signals.append(w.signals)
        w.signals.progress.connect(self._on_upload_progress)
        w.signals.done.connect(self._on_upload_photo_done)
        w.signals.error.connect(self._on_upload_error)
        self._pool.start(w)

    def _on_upload_photo_done(self, _: object) -> None:
        self._finish_upload_progress()
        QTimer.singleShot(600, self._reset_upload_progress)
        self._set_status("✓ Фото загружено")
        QTimer.singleShot(800, self._refresh_photos)

    def _upload_doc(self) -> None:
        if not self._check_config():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать файл", "", "Все файлы (*.*)"
        )
        if not path:
            return
        self._start_upload_progress()
        w = _UploadDocWorker(self._token, self._group_id, path)
        self._worker_signals.append(w.signals)
        w.signals.progress.connect(self._on_upload_progress)
        w.signals.done.connect(self._on_upload_doc_done)
        w.signals.error.connect(self._on_upload_error)
        self._pool.start(w)

    def _on_upload_doc_done(self, _: object) -> None:
        self._finish_upload_progress()
        QTimer.singleShot(600, self._reset_upload_progress)
        self._set_status("✓ Файл загружен")
        QTimer.singleShot(800, self._refresh_docs)

    def _on_upload_error(self, msg: str) -> None:
        self._reset_upload_progress()
        self._set_status(f"Ошибка загрузки: {msg}", error=True)

    # ── Config check ─────────────────────────────────────────────

    def _check_config(self, need_album: bool = False) -> bool:
        if not self._token:
            self._set_status("VK_USER_TOKEN не задан в .env", error=True)
            return False
        if not self._group_id:
            self._set_status("SHARED_VK_GROUP_ID не задан в .env", error=True)
            return False
        if need_album and not self._album_id:
            self._set_status("SHARED_VK_ALBUM_ID не задан в .env", error=True)
            return False
        return True

    # ── Status & Spinner ─────────────────────────────────────────

    _SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def _set_status(self, msg: str, error: bool = False) -> None:
        color = "#e74c3c" if error else self._c["text_muted"]
        self._status_lbl.setStyleSheet(
            f"font-size:11px; color:{color}; padding-left:14px; background:transparent;"
        )
        if error:
            self._spinner_timer.stop()
            self._status_lbl.setText(msg)
        else:
            self._spin_msg = msg
            self._spin_frame = 0
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
            self._status_lbl.setText(f"{self._SPINNER[0]}  {msg}")
        self._status_lbl.setVisible(bool(msg))

    def _spin_step(self) -> None:
        self._spin_frame = (self._spin_frame + 1) % len(self._SPINNER)
        self._status_lbl.setText(f"{self._SPINNER[self._spin_frame]}  {self._spin_msg}")

    def _clear_status(self) -> None:
        self._spinner_timer.stop()
        self._status_lbl.setVisible(False)

    # ── Theme ────────────────────────────────────────────────────

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self._c = dict(_DARK if dark else _LIGHT)
        self._apply_theme()
        for card in self._photo_cards.values():
            card.set_dark(dark)

    def _apply_theme(self) -> None:
        c = self._c
        self.setStyleSheet(f"""
            SharedFilesPanel, QWidget {{ background:{c['bg']}; }}
            QFrame#sfHeader {{ background:{c['header_bg']}; }}
            QFrame#sfBottom {{ background:{c['header_bg']}; }}
            QFrame#sfSep {{ background:{c['sep']}; border:none; }}
            QLabel {{ color:{c['text']}; }}
            QScrollArea {{ background:{c['bg']}; border:none; }}
            QFrame#photoCard {{
                background:{c['card_bg']};
                border:1px solid {c['border']};
                border-radius:6px;
            }}
            QFrame#docItem {{
                background:{c['card_bg']};
                border:1px solid {c['border']};
                border-radius:5px;
            }}
            QScrollBar:vertical {{
                background:{c['bg']}; width:8px; border:none;
            }}
            QScrollBar::handle:vertical {{
                background:{c['scroll']}; border-radius:4px; min-height:20px;
            }}
        """)
        self._apply_tab_style()
        self._apply_btn_style()

    def _apply_tab_style(self) -> None:
        c = self._c
        for btn in (self._btn_tab_photos, self._btn_tab_docs):
            if btn.isChecked():
                btn.setStyleSheet(
                    f"QPushButton{{background:{c['tab_active']};color:#fff;"
                    "border:none;border-radius:4px;padding:0 12px;font-size:12px;}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:{c['tab_inactive']};color:{c['text_muted']};"
                    "border:none;border-radius:4px;padding:0 12px;font-size:12px;}"
                    f"QPushButton:hover{{color:{c['text']};}}"
                )

    def _apply_btn_style(self) -> None:
        c = self._c
        self._btn_upload.setStyleSheet(
            "QPushButton{background:#4a6cf7;color:#fff;border:none;"
            "border-radius:8px;font-size:14px;font-weight:bold;letter-spacing:0.5px;}"
            "QPushButton:hover{background:#5a7cff;}"
            "QPushButton:disabled{background:#2d3a6f;color:#6e7ea0;}"
        )
        self._btn_refresh.setStyleSheet(
            f"QPushButton{{background:{c['tab_inactive']};color:{c['text']};"
            "border:none;border-radius:4px;font-size:16px;}"
            "QPushButton:hover{background:#3a3a5f;color:#fff;}"
        )

    # ── Lifecycle ────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._photos and self._tab_stack.currentIndex() == 0:
            self._refresh_photos()
