"""
vk_messages_panel.py — Панель сообщений VK-сообщества для MAX POST.

Поддерживает:
- Список диалогов с аватарами, превью и счётчиком непрочитанных
- Просмотр истории переписки с отображением вложений
- Отправку текста, фото и документов
- Long Poll для получения новых сообщений в реальном времени
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from PyQt6.QtCore import (
    QEvent, QObject, QRunnable, QSize, QThread, QTimer, QUrl,
    Qt, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QFontMetrics,
    QKeyEvent, QPainter, QPainterPath, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSizePolicy,
    QSpacerItem, QTextEdit, QVBoxLayout, QWidget,
)

from env_utils import get_env_path
from ui.widgets import SpellCheckTextEdit

_log = logging.getLogger(__name__)

_VK_API   = "https://api.vk.com/method"
_VK_VER   = "5.199"
_LP_WAIT  = 25      # секунд ожидания Long Poll
_LP_RETRY = 10      # секунд до переподключения при ошибке

# ─────────────────────────── helpers ────────────────────────────────────────

def _api(method: str, token: str, post: bool = False, **params) -> dict:
    """Обёртка над VK API. Поднимает RuntimeError при ошибке."""
    params.update({"access_token": token, "v": _VK_VER})
    url = f"{_VK_API}/{method}"
    try:
        if post:
            r = requests.post(url, data=params, timeout=30)
        else:
            r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise RuntimeError(f"Сеть: {e}") from e
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"VK {err.get('error_code','?')}: {err.get('error_msg','')}")
    return data.get("response", data)


def _fmt_time(ts: int) -> str:
    """Форматирует unix-timestamp: ЧЧ:ММ сегодня, «d Mon» — этот год, иначе дд.мм.гггг."""
    try:
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        months = ["янв","фев","мар","апр","май","июн",
                  "июл","авг","сен","окт","ноя","дек"]
        if dt.year == now.year:
            return f"{dt.day} {months[dt.month - 1]}"
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return ""


def _profile_name(profile: dict) -> str:
    """Возвращает имя из профиля пользователя или группы."""
    if "name" in profile:
        return profile["name"]
    fn = profile.get("first_name", "")
    ln = profile.get("last_name", "")
    return f"{fn} {ln}".strip() or "Неизвестно"


def _profile_initials(profile: dict) -> str:
    if "name" in profile:
        parts = profile["name"].split()
        return (parts[0][0] if parts else "?").upper()
    fn = profile.get("first_name", "")
    ln = profile.get("last_name", "")
    i1 = fn[0].upper() if fn else ""
    i2 = ln[0].upper() if ln else ""
    return (i1 + i2) or "?"


# ─────────────────────────── workers ────────────────────────────────────────

class _LongPollWorker(QThread):
    """Слушает Long Poll VK группы, эмитирует события."""
    message_new   = pyqtSignal(dict)   # объект сообщения из updates
    need_refresh  = pyqtSignal()       # обновить список диалогов

    def __init__(self, token: str, group_id: int, parent=None):
        super().__init__(parent)
        self._token    = token
        self._group_id = group_id
        self._stop     = False

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            try:
                lp = _api("groups.getLongPollServer",
                          self._token, group_id=self._group_id)
                server = lp["server"]
                key    = lp["key"]
                ts     = lp["ts"]

                while not self._stop:
                    try:
                        url = f"{server}?act=a_check&key={key}&ts={ts}&wait={_LP_WAIT}"
                        r = requests.get(url, timeout=_LP_WAIT + 5)
                        r.raise_for_status()
                        data = r.json()
                    except Exception as e:
                        _log.warning("LP network error: %s", e)
                        break

                    if "failed" in data:
                        code = data["failed"]
                        if code == 1:
                            ts = data["ts"]
                            continue
                        break  # 2 or 3 — need new server

                    ts = data.get("ts", ts)
                    for upd in data.get("updates", []):
                        if self._stop:
                            return
                        t = upd.get("type")
                        if t == "message_new":
                            obj = upd.get("object", {})
                            msg = obj.get("message", obj)
                            self.message_new.emit(msg)

            except Exception as e:
                _log.warning("LP outer error: %s", e)

            if not self._stop:
                # ждём перед переподключением
                for _ in range(_LP_RETRY * 10):
                    if self._stop:
                        return
                    time.sleep(0.1)


class _ConvWorker(QThread):
    """Загружает список диалогов."""
    done  = pyqtSignal(list, dict)
    error = pyqtSignal(str)

    def __init__(self, token: str, group_id: int, parent=None):
        super().__init__(parent)
        self._token    = token
        self._group_id = group_id
        self._stop     = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            data = _api(
                "messages.getConversations",
                self._token,
                count=30,
                filter="all",
                extended=1,
                fields="photo_50,first_name,last_name,screen_name,name",
                group_id=self._group_id,
            )
            if self._stop:
                return
            items    = data.get("items", [])
            profiles = {p["id"]: p for p in data.get("profiles", [])}
            profiles.update({-g["id"]: g for g in data.get("groups", [])})
            self.done.emit(items, profiles)
        except Exception as e:
            if not self._stop:
                self.error.emit(str(e))


class _HistoryWorker(QThread):
    """Загружает историю переписки."""
    done  = pyqtSignal(list, dict, int)
    error = pyqtSignal(str)

    def __init__(self, token: str, group_id: int, peer_id: int, parent=None):
        super().__init__(parent)
        self._token    = token
        self._group_id = group_id
        self._peer_id  = peer_id
        self._stop     = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            data = _api(
                "messages.getHistory",
                self._token,
                peer_id=self._peer_id,
                group_id=self._group_id,
                count=50,
                rev=0,
                extended=1,
                fields="photo_50,first_name,last_name,name",
            )
            if self._stop:
                return
            items    = data.get("items", [])
            profiles = {p["id"]: p for p in data.get("profiles", [])}
            profiles.update({-g["id"]: g for g in data.get("groups", [])})
            self.done.emit(items, profiles, self._group_id)
        except Exception as e:
            if not self._stop:
                self.error.emit(str(e))


class _MarkReadWorker(QThread):
    """Отмечает сообщения прочитанными — молча."""
    def __init__(self, token: str, group_id: int, peer_id: int, parent=None):
        super().__init__(parent)
        self._token    = token
        self._group_id = group_id
        self._peer_id  = peer_id

    def run(self):
        try:
            _api("messages.markAsRead", self._token,
                 peer_id=self._peer_id, group_id=self._group_id)
        except Exception:
            pass


class _SendWorker(QThread):
    """Отправляет сообщение (с возможными вложениями)."""
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, token: str, group_id: int, peer_id: int,
                 message: str, files: list[str], parent=None):
        super().__init__(parent)
        self._token    = token
        self._group_id = group_id
        self._peer_id  = peer_id
        self._message  = message
        self._files    = files  # list of local file paths
        self._stop     = False

    def stop(self):
        self._stop = True

    def _upload(self, fpath: str) -> str:
        """Загружает файл и возвращает строку вложения (photo123_456 / doc123_456)."""
        mime, _ = mimetypes.guess_type(fpath)
        is_photo = mime and mime.startswith("image/")

        if is_photo:
            srv = _api("photos.getMessagesUploadServer", self._token,
                       peer_id=self._peer_id)
            upload_url = srv["upload_url"]
            with open(fpath, "rb") as f:
                r = requests.post(upload_url, files={"photo": f}, timeout=60)
                r.raise_for_status()
                udata = r.json()
            saved = _api("photos.saveMessagesPhoto", self._token, post=True,
                         server=udata["server"],
                         photo=udata["photo"],
                         hash=udata["hash"])
            p = saved[0]
            return f"photo{p['owner_id']}_{p['id']}"
        else:
            srv = _api("docs.getMessagesUploadServer", self._token,
                       peer_id=self._peer_id, type="doc")
            upload_url = srv["upload_url"]
            fname = Path(fpath).name
            with open(fpath, "rb") as f:
                r = requests.post(upload_url, files={"file": (fname, f)}, timeout=60)
                r.raise_for_status()
                udata = r.json()
            saved = _api("docs.save", self._token, post=True,
                         file=udata["file"],
                         title=fname)
            d = saved.get("doc", saved)
            return f"doc{d['owner_id']}_{d['id']}"

    def run(self):
        try:
            attachments = []
            for fpath in self._files:
                if self._stop:
                    return
                att_str = self._upload(fpath)
                attachments.append(att_str)

            if self._stop:
                return

            params = dict(
                peer_id   = self._peer_id,
                group_id  = self._group_id,
                random_id = random.randint(0, 2**31),
            )
            if self._message.strip():
                params["message"] = self._message
            if attachments:
                params["attachment"] = ",".join(attachments)

            result = _api("messages.send", self._token, post=True, **params)
            if not self._stop:
                self.done.emit({"message_id": result})
        except Exception as e:
            if not self._stop:
                self.error.emit(str(e))


class _ImageLoader(QThread):
    """Асинхронно загружает изображение по URL."""
    loaded = pyqtSignal(str, QPixmap)   # url, pixmap

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url  = url
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not self._url or self._stop:
            return
        try:
            r = requests.get(self._url, timeout=15)
            r.raise_for_status()
            px = QPixmap()
            px.loadFromData(r.content)
            if not self._stop and not px.isNull():
                self.loaded.emit(self._url, px)
        except Exception:
            pass


# ─────────────────────────── widgets ────────────────────────────────────────

class _AvatarLabel(QLabel):
    """Круглый аватар с асинхронной загрузкой и fallback-инициалами."""

    _COLORS = [
        "#4a6cf7", "#e91e8c", "#21d07a", "#ff6b35",
        "#9c27b0", "#00bcd4", "#ff5722", "#607d8b",
    ]

    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self._sz      = size
        self._initials = "?"
        self._color    = self._COLORS[0]
        self._pixmap_raw: Optional[QPixmap] = None
        self._loader: Optional[_ImageLoader] = None
        self.setFixedSize(size, size)

    def set_profile(self, profile: dict):
        self._initials = _profile_initials(profile)
        name = _profile_name(profile)
        idx  = abs(hash(name)) % len(self._COLORS)
        self._color    = self._COLORS[idx]
        self._pixmap_raw = None
        self.update()

        url = profile.get("photo_50", "")
        if url:
            self._start_load(url)

    def _start_load(self, url: str):
        if self._loader:
            self._loader.stop()
            self._loader.quit()
            self._loader.wait(500)
            self._loader.deleteLater()
        self._loader = _ImageLoader(url, parent=self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.finished.connect(lambda: setattr(self, "_loader", None))
        self._loader.start()

    def _on_loaded(self, _url: str, px: QPixmap):
        self._pixmap_raw = px
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, self._sz, self._sz)
        painter.setClipPath(path)

        if self._pixmap_raw and not self._pixmap_raw.isNull():
            scaled = self._pixmap_raw.scaled(
                self._sz, self._sz,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self._sz - scaled.width())  // 2
            y = (self._sz - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.fillPath(path, QColor(self._color))
            painter.setPen(QColor("#ffffff"))
            font = QFont()
            font.setPixelSize(int(self._sz * 0.38))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(0, 0, self._sz, self._sz,
                             Qt.AlignmentFlag.AlignCenter, self._initials)
        painter.end()


class _UnreadBadge(QLabel):
    """Красный бейдж с числом непрочитанных."""
    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self._count = count
        self._update()

    def set_count(self, count: int):
        self._count = count
        self._update()

    @property
    def count(self) -> int:
        return self._count

    def _update(self):
        if self._count > 0:
            text = str(self._count) if self._count < 100 else "99+"
            self.setText(text)
            self.setVisible(True)
            self.setStyleSheet("""
                background: #e91e4c;
                color: white;
                border-radius: 9px;
                font-size: 10px;
                font-weight: bold;
                padding: 1px 5px;
                min-width: 18px;
                min-height: 18px;
            """)
            self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.setVisible(False)


class _ConvItem(QWidget):
    """Элемент списка диалогов."""
    clicked = pyqtSignal(int)  # peer_id

    def __init__(self, peer_id: int, profile: dict,
                 last_msg: str, ts: int, unread: int, parent=None):
        super().__init__(parent)
        self._peer_id  = peer_id
        self._selected = False

        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_style(False)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        self._avatar = _AvatarLabel(42)
        self._avatar.set_profile(profile)
        lay.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        center = QVBoxLayout()
        center.setSpacing(2)
        center.setContentsMargins(0, 0, 0, 0)

        name_row = QHBoxLayout()
        name_row.setSpacing(4)
        name_row.setContentsMargins(0, 0, 0, 0)

        self._name_lbl = QLabel(_profile_name(profile))
        self._name_lbl.setStyleSheet("color:#e0e0f0; font-weight:600; font-size:13px;")
        name_row.addWidget(self._name_lbl)
        name_row.addStretch()

        self._time_lbl = QLabel(_fmt_time(ts))
        self._time_lbl.setStyleSheet("color:#6e6e8e; font-size:11px;")
        name_row.addWidget(self._time_lbl, 0, Qt.AlignmentFlag.AlignRight)
        center.addLayout(name_row)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(4)
        preview_row.setContentsMargins(0, 0, 0, 0)

        self._preview = QLabel(last_msg)
        self._preview.setStyleSheet("color:#7070a0; font-size:12px;")
        self._preview.setMaximumWidth(220)
        fm = QFontMetrics(self._preview.font())
        elided = fm.elidedText(last_msg, Qt.TextElideMode.ElideRight, 210)
        self._preview.setText(elided)
        preview_row.addWidget(self._preview)
        preview_row.addStretch()

        self._badge = _UnreadBadge(unread)
        preview_row.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignRight)
        center.addLayout(preview_row)

        lay.addLayout(center, 1)

    def _apply_style(self, selected: bool):
        if selected:
            self.setStyleSheet("""
                _ConvItem, QWidget { background: #252540; border-radius: 8px; }
            """)
        else:
            self.setStyleSheet("""
                _ConvItem, QWidget { background: transparent; border-radius: 8px; }
                _ConvItem:hover, QWidget:hover { background: #1e1e38; }
            """)

    def set_selected(self, selected: bool):
        self._selected = selected
        bg = "#252540" if selected else "transparent"
        self.setStyleSheet(f"background: {bg}; border-radius: 8px;")

    def update_unread(self, count: int):
        self._badge.set_count(count)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._peer_id)
        super().mousePressEvent(event)


class _AttachmentWidget(QWidget):
    """Отображает одно вложение внутри пузыря сообщения."""
    def __init__(self, att: dict, outgoing: bool, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(2)

        t = att.get("type", "")

        if t == "photo":
            self._photo_lbl = QLabel("[Фото]")
            self._photo_lbl.setStyleSheet("color:#9090c0; font-size:12px;")
            lay.addWidget(self._photo_lbl)
            sizes = att["photo"].get("sizes", [])
            if sizes:
                url = sizes[-1].get("url", "")
                if url:
                    self._start_load(url)

        elif t == "doc":
            doc  = att["doc"]
            url  = doc.get("url", "")
            name = doc.get("title", "Документ")
            lbl  = QLabel(f'<a href="{url}" style="color:#7ab4ff;">📎 {name}</a>')
            lbl.setOpenExternalLinks(False)
            lbl.linkActivated.connect(lambda href: QDesktopServices.openUrl(QUrl(href)))
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        elif t == "video":
            lbl = QLabel("🎬 Видео")
            lbl.setStyleSheet("color:#9090c0; font-size:12px;")
            lay.addWidget(lbl)

        elif t == "audio":
            lbl = QLabel("🎵 Аудио")
            lbl.setStyleSheet("color:#9090c0; font-size:12px;")
            lay.addWidget(lbl)

        elif t == "sticker":
            lbl = QLabel("🖼 Стикер")
            lbl.setStyleSheet("color:#9090c0; font-size:12px;")
            lay.addWidget(lbl)

        elif t == "link":
            link = att.get("link", {})
            href = link.get("url", "")
            title = link.get("title", href)
            lbl = QLabel(f'<a href="{href}" style="color:#7ab4ff;">🔗 {title}</a>')
            lbl.setOpenExternalLinks(True)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        else:
            lbl = QLabel(f"[{t}]")
            lbl.setStyleSheet("color:#9090c0; font-size:12px;")
            lay.addWidget(lbl)

    def _start_load(self, url: str):
        self._loader = _ImageLoader(url, parent=self)
        self._loader.loaded.connect(self._on_photo)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_photo(self, _url: str, px: QPixmap):
        scaled = px.scaled(240, 160,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._photo_lbl.setPixmap(scaled)
        self._photo_lbl.setFixedSize(scaled.size())


class _MsgBubble(QWidget):
    """Пузырь одного сообщения."""

    def __init__(self, msg: dict, profiles: dict, group_id: int, parent=None):
        super().__init__(parent)
        from_id   = msg.get("from_id", 0)
        group_neg = -abs(group_id)
        outgoing  = (from_id == group_neg) or bool(msg.get("out", 0))

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 2, 8, 2)
        outer.setSpacing(6)

        if outgoing:
            outer.addStretch()

        # Аватар входящего
        if not outgoing:
            profile = profiles.get(from_id, {})
            av = _AvatarLabel(30)
            av.set_profile(profile)
            outer.addWidget(av, 0, Qt.AlignmentFlag.AlignBottom)

        # Контент
        bubble_w = QWidget()
        bubble_lay = QVBoxLayout(bubble_w)
        bubble_lay.setContentsMargins(10, 7, 10, 7)
        bubble_lay.setSpacing(3)

        bg_color = "#3a4cf7" if outgoing else "#2d2d3f"
        bubble_w.setStyleSheet(f"""
            QWidget {{
                background: {bg_color};
                border-radius: 12px;
            }}
        """)
        bubble_w.setMaximumWidth(480)

        # Имя отправителя (для входящих)
        if not outgoing:
            profile = profiles.get(from_id, {})
            name = _profile_name(profile)
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("color:#4a9cf7; font-size:11px; font-weight:600;")
            bubble_lay.addWidget(name_lbl)

        # Текст
        text = msg.get("text", "").strip()
        if text:
            txt_lbl = QLabel(text)
            txt_lbl.setWordWrap(True)
            txt_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            txt_lbl.setStyleSheet("color:#e0e0f0; font-size:13px;")
            txt_lbl.setMaximumWidth(460)
            bubble_lay.addWidget(txt_lbl)

        # Вложения
        for att in msg.get("attachments", []):
            aw = _AttachmentWidget(att, outgoing)
            bubble_lay.addWidget(aw)

        # Пересланные сообщения (краткое упоминание)
        fwd = msg.get("fwd_messages", [])
        if fwd:
            fwd_lbl = QLabel(f"↩ {len(fwd)} пересл. сообщ.")
            fwd_lbl.setStyleSheet("color:#7080a0; font-size:11px; font-style:italic;")
            bubble_lay.addWidget(fwd_lbl)

        # Время
        ts   = msg.get("date", 0)
        time_lbl = QLabel(_fmt_time(ts))
        time_lbl.setStyleSheet("color:#8888b0; font-size:10px;")
        time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight if outgoing else Qt.AlignmentFlag.AlignLeft
        )
        bubble_lay.addWidget(time_lbl)

        outer.addWidget(bubble_w)
        if not outgoing:
            outer.addStretch()


# ─────────────────────────── ConvListPanel ───────────────────────────────────

class _ConvListPanel(QWidget):
    """Левая панель: список диалогов."""
    conv_selected = pyqtSignal(int, str)   # peer_id, name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(310)
        self.setStyleSheet("background: #16162a;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Заголовок ──
        header = QWidget()
        header.setFixedHeight(54)
        header.setStyleSheet("background: #1e1e2e; border-bottom: 1px solid #2d2d4f;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(14, 0, 10, 0)

        title = QLabel("Сообщения")
        title.setStyleSheet("color:#e0e0f0; font-size:15px; font-weight:700;")
        h_lay.addWidget(title)
        h_lay.addStretch()

        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedSize(32, 32)
        self._refresh_btn.setStyleSheet("""
            QPushButton {
                background: #2d2d4f; color: #8888c0;
                border-radius: 6px; font-size: 16px;
            }
            QPushButton:hover { background: #3a3a5f; color: #e0e0f0; }
        """)
        h_lay.addWidget(self._refresh_btn)
        root.addWidget(header)

        # ── Строка состояния ──
        self._status_lbl = QLabel("Загрузка…")
        self._status_lbl.setFixedHeight(26)
        self._status_lbl.setStyleSheet(
            "color:#6e6e9e; font-size:11px; padding-left:14px;"
        )
        root.addWidget(self._status_lbl)

        # ── Прокручиваемый список ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                width: 4px; background: transparent;
            }
            QScrollBar::handle:vertical { background: #3d3d5f; border-radius: 2px; }
        """)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(self._list_widget)
        self._list_lay.setContentsMargins(6, 6, 6, 6)
        self._list_lay.setSpacing(2)
        self._list_lay.addStretch()

        scroll.setWidget(self._list_widget)
        root.addWidget(scroll, 1)

        self._items: dict[int, _ConvItem] = {}   # peer_id → widget
        self._current_peer: Optional[int] = None

    def set_status(self, text: str):
        self._status_lbl.setText(text)

    def load_conversations(self, items: list, profiles: dict):
        # Удалить старые виджеты
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._items.clear()

        total_unread = 0
        for it in items:
            conv   = it.get("conversation", it)
            peer   = conv.get("peer", {})
            peer_id = peer.get("id", 0)
            if not peer_id:
                continue

            unread = conv.get("unread_count", 0)
            total_unread += unread

            last_msg = it.get("last_message", {})
            text  = last_msg.get("text", "") or "[вложение]"
            ts    = last_msg.get("date", 0)

            profile = profiles.get(peer_id, {})
            if not profile:
                profile = {"first_name": f"ID {peer_id}"}

            w = _ConvItem(peer_id, profile, text, ts, unread)
            w.clicked.connect(self._on_item_clicked)
            # Вставить перед stretch (последний элемент)
            self._list_lay.insertWidget(self._list_lay.count() - 1, w)
            self._items[peer_id] = w

        count = len(items)
        self.set_status(f"{count} диалог{'ов' if count != 1 else ''}")

        if self._current_peer and self._current_peer in self._items:
            self._items[self._current_peer].set_selected(True)

    def _on_item_clicked(self, peer_id: int):
        if self._current_peer and self._current_peer in self._items:
            self._items[self._current_peer].set_selected(False)
        self._current_peer = peer_id
        if peer_id in self._items:
            self._items[peer_id].set_selected(True)
        # Определить имя
        name = "Диалог"
        if peer_id in self._items:
            w = self._items[peer_id]
            name = w._name_lbl.text()
        self.conv_selected.emit(peer_id, name)

    def clear_unread(self, peer_id: int):
        if peer_id in self._items:
            self._items[peer_id].update_unread(0)

    def total_unread(self) -> int:
        return sum(w._badge.count for w in self._items.values())


# ─────────────────────────── ChatView ────────────────────────────────────────

class _InputFilter(QObject):
    """Перехватывает Ctrl+Enter в QTextEdit."""
    send_triggered = pyqtSignal()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            ke = event  # type: QKeyEvent
            if (ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.send_triggered.emit()
                return True
        return False


class _ChatView(QWidget):
    """Правая панель: история переписки + поле ввода."""
    send_requested = pyqtSignal(str, list)   # text, [file_paths]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._peer_id   = 0
        self._peer_name = ""
        self._pending_files: list[str] = []

        self.setStyleSheet("background: #1e1e2e;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Заголовок чата ──
        self._header = QWidget()
        self._header.setFixedHeight(54)
        self._header.setStyleSheet(
            "background: #1e1e2e; border-bottom: 1px solid #2d2d4f;"
        )
        h_lay = QHBoxLayout(self._header)
        h_lay.setContentsMargins(16, 0, 16, 0)

        self._chat_name_lbl = QLabel("Выберите диалог")
        self._chat_name_lbl.setStyleSheet(
            "color:#e0e0f0; font-size:15px; font-weight:700;"
        )
        h_lay.addWidget(self._chat_name_lbl)
        h_lay.addStretch()
        root.addWidget(self._header)

        # ── Статус загрузки ──
        self._load_status = QLabel("")
        self._load_status.setFixedHeight(22)
        self._load_status.setStyleSheet(
            "color:#6e6e9e; font-size:11px; padding-left:16px;"
        )
        root.addWidget(self._load_status)

        # ── Прокручиваемые сообщения ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("""
            QScrollArea { border: none; background: #1e1e2e; }
            QScrollBar:vertical {
                width: 5px; background: transparent;
            }
            QScrollBar::handle:vertical { background: #3d3d5f; border-radius: 2px; }
        """)

        self._msg_widget = QWidget()
        self._msg_widget.setStyleSheet("background: #1e1e2e;")
        self._msg_lay = QVBoxLayout(self._msg_widget)
        self._msg_lay.setContentsMargins(0, 10, 0, 10)
        self._msg_lay.setSpacing(4)
        self._msg_lay.addStretch()

        self._scroll.setWidget(self._msg_widget)
        root.addWidget(self._scroll, 1)

        # ── Панель ожидающих вложений ──
        self._att_panel = QWidget()
        self._att_panel.setVisible(False)
        self._att_panel.setFixedHeight(32)
        self._att_panel.setStyleSheet("background: #252540; border-top: 1px solid #3a3a5f;")
        att_lay = QHBoxLayout(self._att_panel)
        att_lay.setContentsMargins(12, 0, 8, 0)
        att_lay.setSpacing(6)

        self._att_lbl = QLabel()
        self._att_lbl.setStyleSheet("color:#8888c0; font-size:12px;")
        att_lay.addWidget(self._att_lbl)
        att_lay.addStretch()

        self._att_clear_btn = QPushButton("✕ Очистить")
        self._att_clear_btn.setFixedHeight(22)
        self._att_clear_btn.setStyleSheet("""
            QPushButton { background:#3a2a2a; color:#c07070;
                          border-radius:4px; font-size:11px; padding:0 8px; }
            QPushButton:hover { background:#4a3030; }
        """)
        self._att_clear_btn.clicked.connect(self._clear_attachments)
        att_lay.addWidget(self._att_clear_btn)
        root.addWidget(self._att_panel)

        # ── Область ввода ──
        input_container = QWidget()
        input_container.setStyleSheet(
            "background: #1e1e2e; border-top: 1px solid #2d2d4f;"
        )
        input_lay = QVBoxLayout(input_container)
        input_lay.setContentsMargins(12, 8, 12, 8)
        input_lay.setSpacing(6)

        self._input = SpellCheckTextEdit()
        self._input.setPlaceholderText("Написать сообщение… (Ctrl+Enter — отправить)")
        self._input.setFixedHeight(70)
        self._input.setStyleSheet("""
            QTextEdit {
                background: #2d2d3f;
                color: #e0e0f0;
                border: 1px solid #3d3d5f;
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QTextEdit:focus { border-color: #4a6cf7; }
        """)
        self._input_filter = _InputFilter()
        self._input_filter.send_triggered.connect(self._on_send)
        self._input.installEventFilter(self._input_filter)
        input_lay.addWidget(self._input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._photo_btn = self._mk_btn("🖼", "Прикрепить фото")
        self._photo_btn.clicked.connect(self._attach_photo)
        btn_row.addWidget(self._photo_btn)

        self._doc_btn = self._mk_btn("📎", "Прикрепить документ")
        self._doc_btn.clicked.connect(self._attach_doc)
        btn_row.addWidget(self._doc_btn)

        btn_row.addStretch()

        self._send_btn = QPushButton("Отправить")
        self._send_btn.setFixedHeight(34)
        self._send_btn.setStyleSheet("""
            QPushButton {
                background: #4a6cf7; color: white;
                border-radius: 7px; font-size: 13px;
                font-weight: 600; padding: 0 18px;
            }
            QPushButton:hover { background: #5a7cff; }
            QPushButton:disabled { background: #2d2d5f; color: #6060a0; }
        """)
        self._send_btn.clicked.connect(self._on_send)
        btn_row.addWidget(self._send_btn)

        input_lay.addLayout(btn_row)
        root.addWidget(input_container)

        self._set_input_enabled(False)

    @staticmethod
    def _mk_btn(icon: str, tip: str) -> QPushButton:
        b = QPushButton(icon)
        b.setFixedSize(34, 34)
        b.setToolTip(tip)
        b.setStyleSheet("""
            QPushButton {
                background: #2d2d4f; color: #8888c0;
                border-radius: 7px; font-size: 16px;
            }
            QPushButton:hover { background: #3a3a5f; color: #e0e0f0; }
            QPushButton:disabled { background: #1e1e38; color: #404060; }
        """)
        return b

    def _set_input_enabled(self, enabled: bool):
        self._input.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)
        self._photo_btn.setEnabled(enabled)
        self._doc_btn.setEnabled(enabled)

    def set_peer(self, peer_id: int, name: str):
        self._peer_id   = peer_id
        self._peer_name = name
        self._chat_name_lbl.setText(name)
        self._set_input_enabled(True)
        self._clear_messages()

    def _clear_messages(self):
        while self._msg_lay.count() > 1:
            item = self._msg_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_load_status(self, text: str):
        self._load_status.setText(text)

    def load_messages(self, items: list, profiles: dict, group_id: int):
        self._clear_messages()
        self._load_status.setText("")
        # items из getHistory приходят от старых к новым при rev=0
        for msg in reversed(items):
            bubble = _MsgBubble(msg, profiles, group_id)
            self._msg_lay.insertWidget(self._msg_lay.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def add_message(self, msg: dict, profiles: dict, group_id: int):
        bubble = _MsgBubble(msg, profiles, group_id)
        self._msg_lay.insertWidget(self._msg_lay.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _attach_photo(self):
        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выбрать фото", "",
            "Изображения (*.png *.jpg *.jpeg *.gif *.webp *.bmp)"
        )
        if paths:
            self._pending_files.extend(paths)
            self._update_att_panel()

    def _attach_doc(self):
        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выбрать документ", "", "Все файлы (*)"
        )
        if paths:
            self._pending_files.extend(paths)
            self._update_att_panel()

    def _update_att_panel(self):
        n = len(self._pending_files)
        if n > 0:
            word = "файл" if n == 1 else ("файла" if n < 5 else "файлов")
            self._att_lbl.setText(f"📎 {n} {word} прикреплено")
            self._att_panel.setVisible(True)
        else:
            self._att_panel.setVisible(False)

    def _clear_attachments(self):
        self._pending_files.clear()
        self._update_att_panel()

    def _on_send(self):
        text  = self._input.toPlainText().strip()
        files = list(self._pending_files)
        if not text and not files:
            return
        self._input.clear()
        self._pending_files.clear()
        self._update_att_panel()
        self.send_requested.emit(text, files)


# ─────────────────────────── VkMessagesPanel ─────────────────────────────────

class VkMessagesPanel(QWidget):
    """Главная панель сообщений VK-сообщества."""
    unread_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._token     = ""
        self._group_id  = 0
        self._creds_ok  = False

        self._conv_worker:    Optional[_ConvWorker]    = None
        self._history_worker: Optional[_HistoryWorker] = None
        self._send_worker:    Optional[_SendWorker]    = None
        self._lp_worker:      Optional[_LongPollWorker] = None

        self._current_peer_id = 0
        self._current_profiles: dict = {}

        self._setup_ui()
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload_history_for_current)
        QApplication.instance().aboutToQuit.connect(self._stop_all_workers)

    # ── UI ──────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet("background: #16162a;")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Разделитель
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #2d2d4f;")

        self._conv_panel = _ConvListPanel()
        self._conv_panel._refresh_btn.clicked.connect(self._load_conversations)
        self._conv_panel.conv_selected.connect(self._on_conv_selected)

        self._chat_view = _ChatView()
        self._chat_view.send_requested.connect(self._on_send_requested)

        root.addWidget(self._conv_panel)
        root.addWidget(sep)
        root.addWidget(self._chat_view, 1)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        prev_token    = self._token
        prev_group_id = self._group_id
        self._load_credentials()
        if self._creds_ok:
            creds_changed = (self._token != prev_token or self._group_id != prev_group_id)
            first_show    = not self._conv_panel._items
            if creds_changed or first_show:
                self._load_conversations()
            self._start_longpoll()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._stop_longpoll()

    def closeEvent(self, event):
        self._stop_all_workers()
        super().closeEvent(event)

    # ── Credentials ──────────────────────────────────────────────────────────

    def _load_credentials(self):
        env_path = get_env_path()
        try:
            mtime = env_path.stat().st_mtime
        except OSError:
            mtime = 0
        if mtime != getattr(self, "_env_mtime", None):
            load_dotenv(env_path, override=True)
            self._env_mtime = mtime
        token    = os.environ.get("VK_GROUP_TOKEN", "").strip()
        group_id = os.environ.get("VK_GROUP_ID",    "").strip()

        if not token or not group_id:
            self._token    = ""
            self._group_id = 0
            self._creds_ok = False
            self._conv_panel.set_status("Нет данных VK в .env")
            return

        try:
            self._group_id = int(group_id)
        except ValueError:
            self._creds_ok = False
            self._conv_panel.set_status("VK_GROUP_ID должен быть числом")
            return

        self._token    = token
        self._creds_ok = True

    # ── Conversations ────────────────────────────────────────────────────────

    def _load_conversations(self):
        if not self._creds_ok:
            return
        self._stop_worker(self._conv_worker)
        self._conv_panel.set_status("Загрузка…")
        _w = _ConvWorker(self._token, self._group_id)
        self._conv_worker = _w
        _w.done.connect(self._on_conv_loaded)
        _w.error.connect(self._on_conv_error)
        _w.finished.connect(_w.deleteLater)
        _w.finished.connect(lambda *_, w=_w: self._conv_worker is w and setattr(self, "_conv_worker", None))
        _w.start()

    def _on_conv_loaded(self, items: list, profiles: dict):
        self._conv_panel.load_conversations(items, profiles)
        total = self._conv_panel.total_unread()
        self.unread_changed.emit(total)

    def _on_conv_error(self, msg: str):
        self._conv_panel.set_status(f"Ошибка: {msg}")

    # ── History ──────────────────────────────────────────────────────────────

    def _on_conv_selected(self, peer_id: int, name: str):
        self._current_peer_id = peer_id
        self._chat_view.set_peer(peer_id, name)
        self._chat_view.set_load_status("Загрузка сообщений…")
        self._load_history(peer_id)
        self._conv_panel.clear_unread(peer_id)

    def _load_history(self, peer_id: int):
        self._stop_worker(self._history_worker)
        _w = _HistoryWorker(self._token, self._group_id, peer_id)
        self._history_worker = _w
        _w.done.connect(self._on_history_loaded)
        _w.error.connect(self._on_history_error)
        _w.finished.connect(_w.deleteLater)
        _w.finished.connect(lambda *_, w=_w: self._history_worker is w and setattr(self, "_history_worker", None))
        _w.start()

    def _on_history_loaded(self, items: list, profiles: dict, group_id: int):
        self._current_profiles = profiles
        self._chat_view.load_messages(items, profiles, group_id)
        # Отметить прочитанными
        if self._current_peer_id:
            w = _MarkReadWorker(self._token, self._group_id, self._current_peer_id, parent=self)
            w.finished.connect(w.deleteLater)
            w.start()

    def _on_history_error(self, msg: str):
        self._chat_view.set_load_status(f"Ошибка: {msg}")

    def _reload_history_for_current(self):
        if self._current_peer_id:
            self._load_history(self._current_peer_id)

    # ── Send ─────────────────────────────────────────────────────────────────

    def _on_send_requested(self, text: str, files: list[str]):
        if not self._creds_ok or not self._current_peer_id:
            return
        self._chat_view._send_btn.setEnabled(False)
        self._stop_worker(self._send_worker)
        _w = _SendWorker(
            self._token, self._group_id,
            self._current_peer_id, text, files
        )
        self._send_worker = _w
        _w.done.connect(self._on_sent)
        _w.error.connect(self._on_send_error)
        _w.finished.connect(_w.deleteLater)
        _w.finished.connect(lambda *_, w=_w: self._send_worker is w and setattr(self, "_send_worker", None))
        _w.start()

    def _on_sent(self, _result: dict):
        self._chat_view._send_btn.setEnabled(True)
        # Перезагрузить историю через 1с (Long Poll может пропустить собственное сообщение)
        self._reload_timer.start(1000)

    def _on_send_error(self, msg: str):
        self._chat_view._send_btn.setEnabled(True)
        self._chat_view.set_load_status(f"Ошибка отправки: {msg}")

    # ── Long Poll ────────────────────────────────────────────────────────────

    def _start_longpoll(self):
        if not self._creds_ok:
            return
        self._stop_longpoll()
        self._lp_worker = _LongPollWorker(self._token, self._group_id)
        self._lp_worker.message_new.connect(self._on_lp_message)
        self._lp_worker.start()

    def _stop_longpoll(self):
        if self._lp_worker:
            w = self._lp_worker
            w.stop()
            w.finished.connect(w.deleteLater)
            w.quit()
            self._lp_worker = None

    def _on_lp_message(self, msg: dict):
        peer_id = msg.get("peer_id", 0)
        if peer_id == self._current_peer_id and peer_id != 0:
            # Добавить пузырь в открытый чат
            from_id  = msg.get("from_id", 0)
            profiles = dict(self._current_profiles)
            # Добавляем fallback-профиль без блокирующего API-вызова;
            # реальные данные появятся при следующей перезагрузке истории
            if from_id and from_id not in profiles:
                if from_id > 0:
                    profiles[from_id] = {"id": from_id, "first_name": f"ID {from_id}"}
                else:
                    profiles[from_id] = {"id": from_id, "name": f"Group {abs(from_id)}"}
            self._current_profiles = profiles
            self._chat_view.add_message(msg, profiles, self._group_id)
            # Отметить прочитанным
            w = _MarkReadWorker(self._token, self._group_id, peer_id, parent=self)
            w.finished.connect(w.deleteLater)
            w.start()
        else:
            # Обновить список диалогов
            self._load_conversations()

    # ── Cleanup ──────────────────────────────────────────────────────────────

    @staticmethod
    def _stop_worker(worker: Optional[QThread]):
        if worker and worker.isRunning():
            if hasattr(worker, "stop"):
                worker.stop()
            worker.quit()
            worker.wait(2000)

    def _stop_all_workers(self):
        self._reload_timer.stop()
        self._stop_longpoll()
        self._stop_worker(self._conv_worker)
        self._stop_worker(self._history_worker)
        self._stop_worker(self._send_worker)


