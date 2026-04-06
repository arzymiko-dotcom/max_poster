import atexit
import collections
import csv
import json
import logging
import os
import re
from dataclasses import dataclass, field as dc_field
import random
import sys
import time
import uuid
import winsound
from datetime import datetime
from pathlib import Path

import requests

from PyQt6.QtCore import QDateTime, QSize, QTime, QTimer, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

# Роль для пометки вручную добавленных адресов
_MANUAL_ROLE: int = Qt.ItemDataRole.UserRole + 1
# Роль для хранения MatchResult в строках лога отправки (для повтора)
_LOG_MATCH_ROLE: int = Qt.ItemDataRole.UserRole + 2

_TOKEN_ROTATION_DAYS = 10  # напоминание сменить токены раз в N дней

import tg_notify
from address_parser import extract_all_addresses
from excel_matcher import ExcelMatcher, MatchResult
import history_manager
import template_manager
from max_sender import MaxSender, SendResult
from state_manager import StateManager
from updater import check_for_updates
from vk_sender import VkSender

from ui.paths import _assets_dir, _fonts_dir
from ui.widgets import LineNumberedEdit, _GripSplitter, _NumberedItemDelegate
from ui.emoji_picker import EmojiPicker
from ui.background import _BgWidget
from ui.animations import SuccessOverlay
from ui.preview_card import PreviewCard
from ui.dialogs import ThemePickerDialog, FontPickerDialog, AddAddressDialog, PasteAddressesDialog, VkEditDialog
from ui.styles import get_stylesheet
from constants import (
    PARSE_DEBOUNCE_MS,
    SAVE_DEBOUNCE_MS,
    UPDATE_CHECK_DELAY_MS,
    TEXT_CHAR_LIMIT,
    VK_WALL_TEXT_LIMIT,
)

_log = logging.getLogger(__name__)


class _ConnCheckWorker(QThread):
    """Проверяет соединение с GREEN-API в фоне, чтобы не блокировать UI."""
    done = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, max_sender, parent=None) -> None:
        super().__init__(parent)
        self._sender = max_sender

    def run(self) -> None:
        result = self._sender.open_max_for_login()
        self.done.emit(result.success, result.message)


class _VkTokenCheckWorker(QThread):
    """Проверяет валидность VK_USER_TOKEN в фоне (не блокирует UI)."""
    result = pyqtSignal(bool, str)  # (valid, error_message)

    def __init__(self, token: str, parent=None) -> None:
        super().__init__(parent)
        self._token = token

    def run(self) -> None:
        try:
            resp = requests.post(
                "https://api.vk.com/method/users.get",
                data={"access_token": self._token, "v": "5.199"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                code = data["error"].get("error_code", 0)
                msg  = data["error"].get("error_msg", "неизвестная ошибка")
                self.result.emit(False, f"[{code}] {msg}")
            else:
                self.result.emit(True, "")
        except Exception as exc:
            self.result.emit(False, str(exc))


class _ExcelWarmupWorker(QThread):
    """Прогревает кэш ExcelMatcher в фоне при старте — чтобы первый поиск/парсинг был мгновенным."""
    def __init__(self, matcher: "ExcelMatcher", parent=None) -> None:
        super().__init__(parent)
        self._matcher = matcher

    def run(self) -> None:
        try:
            self._matcher.load_dataframe()
        except Exception as exc:
            _log.warning("Excel preload failed: %s", exc)


class _AddrSearchWorker(QThread):
    """Ищет адреса в ExcelMatcher в фоне — не блокирует UI при вводе текста."""
    results_ready = pyqtSignal(list)  # list[MatchResult]

    def __init__(self, query: str, matcher: "ExcelMatcher", parent=None) -> None:
        super().__init__(parent)
        self._query = query
        self._matcher = matcher

    def run(self) -> None:
        try:
            results = self._matcher.search(self._query)
        except Exception as exc:
            _log.warning("addr search failed: %s", exc)
            results = []
        self.results_ready.emit(results)


class _AddressCheckWorker(QThread):
    """Парсит адреса и ищет совпадения в Excel в фоне — не блокирует UI."""
    done = pyqtSignal(list, dict)  # (new_items: list[MatchResult], line_marks: dict[int,bool])

    def __init__(self, text: str, matcher: "ExcelMatcher", parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._matcher = matcher
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        parsed_list = extract_all_addresses(self._text)
        if not parsed_list or self._cancelled:
            self.done.emit([], {})
            return

        new_items: list[MatchResult] = []
        seen_ids: set[str] = set()
        line_marks: dict[int, bool] = {}

        for parsed in parsed_list:
            if self._cancelled:
                self.done.emit([], {})
                return
            idx = parsed.line_idx
            try:
                matches = self._matcher.find_matches(parsed)
            except Exception as exc:
                _log.warning("find_matches failed for %r: %s", parsed, exc)
                continue
            found = bool(matches)
            if idx is not None:
                if idx not in line_marks:
                    line_marks[idx] = found
                elif not found:
                    line_marks[idx] = False
            if not matches:
                continue
            best = matches[0]
            if best.chat_id and best.chat_id in seen_ids:
                continue
            if best.chat_id:
                seen_ids.add(best.chat_id)
            new_items.append(best)

        self.done.emit(new_items, line_marks)


class SendWorker(QThread):
    result_ready = pyqtSignal(bool, str)
    progress = pyqtSignal(str)
    progress_step = pyqtSignal(int, int)   # (current, total)
    address_result = pyqtSignal(int, bool, str)  # (idx, success, message)

    def __init__(
        self,
        max_sender: MaxSender,
        vk_sender: VkSender,
        chat_ids: list,
        text: str,
        image_path: "str | None",
        send_max: bool,
        send_vk: bool,
        dry_run: bool = False,
        extra_delay: int = 0,
        delay_min: int = 5,
        delay_max: int = 12,
    ) -> None:
        super().__init__()
        self.max_sender = max_sender
        self.vk_sender = vk_sender
        self.chat_ids = chat_ids
        self.text = text
        self.image_path = image_path
        self.send_max = send_max
        self.send_vk = send_vk
        self.dry_run = dry_run
        self.extra_delay = extra_delay
        self.delay_min = delay_min
        self.delay_max = delay_max
        self._cancelled = False
        self.vk_post_id: int | None = None  # заполняется в run() при успешной отправке в ВК

    def cancel(self) -> None:
        """Запрашивает отмену — поток остановится после текущей отправки."""
        self._cancelled = True

    def run(self) -> None:
        lines: list[str] = []
        success = True

        if self.send_max:
            # Проверяем авторизацию перед стартом рассылки
            self.progress.emit("Проверка авторизации MAX…")
            if not self.max_sender.is_authorized():
                self.result_ready.emit(False,
                    "❌ Аккаунт MAX не авторизован. "
                    "Проверьте подключение в настройках (🔑) и попробуйте снова.")
                return

            total = len(self.chat_ids)
            for i, chat_id in enumerate(self.chat_ids, 1):
                if self._cancelled:
                    lines.append(f"⛔ Отменено после {i - 1}/{total} отправок.")
                    self.result_ready.emit(False, "\n".join(lines))
                    return
                # Случайная пауза между отправками — имитирует живого человека
                if i > 1:
                    lo = min(self.delay_min, self.delay_max)
                    hi = max(self.delay_min, self.delay_max)
                    delay = random.randint(lo, hi) + self.extra_delay
                    for sec in range(delay):
                        if self._cancelled:
                            break
                        self.progress.emit(f"MAX {i}/{total} · пауза {delay - sec}с…")
                        time.sleep(1)
                self.progress.emit(f"MAX {i}/{total}…")
                self.progress_step.emit(i, total)
                if self.dry_run:
                    time.sleep(0.4)
                    r = SendResult(True, "[ТЕСТ] симуляция")
                else:
                    r = self.max_sender.send_post(
                        chat_link=chat_id,
                        text=self.text,
                        image_path=self.image_path,
                    )
                lines.append(f"MAX [{i}/{total}]: {r.message}")
                self.address_result.emit(i - 1, r.success, r.message)
                if not r.success:
                    success = False

        if not self._cancelled and self.send_vk:
            if self.dry_run:
                time.sleep(0.4)
                r = SendResult(True, "[ТЕСТ] симуляция ВК")
            else:
                r = self.vk_sender.send_post(
                    text=self.text,
                    image_path=self.image_path,
                    progress=lambda msg: self.progress.emit(f"ВК: {msg}"),
                )
            lines.append(f"ВК: {r.message}")
            if not r.success:
                success = False
            elif getattr(r, "post_id", None):
                self.vk_post_id = r.post_id

        self.result_ready.emit(success, "\n".join(lines))


class VkScheduleWorker(QThread):
    """Регистрирует отложенный пост на стороне ВКонтакте (wall.post с publish_date).
    После успеха ВК сам опубликует пост в нужное время — программа может быть выключена.
    """
    done = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, vk_sender: "VkSender", text: str, image_path: "str | None",
                 publish_date: int, parent=None) -> None:
        super().__init__(parent)
        self._sender = vk_sender
        self._text = text
        self._image_path = image_path
        self._publish_date = publish_date

    def run(self) -> None:
        r = self._sender.send_post(
            text=self._text,
            image_path=self._image_path,
            publish_date=self._publish_date,
        )
        self.done.emit(r.success, r.message)


class VkEditWorker(QThread):
    """Редактирует пост ВКонтакте в фоновом потоке."""
    done = pyqtSignal(bool, str)

    def __init__(self, vk_sender: "VkSender", post_id: int,
                 text: str, image_path: "str | None", parent=None) -> None:
        super().__init__(parent)
        self._sender = vk_sender
        self._post_id = post_id
        self._text = text
        self._image_path = image_path

    def run(self) -> None:
        r = self._sender.edit_post(
            post_id=self._post_id,
            text=self._text,
            image_path=self._image_path,
        )
        self.done.emit(r.success, r.message)


class VkLoadTextWorker(QThread):
    """Загружает оригинальный текст поста из ВКонтакте."""
    done = pyqtSignal(str)  # текст поста (пустая строка при ошибке)

    def __init__(self, vk_sender: "VkSender", post_id: int, parent=None) -> None:
        super().__init__(parent)
        self._sender = vk_sender
        self._post_id = post_id

    def run(self) -> None:
        text = self._sender.get_post_text(self._post_id)
        self.done.emit(text)


_VK_POPUP_DARK = """
QFrame#vkPostsPopup {
    background: #252535;
    border: 1px solid #3a3a55;
    border-radius: 10px;
}
QLabel#vkPopupTitle {
    color: #4a6cf7;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QPushButton#vkPostCard {
    background: #1e1e2e;
    border: 1px solid #3a3a55;
    border-radius: 7px;
    color: #d0d0e8;
    font-size: 12px;
    text-align: left;
    padding: 8px 10px;
}
QPushButton#vkPostCard:hover {
    background: #2a2a45;
    border-color: #4a6cf7;
    color: #ffffff;
}
QLabel#vkPostDate {
    color: #6b6b99;
    font-size: 10px;
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 4px; background: transparent; }
QScrollBar::handle:vertical { background: #3a3a55; border-radius: 2px; }
"""

_VK_POPUP_LIGHT = """
QFrame#vkPostsPopup {
    background: #ffffff;
    border: 1px solid #c7d0db;
    border-radius: 10px;
}
QLabel#vkPopupTitle {
    color: #2563eb;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QPushButton#vkPostCard {
    background: #f8fafc;
    border: 1px solid #dde3ea;
    border-radius: 7px;
    color: #1a1a2e;
    font-size: 12px;
    text-align: left;
    padding: 8px 10px;
}
QPushButton#vkPostCard:hover {
    background: #eff6ff;
    border-color: #2563eb;
    color: #1e3a8a;
}
QLabel#vkPostDate {
    color: #9ca3af;
    font-size: 10px;
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 4px; background: transparent; }
QScrollBar::handle:vertical { background: #c7d0db; border-radius: 2px; }
"""


class _VkPostsPopup(QFrame):
    """Красивый попап-карточки с последними постами ВК."""
    post_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("vkPostsPopup")
        self.setFixedWidth(400)
        self.setStyleSheet(_VK_POPUP_DARK)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(460)
        outer.addWidget(scroll)

        self._content = QWidget()
        self._content.setFixedWidth(400)
        scroll.setWidget(self._content)

        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(6)

    def set_dark(self, dark: bool) -> None:
        self.setStyleSheet(_VK_POPUP_DARK if dark else _VK_POPUP_LIGHT)

    def populate(self, posts: list) -> None:
        # очистить старые карточки
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        title = QLabel("ПОСЛЕДНИЕ ПОСТЫ ВКонтакте")
        title.setObjectName("vkPopupTitle")
        self._layout.addWidget(title)

        from datetime import datetime as _dt
        for post in posts:
            text = (post.get("text") or "").strip()
            if not text:
                continue
            date_str = _dt.fromtimestamp(post.get("date", 0)).strftime("%d.%m.%Y  %H:%M")
            preview = text[:80].replace("\n", " ")
            if len(text) > 80:
                preview += "…"

            card = QPushButton()
            card.setObjectName("vkPostCard")
            card.setFlat(False)
            card.setText(f"{date_str}\n{preview}")
            card.setMinimumHeight(54)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setToolTip(text[:400])
            card.clicked.connect(lambda checked, t=text: self._pick(t))
            self._layout.addWidget(card)

        self._layout.addStretch()
        self.adjustSize()

    def _pick(self, text: str) -> None:
        self.post_selected.emit(text)
        self.hide()

    def show_below(self, btn: "QPushButton") -> None:
        self.adjustSize()
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self.move(pos.x(), pos.y() + 4)
        self.show()


class _VkWallFetchWorker(QThread):
    """Загружает последние посты со стены ВК-группы."""
    done  = pyqtSignal(list)   # list[dict] — элементы wall.get
    error = pyqtSignal(str)

    def __init__(self, token: str, group_id: str, count: int = 10, parent=None) -> None:
        super().__init__(parent)
        self._token    = token
        self._group_id = group_id
        self._count    = count

    def run(self) -> None:
        try:
            from vk_utils import vk_api_call
            resp = vk_api_call(
                "wall.get", self._token,
                owner_id=f"-{self._group_id}",
                count=self._count,
                filter="owner",
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            self.done.emit(items)
        except Exception as exc:
            self.error.emit(str(exc))


class _PasteConfirmDialog(QDialog):
    """Показывает результаты поиска вставленных адресов в Excel перед добавлением."""

    def __init__(self, results: list, parent=None) -> None:
        # results: list[tuple[str, MatchResult | None]]
        super().__init__(parent)
        self.setWindowTitle("Результаты поиска адресов")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        found_count  = sum(1 for _, m in results if m and m.chat_id)
        missed_count = len(results) - found_count

        summary = QLabel(
            f"Найдено в реестре: <b>{found_count}</b>    "
            f"Не найдено: <b style='color:#dc2626;'>{missed_count}</b>"
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(summary)

        self._checks: list[tuple[QCheckBox, "MatchResult"]] = []

        for original, match in results:
            row = QHBoxLayout()
            if match and match.chat_id:
                chk = QCheckBox()
                chk.setChecked(True)
                label = QLabel(f"✓  <b>{match.address}</b>")
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setStyleSheet("color: #16a34a;")
                row.addWidget(chk)
                row.addWidget(label, 1)
                self._checks.append((chk, match))
            else:
                icon = QLabel("✗")
                icon.setFixedWidth(20)
                icon.setStyleSheet("color: #dc2626; font-weight: bold;")
                label = QLabel(f"{original}  <span style='color:#9ca3af;'>(не найден в реестре)</span>")
                label.setTextFormat(Qt.TextFormat.RichText)
                row.addWidget(icon)
                row.addWidget(label, 1)
            layout.addLayout(row)

        layout.addSpacing(4)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Добавить отмеченные")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def accepted_matches(self) -> list:
        return [m for chk, m in self._checks if chk.isChecked()]


# ---------------------------------------------------------------------------
# Умная рассылка — dataclass, парсинг блоков, диалог предпросмотра, воркер
# ---------------------------------------------------------------------------

@dataclass
class _SmartBlock:
    text: str
    matches: list = dc_field(default_factory=list)   # list[MatchResult]
    not_found: list = dc_field(default_factory=list)  # list[str] — не найдены в реестре


def _parse_smart_blocks(text: str, matcher) -> "tuple[list[_SmartBlock], str, str]":
    """Разбивает text на блоки по пустой строке.

    Возвращает (blocks_with_addresses, header_text, footer_text).
    header — текст до первого адресного блока, footer — после последнего.
    """
    raw_blocks = re.split(r"\n\s*\n", text.strip())
    address_blocks: list[_SmartBlock] = []
    # Индексы raw_blocks которые содержат адреса
    addr_indices: list[int] = []
    parsed_cache: list[list] = []

    for i, raw in enumerate(raw_blocks):
        raw = raw.strip()
        raw_blocks[i] = raw
        if not raw:
            parsed_cache.append([])
            continue
        try:
            parsed_list = extract_all_addresses(raw)
        except Exception:
            parsed_list = []
        parsed_cache.append(parsed_list)
        if parsed_list:
            addr_indices.append(i)

    if not addr_indices:
        return [], "", ""

    first_addr = addr_indices[0]
    last_addr = addr_indices[-1]

    header = "\n\n".join(raw_blocks[i] for i in range(first_addr) if raw_blocks[i])
    footer = "\n\n".join(raw_blocks[i] for i in range(last_addr + 1, len(raw_blocks)) if raw_blocks[i])

    for i in addr_indices:
        raw = raw_blocks[i]
        block = _SmartBlock(text=raw)
        for parsed in parsed_cache[i]:
            try:
                results = matcher.find_matches(parsed)
            except Exception:
                results = []
            if results:
                for r in results:
                    if not any(m.chat_id == r.chat_id for m in block.matches):
                        block.matches.append(r)
            else:
                block.not_found.append(getattr(parsed, "original", str(parsed)))
        address_blocks.append(block)

    return address_blocks, header, footer


class _SmartSendPreviewDialog(QDialog):
    """Диалог предпросмотра умной рассылки — показывает разбивку до отправки."""

    def __init__(self, blocks: "list[_SmartBlock]", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Умная рассылка — предпросмотр")
        self.setMinimumWidth(520)
        self.setMinimumHeight(400)
        self._blocks = blocks

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)

        # Заголовок
        title = QLabel(f"Найдено блоков с адресами: <b>{len(blocks)}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title)

        # Предупреждение о дублирующихся адресах
        all_ids = [m.chat_id for b in blocks for m in b.matches]
        dup_ids = {cid for cid, cnt in collections.Counter(all_ids).items() if cnt > 1}
        if dup_ids:
            dup_names = [m.address for b in blocks for m in b.matches if m.chat_id in dup_ids]
            warn = QLabel(f"⚠️ Один адрес встречается в нескольких блоках: {', '.join(dict.fromkeys(dup_names))}")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b45309; background: #fef3c7; padding: 6px 8px; border-radius: 4px;")
            root.addWidget(warn)

        # Список блоков в скролле
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        scroll.setWidget(container)
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(10)
        container.setFixedWidth(480)

        self._checks: list[tuple["QCheckBox", "_SmartBlock"]] = []

        for idx, block in enumerate(blocks, 1):
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(8, 6, 8, 6)
            fl.setSpacing(4)

            # Превью первой строки
            first_line = block.text.splitlines()[0][:80]
            chk = QCheckBox(f"Блок {idx}: {first_line}")
            chk.setChecked(True)
            chk.setStyleSheet("font-weight: 600;")
            fl.addWidget(chk)
            self._checks.append((chk, block))

            # Превью полного текста (первые 300 символов)
            preview_text = block.text if len(block.text) <= 300 else block.text[:300] + "…"
            preview_lbl = QLabel(preview_text)
            preview_lbl.setWordWrap(True)
            preview_lbl.setStyleSheet(
                "color: #374151; font-size: 11px; padding: 4px 16px;"
                "background: rgba(0,0,0,0.04); border-radius: 4px;"
            )
            fl.addWidget(preview_lbl)

            # Найденные адреса — зелёным
            for m in block.matches:
                lbl = QLabel(f"  ✓  {m.address}")
                lbl.setStyleSheet("color: #16a34a; padding-left: 16px;")
                fl.addWidget(lbl)

            # Не найденные — красным
            for addr in block.not_found:
                lbl = QLabel(f"  ✗  {addr}  (не найден в реестре)")
                lbl.setStyleSheet("color: #dc2626; padding-left: 16px;")
                fl.addWidget(lbl)

            # Кол-во получателей
            cnt = len(block.matches)
            info = QLabel(f"  Получателей: {cnt}")
            info.setStyleSheet("color: #6b7280; font-size: 11px; padding-left: 16px;")
            fl.addWidget(info)

            vbox.addWidget(frame)

        vbox.addStretch()
        root.addWidget(scroll, 1)

        # Кнопки: [Тест] слева, [Отмена] [Отправить] справа
        self.dry_run = False
        btn_row = QHBoxLayout()
        btn_test = QPushButton("Тест")
        btn_test.setObjectName("testButton")
        btn_test.setToolTip("Пробный прогон — сообщения не отправляются")
        btn_cancel = QPushButton("Отмена")
        btn_send = QPushButton("Отправить")
        btn_send.setDefault(True)

        def _do_test():
            self.dry_run = True
            self.accept()

        btn_test.clicked.connect(_do_test)
        btn_cancel.clicked.connect(self.reject)
        btn_send.clicked.connect(self.accept)

        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_send)
        root.addLayout(btn_row)

    def accepted_blocks(self) -> "list[_SmartBlock]":
        return [b for chk, b in self._checks if chk.isChecked() and b.matches]


class _SmartSendWorker(QThread):
    """Рассылает блоки умной рассылки по своим адресам."""

    progress = pyqtSignal(str)
    address_result = pyqtSignal(int, bool, str)  # (global_idx, success, message)
    all_done = pyqtSignal(bool, str)             # (overall_success, summary)

    def __init__(
        self,
        max_sender,
        chat_ids_per_block: "list[tuple[str, list[str]]]",
        image_path: "str | None",
        send_max: bool,
        dry_run: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._sender = max_sender
        self._blocks = chat_ids_per_block
        self._image_path = image_path
        self._send_max = send_max
        self.dry_run = dry_run
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if not self._send_max:
            self.all_done.emit(False, "Умная рассылка работает только для MAX.")
            return

        total_blocks = len(self._blocks)
        ok_count = 0
        fail_count = 0
        send_num = 0
        global_idx = 0

        for b_idx, (text, chat_ids) in enumerate(self._blocks):
            for chat_id in chat_ids:
                if self._cancelled:
                    self.all_done.emit(False, f"Отменено. Отправлено: {ok_count} | Ошибок: {fail_count}")
                    return
                if send_num > 0 and not self.dry_run:
                    delay = random.randint(5, 12)
                    for sec in range(delay):
                        if self._cancelled:
                            break
                        self.progress.emit(
                            f"Блок {b_idx + 1}/{total_blocks} · пауза {delay - sec}с…"
                        )
                        time.sleep(1)
                if self._cancelled:
                    self.all_done.emit(False, f"Отменено. Отправлено: {ok_count} | Ошибок: {fail_count}")
                    return
                self.progress.emit(f"Блок {b_idx + 1}/{total_blocks}: отправка {send_num + 1}…")
                if self.dry_run:
                    time.sleep(0.3)
                    ok_count += 1
                    self.address_result.emit(global_idx, True, "[ТЕСТ] симуляция")
                else:
                    try:
                        r = self._sender.send_post(
                            chat_link=chat_id,
                            text=text,
                            image_path=self._image_path,
                        )
                        if r.success:
                            ok_count += 1
                            self.address_result.emit(global_idx, True, r.message)
                        else:
                            fail_count += 1
                            self.address_result.emit(global_idx, False, r.message)
                    except Exception as exc:
                        fail_count += 1
                        self.address_result.emit(global_idx, False, str(exc))
                send_num += 1
                global_idx += 1

        summary = f"Блоков: {total_blocks} | Отправлено: {ok_count} | Ошибок: {fail_count}"
        self.all_done.emit(fail_count == 0, summary)


# ---------------------------------------------------------------------------

class SendResultDialog(QDialog):
    """Диалог с детальными результатами отправки."""

    def __init__(self, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Результаты отправки")
        self.setMinimumWidth(520)
        self.setMinimumHeight(300)

        lines = [ln for ln in message.strip().splitlines() if ln.strip()]
        ok_count = sum(1 for ln in lines if "Отправлено" in ln or "отправлено" in ln.lower())
        fail_count = sum(1 for ln in lines if "Ошибка" in ln or "ошибка" in ln.lower() or "⛔" in ln)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # Summary row
        summary_row = QHBoxLayout()
        ok_lbl = QLabel(f"✅  Успешно: {ok_count}")
        ok_lbl.setStyleSheet("font-size:14px; font-weight:600; color:#16a34a;")
        fail_lbl = QLabel(f"❌  Ошибок: {fail_count}")
        fail_lbl.setStyleSheet("font-size:14px; font-weight:600; color:#dc2626;")
        summary_row.addWidget(ok_lbl)
        summary_row.addSpacing(24)
        summary_row.addWidget(fail_lbl)
        summary_row.addStretch()
        layout.addLayout(summary_row)

        # Results list
        list_w = QListWidget()
        list_w.setAlternatingRowColors(True)
        list_w.setStyleSheet("""
            QListWidget { border:1px solid #e4eaf0; border-radius:8px;
                          font-size:13px; background:#ffffff;
                          alternate-background-color:#f8fafc; }
            QListWidget::item { padding:5px 8px; }
        """)
        for line in lines:
            item = QListWidgetItem(line)
            low = line.lower()
            if "ошибка" in low or "⛔" in line or "error" in low:
                item.setForeground(QColor("#dc2626"))
                item.setBackground(QColor("#fff5f5"))
            elif "отправлено" in low or "ok" in low:
                item.setForeground(QColor("#16a34a"))
            list_w.addItem(item)
        layout.addWidget(list_w)

        btn_row = QHBoxLayout()

        copy_btn = QPushButton("📋 Копировать")
        copy_btn.setFixedHeight(30)
        copy_btn.setToolTip("Скопировать текст ошибки в буфер обмена")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(message))

        save_btn = QPushButton("💾 Сохранить")
        save_btn.setFixedHeight(30)
        save_btn.setToolTip("Сохранить ошибку в текстовый файл")

        def _save_error() -> None:
            default = f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить ошибку", default, "Текстовые файлы (*.txt)"
            )
            if path:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(message)
                except OSError as exc:
                    QMessageBox.warning(self, "Ошибка", str(exc))

        save_btn.clicked.connect(_save_error)

        ok_btn = QPushButton("OK")
        ok_btn.setFixedHeight(30)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)

        btn_row.addWidget(copy_btn)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("MAX POST")
        self.setWindowIcon(QIcon(str(_assets_dir() / "MAX POST.ico")))
        self.resize(1280, 760)

        # Версия — читаем один раз
        _ver_file = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "version.txt"
        _ver_lines = _ver_file.read_text(encoding="utf-8").splitlines() if _ver_file.exists() else []
        self._app_version: str = _ver_lines[0].strip() if _ver_lines else "?"

        self.excel_path: Path = self._resolve_excel_path()
        self.image_path: "Path | None" = None
        self._photo_pinned: bool = False
        self._recent_photos: list[str] = []   # до 5 последних путей к фото
        self._worker: "SendWorker | None" = None
        self._smart_worker: "_SmartSendWorker | None" = None
        self._vk_sched_worker: "VkScheduleWorker | None" = None
        self._vk_fetch_worker: "_VkWallFetchWorker | None" = None
        self._addr_search_worker: "_AddrSearchWorker | None" = None
        self._excel_mtime: float = (
            self.excel_path.stat().st_mtime if self.excel_path.exists() else 0.0
        )
        self._send_log_results: list[tuple[str, bool, str]] = []  # (адрес, успех, время)
        self._sel_worker: "SendWorker | None" = None  # воркер отправки выделенного текста
        self._bg_index: "int | None" = None
        self._bg_mode: int = 0  # 0 = фон, 1 = наложение
        self._bg_opacity: int = 50
        self._bg_widget: "_BgWidget | None" = None

        # Шрифты интерфейса — загружаются отложено после показа окна
        self._ui_font_family: str = ""
        self._ui_font_size: int = 13
        self._ui_font_families: list[str] = []
        self._pending_font_family: str = ""
        self._pending_font_size: int = 0

        # Кэшированный ExcelMatcher — читает Excel только один раз
        self._matcher: "ExcelMatcher | None" = (
            ExcelMatcher(self.excel_path) if self.excel_path.exists() else None
        )
        self._addr_check_worker: "_AddressCheckWorker | None" = None

        _appdata = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST" if getattr(sys, "frozen", False) else Path(__file__).parent
        if getattr(sys, "frozen", False):
            _old_appdata = Path(os.environ.get("APPDATA", Path.home())) / "max_poster"
            if not _appdata.exists() and _old_appdata.exists():
                try:
                    _old_appdata.rename(_appdata)
                except OSError:
                    pass  # если переименовать не удалось — просто создадим новую папку ниже
        _appdata.mkdir(parents=True, exist_ok=True)
        self.state_manager = StateManager(_appdata / "app_state.json")
        self.max_sender = MaxSender()
        self.vk_sender = VkSender()
        self._last_token_rotation: str = ""      # дата последней смены токенов (YYYY-MM-DD)
        self._last_vk_invalid_warning: str = ""  # дата последнего предупреждения о VK токене
        atexit.register(self._do_save_state)  # сохраняем состояние и при крэше
        self._pending_history: dict = {}
        self._scheduled_posts: dict = {}  # entry_id -> {"timer": QTimer, "data": dict}

        self._parse_timer = QTimer(self)
        self._parse_timer.setSingleShot(True)
        self._parse_timer.setInterval(PARSE_DEBOUNCE_MS)
        self._parse_timer.timeout.connect(self._auto_check_addresses)

        # Дебаунс сохранения состояния — не пишем на каждый символ
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._do_save_state)

        # Слежение за изменением Excel-реестра — проверяем каждые 10 сек
        self._excel_watch_timer = QTimer(self)
        self._excel_watch_timer.setInterval(10_000)
        self._excel_watch_timer.timeout.connect(self._check_excel_changed)
        if self.excel_path.exists():
            self._excel_watch_timer.start()

        self.setAcceptDrops(True)
        self._real_quit = False  # True → полное закрытие; False → сворачивание в трей

        self._build_menu()
        self._build_ui()
        self._apply_styles()
        self._setup_tray()
        self.load_state()
        # Загружаем шрифты и применяем сохранённый шрифт после показа окна
        QTimer.singleShot(0, self._deferred_font_load)
        QTimer.singleShot(500, self._load_scheduled_from_disk)
        # Прогрев Excel — загружаем датафрейм в фоне чтобы первый парсинг был мгновенным
        if self._matcher is not None:
            _warmup = _ExcelWarmupWorker(self._matcher, self)
            _warmup.finished.connect(_warmup.deleteLater)
            _warmup.start()
        # Проверки безопасности — откладываем чтобы не мешать старту
        QTimer.singleShot(4000, self._check_token_reminder)
        QTimer.singleShot(6000, self._check_vk_token)

        # Горячие клавиши (Ctrl+Return и Ctrl+L заданы через QAction в меню)

    @staticmethod
    def _resolve_excel_path() -> Path:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        return base / "max_address.xlsx"

    @staticmethod
    def _load_font_families() -> list[str]:
        """Загрузить шрифты из папки fonts/ и вернуть отсортированный список семейств."""
        fonts_dir = _fonts_dir()
        seen: set[str] = set()
        families: list[str] = []
        for f in sorted(list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf"))):
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid >= 0:
                for fam in QFontDatabase.applicationFontFamilies(fid):
                    if fam not in seen:
                        seen.add(fam)
                        families.append(fam)
        return sorted(families)

    def _deferred_font_load(self) -> None:
        """Загружает шрифты и применяет сохранённый шрифт. Вызывается после показа окна."""
        self._ui_font_families = self._load_font_families()
        fam = self._pending_font_family
        sz = self._pending_font_size
        if fam and fam in self._ui_font_families:
            self._apply_ui_font(fam, sz or self._ui_font_size)

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("Файл")
        actions_menu = menu.addMenu("Действия")
        view_menu = menu.addMenu("Вид")
        help_menu = menu.addMenu("Справка")

        self._tpl_file_menu = file_menu.addMenu("📋 Шаблоны")
        self._rebuild_templates_file_menu()

        file_menu.addSeparator()

        open_image_action = QAction("Открыть фото", self)
        open_image_action.setShortcut(QKeySequence("Ctrl+L"))
        open_image_action.triggered.connect(self.select_image)
        file_menu.addAction(open_image_action)

        clear_photo_action = QAction("Очистить фото", self)
        clear_photo_action.triggered.connect(self._clear_photo)
        file_menu.addAction(clear_photo_action)

        file_menu.addSeparator()

        reload_excel_action = QAction("Обновить реестр адресов", self)
        reload_excel_action.triggered.connect(self._reload_excel)
        file_menu.addAction(reload_excel_action)

        file_menu.addSeparator()

        clear_action = QAction("Очистить форму", self)
        clear_action.triggered.connect(self.clear_form)
        file_menu.addAction(clear_action)

        file_menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self._quit_app)
        file_menu.addAction(exit_action)

        send_action = QAction("Опубликовать", self)
        send_action.setShortcut(QKeySequence("Ctrl+Return"))
        send_action.triggered.connect(self.send_post)
        actions_menu.addAction(send_action)

        actions_menu.addSeparator()

        conn_action = QAction("Проверить соединение MAX…", self)
        conn_action.triggered.connect(self._check_max_connection)
        actions_menu.addAction(conn_action)

        actions_menu.addSeparator()

        rotate_action = QAction("🔑 Сменил токены VK", self)
        rotate_action.setToolTip(f"Отметить что VK токен был обновлён сегодня (напоминание каждые {_TOKEN_ROTATION_DAYS} дней)")
        rotate_action.triggered.connect(self._mark_tokens_rotated)
        actions_menu.addAction(rotate_action)

        theme_action = QAction("Тема оформления…", self)
        theme_action.triggered.connect(self._open_theme_picker)
        view_menu.addAction(theme_action)

        font_action = QAction("Шрифт интерфейса…", self)
        font_action.triggered.connect(self._open_font_picker)
        view_menu.addAction(font_action)

        guide_action = QAction("📖 Руководство пользователя", self)
        guide_action.setShortcut(QKeySequence("F1"))
        guide_action.triggered.connect(self._open_help)
        help_menu.addAction(guide_action)

        help_menu.addSeparator()

        about_action = QAction("О программе", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        shortcuts_action = QAction("Горячие клавиши", self)
        shortcuts_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts_action)

        help_menu.addSeparator()

        update_action = QAction("Проверить обновления…", self)
        update_action.triggered.connect(lambda: check_for_updates(self, silent=False))
        help_menu.addAction(update_action)

        integrity_action = QAction("Проверка целостности программы…", self)
        integrity_action.triggered.connect(self._check_integrity)
        help_menu.addAction(integrity_action)

    def _build_ui(self) -> None:
        central = _BgWidget()
        central.setObjectName("maxPosterContent")
        self._bg_widget = central
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        self._main_splitter = _GripSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setHandleWidth(8)
        self._main_splitter.setChildrenCollapsible(False)
        root.addWidget(self._main_splitter)

        # ══════════════════════════════════════════════════════════════
        # ЛЕВАЯ ПАНЕЛЬ — только поле ввода текста (широкое)
        # ══════════════════════════════════════════════════════════════
        text_box = QGroupBox()
        text_box.setObjectName("sidePanel")
        text_layout = QVBoxLayout(text_box)
        text_layout.setSpacing(8)
        text_layout.setContentsMargins(12, 10, 12, 12)

        left_header = QFrame()
        left_header.setObjectName("checklistFrame")
        lh_layout = QHBoxLayout(left_header)
        lh_layout.setContentsMargins(14, 10, 14, 10)
        lh_title = QLabel("Ввод данных")
        lh_title.setObjectName("checklistTitle")
        lh_layout.addWidget(lh_title)
        lh_layout.addStretch()
        self._vk_posts_btn = QPushButton("📰 ВК посты")
        self._vk_posts_btn.setObjectName("tplMiniBtn")
        self._vk_posts_btn.setFixedHeight(28)
        self._vk_posts_btn.setMinimumWidth(85)
        self._vk_posts_btn.setToolTip("Вставить текст из последних постов группы ВКонтакте")
        self._vk_posts_btn.clicked.connect(self._open_vk_posts_picker)
        lh_layout.addWidget(self._vk_posts_btn)

        self._smart_send_btn = QPushButton("🔀 Умная рассылка")
        self._smart_send_btn.setObjectName("tplMiniBtn")
        self._smart_send_btn.setFixedHeight(28)
        self._smart_send_btn.setMinimumWidth(85)
        self._smart_send_btn.setToolTip(
            "Умная рассылка — разбить текст на блоки и отправить каждый по своим адресам"
        )
        self._smart_send_btn.clicked.connect(self._start_smart_send)
        lh_layout.addWidget(self._smart_send_btn)

        self._open_file_btn = QPushButton("📄 Файл")
        self._open_file_btn.setObjectName("tplMiniBtn")
        self._open_file_btn.setFixedHeight(28)
        self._open_file_btn.setToolTip("Открыть текстовый файл (.txt, .docx) и вставить текст")
        self._open_file_btn.clicked.connect(self._open_text_file)
        lh_layout.addWidget(self._open_file_btn)

        self._tpl_btn = QPushButton("📋")
        self._tpl_btn.setObjectName("tplMiniBtn")
        self._tpl_btn.setFixedSize(28, 28)
        self._tpl_btn.setToolTip("Шаблоны текста")
        self._tpl_btn.clicked.connect(self._open_templates_menu)
        lh_layout.addWidget(self._tpl_btn)
        text_layout.addWidget(left_header)

        self.text_input = LineNumberedEdit()
        self.text_input.textChanged.connect(self.sync_preview)
        self.text_input.setPlaceholderText(
            "Введите текст объявления…\n\nАдрес будет найден автоматически"
        )
        self.text_input.send_selected_max.connect(self._send_selection_max)
        self.text_input.send_selected_vk.connect(self._send_selection_vk)
        self.text_input.addr_count_getter = self._get_checked_addr_count
        self.text_input.vk_token_getter   = lambda: bool(os.getenv("VK_GROUP_TOKEN"))

        self._emoji_picker: "EmojiPicker | None" = None
        self._emoji_btn = QPushButton("😊")
        self._emoji_btn.setObjectName("emojiButton")
        self._emoji_btn.setFixedSize(28, 28)
        self._emoji_btn.clicked.connect(self._toggle_emoji_picker)

        self._char_counter = QLabel("0/4000")
        self._char_counter.setObjectName("charCounter")

        self._vk_char_counter = QLabel()
        self._vk_char_counter.setObjectName("charCounter")
        self._vk_char_counter.hide()

        right_bar = QWidget()
        rb_layout = QVBoxLayout(right_bar)
        rb_layout.setContentsMargins(0, 2, 2, 2)
        rb_layout.setSpacing(2)
        rb_layout.addWidget(self._emoji_btn, alignment=Qt.AlignmentFlag.AlignRight)
        rb_layout.addWidget(self._char_counter, alignment=Qt.AlignmentFlag.AlignRight)
        rb_layout.addWidget(self._vk_char_counter, alignment=Qt.AlignmentFlag.AlignRight)

        bottom_bar = QWidget()
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(0, 0, 0, 0)
        bb_layout.addStretch()
        bb_layout.addWidget(right_bar)

        text_container = QFrame()
        text_container.setObjectName("textContainer")
        self._text_container = text_container
        tc_layout = QVBoxLayout(text_container)
        tc_layout.setContentsMargins(0, 0, 0, 0)
        tc_layout.setSpacing(0)
        tc_layout.addWidget(self.text_input)
        tc_layout.addWidget(bottom_bar)

        text_layout.addWidget(text_container, 1)

        self._addr_notfound_hint = QLabel("⚠ Строки с оранжевым фоном — адрес не найден в базе")
        self._addr_notfound_hint.setObjectName("addrNotFoundHint")
        self._addr_notfound_hint.setWordWrap(True)
        self._addr_notfound_hint.hide()
        text_layout.addWidget(self._addr_notfound_hint)

        # Кнопка загрузки фото + закрепить — под текстом
        self.photo_button = QPushButton("Загрузить фото")
        self.photo_button.clicked.connect(self.select_image)
        self.photo_button.setToolTip("Выбрать фото (Ctrl+L)")

        self._pin_photo_btn = QPushButton("📌")
        self._pin_photo_btn.setObjectName("tplMiniBtn")
        self._pin_photo_btn.setFixedSize(28, 28)
        self._pin_photo_btn.setCheckable(True)
        self._pin_photo_btn.setToolTip("Закрепить фото — не сбрасывать при очистке формы")
        self._pin_photo_btn.toggled.connect(self._on_photo_pin_toggled)

        photo_row_w = QWidget()
        photo_row_l = QHBoxLayout(photo_row_w)
        photo_row_l.setContentsMargins(0, 4, 0, 0)
        photo_row_l.setSpacing(4)
        photo_row_l.addWidget(self.photo_button, 1)
        photo_row_l.addWidget(self._pin_photo_btn)
        text_layout.addWidget(photo_row_w)

        # Галерея последних фото — встраивается в строку платформ
        self._recent_bar = QFrame()
        self._recent_bar.setObjectName("recentPhotoBar")
        self._recent_bar_layout = QHBoxLayout(self._recent_bar)
        self._recent_bar_layout.setContentsMargins(4, 0, 4, 0)
        self._recent_bar_layout.setSpacing(4)
        self._recent_bar.hide()

        # ══════════════════════════════════════════════════════════════
        # ПРАВАЯ ПАНЕЛЬ — адреса и управление
        # ══════════════════════════════════════════════════════════════
        ctrl_box = QGroupBox()
        ctrl_box.setObjectName("sidePanel")
        ctrl_layout = QVBoxLayout(ctrl_box)
        ctrl_layout.setSpacing(8)
        ctrl_layout.setContentsMargins(12, 10, 12, 12)

        self._addr_list = QListWidget()
        self._addr_list.setMinimumHeight(80)
        self._addr_list.setObjectName("addrList")
        self._addr_list.setAlternatingRowColors(True)
        self._addr_list.setItemDelegate(_NumberedItemDelegate(self._addr_list))
        self._addr_list.itemChanged.connect(self._on_addr_item_changed)
        self._addr_list.itemDoubleClicked.connect(self._toggle_addr_item)
        self._addr_list.installEventFilter(self)
        self._addr_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._addr_list.customContextMenuRequested.connect(self._addr_list_context_menu)
        self._addr_list.currentItemChanged.connect(
            lambda cur, _: setattr(self, "_last_addr_row", self._addr_list.row(cur) if cur else self._last_addr_row)
        )
        self._last_addr_row = -1

        addr_header_frame = QFrame()
        addr_header_frame.setObjectName("checklistFrame")
        ah_layout = QHBoxLayout(addr_header_frame)
        ah_layout.setContentsMargins(14, 10, 14, 10)
        self._addr_count_lbl = QLabel("Адреса для рассылки MAX")
        self._addr_count_lbl.setObjectName("checklistTitle")
        self._del_addr_btn = QPushButton("🗑")
        self._del_addr_btn.setObjectName("tplMiniBtn")
        self._del_addr_btn.setFixedSize(28, 28)
        self._del_addr_btn.setToolTip("Удалить выбранный адрес (или ПКМ на адрес)")
        self._del_addr_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._del_addr_btn.clicked.connect(self._delete_selected_address)
        self._add_addr_btn = QPushButton("+")
        self._add_addr_btn.setObjectName("addAddrBtn")
        self._add_addr_btn.setFixedSize(24, 24)
        self._add_addr_btn.setToolTip("Добавить адрес вручную")
        self._add_addr_btn.clicked.connect(self._add_address_manually)
        self._paste_addr_btn = QPushButton("📋")
        self._paste_addr_btn.setObjectName("tplMiniBtn")
        self._paste_addr_btn.setFixedSize(28, 28)
        self._paste_addr_btn.setToolTip("Вставить несколько адресов сразу")
        self._paste_addr_btn.clicked.connect(self._paste_addresses)
        self._hist_btn = QPushButton("🕐")
        self._hist_btn.setObjectName("tplMiniBtn")
        self._hist_btn.setFixedSize(28, 28)
        self._hist_btn.setToolTip("История публикаций")
        self._hist_btn.clicked.connect(self._toggle_history_popup)
        self._select_all_btn = QPushButton("☑")
        self._select_all_btn.setObjectName("tplMiniBtn")
        self._select_all_btn.setFixedSize(28, 28)
        self._select_all_btn.setToolTip("Выбрать все / Снять все")
        self._select_all_btn.clicked.connect(self._toggle_select_all)

        ah_layout.addWidget(self._addr_count_lbl)
        ah_layout.addStretch()
        ah_layout.addWidget(self._hist_btn)
        ah_layout.addWidget(self._select_all_btn)
        ah_layout.addWidget(self._paste_addr_btn)
        ah_layout.addWidget(self._del_addr_btn)
        ah_layout.addWidget(self._add_addr_btn)
        ctrl_layout.addWidget(addr_header_frame)

        # Тостер: реестр изменён
        self._excel_changed_bar = QPushButton("⟳  Реестр адресов изменён — нажмите для обновления")
        self._excel_changed_bar.setObjectName("excelChangedBar")
        self._excel_changed_bar.setFixedHeight(26)
        self._excel_changed_bar.clicked.connect(self._reload_excel_silent)
        self._excel_changed_bar.hide()
        ctrl_layout.addWidget(self._excel_changed_bar)

        self._addr_search = QLineEdit()
        self._addr_search.setPlaceholderText("🔍 Поиск в max_address.xlsx…")
        self._addr_search.setObjectName("addrSearch")
        self._addr_search.setFixedHeight(26)
        self._addr_search.textChanged.connect(self._on_addr_search_changed)
        self._addr_search.returnPressed.connect(self._addr_search_accept_first)
        if self._matcher is not None:
            self._addr_search.show()
        else:
            self._addr_search.hide()
        ctrl_layout.addWidget(self._addr_search)

        self._addr_search_results = QListWidget()
        self._addr_search_results.setObjectName("addrSearchResults")
        self._addr_search_results.setMaximumHeight(180)
        self._addr_search_results.hide()
        self._addr_search_results.itemChanged.connect(self._on_addr_search_check_changed)
        ctrl_layout.addWidget(self._addr_search_results)

        self._addr_search_add_btn = QPushButton("Добавить")
        self._addr_search_add_btn.setObjectName("tplMiniBtn")
        self._addr_search_add_btn.setFixedHeight(24)
        self._addr_search_add_btn.hide()
        self._addr_search_add_btn.clicked.connect(self._add_checked_search_results)
        ctrl_layout.addWidget(self._addr_search_add_btn)

        self._addr_search_timer = QTimer(self)
        self._addr_search_timer.setSingleShot(True)
        self._addr_search_timer.timeout.connect(self._do_addr_search)

        addr_hint = QLabel("⚠️ Не более 10 групп за раз (5 мин) — бан МАХ")
        addr_hint.setObjectName("addrHintLbl")
        ctrl_layout.addWidget(addr_hint)

        ctrl_layout.addWidget(self._addr_list, 1)

        self._send_log_list = QListWidget()
        self._send_log_list.setObjectName("sendLogList")
        self._send_log_list.setAlternatingRowColors(True)
        self._send_log_list.hide()
        ctrl_layout.addWidget(self._send_log_list, 1)

        self.clear_button = QPushButton("Очистить")
        self.clear_button.setObjectName("clearButton")
        self.clear_button.clicked.connect(self.clear_form)

        # ── Платформы ────────────────────────────────────────────────
        platforms_section = QWidget()
        platforms_section.setObjectName("platformsSection")
        pl_layout = QVBoxLayout(platforms_section)
        pl_layout.setContentsMargins(0, 0, 0, 0)
        pl_layout.setSpacing(6)

        pl_title = QLabel("Платформы")
        pl_title.setObjectName("sectionTitle")
        pl_layout.addWidget(pl_title)

        self.chk_max = QCheckBox("MAX")
        self.chk_max.setChecked(True)
        self.chk_vk = QCheckBox("ВКонтакте")
        self.chk_vk.setChecked(False)

        _assets = _assets_dir()
        _max_icon_path = _assets / "max.ico"
        _vk_icon_path = _assets / "vk_2.ico"
        if _max_icon_path.exists():
            self.chk_max.setIcon(QIcon(str(_max_icon_path)))
            self.chk_max.setIconSize(QSize(18, 18))
        if _vk_icon_path.exists():
            self.chk_vk.setIcon(QIcon(str(_vk_icon_path)))
            self.chk_vk.setIconSize(QSize(18, 18))

        platforms_row = QHBoxLayout()
        platforms_row.setContentsMargins(0, 0, 0, 0)
        platforms_row.setSpacing(8)

        chk_max_frame = QFrame()
        chk_max_frame.setObjectName("platformChip")
        chk_max_fl = QHBoxLayout(chk_max_frame)
        chk_max_fl.setContentsMargins(10, 6, 10, 6)
        chk_max_fl.addWidget(self.chk_max)

        chk_vk_frame = QFrame()
        chk_vk_frame.setObjectName("platformChip")
        chk_vk_fl = QHBoxLayout(chk_vk_frame)
        chk_vk_fl.setContentsMargins(10, 6, 10, 6)
        chk_vk_fl.addWidget(self.chk_vk)

        self._save_report_btn = QPushButton("Сохранить отчёт")
        self._save_report_btn.setObjectName("saveReportBtn")
        self._save_report_btn.setFixedHeight(28)
        self._save_report_btn.setToolTip("Сохранить лог отправки в CSV")
        self._save_report_btn.hide()
        self._save_report_btn.clicked.connect(self._export_report_csv)

        self._retry_btn = QPushButton("🔁 Повторить")
        self._retry_btn.setObjectName("saveReportBtn")
        self._retry_btn.setFixedHeight(28)
        self._retry_btn.setToolTip("Повторить отправку в адреса с ошибками")
        self._retry_btn.hide()
        self._retry_btn.clicked.connect(self._retry_send)

        self._recent_sep_left = QFrame()
        self._recent_sep_left.setFrameShape(QFrame.Shape.VLine)
        self._recent_sep_left.setObjectName("recentPhotoSep")
        self._recent_sep_left.hide()

        self._recent_sep_right = QFrame()
        self._recent_sep_right.setFrameShape(QFrame.Shape.VLine)
        self._recent_sep_right.setObjectName("recentPhotoSep")
        self._recent_sep_right.hide()

        platforms_row.addWidget(chk_max_frame)
        platforms_row.addWidget(chk_vk_frame)
        platforms_row.addWidget(self._recent_sep_left)
        platforms_row.addWidget(self._recent_bar)
        platforms_row.addWidget(self._recent_sep_right)
        platforms_row.addStretch()
        platforms_row.addWidget(self._retry_btn)
        platforms_row.addWidget(self._save_report_btn)
        platforms_row.addWidget(self.clear_button)
        pl_layout.addLayout(platforms_row)

        # ── Кнопки действий ─────────────────────────────────────────
        buttons_row = QGridLayout()
        buttons_row.setSpacing(8)

        self.send_button = QPushButton("Опубликовать")
        self.send_button.clicked.connect(self.send_post)
        self.send_button.setObjectName("primaryButton")
        self.send_button.setToolTip("Опубликовать пост (Ctrl+Return)")

        sched_frame = QFrame()
        sched_frame.setObjectName("scheduleRow")
        sched_fl = QHBoxLayout(sched_frame)
        sched_fl.setContentsMargins(0, 0, 0, 0)
        sched_fl.setSpacing(8)
        self._chk_schedule = QCheckBox("Отложить")
        self._chk_schedule.setObjectName("scheduleChk")
        self._chk_schedule.toggled.connect(self._on_schedule_toggled)

        _now30 = QDateTime.currentDateTime().addSecs(30 * 60)
        self._sched_date = QDateEdit()
        self._sched_date.setObjectName("scheduleDate")
        self._sched_date.setDisplayFormat("dd.MM.yyyy")
        self._sched_date.setCalendarPopup(True)
        self._sched_date.setDate(_now30.date())
        self._sched_date.setMinimumDate(QDateTime.currentDateTime().date())

        self._sched_hour = QSpinBox()
        self._sched_hour.setObjectName("scheduleHour")
        self._sched_hour.setRange(0, 23)
        self._sched_hour.setValue(_now30.time().hour())
        self._sched_hour.setWrapping(True)
        self._sched_hour.setFixedWidth(42)
        self._sched_hour.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._sched_min = QSpinBox()
        self._sched_min.setObjectName("scheduleMin")
        self._sched_min.setRange(0, 59)
        self._sched_min.setValue(_now30.time().minute())
        self._sched_min.setWrapping(True)
        self._sched_min.setFixedWidth(42)
        self._sched_min.setAlignment(Qt.AlignmentFlag.AlignCenter)

        _sep = QLabel(":")
        _sep.setObjectName("scheduleTimeSep")

        self._sched_widget = QFrame()
        self._sched_widget.setObjectName("scheduleRow")
        _sw = QHBoxLayout(self._sched_widget)
        _sw.setContentsMargins(0, 0, 0, 0)
        _sw.setSpacing(4)
        _sw.addWidget(self._sched_date, 1)
        _sw.addWidget(self._sched_hour)
        _sw.addWidget(_sep)
        _sw.addWidget(self._sched_min)
        self._sched_widget.hide()

        sched_fl.addWidget(self._chk_schedule)
        sched_fl.addWidget(self._sched_widget, 1)
        buttons_row.addWidget(sched_frame, 0, 0, 1, 2)

        self._sched_hint_lbl = QLabel()
        self._sched_hint_lbl.setObjectName("schedHintLbl")
        self._sched_hint_lbl.setWordWrap(True)
        self._sched_hint_lbl.hide()
        buttons_row.addWidget(self._sched_hint_lbl, 1, 0, 1, 2)

        self._cancel_button = QPushButton("✕  Отменить отправку")
        self._cancel_button.setObjectName("cancelSendBtn")
        self._cancel_button.hide()
        self._cancel_button.clicked.connect(self._cancel_send)

        self._chk_require_photo = QCheckBox("📷 Фото")
        self._chk_require_photo.setObjectName("requirePhotoChk")
        self._chk_require_photo.setChecked(True)
        self._chk_require_photo.setToolTip("Требовать фото перед публикацией")
        self._chk_require_photo.toggled.connect(self._on_require_photo_toggled)

        # ── Пауза (стиль как у «Отложить») ───────────────────────────
        delay_frame = QFrame()
        delay_frame.setObjectName("scheduleRow")
        delay_fl = QHBoxLayout(delay_frame)
        delay_fl.setContentsMargins(0, 0, 0, 0)
        delay_fl.setSpacing(8)

        self._chk_delay = QCheckBox("Пауза")
        self._chk_delay.setObjectName("scheduleChk")
        self._chk_delay.setToolTip("Случайная пауза между отправками в группы MAX")
        self._chk_delay.setChecked(True)
        self._chk_delay.toggled.connect(self._on_delay_toggled)

        self._delay_min_spin = QSpinBox()
        self._delay_min_spin.setObjectName("scheduleHour")
        self._delay_min_spin.setRange(1, 120)
        self._delay_min_spin.setValue(5)
        self._delay_min_spin.setSuffix(" с")
        self._delay_min_spin.setFixedWidth(52)
        self._delay_min_spin.setToolTip("Минимальная пауза (сек)")

        delay_dash = QLabel("–")
        delay_dash.setObjectName("scheduleTimeSep")

        self._delay_max_spin = QSpinBox()
        self._delay_max_spin.setObjectName("scheduleMin")
        self._delay_max_spin.setRange(1, 120)
        self._delay_max_spin.setValue(12)
        self._delay_max_spin.setSuffix(" с")
        self._delay_max_spin.setFixedWidth(52)
        self._delay_max_spin.setToolTip("Максимальная пауза (сек)")

        self._delay_widget = QFrame()
        self._delay_widget.setObjectName("scheduleRow")
        _dw = QHBoxLayout(self._delay_widget)
        _dw.setContentsMargins(0, 0, 0, 0)
        _dw.setSpacing(4)
        _dw.addWidget(self._delay_min_spin)
        _dw.addWidget(delay_dash)
        _dw.addWidget(self._delay_max_spin)

        delay_fl.addWidget(self._chk_delay)
        delay_fl.addWidget(self._delay_widget)
        delay_fl.addStretch()
        buttons_row.addWidget(delay_frame, 2, 0, 1, 2)

        # ── Выбрать все адреса (стиль как у «Отложить») ──────────────
        select_all_frame = QFrame()
        select_all_frame.setObjectName("scheduleRow")
        select_all_fl = QHBoxLayout(select_all_frame)
        select_all_fl.setContentsMargins(0, 0, 0, 0)
        select_all_fl.setSpacing(8)

        self._chk_select_all = QCheckBox("Выбрать все адреса")
        self._chk_select_all.setObjectName("scheduleChk")
        self._chk_select_all.setToolTip("Загрузить все адреса из реестра и отметить для рассылки")
        self._chk_select_all.clicked.connect(self._on_select_all_chk)
        select_all_fl.addWidget(self._chk_select_all)
        select_all_fl.addStretch()
        buttons_row.addWidget(select_all_frame, 3, 0, 1, 2)

        send_row_w = QWidget()
        send_row_l = QHBoxLayout(send_row_w)
        send_row_l.setContentsMargins(0, 0, 0, 0)
        send_row_l.setSpacing(6)
        send_row_l.addWidget(self._chk_require_photo)
        send_row_l.addWidget(self.send_button, 1)

        send_area = QFrame()
        sa_layout = QVBoxLayout(send_area)
        sa_layout.setContentsMargins(0, 0, 0, 0)
        sa_layout.setSpacing(0)
        sa_layout.addWidget(send_row_w)
        sa_layout.addWidget(self._cancel_button)
        buttons_row.addWidget(send_area, 4, 0, 1, 2)

        ctrl_layout.addWidget(platforms_section)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("sendProgress")
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()

        ctrl_layout.addLayout(buttons_row)
        ctrl_layout.addWidget(self._progress_bar)

        # ── Чеклист ──────────────────────────────────────────────────
        checklist_bar = QFrame()
        checklist_bar.setObjectName("checklistBarFrame")
        cb_layout = QHBoxLayout(checklist_bar)
        cb_layout.setContentsMargins(4, 4, 4, 4)
        cb_layout.setSpacing(12)

        self._cl_text     = QLabel()
        self._cl_photo    = QLabel()
        self._cl_address  = QLabel()
        self._cl_platform = QLabel()

        for lbl in (self._cl_text, self._cl_photo, self._cl_address, self._cl_platform):
            lbl.setObjectName("checklistItem")
            cb_layout.addWidget(lbl)
        cb_layout.addStretch()

        ctrl_layout.addWidget(checklist_bar)

        version_label = QLabel(f"Version {self._app_version}")
        version_label.setObjectName("versionLabel")
        ctrl_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignLeft)

        # ── Подключения чекбоксов ─────────────────────────────────────
        self.chk_max.stateChanged.connect(self._update_checklist)
        self.chk_vk.stateChanged.connect(self._update_checklist)
        self.chk_max.stateChanged.connect(self._sync_preview_avatar)
        self.chk_vk.stateChanged.connect(self._sync_preview_avatar)
        self.chk_vk.stateChanged.connect(lambda _: self.sync_preview())
        self.chk_max.stateChanged.connect(lambda _: self._update_sched_hint())
        self.chk_vk.stateChanged.connect(lambda _: self._update_sched_hint())

        # ── Preview — нужен для логики, не отображается ───────────────
        self.preview = PreviewCard()
        self.preview.setParent(central)
        self.preview.hide()

        # ── История — всплывающее окно по кнопке 🕐 ──────────────────
        self._hist_popup = QFrame(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._hist_popup.setObjectName("historyFrame")
        self._hist_popup.setMinimumWidth(540)
        popup_l = QVBoxLayout(self._hist_popup)
        popup_l.setContentsMargins(0, 0, 0, 0)
        popup_l.addWidget(self._build_history_panel())

        self._main_splitter.addWidget(text_box)
        self._main_splitter.addWidget(ctrl_box)
        self._main_splitter.setStretchFactor(0, 6)
        self._main_splitter.setStretchFactor(1, 4)

        self._success_overlay = SuccessOverlay(central)
        self._sync_preview_avatar()


    # ──────────────────────────────────────────────────────────────────
    #  История публикаций
    # ──────────────────────────────────────────────────────────────────

    def _toggle_history_popup(self) -> None:
        """Показывает/скрывает всплывающее окно истории под кнопкой 🕐."""
        if self._hist_popup.isVisible():
            self._hist_popup.hide()
            return
        self._hist_search.clear()
        self._refresh_history()
        btn = self._hist_btn
        pos = btn.mapToGlobal(btn.rect().bottomRight())
        self._hist_popup.adjustSize()
        w = max(self._hist_popup.width(), 540)
        self._hist_popup.setFixedWidth(w)
        self._hist_popup.move(pos.x() - w, pos.y() + 2)
        self._hist_popup.show()

    def _build_history_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("historyFrame")

        outer = QVBoxLayout(frame)
        outer.setContentsMargins(14, 10, 14, 8)
        outer.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel("История публикаций")
        title.setObjectName("checklistTitle")
        clear_btn = QPushButton("Очистить")
        clear_btn.setObjectName("histClearBtn")
        clear_btn.setFixedHeight(22)
        clear_btn.clicked.connect(self._clear_history)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(clear_btn)
        outer.addLayout(title_row)

        self._hist_search = QLineEdit()
        self._hist_search.setPlaceholderText("🔍 Поиск в истории…")
        self._hist_search.setFixedHeight(26)
        self._hist_search.setObjectName("addrSearch")
        self._hist_search_timer = QTimer(self)
        self._hist_search_timer.setSingleShot(True)
        self._hist_search_timer.setInterval(200)
        self._hist_search_timer.timeout.connect(self._refresh_history)
        self._hist_search.textChanged.connect(self._hist_search_timer.start)
        outer.addWidget(self._hist_search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._hist_container = QWidget()
        self._hist_layout = QVBoxLayout(self._hist_container)
        self._hist_layout.setContentsMargins(0, 0, 4, 0)
        self._hist_layout.setSpacing(3)
        self._hist_layout.addStretch()

        scroll.setWidget(self._hist_container)
        outer.addWidget(scroll)
        self._hist_scroll = scroll

        # Кэшируем иконки один раз
        _ico_size = QSize(16, 16)
        def _load_icon(name: str) -> "QPixmap | None":
            p = _assets_dir() / name
            if not p.exists():
                return None
            pix = QPixmap(str(p))
            return pix.scaled(_ico_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation) if not pix.isNull() else None

        self._hist_max_pix = _load_icon("max.ico")
        self._hist_vk_pix = _load_icon("vk_2.ico")

        self._refresh_history()
        return frame

    def _refresh_history(self) -> None:
        # удаляем все виджеты кроме последнего stretch
        while self._hist_layout.count() > 1:
            item = self._hist_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        filter_text = self._hist_search.text().strip().lower()

        entries = history_manager.load()
        if filter_text:
            def _matches(e: dict) -> bool:
                addrs = " ".join(e.get("max", [])).lower()
                platforms = ("max " if e.get("max") else "") + ("вк вконтакте " if e.get("vk") else "")
                return filter_text in addrs or filter_text in platforms
            entries = [e for e in entries if _matches(e)]

        if not entries:
            lbl = QLabel("Нет записей")
            lbl.setObjectName("histEmpty")
            self._hist_layout.insertWidget(0, lbl)
            return

        max_pix = self._hist_max_pix
        vk_pix = self._hist_vk_pix

        for entry in entries:
            row = self._make_history_row(entry, max_pix, vk_pix)
            self._hist_layout.insertWidget(self._hist_layout.count() - 1, row)

        # Прокручиваем к началу (новые записи — вверху)
        QTimer.singleShot(0, lambda: self._hist_scroll.verticalScrollBar().setValue(0))

    def _make_history_row(self, entry: dict, max_pix: "QPixmap | None", vk_pix: "QPixmap | None") -> QFrame:
        row = QFrame()
        status = entry.get("status", "")
        entry_id = entry.get("id", "")

        if status in ("scheduled", "scheduled_vk"):
            row.setObjectName("histEntryScheduled")
        elif status == "publishing":
            row.setObjectName("histEntryPublishing")
        elif status == "failed":
            row.setObjectName("histEntryFailed")
        else:
            row.setObjectName("histEntry")

        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Дата/время
        sched_at = entry.get("scheduled_at", "")
        ts = entry.get("ts", "").replace("  ", " ").strip()
        if status in ("scheduled", "scheduled_vk") and sched_at:
            ts_display = sched_at[:16]
        elif status == "publishing":
            ts_display = "Публикуется…"
        else:
            ts_display = ts[:16] if len(ts) > 16 else ts

        date_lbl = QLabel(ts_display)
        date_lbl.setObjectName("histDate")
        layout.addWidget(date_lbl)

        # Бейдж для отложенных постов
        if status == "scheduled_vk":
            badge = QLabel("ВК: в очереди")
            badge.setObjectName("histBadgeScheduled")
            layout.addWidget(badge)
        elif status == "scheduled":
            badge = QLabel("Отложен")
            badge.setObjectName("histBadgeScheduled")
            layout.addWidget(badge)

        # Иконки платформ
        for has, pix, fallback_text in (
            ("max" in entry, max_pix, "MAX"),
            (bool(entry.get("vk")), vk_pix, "VK"),
        ):
            if not has:
                continue
            ico_lbl = QLabel()
            if pix:
                ico_lbl.setPixmap(pix)
                ico_lbl.setFixedSize(16, 16)
            else:
                ico_lbl.setText(fallback_text)
                ico_lbl.setObjectName("histPlatformFallback")
            layout.addWidget(ico_lbl)

        # Текст публикации
        snippet = entry.get("text", "")
        if not snippet and "max" in entry:
            addrs = entry["max"]
            snippet = ", ".join(addrs[:1]) if isinstance(addrs, list) else str(addrs)
        text_lbl = QLabel(snippet or "—")
        text_lbl.setObjectName("histText")
        text_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(text_lbl)

        # Кнопка отмены только для "scheduled"
        if status == "scheduled" and entry_id:
            cancel_btn = QPushButton("✕")
            cancel_btn.setObjectName("histCancelScheduled")
            cancel_btn.setFixedSize(18, 18)
            cancel_btn.setToolTip("Отменить отложенный пост")
            cancel_btn.clicked.connect(lambda _=False, eid=entry_id: self._cancel_scheduled(eid))
            layout.addWidget(cancel_btn)
        elif status == "scheduled_vk" and entry_id:
            info_lbl = QLabel("отмена — через ВК")
            info_lbl.setObjectName("histDate")
            layout.addWidget(info_lbl)

        # Кнопка редактирования — для опубликованных ВК-постов с post_id
        vk_post_id = entry.get("vk_post_id")
        if vk_post_id and entry.get("vk") and status not in ("scheduled", "scheduled_vk", "publishing"):
            edit_btn = QPushButton("✏")
            edit_btn.setObjectName("histEditBtn")
            edit_btn.setFixedSize(22, 22)
            edit_btn.setToolTip("Редактировать пост в ВКонтакте")
            edit_btn.clicked.connect(
                lambda _=False, pid=vk_post_id, t=entry.get("text", ""): self._edit_vk_post(pid, t)
            )
            layout.addWidget(edit_btn)

        # Информация об отсутствии редактирования для MAX
        if entry.get("max") and not entry.get("vk") and status not in ("scheduled", "publishing", "failed"):
            info_btn = QPushButton("ℹ")
            info_btn.setObjectName("histInfoBtn")
            info_btn.setFixedSize(22, 22)
            info_btn.setToolTip("Редактирование MAX")
            info_btn.clicked.connect(self._show_max_edit_info)
            layout.addWidget(info_btn)

        return row

    # ──────────────────────────────────────────────────────────────────
    #  Редактирование постов
    # ──────────────────────────────────────────────────────────────────

    def _edit_vk_post(self, post_id: int, current_text: str) -> None:
        """Открывает диалог редактирования поста ВКонтакте."""
        dlg = VkEditDialog(post_id=post_id, current_text=current_text, parent=self)
        dlg.load_requested.connect(lambda: self._load_vk_post_text(dlg, post_id))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_text = dlg.new_text()
        if not new_text:
            QMessageBox.warning(self, "Редактирование", "Текст не может быть пустым.")
            return
        image_path = str(dlg.new_image_path()) if dlg.new_image_path() else None
        worker = VkEditWorker(
            vk_sender=self.vk_sender,
            post_id=post_id,
            text=new_text,
            image_path=image_path,
            parent=self,
        )
        worker.done.connect(self._on_vk_edit_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.send_button.setEnabled(False)
        self.send_button.setText("Обновление ВК…")

    def _load_vk_post_text(self, dlg: "VkEditDialog", post_id: int) -> None:
        """Загружает текст поста из ВК и вставляет в диалог."""
        worker = VkLoadTextWorker(vk_sender=self.vk_sender, post_id=post_id, parent=self)
        worker.done.connect(lambda text: dlg.set_loaded_text(text) if text else
                            QMessageBox.warning(dlg, "Ошибка", "Не удалось загрузить текст из ВКонтакте."))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_vk_edit_done(self, success: bool, message: str) -> None:
        self.send_button.setEnabled(True)
        self.send_button.setText("Опубликовать")
        if success:
            self._tray_notify("ВКонтакте: пост обновлён ✓", message)
            QMessageBox.information(self, "Готово", message)
        else:
            tg_notify.send_error("Ошибка редактирования ВК", message)
            QMessageBox.critical(self, "Ошибка", f"Не удалось обновить пост:\n\n{message}")

    def _show_max_edit_info(self) -> None:
        QMessageBox.information(
            self, "Редактирование MAX",
            "Редактирование сообщений в MAX через API не поддерживается.\n\n"
            "Чтобы исправить пост:\n"
            "1. Зайдите в группу MAX вручную\n"
            "2. Удалите старое сообщение\n"
            "3. Отправьте исправленную версию через программу",
        )

    def _clear_history(self) -> None:
        history_manager.clear()
        self._refresh_history()

    # ──────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet(get_stylesheet())

    def _has_unchecked_items(self) -> bool:
        """True если есть хотя бы один непомеченный адрес."""
        return any(
            self._addr_list.item(i).checkState() != Qt.CheckState.Checked
            for i in range(self._addr_list.count())
            if self._addr_list.item(i)
        )

    def _on_addr_item_changed(self) -> None:
        """Один слот вместо двух подключений itemChanged."""
        self._update_checklist()
        self.save_state()
        self._select_all_btn.setToolTip(
            "Снять все" if not self._has_unchecked_items() else "Выбрать все"
        )

    def _toggle_select_all(self) -> None:
        """Выбирает все адреса или снимает все."""
        has_unchecked = self._has_unchecked_items()
        new_state = Qt.CheckState.Checked if has_unchecked else Qt.CheckState.Unchecked
        try:
            self._addr_list.blockSignals(True)
            for i in range(self._addr_list.count()):
                item = self._addr_list.item(i)
                if item:
                    item.setCheckState(new_state)
        finally:
            self._addr_list.blockSignals(False)
        self._on_addr_item_changed()

    def _on_select_all_chk(self, checked: bool) -> None:
        """Чекбокс «Выбрать все адреса» — загружает все адреса из Excel и отмечает их."""
        if checked:
            try:
                if self._matcher is None:
                    raise RuntimeError("Реестр адресов не загружен")
                all_matches = self._matcher.get_all()
            except Exception as exc:
                _log.warning("Не удалось загрузить все адреса: %s", exc)
                self._chk_select_all.blockSignals(True)
                self._chk_select_all.setChecked(False)
                self._chk_select_all.blockSignals(False)
                return
            # Собираем уже добавленные chat_id и адреса
            existing_ids: set[str] = set()
            existing_addrs: set[str] = set()
            for i in range(self._addr_list.count()):
                it = self._addr_list.item(i)
                if not it:
                    continue
                m = it.data(Qt.ItemDataRole.UserRole)
                if m:
                    existing_addrs.add(m.address)
                    if m.chat_id:
                        existing_ids.add(m.chat_id)
            try:
                self._addr_list.blockSignals(True)
                for match in all_matches:
                    if match.address in existing_addrs:
                        # уже есть — просто отмечаем
                        for i in range(self._addr_list.count()):
                            it = self._addr_list.item(i)
                            if it and it.data(Qt.ItemDataRole.UserRole) and \
                                    it.data(Qt.ItemDataRole.UserRole).address == match.address:
                                it.setCheckState(Qt.CheckState.Checked)
                        continue
                    if match.chat_id and match.chat_id in existing_ids:
                        continue
                    item = QListWidgetItem(match.address)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                    item.setData(Qt.ItemDataRole.UserRole, match)
                    item.setData(_MANUAL_ROLE, True)
                    self._addr_list.addItem(item)
                    existing_addrs.add(match.address)
                    if match.chat_id:
                        existing_ids.add(match.chat_id)
            finally:
                self._addr_list.blockSignals(False)
        else:
            # Снимаем галочки со всех адресов
            try:
                self._addr_list.blockSignals(True)
                for i in range(self._addr_list.count()):
                    it = self._addr_list.item(i)
                    if it:
                        it.setCheckState(Qt.CheckState.Unchecked)
            finally:
                self._addr_list.blockSignals(False)
        self._on_addr_item_changed()

    def _export_report_csv(self) -> None:
        """Сохраняет лог последней рассылки в CSV."""
        if not self._send_log_results:
            return
        default_name = f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить отчёт", default_name, "CSV файлы (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Адрес", "Статус", "Время"])
                for addr, success, ts in self._send_log_results:
                    writer.writerow([addr, "Успех" if success else "Ошибка", ts])
            QMessageBox.information(self, "Отчёт", f"Отчёт сохранён:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить файл:\n{exc}")

    def _save_error_log(self, message: str) -> None:
        """Автоматически дописывает ошибку отправки в %APPDATA%\\MAX POST\\send_errors.log."""
        try:
            log_dir = Path(os.getenv("APPDATA", "")) / "MAX POST"
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / "send_errors.log"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n{ts}\n{message}\n")
        except OSError:
            pass

    def _retry_one(self, match) -> None:
        """Повторяет отправку для одного адреса."""
        self._load_matches_to_addr_list([match])
        self.send_post()

    def _retry_send(self) -> None:
        """Повторяет отправку для всех адресов с ошибками из последней рассылки."""
        failed: list = []
        for i in range(self._send_log_list.count()):
            item = self._send_log_list.item(i)
            if item:
                m = item.data(_LOG_MATCH_ROLE)
                if m and not any(ok for a, ok, _ in self._send_log_results if a == m.address):
                    failed.append(m)
        if not failed:
            return
        self._load_matches_to_addr_list(failed)
        self.send_post()

    def _load_matches_to_addr_list(self, matches: list) -> None:
        """Заменяет список адресов переданными MatchResult и показывает список."""
        self._addr_list.clear()
        try:
            self._addr_list.blockSignals(True)
            for match in matches:
                new_item = QListWidgetItem(match.address)
                new_item.setFlags(new_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                new_item.setCheckState(Qt.CheckState.Checked)
                new_item.setData(Qt.ItemDataRole.UserRole, match)
                new_item.setData(_MANUAL_ROLE, True)
                self._addr_list.addItem(new_item)
        finally:
            self._addr_list.blockSignals(False)
        self._on_addr_item_changed()
        self._retry_btn.hide()
        self._save_report_btn.hide()
        self._send_log_list.hide()
        self._addr_list.show()

    # ── Отправка выделенного текста (ПКМ в поле ввода) ───────────

    def _send_selection_max(self, text: str) -> None:
        """Отправить выделенный текст в MAX (во все отмеченные адреса)."""
        if self._worker is not None or self._smart_worker is not None or self._sel_worker is not None:
            QMessageBox.warning(self, "Отправка", "Дождитесь завершения текущей рассылки.")
            return
        checked = self._get_checked_matches()
        chat_ids = list(dict.fromkeys(m.chat_id for m in checked if m.chat_id))
        if not chat_ids:
            QMessageBox.warning(
                self, "Нет адресов",
                "Не выбрано ни одного адреса для отправки в MAX.\n\n"
                "Выберите адреса через поле 🔍 Поиск или кнопку «+»,\n"
                "затем повторите отправку через ПКМ."
            )
            self._addr_search.setFocus()
            return
        preview = text[:120].replace("&", "&amp;").replace("<", "&lt;")
        if len(text) > 120:
            preview += "…"
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Отправить в MAX")
        dlg.setText(
            f"Отправить выделенный текст в <b>{len(chat_ids)}</b> групп MAX?"
            f"<br><br><i>{preview}</i>"
        )
        dlg.setIcon(QMessageBox.Icon.Question)
        btn_yes = dlg.addButton("Отправить", QMessageBox.ButtonRole.AcceptRole)
        dlg.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        dlg.exec()
        if dlg.clickedButton() != btn_yes:
            return
        self._run_selection_worker(text, send_max=True, send_vk=False, chat_ids=chat_ids)

    def _send_selection_vk(self, text: str) -> None:
        """Отправить выделенный текст в ВКонтакте."""
        if self._worker is not None or self._smart_worker is not None or self._sel_worker is not None:
            QMessageBox.warning(self, "Отправка", "Дождитесь завершения текущей рассылки.")
            return
        if not os.getenv("VK_GROUP_TOKEN"):
            QMessageBox.warning(
                self, "Отправка",
                "Не задан токен ВКонтакте (VK_GROUP_TOKEN).\n"
                "Откройте Настройки подключений (🔑) и заполните данные."
            )
            return
        preview = text[:120].replace("&", "&amp;").replace("<", "&lt;")
        if len(text) > 120:
            preview += "…"
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Отправить в ВКонтакте")
        dlg.setText(
            f"Отправить выделенный текст в ВКонтакте?"
            f"<br><br><i>{preview}</i>"
        )
        dlg.setIcon(QMessageBox.Icon.Question)
        btn_yes = dlg.addButton("Отправить", QMessageBox.ButtonRole.AcceptRole)
        dlg.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        dlg.exec()
        if dlg.clickedButton() != btn_yes:
            return
        self._run_selection_worker(text, send_max=False, send_vk=True, chat_ids=[])

    def _run_selection_worker(self, text: str, send_max: bool, send_vk: bool, chat_ids: list) -> None:
        """Запускает SendWorker для отправки выделенного текста."""
        worker = SendWorker(
            max_sender=self.max_sender,
            vk_sender=self.vk_sender,
            chat_ids=chat_ids,
            text=text,
            image_path=str(self.image_path) if self.image_path else None,
            send_max=send_max,
            send_vk=send_vk,
            dry_run=False,
            extra_delay=int(os.getenv("SEND_DELAY_SEC", "0") or 0),
            delay_min=0,
            delay_max=0,
        )
        self._sel_worker = worker
        worker.progress.connect(lambda msg: self.setWindowTitle(f"MAX POST — {msg}"))
        worker.result_ready.connect(self._on_selection_send_done)
        worker.finished.connect(lambda: self.setWindowTitle("MAX POST"))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: setattr(self, "_sel_worker", None))
        worker.start()

    def _on_selection_send_done(self, success: bool, msg: str) -> None:
        icon = "✅" if success else "⚠️"
        QMessageBox.information(self, "Результат отправки выделенного текста", f"{icon}\n\n{msg}")

    def _update_checklist(self) -> None:
        def row(ok: bool, label: str) -> str:
            if ok:
                return f'<span style="color:#22a35a;">&#10003;</span>  {label}'
            return f'<span style="color:#c0c8d4;">&#9679;</span>  <span style="color:#aab0bb;">{label}</span>'

        has_text    = bool(self.text_input.toPlainText().strip())
        has_photo   = self.image_path is not None
        checked     = self._get_checked_matches()
        has_address = len(checked) > 0
        has_platform = self.chk_max.isChecked() or self.chk_vk.isChecked()

        # Счётчик выбранных адресов в заголовке (отмечено/всего)
        n = len(checked)
        total = sum(
            1 for i in range(self._addr_list.count())
            if self._addr_list.item(i) and self._addr_list.item(i).data(Qt.ItemDataRole.UserRole)
        )
        if total > 0:
            self._addr_count_lbl.setText(f"Адреса для рассылки MAX  ({n}/{total})")
        else:
            self._addr_count_lbl.setText("Адреса для рассылки MAX")

        # Поиск по Excel — показываем если файл адресов доступен
        if self._matcher is None and self.excel_path.exists():
            self._matcher = ExcelMatcher(self.excel_path)
        if self._matcher is not None:
            self._addr_search.show()

        self._cl_text.setText(row(has_text, "Текст введён"))
        if has_photo:
            self._cl_photo.setText(
                '<span style="color:#22a35a;">&#10003;</span>  Фото загружено'
            )
        elif self._chk_require_photo.isChecked():
            self._cl_photo.setText(
                '<span style="color:#e07b00;">&#9679;</span>'
                '  <span style="color:#e07b00;">Фото обязательно</span>'
            )
        else:
            self._cl_photo.setText(
                '<span style="color:#c0c8d4;">&#9675;</span>'
                '  <span style="color:#aab0bb;">Без фото (опционально)</span>'
            )
        self._cl_address.setText(row(has_address, "Адрес найден"))
        self._cl_platform.setText(row(has_platform, "Платформа выбрана"))

    def sync_preview(self) -> None:
        text = self.text_input.toPlainText()
        self.preview.set_preview_text(text)
        count = len(text)
        self._char_counter.setText(f"{count}/{TEXT_CHAR_LIMIT}")
        if count > TEXT_CHAR_LIMIT:
            self._char_counter.setStyleSheet("color: #cc0000; font-weight: 700;")
        elif count > 3500:
            self._char_counter.setStyleSheet("color: #e07800; font-weight: 600;")
        else:
            self._char_counter.setStyleSheet("color: #888;")

        if self.chk_vk.isChecked():
            self._vk_char_counter.setText(f"ВК {count}/{VK_WALL_TEXT_LIMIT}")
            if count > VK_WALL_TEXT_LIMIT:
                self._vk_char_counter.setStyleSheet("color: #cc0000; font-weight: 700;")
            elif count > VK_WALL_TEXT_LIMIT * 0.9:
                self._vk_char_counter.setStyleSheet("color: #e07800; font-weight: 600;")
            else:
                self._vk_char_counter.setStyleSheet("color: #888;")
            self._vk_char_counter.show()
        else:
            self._vk_char_counter.hide()

        self._update_checklist()
        self.save_state()
        self._parse_timer.start()

    def _toggle_emoji_picker(self) -> None:
        if self._emoji_picker is None:
            self._emoji_picker = EmojiPicker(self)
            self._emoji_picker.emoji_selected.connect(self._insert_emoji)
        if self._emoji_picker.isVisible():
            self._emoji_picker.hide()
        else:
            self._emoji_picker.show_near(self._emoji_btn)

    def _insert_emoji(self, emoji: str) -> None:
        cursor = self.text_input.textCursor()
        cursor.insertText(emoji)
        self.text_input.setTextCursor(cursor)
        self.text_input.setFocus()

    def _on_photo_pin_toggled(self, checked: bool) -> None:
        self._photo_pinned = checked
        self._pin_photo_btn.setToolTip(
            "Фото закреплено — не сбросится при очистке формы" if checked
            else "Закрепить фото — не сбрасывать при очистке формы"
        )

    def _on_require_photo_toggled(self, _checked: bool) -> None:
        self._update_checklist()
        self.save_state()

    def _check_excel_changed(self) -> None:
        """Проверяет, изменился ли файл реестра адресов."""
        try:
            mtime = self.excel_path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime != self._excel_mtime:
            self._excel_mtime = mtime
            self._excel_changed_bar.show()
            self._tray_notify(
                "Реестр адресов изменён",
                f"{self.excel_path.name} обновлён — нажмите кнопку в приложении для перезагрузки",
            )

    def _reload_excel_silent(self) -> None:
        """Перезагружает Excel без диалога, скрывает тостер."""
        self._excel_changed_bar.hide()
        self._matcher = None
        if self.excel_path.exists():
            self._matcher = ExcelMatcher(self.excel_path)
            _warmup = _ExcelWarmupWorker(self._matcher, self)
            _warmup.finished.connect(_warmup.deleteLater)
            _warmup.start()
            self._excel_mtime = self.excel_path.stat().st_mtime
            self._tray_notify("Реестр обновлён", f"{self.excel_path.name} перезагружен.")

    def _add_to_recent_photos(self, path: str) -> None:
        """Добавляет путь в начало списка последних фото (макс. 5, без дублей)."""
        self._recent_photos = [p for p in self._recent_photos if p != path]
        self._recent_photos.insert(0, path)
        self._recent_photos = self._recent_photos[:5]
        self._rebuild_recent_bar()

    def _rebuild_recent_bar(self) -> None:
        """Перестраивает галерею миниатюр последних фото."""
        while self._recent_bar_layout.count() > 0:
            item = self._recent_bar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        valid = [p for p in self._recent_photos if Path(p).exists()]
        self._recent_photos = valid[:5]

        for path in valid:
            pix = QPixmap(path)
            if pix.isNull():
                continue
            thumb = pix.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            btn = QPushButton()
            btn.setFixedSize(48, 48)
            btn.setIcon(QIcon(thumb))
            btn.setIconSize(QSize(44, 44))
            btn.setToolTip(Path(path).name)
            btn.setObjectName("recentPhotoBtn")
            btn.clicked.connect(lambda _checked, p=path: self._use_recent_photo(p))
            self._recent_bar_layout.addWidget(btn)

        visible = bool(valid)
        self._recent_bar.setVisible(visible)
        self._recent_sep_left.setVisible(visible)
        self._recent_sep_right.setVisible(visible)

    def _use_recent_photo(self, path: str) -> None:
        """Устанавливает выбранное из галереи фото как текущее."""
        p = Path(path)
        if not p.exists():
            self._rebuild_recent_bar()
            return
        self.image_path = p
        self.preview.set_image(str(p))
        self._set_photo_button_name(p.name)
        self._update_photo_thumb()
        self._update_checklist()
        self.save_state()

    def _update_photo_thumb(self) -> None:
        """Миниатюра отключена."""
        return
        if self.image_path:
            pix = QPixmap(str(self.image_path))
            if not pix.isNull():
                thumb = pix.scaled(
                    self._photo_thumb.width() or 400, 100,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._photo_thumb.setPixmap(thumb)
                self._photo_thumb.show()
                return
        self._photo_thumb.clear()
        self._photo_thumb.hide()

    def select_image(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Выберите изображение", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not file_name:
            return
        self.image_path = Path(file_name)
        self.preview.set_image(str(self.image_path))
        self._set_photo_button_name(self.image_path.name)
        self._update_photo_thumb()
        self._add_to_recent_photos(str(self.image_path))
        self._update_checklist()
        self.save_state()

    def set_photo_from_external(self, path: str) -> None:
        """Вставляет фото из внешнего источника (например, Общие файлы)."""
        self.image_path = Path(path)
        self.preview.set_image(str(self.image_path))
        self._set_photo_button_name(self.image_path.name)
        self._update_photo_thumb()
        self._add_to_recent_photos(str(self.image_path))
        self._update_checklist()
        self.save_state()

    def _set_photo_button_name(self, name: str) -> None:
        short = name if len(name) <= 22 else name[:19] + "…"
        self.photo_button.setText(f"✓  {short}")
        self.photo_button.setObjectName("photoButtonDone")
        self.photo_button.setStyle(self.photo_button.style())

    def _on_addr_search_changed(self, text: str) -> None:
        """Запускает debounce-таймер при изменении текста в поиске."""
        self._addr_search_timer.start(150)
        if not text.strip():
            self._addr_search_results.hide()
            self._addr_search_results.clear()
            self._addr_search_add_btn.hide()

    def _do_addr_search(self) -> None:
        """Запускает поиск адресов в фоновом потоке — не блокирует UI."""
        q = self._addr_search.text().strip()
        if not q or len(q) < 2:
            self._addr_search_results.hide()
            self._addr_search_results.clear()
            return

        if self._matcher is None:
            if self.excel_path.exists():
                self._matcher = ExcelMatcher(self.excel_path)
            else:
                return

        # Отменяем предыдущий поиск если ещё идёт
        if self._addr_search_worker and self._addr_search_worker.isRunning():
            self._addr_search_worker.quit()
            self._addr_search_worker.wait(50)

        worker = _AddrSearchWorker(q, self._matcher, self)
        worker.results_ready.connect(self._on_addr_search_results)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: setattr(self, '_addr_search_worker', None) if self._addr_search_worker is w else None)
        self._addr_search_worker = worker
        worker.start()

    def _on_addr_search_results(self, results: list) -> None:
        """Показывает результаты поиска с чекбоксами (вызывается из фонового потока)."""
        try:
            self._addr_search_results.blockSignals(True)
            self._addr_search_results.clear()
            if not results:
                self._addr_search_results.hide()
                self._addr_search_add_btn.hide()
                return
            for match in results:
                item = QListWidgetItem(match.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, match)
                self._addr_search_results.addItem(item)
            self._addr_search_results.show()
            self._update_search_add_btn()
        finally:
            self._addr_search_results.blockSignals(False)

    def _on_addr_search_check_changed(self, item: QListWidgetItem) -> None:
        self._update_search_add_btn()

    def _update_search_add_btn(self) -> None:
        """Обновляет текст и видимость кнопки «Добавить»."""
        total = self._addr_search_results.count()
        if total == 0:
            self._addr_search_add_btn.hide()
            return
        checked = sum(
            1 for i in range(total)
            if self._addr_search_results.item(i).checkState() == Qt.CheckState.Checked
        )
        if checked:
            self._addr_search_add_btn.setText(f"✓ Добавить ({checked})")
        else:
            self._addr_search_add_btn.setText(f"Добавить все ({total})")
        self._addr_search_add_btn.show()

    def _addr_search_accept_first(self) -> None:
        """Enter в поле поиска — добавляет отмеченные (или все если ничего не отмечено)."""
        if self._addr_search_results.count() > 0:
            self._add_checked_search_results()

    def _add_checked_search_results(self) -> None:
        """Добавляет все отмеченные адреса (или все если ничего не отмечено)."""
        total = self._addr_search_results.count()
        if not total:
            return

        checked_items = [
            self._addr_search_results.item(i)
            for i in range(total)
            if self._addr_search_results.item(i).checkState() == Qt.CheckState.Checked
        ]
        # Если ничего не отмечено — добавляем все
        if not checked_items:
            checked_items = [self._addr_search_results.item(i) for i in range(total)]

        added = 0
        for item in checked_items:
            match: MatchResult | None = item.data(Qt.ItemDataRole.UserRole)
            if not match:
                continue
            # Проверяем дубликаты
            duplicate = False
            for i in range(self._addr_list.count()):
                ex = self._addr_list.item(i)
                if not ex:
                    continue
                ex_m = ex.data(Qt.ItemDataRole.UserRole)
                if ex_m and (ex_m.address == match.address or
                             (match.chat_id and ex_m.chat_id == match.chat_id)):
                    duplicate = True
                    break
            if duplicate:
                continue
            new_item = QListWidgetItem(match.address)
            new_item.setFlags(new_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            new_item.setCheckState(Qt.CheckState.Checked)
            new_item.setData(Qt.ItemDataRole.UserRole, match)
            new_item.setData(_MANUAL_ROLE, True)
            self._addr_list.addItem(new_item)
            added += 1

        if added:
            self._update_checklist()
            self.save_state()

        self._addr_search.clear()
        self._addr_search_results.hide()
        self._addr_search_add_btn.hide()

    def clear_form(self) -> None:
        self._save_report_btn.hide()
        self._retry_btn.hide()
        self._send_log_results = []
        self.text_input.clear()
        self._addr_search.clear()
        self._addr_list.clear()
        self.preview.set_preview_text("")
        if not self._photo_pinned:
            self.preview.set_image(None)
            self.image_path = None
            self.photo_button.setText("Загрузить фото")
            self.photo_button.setObjectName("photoButton")
            self.photo_button.setStyle(self.photo_button.style())
        self._update_photo_thumb()
        self._update_checklist()
        self.save_state()

    def _get_checked_addr_count(self) -> int:
        """Количество отмеченных адресов с chat_id (для счётчика в ПКМ меню)."""
        return sum(
            1 for i in range(self._addr_list.count())
            if (item := self._addr_list.item(i))
            and item.checkState() == Qt.CheckState.Checked
            and item.data(Qt.ItemDataRole.UserRole)
            and item.data(Qt.ItemDataRole.UserRole).chat_id
        )

    def _get_checked_matches(self) -> list[MatchResult]:
        results = []
        for i in range(self._addr_list.count()):
            item = self._addr_list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                match = item.data(Qt.ItemDataRole.UserRole)
                if match:
                    results.append(match)
        return results

    def _toggle_addr_item(self, item: "QListWidgetItem") -> None:
        """Двойной клик — переключает галочку адреса."""
        new_state = (Qt.CheckState.Unchecked
                     if item.checkState() == Qt.CheckState.Checked
                     else Qt.CheckState.Checked)
        try:
            self._addr_list.blockSignals(True)
            item.setCheckState(new_state)
        finally:
            self._addr_list.blockSignals(False)
        self._on_addr_item_changed()

    def _open_font_picker(self) -> None:
        prev_family = self._ui_font_family
        prev_size   = self._ui_font_size
        families = self._ui_font_families or ["Sans Serif"]
        dlg = FontPickerDialog(
            families, prev_family, prev_size, parent=self
        )
        dlg.font_changed.connect(self._apply_ui_font)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.reset_requested():
                self._apply_ui_font("", 13)
            else:
                self._apply_ui_font(dlg.selected_family(), dlg.selected_size())
            self.save_state()
        else:
            self._apply_ui_font(prev_family, prev_size)  # откат к сохранённому

    def _apply_ui_font(self, family: str, size: int) -> None:
        """Применить шрифт ко всему интерфейсу. family='' — сброс на системный."""
        self._ui_font_family = family
        self._ui_font_size = size if size > 0 else 13
        app = QApplication.instance()
        if app:
            app.setFont(QFont(family, self._ui_font_size) if family else QFont())
        self._apply_styles()   # перегенерировать stylesheet с новым font-family

    def _open_theme_picker(self) -> None:
        prev_index = self._bg_index
        prev_mode = self._bg_mode
        prev_opacity = self._bg_opacity
        dlg = ThemePickerDialog(
            _assets_dir(), self._bg_index, self._bg_mode, self._bg_opacity, parent=self
        )
        dlg.preview_changed.connect(lambda idx, m, o: self._apply_theme(idx, m, o, save=False))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_theme(dlg.selected_index(), dlg.selected_mode(), dlg.selected_opacity())
        else:
            self._apply_theme(prev_index, prev_mode, prev_opacity, save=False)

    def _apply_theme(self, index: "int | None", mode: int = 0, opacity_pct: int = 50, save: bool = True) -> None:
        self._bg_index = index
        self._bg_mode = mode
        self._bg_opacity = opacity_pct
        if self._bg_widget is not None:
            if index is None:
                self._bg_widget.set_background(None)
            else:
                assets = _assets_dir()
                path = next(
                    (assets / f"fon_{index}{ext}" for ext in (".jpg", ".png")
                     if (assets / f"fon_{index}{ext}").exists()),
                    assets / f"fon_{index}.jpg",
                )
                pix = QPixmap(str(path)) if path.exists() else QPixmap()
                self._bg_widget.set_background(pix, mode, opacity_pct)
        if save:
            self.save_state()

    # ──────────────────────────────────────────────────────────────────
    #  Аватар предпросмотра
    # ──────────────────────────────────────────────────────────────────

    def _sync_preview_avatar(self, _state=None) -> None:
        """Обновить шапку платформы в зависимости от выбранной платформы."""
        if self.chk_vk.isChecked() and not self.chk_max.isChecked():
            self.preview.set_platform_avatar("vk", _assets_dir())
        else:
            self.preview.set_platform_avatar("max", _assets_dir())

    def _addr_list_context_menu(self, pos) -> None:
        """ПКМ на адресе — меню с кнопкой Удалить."""
        item = self._addr_list.itemAt(pos)
        if not item:
            return
        self._addr_list.setCurrentItem(item)
        self._last_addr_row = self._addr_list.row(item)
        menu = QMenu(self)
        del_act = QAction("🗑  Удалить адрес", self)
        del_act.triggered.connect(self._delete_selected_address)
        menu.addAction(del_act)
        menu.exec(self._addr_list.mapToGlobal(pos))

    def eventFilter(self, obj, event) -> bool:
        from PyQt6.QtCore import QEvent
        if obj is self._addr_list and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._delete_selected_address()
                return True
        return super().eventFilter(obj, event)

    def _delete_selected_address(self) -> None:
        """Удаляет выбранный адрес из списка."""
        row = self._last_addr_row
        cur = self._addr_list.currentItem()
        if cur is not None:
            row = self._addr_list.row(cur)
        if row < 0 or row >= self._addr_list.count():
            return
        item = self._addr_list.item(row)
        if item is None:
            return
        self._addr_list.takeItem(row)
        self._last_addr_row = -1
        self._update_checklist()
        self.save_state()

    def _add_address_manually(self) -> None:
        dlg = AddAddressDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        match = dlg.result_match()
        # Если chat_id не введён вручную — ищем адрес в Excel
        if not match.chat_id:
            if self._matcher is None and self.excel_path.exists():
                self._matcher = ExcelMatcher(self.excel_path)
            if self._matcher is not None:
                resolved = None
                parsed_list = extract_all_addresses(match.address)
                if parsed_list:
                    try:
                        hits = self._matcher.find_matches(parsed_list[0])
                        resolved = hits[0] if hits else None
                    except Exception:
                        pass
                if resolved is None:
                    hits = self._matcher.search(match.address, limit=1)
                    resolved = hits[0] if hits else None
                if resolved:
                    match = resolved
        item = QListWidgetItem(match.address)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, match)
        item.setData(_MANUAL_ROLE, True)
        self._addr_list.addItem(item)
        self._update_checklist()
        self.save_state()

    def _paste_addresses(self) -> None:
        """Вставка нескольких адресов сразу через диалог с подтверждением."""
        dlg = PasteAddressesDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        raw_matches = dlg.result_matches()

        # Для каждого адреса без chat_id — ищем в Excel
        if self._matcher is None and self.excel_path.exists():
            self._matcher = ExcelMatcher(self.excel_path)

        # Строим список (исходный текст, resolved MatchResult | None)
        results: list[tuple[str, "MatchResult | None"]] = []
        for m in raw_matches:
            original = m.address
            if m.chat_id:
                results.append((original, m))
            elif self._matcher is not None:
                # Используем extract_all_addresses + find_matches для поиска в реестре
                parsed_list = extract_all_addresses(original)
                resolved = None
                if parsed_list:
                    try:
                        hits = self._matcher.find_matches(parsed_list[0])
                        resolved = hits[0] if hits else None
                    except Exception:
                        pass
                # Fallback: простой поиск по тексту
                if resolved is None:
                    hits = self._matcher.search(original, limit=1)
                    resolved = hits[0] if hits else None
                results.append((original, resolved))
            else:
                results.append((original, None))

        # Показываем диалог с результатами поиска
        confirm = _PasteConfirmDialog(results, parent=self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return

        to_add = confirm.accepted_matches()

        # Собираем уже существующие для проверки дублей
        existing_addrs: set[str] = set()
        existing_ids: set[str] = set()
        for i in range(self._addr_list.count()):
            ex = self._addr_list.item(i)
            if not ex:
                continue
            ex_m = ex.data(Qt.ItemDataRole.UserRole)
            if ex_m:
                existing_addrs.add(ex_m.address)
                if ex_m.chat_id:
                    existing_ids.add(ex_m.chat_id)

        added = 0
        for match in to_add:
            if match.address in existing_addrs:
                continue
            if match.chat_id and match.chat_id in existing_ids:
                continue
            item = QListWidgetItem(match.address)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, match)
            item.setData(_MANUAL_ROLE, True)
            self._addr_list.addItem(item)
            existing_addrs.add(match.address)
            if match.chat_id:
                existing_ids.add(match.chat_id)
            added += 1
        if added:
            self._update_checklist()
            self.save_state()

    def send_post(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # отправка уже идёт — игнорируем повторное нажатие
        if self._chk_schedule.isChecked():
            self._schedule_post()
            return
        # Сбрасываем предыдущий лог и скрываем кнопки отчёта/повтора
        self._send_log_results = []
        self._save_report_btn.hide()
        self._retry_btn.hide()
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Отправка", "Нельзя отправить пустой текст.")
            return

        if self._chk_require_photo.isChecked() and self.image_path is None:
            QMessageBox.warning(self, "Отправка", "Фото не загружено.\nВыберите фото или снимите галочку «📷 Фото».")
            return

        send_max = self.chk_max.isChecked()
        send_vk = self.chk_vk.isChecked()

        if not send_max and not send_vk:
            QMessageBox.warning(self, "Отправка", "Выбери хотя бы одну платформу (MAX или ВКонтакте).")
            return

        checked = self._get_checked_matches()
        chat_ids = list(dict.fromkeys(m.chat_id for m in checked if m.chat_id))

        if send_max and not chat_ids:
            QMessageBox.warning(self, "Отправка", "Нет отмеченных адресов. Добавь адрес через кнопку + или вставь текст с адресом.")
            return

        # Проверяем наличие токенов
        if send_max and not (os.getenv("MAX_ID_INSTANCE") and os.getenv("MAX_API_TOKEN")):
            QMessageBox.warning(
                self, "Отправка",
                "Не заданы токены MAX (ID инстанса / API токен).\n"
                "Откройте Настройки подключений (🔑) и заполните данные."
            )
            return
        if send_vk and not os.getenv("VK_GROUP_TOKEN"):
            QMessageBox.warning(
                self, "Отправка",
                "Не задан токен ВКонтакте (VK_GROUP_TOKEN).\n"
                "Откройте Настройки подключений (🔑) и заполните данные."
            )
            return

        # Подтверждение при массовой рассылке (> 5 групп)
        if send_max and len(chat_ids) > 5:
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Подтверждение отправки")
            dlg.setText(
                f"Публикация будет отправлена в <b>{len(chat_ids)}</b> групп MAX."
                f"<br><br>Продолжить?"
            )
            dlg.setIcon(QMessageBox.Icon.Question)
            btn_yes = dlg.addButton("Отправить", QMessageBox.ButtonRole.AcceptRole)
            dlg.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            dlg.exec()
            if dlg.clickedButton() != btn_yes:
                return

        self.send_button.hide()
        self._cancel_button.setEnabled(True)
        self._cancel_button.setText("✕  Отменить отправку")
        self._cancel_button.show()
        self._progress_bar.show()

        self._pending_history = {
            "addresses": [m.address for m in checked],
            "send_max": send_max,
            "send_vk": send_vk,
            "text": text,
        }

        extra_delay = int(os.getenv("SEND_DELAY_SEC", "0") or 0)
        self._worker = SendWorker(
            max_sender=self.max_sender,
            vk_sender=self.vk_sender,
            chat_ids=chat_ids,
            text=text,
            image_path=str(self.image_path) if self.image_path else None,
            send_max=send_max,
            send_vk=send_vk,
            dry_run=False,
            extra_delay=extra_delay,
            delay_min=self._delay_min_spin.value() if self._chk_delay.isChecked() else 0,
            delay_max=self._delay_max_spin.value() if self._chk_delay.isChecked() else 0,
        )
        self._worker.progress.connect(self._on_send_progress)
        self._worker.progress_step.connect(self._on_send_step)
        self._worker.result_ready.connect(self._on_send_finished)

        # Лог отправки — показываем только если отправляем в MAX
        if send_max and checked:
            self._send_log_list.clear()
            for m in checked:
                log_item = QListWidgetItem(f"⏳  {m.address}")
                log_item.setData(Qt.ItemDataRole.UserRole, m.address)
                log_item.setData(_LOG_MATCH_ROLE, m)
                self._send_log_list.addItem(log_item)
            self._addr_list.hide()
            self._send_log_list.show()
            self._worker.address_result.connect(self._on_address_result)

        self._progress_bar.setRange(0, len(chat_ids) if send_max else 0)
        self._progress_bar.setValue(0)
        self._worker.start()

    def _on_address_result(self, idx: int, success: bool, msg: str) -> None:
        """Обновляет иконку строки в логе отправки (✓/✗)."""
        item = self._send_log_list.item(idx)
        if not item:
            return
        addr = item.data(Qt.ItemDataRole.UserRole)
        match = item.data(_LOG_MATCH_ROLE)
        if success:
            item.setText(f"✓  {addr}")
            item.setForeground(QColor("#16a34a"))
        else:
            item.setText("")
            item.setForeground(QColor("#dc2626"))
            # Вставляем виджет с кнопкой повтора для провальных адресов
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 0, 4, 0)
            row_layout.setSpacing(6)
            lbl = QLabel(f"✗  {addr}")
            lbl.setStyleSheet("color: #dc2626;")
            retry_btn = QPushButton("🔁")
            retry_btn.setFixedSize(24, 20)
            retry_btn.setToolTip("Повторить отправку")
            retry_btn.setStyleSheet(
                "QPushButton { font-size: 11px; padding: 0; border-radius: 3px; }"
            )
            if match:
                retry_btn.clicked.connect(lambda _checked=False, m=match: self._retry_one(m))
            row_layout.addWidget(lbl, 1)
            row_layout.addWidget(retry_btn)
            item.setSizeHint(row_widget.sizeHint())
            self._send_log_list.setItemWidget(item, row_widget)
        self._send_log_list.scrollToItem(item)
        ts = datetime.now().strftime("%H:%M:%S")
        self._send_log_results.append((addr, success, ts))

    def _on_send_progress(self, step: str) -> None:
        self.send_button.setText(step)
        self.setWindowTitle(f"MAX POST — {step}")

    def _on_send_step(self, current: int, total: int) -> None:
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)

    def _cancel_send(self) -> None:
        """Запрашивает отмену текущей рассылки."""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._cancel_button.setEnabled(False)
            self._cancel_button.setText("✕  Отменяется…")
            self.setWindowTitle("MAX POST — Отменяется…")
        if self._smart_worker and self._smart_worker.isRunning():
            self._smart_worker.cancel()
            self._cancel_button.setEnabled(False)
            self._cancel_button.setText("✕  Отменяется…")
            self.setWindowTitle("MAX POST — Отменяется…")
        if self._vk_sched_worker and self._vk_sched_worker.isRunning():
            self._vk_sched_worker.quit()
            self._vk_sched_worker = None
            self.send_button.setEnabled(True)
            self.send_button.setText("Запланировать" if self._chk_schedule.isChecked() else "Опубликовать")

    def _on_send_finished(self, success: bool, message: str) -> None:
        self._cancel_button.hide()
        self.send_button.show()
        self.send_button.setEnabled(True)
        self.send_button.setText("Опубликовать")
        self._progress_bar.hide()
        self.setWindowTitle("MAX POST")
        # Восстанавливаем список адресов после отправки
        self._send_log_list.hide()
        self._addr_list.show()
        # Показываем кнопки отчёта и повтора если есть результаты MAX
        if self._send_log_results:
            self._save_report_btn.show()
            failed_count = sum(1 for _, ok, _ in self._send_log_results if not ok)
            if failed_count:
                self._retry_btn.setText(f"🔁 Повторить ({failed_count})")
                self._retry_btn.show()
        vk_post_id = getattr(self._worker, "vk_post_id", None) if self._worker else None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if success:
            self._success_overlay.show_success()
            h = self._pending_history
            try:
                history_manager.add_entry(
                    addresses=h.get("addresses", []),
                    sent_max=h.get("send_max", False),
                    sent_vk=h.get("send_vk", False),
                    text=h.get("text", ""),
                    vk_post_id=vk_post_id,
                )
                self._refresh_history()
            except Exception as exc:
                _log.warning("Не удалось сохранить историю: %s", exc)
            tg_notify.send_post_done(
                addresses=h.get("addresses", []),
                send_max=h.get("send_max", False),
                send_vk=h.get("send_vk", False),
                text=h.get("text", ""),
            )
        else:
            tg_notify.send_error("Ошибка отправки поста", message)
            self._save_error_log(message)
            SendResultDialog(message, self).exec()

        self._notify_send_done(success)

    # ──────────────────────────────────────────────────────────────────
    #  Отложенные посты
    # ──────────────────────────────────────────────────────────────────

    def _on_delay_toggled(self, checked: bool) -> None:
        self._delay_widget.setVisible(checked)

    def _on_schedule_toggled(self, checked: bool) -> None:
        if checked:
            _now30 = QDateTime.currentDateTime().addSecs(30 * 60)
            self._sched_date.setMinimumDate(QDateTime.currentDateTime().date())
            sel_dt = self._get_sched_datetime()
            if sel_dt <= QDateTime.currentDateTime():
                self._sched_date.setDate(_now30.date())
                self._sched_hour.setValue(_now30.time().hour())
                self._sched_min.setValue(_now30.time().minute())
            self._sched_widget.show()
            self.send_button.setText("Запланировать")
            self._update_sched_hint()
            self._sched_hint_lbl.show()
        else:
            self._sched_widget.hide()
            self._sched_hint_lbl.hide()
            self.send_button.setText("Опубликовать")

    def _update_sched_hint(self) -> None:
        """Обновляет подсказку под строкой расписания в зависимости от выбранных платформ."""
        if not self._chk_schedule.isChecked():
            return
        vk = self.chk_vk.isChecked()
        mx = self.chk_max.isChecked()
        if vk and mx:
            self._sched_hint_lbl.setText(
                "ВК: пост зарегистрирован в очереди ВКонтакте — компьютер можно выключить.\n"
                "MAX: таймер работает только пока программа запущена (держите в трее)."
            )
        elif vk:
            self._sched_hint_lbl.setText(
                "Пост будет зарегистрирован в очереди ВКонтакте.\n"
                "Компьютер можно выключить — ВК опубликует сам."
            )
        elif mx:
            self._sched_hint_lbl.setText(
                "MAX: таймер работает только пока программа запущена.\n"
                "Сверните в трей — не выключайте компьютер до времени отправки."
            )

    def _get_sched_datetime(self) -> QDateTime:
        d = self._sched_date.date()
        t = QTime(self._sched_hour.value(), self._sched_min.value())
        return QDateTime(d, t)

    def _schedule_post(self) -> None:
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Отправка", "Нельзя отправить пустой текст.")
            return

        send_max = self.chk_max.isChecked()
        send_vk = self.chk_vk.isChecked()
        if not send_max and not send_vk:
            QMessageBox.warning(self, "Отправка", "Выбери хотя бы одну платформу (MAX или ВКонтакте).")
            return

        checked = self._get_checked_matches()
        chat_ids = list(dict.fromkeys(m.chat_id for m in checked if m.chat_id))
        if send_max and not chat_ids:
            QMessageBox.warning(self, "Отправка", "Нет отмеченных адресов. Добавь адрес через кнопку + или вставь текст с адресом.")
            return

        if send_max and not (os.getenv("MAX_ID_INSTANCE") and os.getenv("MAX_API_TOKEN")):
            QMessageBox.warning(self, "Отправка",
                "Не заданы токены MAX.\nОткройте Настройки подключений (🔑).")
            return
        if send_vk and not os.getenv("VK_GROUP_TOKEN"):
            QMessageBox.warning(self, "Отправка",
                "Не задан токен ВКонтакте.\nОткройте Настройки подключений (🔑).")
            return

        sched_dt = self._get_sched_datetime()
        now = QDateTime.currentDateTime()
        ms = now.msecsTo(sched_dt)
        if ms <= 0:
            QMessageBox.warning(self, "Отложенный пост", "Выберите время в будущем.")
            return

        entry_id = uuid.uuid4().hex[:8]
        sched_str = sched_dt.toString("dd.MM.yyyy  HH:mm")
        addresses = [m.address for m in checked]
        unix_ts = sched_dt.toSecsSinceEpoch()

        try:
            history_manager.add_scheduled_entry(
                entry_id=entry_id,
                addresses=addresses,
                sent_max=send_max,
                sent_vk=send_vk,
                text=text,
                scheduled_at=sched_str,
            )
            self._refresh_history()
        except Exception as exc:
            _log.warning("Не удалось сохранить отложенную запись: %s", exc)

        # ── ВК: отправляем API-запрос сразу, ВК сам опубликует в нужное время ──────
        if send_vk:
            image_path_str = str(self.image_path) if self.image_path else None
            self._vk_sched_worker = VkScheduleWorker(
                vk_sender=self.vk_sender,
                text=text,
                image_path=image_path_str,
                publish_date=unix_ts,
                parent=self,
            )
            self._vk_sched_worker.done.connect(
                lambda ok, msg, eid=entry_id, s=sched_str: self._on_vk_schedule_done(ok, msg, eid, s)
            )
            self._vk_sched_worker.finished.connect(self._vk_sched_worker.deleteLater)
            self._vk_sched_worker.start()
            self.send_button.setEnabled(False)
            self.send_button.setText("Регистрация в ВК…")
            # Таймаут 60 сек — если ВК не ответил, разблокируем UI
            QTimer.singleShot(60_000, self._on_vk_schedule_timeout)

        # ── MAX: локальный таймер, работает только пока программа запущена ──────────
        if send_max:
            sched_data = {
                "entry_id": entry_id,
                "scheduled_at_iso": sched_dt.toString(Qt.DateFormat.ISODate),
                "addresses": addresses,
                "chat_ids": chat_ids,
                "send_max": True,
                "send_vk": False,   # VK уже обработан выше
                "text": text,
                "image_path": str(self.image_path) if self.image_path else None,
            }
            self._save_scheduled_to_disk(sched_data)
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda eid=entry_id: self._fire_scheduled(eid))
            timer.start(int(ms))
            self._scheduled_posts[entry_id] = {"timer": timer, "data": sched_data}

        if not send_vk:
            # VK не выбран — сразу показываем подтверждение для MAX
            QMessageBox.information(
                self, "Запланировано",
                f"MAX-пост будет опубликован\n{sched_dt.toString('dd.MM.yyyy  в  HH:mm')}\n\n"
                "Держите программу запущенной (трей).",
            )
            self._chk_schedule.setChecked(False)
        # Если VK выбран — подтверждение покажет _on_vk_schedule_done

    def _on_vk_schedule_timeout(self) -> None:
        """Таймаут 60 сек — ВК не ответил, разблокируем UI и сообщаем об ошибке."""
        if self._vk_sched_worker is None:
            return  # уже завершился нормально
        _log.warning("VkScheduleWorker: таймаут 60 сек")
        self._vk_sched_worker.quit()
        self._vk_sched_worker = None
        self.send_button.setEnabled(True)
        self.send_button.setText("Запланировать" if self._chk_schedule.isChecked() else "Опубликовать")
        QMessageBox.warning(
            self, "Ошибка ВКонтакте",
            "Сервер ВКонтакте не ответил за 60 секунд.\n"
            "Пост мог не зарегистрироваться. Проверьте вручную."
        )

    def _on_vk_schedule_done(self, success: bool, message: str, entry_id: str, sched_str: str) -> None:
        """Вызывается когда VkScheduleWorker завершил регистрацию поста в ВКонтакте."""
        self._vk_sched_worker = None
        self.send_button.setEnabled(True)
        self.send_button.setText("Запланировать" if self._chk_schedule.isChecked() else "Опубликовать")

        if success:
            try:
                history_manager.update_entry_status(entry_id, "scheduled_vk")
                self._refresh_history()
            except Exception as exc:
                _log.warning("Ошибка обновления статуса VK scheduled: %s", exc)
            self._tray_notify(
                "ВКонтакте: пост запланирован",
                f"Пост зарегистрирован в очереди ВК на {sched_str}.\n"
                "Компьютер можно выключить.",
            )
            QMessageBox.information(
                self, "Запланировано в ВКонтакте",
                f"Пост зарегистрирован в очереди ВКонтакте\nна {sched_str}.\n\n"
                "ВКонтакте опубликует его сам — компьютер можно выключить.",
            )
            self._chk_schedule.setChecked(False)
        else:
            tg_notify.send_error("Ошибка планирования ВК", message)
            QMessageBox.critical(
                self, "Ошибка планирования",
                f"Не удалось зарегистрировать пост в ВКонтакте:\n\n{message}",
            )

    def _fire_scheduled(self, entry_id: str) -> None:
        scheduled = self._scheduled_posts.pop(entry_id, None)
        if not scheduled:
            return
        data = scheduled["data"]

        try:
            history_manager.update_entry_status(entry_id, "publishing")
            self._refresh_history()
        except Exception as exc:
            _log.warning("Ошибка обновления статуса: %s", exc)

        self._remove_scheduled_from_disk(entry_id)

        worker = SendWorker(
            max_sender=self.max_sender,
            vk_sender=self.vk_sender,
            chat_ids=data["chat_ids"],
            text=data["text"],
            image_path=data.get("image_path"),
            send_max=data["send_max"],
            send_vk=data["send_vk"],
        )
        worker.result_ready.connect(
            lambda ok, msg, eid=entry_id, d=data: self._on_scheduled_finished(eid, d, ok, msg)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()
        # Сохраняем ссылку, чтобы GC не удалил поток
        self._scheduled_posts[f"_running_{entry_id}"] = {"timer": None, "worker": worker, "data": data}

    def _on_scheduled_finished(self, entry_id: str, data: dict, success: bool, message: str) -> None:
        running_key = f"_running_{entry_id}"
        info = self._scheduled_posts.pop(running_key, None)
        if info and info.get("worker"):
            info["worker"].deleteLater()

        new_status = "done" if success else "failed"
        try:
            history_manager.update_entry_status(entry_id, new_status)
            self._refresh_history()
        except Exception as exc:
            _log.warning("Ошибка обновления статуса: %s", exc)

        if success:
            if self.isVisible():
                self._success_overlay.show_success()
            else:
                self._tray_notify(
                    "Отложенный пост отправлен ✓",
                    data.get("text", "")[:80] or "Публикация успешно отправлена.",
                )
        else:
            tg_notify.send_error("Ошибка отложенной отправки", message)
            if self.isVisible():
                SendResultDialog(message, self).exec()
            else:
                self._tray_notify(
                    "Ошибка отправки",
                    message[:120],
                    QSystemTrayIcon.MessageIcon.Critical,
                )

    def _cancel_scheduled(self, entry_id: str) -> None:
        reply = QMessageBox.question(
            self, "Отмена поста",
            "Отменить этот отложенный пост?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        scheduled = self._scheduled_posts.pop(entry_id, None)
        if scheduled and scheduled.get("timer"):
            scheduled["timer"].stop()
        self._remove_scheduled_from_disk(entry_id)
        try:
            history_manager.update_entry_status(entry_id, "cancelled")
            self._refresh_history()
        except Exception as exc:
            _log.warning("Ошибка отмены: %s", exc)

    def _save_scheduled_to_disk(self, data: dict) -> None:
        path = history_manager._data_dir() / "scheduled.json"
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            items = []
        items.append(data)
        try:
            history_manager._atomic_write(path, json.dumps(items, ensure_ascii=False, indent=2))
        except Exception as exc:
            _log.warning("Ошибка сохранения scheduled.json: %s", exc)

    def _remove_scheduled_from_disk(self, entry_id: str) -> None:
        path = history_manager._data_dir() / "scheduled.json"
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            items = [i for i in items if i.get("entry_id") != entry_id]
            history_manager._atomic_write(path, json.dumps(items, ensure_ascii=False, indent=2))
        except Exception as exc:
            _log.warning("Ошибка удаления из scheduled.json: %s", exc)

    def _load_scheduled_from_disk(self) -> None:
        path = history_manager._data_dir() / "scheduled.json"
        if not path.exists():
            return
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        now = QDateTime.currentDateTime()
        overdue: list[dict] = []
        for item in items:
            try:
                sched_dt = QDateTime.fromString(item["scheduled_at_iso"], Qt.DateFormat.ISODate)
                ms = now.msecsTo(sched_dt)
                entry_id = item["entry_id"]
                if ms <= 0:
                    overdue.append(item)
                    continue
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda eid=entry_id: self._fire_scheduled(eid))
                timer.start(int(ms))
                self._scheduled_posts[entry_id] = {"timer": timer, "data": item}
                _log.info("Восстановлен отложенный пост %s, запуск через %d мс", entry_id, ms)
            except Exception as exc:
                _log.warning("Ошибка загрузки отложенного поста: %s", exc)

        if overdue:
            # Просроченные посты отправляем автоматически — диалог не нужен,
            # т.к. программа обычно работает в трее и посты должны уходить без участия пользователя.
            n = len(overdue)
            label = "пост" if n == 1 else ("поста" if n < 5 else "постов")
            _log.info("Найдено %d просроченных отложенных постов — отправляем автоматически", n)
            for delay_idx, item in enumerate(overdue):
                entry_id = item["entry_id"]
                timer = QTimer(self)
                timer.setSingleShot(True)
                # Разносим запуски по 2 сек, чтобы не запустить все одновременно
                timer.timeout.connect(lambda eid=entry_id: self._fire_scheduled(eid))
                timer.start(1000 + delay_idx * 2000)
                self._scheduled_posts[entry_id] = {"timer": timer, "data": item}
                _log.info("Просроченный пост %s: будет отправлен через %d сек", entry_id, 1 + delay_idx * 2)
            self._tray_notify(
                "Отправка просроченных постов",
                f"Найдено {n} отложенных {label}, пропущенных во время паузы.\n"
                "Отправляем автоматически.",
            )

    def _clear_auto_addr_items(self) -> None:
        """Удаляет из списка все автоматически найденные адреса, оставляя ручные."""
        self.text_input.set_address_marks({})
        self._addr_notfound_hint.hide()
        manual_entries = []
        for i in range(self._addr_list.count()):
            itm = self._addr_list.item(i)
            if not itm:
                continue
            if itm.data(_MANUAL_ROLE):
                m = itm.data(Qt.ItemDataRole.UserRole)
                manual_entries.append((m, itm.checkState()))
        if manual_entries or self._addr_list.count() > 0:
            self._addr_list.blockSignals(True)
            try:
                self._addr_list.clear()
                for m, state in manual_entries:
                    item = QListWidgetItem(m.address)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(state)
                    item.setData(Qt.ItemDataRole.UserRole, m)
                    item.setData(_MANUAL_ROLE, True)
                    self._addr_list.addItem(item)
            finally:
                self._addr_list.blockSignals(False)
            self._update_checklist()
            self.save_state()

    def _auto_check_addresses(self) -> None:
        """Тихий автопарсинг адресов — тяжёлая работа идёт в фоновом потоке."""
        text = self.text_input.toPlainText().strip()

        # Отменяем предыдущий воркер если ещё не завершился
        if self._addr_check_worker is not None:
            self._addr_check_worker.cancel()
            self._addr_check_worker = None

        if not text or not self.excel_path.exists():
            self._clear_auto_addr_items()
            return

        if self._matcher is None:
            self._matcher = ExcelMatcher(self.excel_path)

        w = _AddressCheckWorker(text, self._matcher)
        self._addr_check_worker = w
        w.done.connect(lambda items, marks, _w=w: self._on_addr_check_done(items, marks, _w))
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_addr_check_done(
        self, new_items: list, line_marks: dict, worker: "_AddressCheckWorker"
    ) -> None:
        """Вызывается из фонового потока — обновляет UI после матчинга адресов."""
        if worker is not self._addr_check_worker:
            return  # устаревший результат — игнорируем
        self._addr_check_worker = None

        self.text_input.set_address_marks(line_marks)

        # Показываем подсказку если есть строки с ненайденными адресами (оранжевый фон)
        has_notfound = any(not found for found in line_marks.values())
        if has_notfound:
            notfound_count = sum(1 for found in line_marks.values() if not found)
            self._addr_notfound_hint.setText(
                f"⚠ {notfound_count} {'строка' if notfound_count == 1 else 'строки' if notfound_count < 5 else 'строк'}"
                " с оранжевым фоном — адрес не найден в базе"
            )
            self._addr_notfound_hint.show()
        else:
            self._addr_notfound_hint.hide()

        if not new_items:
            self._clear_auto_addr_items()
            return

        # Собираем вручную добавленные адреса — они НЕ перезаписываются автопарсингом
        manual_entries: list[tuple[MatchResult, Qt.CheckState]] = []
        checked_ids: set[str] = set()
        existing_auto_ids: set[str] = set()
        for i in range(self._addr_list.count()):
            itm = self._addr_list.item(i)
            if not itm:
                continue
            m = itm.data(Qt.ItemDataRole.UserRole)
            if not m:
                continue
            if itm.data(_MANUAL_ROLE):
                manual_entries.append((m, itm.checkState()))
            else:
                existing_auto_ids.add(m.chat_id)
            if itm.checkState() == Qt.CheckState.Checked:
                checked_ids.add(m.chat_id)

        # Не перерисовываем если автоадреса не изменились
        new_ids = {b.chat_id for b in new_items}
        if new_ids == existing_auto_ids:
            return

        self._addr_list.blockSignals(True)
        try:
            self._addr_list.clear()
            for best in new_items:
                item = QListWidgetItem(best.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                state = (Qt.CheckState.Unchecked
                         if (checked_ids and best.chat_id not in checked_ids)
                         else Qt.CheckState.Checked)
                item.setCheckState(state)
                item.setData(Qt.ItemDataRole.UserRole, best)
                item.setData(_MANUAL_ROLE, True)  # сразу закрепляем — не удалять при редактировании текста
                self._addr_list.addItem(item)
            for m, state in manual_entries:
                if m.chat_id in new_ids:
                    continue
                item = QListWidgetItem(m.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(state)
                item.setData(Qt.ItemDataRole.UserRole, m)
                item.setData(_MANUAL_ROLE, True)
                self._addr_list.addItem(item)
        finally:
            self._addr_list.blockSignals(False)
        self._update_checklist()
        self.save_state()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            if any(
                u.toLocalFile().lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                self._text_container.setStyleSheet(
                    "QFrame#textContainer { border: 2px solid #2d6cdf; border-radius: 8px; background: #eef3ff; }"
                )
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._text_container.setStyleSheet("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._text_container.setStyleSheet("")
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                self.image_path = Path(path)
                self.preview.set_image(str(self.image_path))
                self._set_photo_button_name(self.image_path.name)
                self._update_checklist()
                self.save_state()
                break

    def reload_senders(self) -> None:
        """Пересоздаёт sender-объекты после обновления токенов в .env."""
        self.max_sender = MaxSender()
        self.vk_sender = VkSender()

    # ── Шаблоны текста ──────────────────────────────────────────────────────

    def _open_vk_posts_picker(self) -> None:
        """Загружает последние посты ВК-группы и показывает красивый попап."""
        token    = self.vk_sender.user_token or self.vk_sender.group_token
        group_id = self.vk_sender.group_id
        if not token or not group_id:
            QMessageBox.warning(self, "ВК посты", "Не заданы VK_USER_TOKEN и VK_GROUP_ID в .env.")
            return
        self._vk_posts_btn.setEnabled(False)
        self._vk_posts_btn.setText("⏳ Загрузка…")
        worker = _VkWallFetchWorker(token, group_id, count=15, parent=self)
        worker.done.connect(self._on_vk_wall_fetched)
        worker.error.connect(self._on_vk_wall_error)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._vk_fetch_worker = worker

    def _on_vk_wall_fetched(self, posts: list) -> None:
        self._vk_posts_btn.setEnabled(True)
        self._vk_posts_btn.setText("📰 ВК посты")
        posts_with_text = [p for p in posts if (p.get("text") or "").strip()]
        if not posts_with_text:
            QMessageBox.information(self, "ВК посты", "Постов с текстом не найдено.")
            return
        if not hasattr(self, "_vk_popup"):
            self._vk_popup = _VkPostsPopup(self)
            self._vk_popup.post_selected.connect(self._apply_vk_post)
        dark = getattr(getattr(self, "_shell_window", None), "_dark_mode", True)
        self._vk_popup.set_dark(dark)
        self._vk_popup.populate(posts_with_text)
        self._vk_popup.show_below(self._vk_posts_btn)

    def _apply_vk_post(self, text: str) -> None:
        self.text_input.setPlainText(text)
        self.text_input.moveCursor(self.text_input.textCursor().MoveOperation.End)
        self.text_input.setFocus()

    def _on_vk_wall_error(self, msg: str) -> None:
        self._vk_posts_btn.setEnabled(True)
        self._vk_posts_btn.setText("📰 ВК посты")
        QMessageBox.warning(self, "ВК посты", f"Ошибка загрузки постов:\n{msg}")

    def _open_text_file(self) -> None:
        """Открывает .txt или .docx и вставляет текст в поле ввода."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть файл", "",
            "Текстовые файлы (*.txt *.docx);;Все файлы (*)"
        )
        if not path:
            return
        p = Path(path)
        try:
            if p.suffix.lower() == ".docx":
                try:
                    from docx import Document
                except ImportError:
                    QMessageBox.warning(
                        self, "Ошибка",
                        "Для открытия .docx установите:\npip install python-docx"
                    )
                    return
                doc = Document(str(p))
                text = "\n".join(para.text for para in doc.paragraphs if para.text.strip())
            else:
                raw = p.read_bytes()
                for enc in ("utf-8-sig", "utf-8", "cp1251"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    text = raw.decode("utf-8", errors="replace")
            text = text.strip()
            if text:
                self.text_input.setPlainText(text)
        except Exception as exc:
            QMessageBox.warning(self, "Ошибка открытия файла", str(exc))

    def _open_templates_menu(self) -> None:
        """Показывает меню шаблонов под кнопкой 📋."""
        menu = QMenu(self)
        save_act = QAction("💾  Сохранить текущий текст как шаблон…", self)
        save_act.triggered.connect(self._save_as_template)
        menu.addAction(save_act)

        templates = template_manager.load()
        if templates:
            menu.addSeparator()
            for tpl in templates:
                act = QAction(tpl["name"], self)
                act.triggered.connect(lambda checked, t=tpl["text"]: self._apply_template(t))
                menu.addAction(act)
            menu.addSeparator()
            manage_act = QAction("🗑  Удалить шаблон…", self)
            manage_act.triggered.connect(self._delete_template_dialog)
            menu.addAction(manage_act)

        menu.exec(self._tpl_btn.mapToGlobal(self._tpl_btn.rect().bottomLeft()))

    def _save_as_template(self) -> None:
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Шаблоны", "Поле текста пустое — нечего сохранять.")
            return
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Сохранить шаблон", "Название шаблона:")
        if not ok or not name.strip():
            return
        template_manager.save_template(name.strip(), text)
        self._rebuild_templates_file_menu()
        QMessageBox.information(self, "Шаблоны", f"Шаблон «{name.strip()}» сохранён.")

    def _apply_template(self, text: str) -> None:
        checked = self._get_checked_matches()
        first_addr = checked[0].address if checked else None
        text = template_manager.apply_variables(text, first_addr)
        self.text_input.setPlainText(text)
        self.text_input.moveCursor(self.text_input.textCursor().MoveOperation.End)

    def _delete_template_dialog(self) -> None:
        templates = template_manager.load()
        if not templates:
            return
        from PyQt6.QtWidgets import QInputDialog
        names = [t["name"] for t in templates]
        name, ok = QInputDialog.getItem(
            self, "Удалить шаблон", "Выберите шаблон для удаления:", names, 0, False
        )
        if not ok:
            return
        reply = QMessageBox.question(
            self, "Удалить шаблон",
            f"Удалить шаблон «{name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            template_manager.delete_template(name)
            self._rebuild_templates_file_menu()

    def _rebuild_templates_file_menu(self) -> None:
        """Обновляет подменю Файл → Шаблоны."""
        self._tpl_file_menu.clear()
        save_act = QAction("💾  Сохранить текущий текст как шаблон…", self)
        save_act.triggered.connect(self._save_as_template)
        self._tpl_file_menu.addAction(save_act)

        templates = template_manager.load()
        if templates:
            self._tpl_file_menu.addSeparator()
            for tpl in templates:
                act = QAction(tpl["name"], self)
                act.triggered.connect(lambda checked, t=tpl["text"]: self._apply_template(t))
                self._tpl_file_menu.addAction(act)
            self._tpl_file_menu.addSeparator()
            manage_act = QAction("🗑  Удалить шаблон…", self)
            manage_act.triggered.connect(self._delete_template_dialog)
            self._tpl_file_menu.addAction(manage_act)

    # ── Excel ────────────────────────────────────────────────────────────────

    def _reload_excel(self) -> None:
        """Перезагружает Excel-реестр адресов с диска."""
        self._matcher = None
        if self.excel_path.exists():
            self._matcher = ExcelMatcher(self.excel_path)
            _warmup = _ExcelWarmupWorker(self._matcher, self)
            _warmup.finished.connect(_warmup.deleteLater)
            _warmup.start()
            QMessageBox.information(
                self, "Реестр обновлён",
                f"Файл {self.excel_path.name} перезагружен."
            )
        else:
            QMessageBox.warning(
                self, "Файл не найден",
                f"Файл {self.excel_path.name} не найден.\nПоложите его рядом с программой."
            )

    def save_state(self) -> None:
        """Запускает таймер — реальная запись через 400мс после последнего вызова."""
        self._save_timer.start()

    def _do_save_state(self) -> None:
        try:
            _ = self._addr_list.count()  # проверяем, живы ли C++ объекты
        except RuntimeError:
            return  # Qt уже уничтожил объекты (вызов через atexit при завершении)
        checked_ids = {m.chat_id for m in self._get_checked_matches()}
        addresses = []
        for i in range(self._addr_list.count()):
            item = self._addr_list.item(i)
            if not item:
                continue
            m = item.data(Qt.ItemDataRole.UserRole)
            if m:
                addresses.append({
                    "address": m.address,
                    "chat_id": m.chat_id,
                    "chat_link": m.chat_link,
                    "manual": bool(item.data(_MANUAL_ROLE)),
                })
        self.state_manager.save({
            "image_path": str(self.image_path) if self.image_path else "",
            "text": self.text_input.toPlainText(),
            "width": self.width(),
            "height": self.height(),
            "bg_index": self._bg_index,
            "bg_mode": self._bg_mode,
            "bg_opacity": self._bg_opacity,
            "addresses": addresses,
            "checked_ids": list(checked_ids),
            "ui_font_family": self._ui_font_family,
            "ui_font_size": self._ui_font_size,
            "last_token_rotation": self._last_token_rotation,
            "last_vk_invalid_warning": self._last_vk_invalid_warning,
            "recent_photos": self._recent_photos,
            "photo_pinned": self._photo_pinned,
            "require_photo": self._chk_require_photo.isChecked(),
            "splitter_sizes": self._main_splitter.sizes(),
            "delay_enabled": self._chk_delay.isChecked(),
            "delay_min": self._delay_min_spin.value(),
            "delay_max": self._delay_max_spin.value(),
        })

    def load_state(self) -> None:
        data = self.state_manager.load()
        try:
            w = int(data.get("width", 1280) or 1280)
            h = int(data.get("height", 760) or 760)
        except (ValueError, TypeError):
            w, h = 1280, 760
        self.resize(w, h)

        # Шрифт интерфейса — применяется в _deferred_font_load после загрузки шрифтов
        self._pending_font_family = data.get("ui_font_family", "")
        try:
            self._pending_font_size = int(data.get("ui_font_size", 0) or 0)
        except (ValueError, TypeError):
            self._pending_font_size = 0

        self._last_token_rotation    = data.get("last_token_rotation", "")
        self._last_vk_invalid_warning = data.get("last_vk_invalid_warning", "")
        self._recent_photos = data.get("recent_photos", [])
        self._rebuild_recent_bar()
        self._photo_pinned = bool(data.get("photo_pinned", False))
        self._pin_photo_btn.setChecked(self._photo_pinned)
        self._chk_require_photo.setChecked(bool(data.get("require_photo", True)))
        try:
            self._chk_delay.setChecked(bool(data.get("delay_enabled", True)))
            self._delay_min_spin.setValue(int(data.get("delay_min", 5) or 5))
            self._delay_max_spin.setValue(int(data.get("delay_max", 12) or 12))
        except (ValueError, TypeError):
            pass
        splitter_sizes = data.get("splitter_sizes")
        if splitter_sizes and len(splitter_sizes) == 2:
            self._main_splitter.setSizes(splitter_sizes)

        bg_index = data.get("bg_index", None)
        bg_mode = data.get("bg_mode", 0)
        bg_opacity = data.get("bg_opacity", 50)
        if bg_index is not None:
            self._apply_theme(bg_index, bg_mode, bg_opacity)

        text = data.get("text", "")
        if text:
            self.text_input.setPlainText(text)

        image_path = data.get("image_path", "")
        if image_path and Path(image_path).exists():
            self.image_path = Path(image_path)
            self.preview.set_image(str(self.image_path))
            self._set_photo_button_name(self.image_path.name)

        addresses = data.get("addresses", [])
        checked_ids = set(data.get("checked_ids", []))
        self._addr_list.blockSignals(True)
        try:
            self._addr_list.clear()
            for a in addresses:
                match = MatchResult(
                    address=a.get("address", ""),
                    score=0,
                    chat_link=a.get("chat_link", ""),
                    chat_id=a.get("chat_id", ""),
                )
                item = QListWidgetItem(match.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                state = Qt.CheckState.Checked if (not checked_ids or match.chat_id in checked_ids) else Qt.CheckState.Unchecked
                item.setCheckState(state)
                item.setData(Qt.ItemDataRole.UserRole, match)
                if a.get("manual"):
                    item.setData(_MANUAL_ROLE, True)
                self._addr_list.addItem(item)
        finally:
            self._addr_list.blockSignals(False)

        self.sync_preview()
        self.text_input.setFocus()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_success_overlay") and self._bg_widget:
            self._success_overlay.setGeometry(self._bg_widget.rect())

    # ──────────────────────────────────────────────────────────────────
    #  Системный трей
    # ──────────────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        """Создаёт иконку в системном трее."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = QIcon(str(_assets_dir() / "MAX POST.ico"))
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("MAX POST")

        menu = QMenu()
        show_action = menu.addAction("Показать окно")
        show_action.triggered.connect(self._show_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("Выход")
        quit_action.triggered.connect(self._quit_app)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        shell = getattr(self, "_shell_window", None)
        target = shell if shell is not None else self
        target.showNormal()
        target.activateWindow()
        target.raise_()

    def _quit_app(self) -> None:
        """Полное закрытие приложения (из меню трея или Файл → Выход)."""
        self._real_quit = True
        shell = getattr(self, "_shell_window", None)
        if shell is not None:
            shell.close()
        else:
            self.close()

    def _tray_notify(self, title: str, message: str,
                     icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
                     duration_ms: int = 5000) -> None:
        """Показывает balloon-уведомление из трея (только если есть трей)."""
        tray = getattr(self, "_tray", None)
        if tray and tray.isVisible():
            tray.showMessage(title, message, icon, duration_ms)

    def _notify_send_done(self, success: bool) -> None:
        """Balloon + звук по завершении рассылки."""
        h = self._pending_history
        send_max = h.get("send_max", False)
        send_vk = h.get("send_vk", False)

        parts = []
        if send_max and self._send_log_results:
            ok = sum(1 for _, s, _ in self._send_log_results if s)
            total = len(self._send_log_results)
            parts.append(f"MAX: {ok}/{total}")
        if send_vk:
            parts.append("ВК: ✓" if success else "ВК: ✗")

        if not parts:
            return

        if success:
            title = "✓ Рассылка завершена"
            icon = QSystemTrayIcon.MessageIcon.Information
            sound = winsound.MB_OK
        else:
            title = "✗ Рассылка завершена с ошибками"
            icon = QSystemTrayIcon.MessageIcon.Warning
            sound = winsound.MB_ICONEXCLAMATION

        self._tray_notify(title, " · ".join(parts), icon, 6000)
        winsound.MessageBeep(sound)

    def closeEvent(self, event) -> None:
        # Если нажали X (не «Выход») — сворачиваем в трей
        _tray = getattr(self, "_tray", None)
        if not self._real_quit and _tray is not None and _tray.isVisible():
            event.ignore()
            self.hide()
            n_sched = sum(
                1 for v in self._scheduled_posts.values()
                if v.get("timer") and v["timer"].isActive()
            )
            if n_sched:
                self._tray_notify(
                    "MAX POST свёрнут",
                    f"Программа работает в фоне.\n"
                    f"Отложенных постов: {n_sched}.",
                )
            else:
                self._tray_notify("MAX POST свёрнут", "Программа работает в фоне.")
            return

        # Полное закрытие
        self._save_timer.stop()
        self._parse_timer.stop()
        self._addr_search_timer.stop()
        self._hist_search_timer.stop()
        self._excel_watch_timer.stop()
        if self._addr_search_worker and self._addr_search_worker.isRunning():
            self._addr_search_worker.quit()
            self._addr_search_worker.wait(200)
        self._do_save_state()

        for entry in self._scheduled_posts.values():
            t = entry.get("timer")
            if t:
                t.stop()
            w = entry.get("worker")
            if w and w.isRunning():
                w.quit()
                w.wait(2000)

        if self._vk_sched_worker and self._vk_sched_worker.isRunning():
            self._vk_sched_worker.quit()
            self._vk_sched_worker.wait(2000)
            self._vk_sched_worker = None

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.quit()
            self._worker.wait(3000)

        if self._smart_worker and self._smart_worker.isRunning():
            self._smart_worker.cancel()
            self._smart_worker.quit()
            self._smart_worker.wait(3000)

        if self._sel_worker and self._sel_worker.isRunning():
            self._sel_worker.quit()
            self._sel_worker.wait(2000)

        if self._addr_check_worker and self._addr_check_worker.isRunning():
            self._addr_check_worker.cancel()
            self._addr_check_worker.quit()
            self._addr_check_worker.wait(1000)

        if self._vk_fetch_worker and self._vk_fetch_worker.isRunning():
            self._vk_fetch_worker.quit()
            self._vk_fetch_worker.wait(1000)
            self._vk_fetch_worker = None

        conn = getattr(self, "_conn_worker", None)
        if conn and conn.isRunning():
            conn.quit()
            conn.wait(1000)

        tray = getattr(self, "_tray", None)
        if tray:
            tray.hide()

        self.max_sender.close()
        super().closeEvent(event)

    def _clear_photo(self) -> None:
        """Очищает только фото, не трогая текст и адреса."""
        self.image_path = None
        self.preview.set_image(None)
        self.photo_button.setText("Загрузить фото")
        self.photo_button.setObjectName("photoButton")
        self.photo_button.setStyle(self.photo_button.style())
        self._update_photo_thumb()
        self._update_checklist()
        self.save_state()

    def _check_max_connection(self) -> None:
        """Запускает проверку соединения с GREEN-API в фоновом потоке."""
        if hasattr(self, "_conn_worker") and self._conn_worker and self._conn_worker.isRunning():
            return
        self._conn_worker = _ConnCheckWorker(self.max_sender, self)
        self._conn_worker.done.connect(self._on_conn_check_done)
        self._conn_worker.done.connect(self._conn_worker.deleteLater)
        self._conn_worker.start()

    def _on_conn_check_done(self, success: bool, message: str) -> None:
        self._conn_worker = None
        if success:
            QMessageBox.information(self, "Соединение MAX", message)
        else:
            QMessageBox.warning(self, "Соединение MAX", message)

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self, "Горячие клавиши",
            "Ctrl + Enter  —  Опубликовать\n"
            "Ctrl + L       —  Загрузить фото\n"
        )

    def _open_help(self) -> None:
        """Открывает руководство пользователя в браузере."""
        base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent
        help_path = base / "assets" / "help.html"
        if help_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(help_path)))
        else:
            QMessageBox.warning(self, "Справка", f"Файл руководства не найден:\n{help_path}")

    def show_about(self) -> None:
        QMessageBox.information(
            self, "О программе",
            "MAX POST\n\n"
            "Отправка сообщений в группы MAX через GREEN-API.\n\n"
            "Emoji provided free by Twitter (Twemoji) under CC BY 4.0\n"
            "https://creativecommons.org/licenses/by/4.0/"
        )

    def _check_integrity(self) -> None:
        """Проверяет наличие ключевых файлов программы."""
        from env_utils import get_env_path
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        results: list[str] = []

        checks = [
            (base / "version.txt",               "version.txt"),
            (_assets_dir() / "MAX POST.ico",     "Иконка приложения"),
            (get_env_path(),                      "Файл настроек .env"),
        ]
        if self.excel_path:
            checks.append((self.excel_path, f"Excel-файл ({self.excel_path.name})"))

        all_ok = True
        for path, label in checks:
            if path.exists():
                results.append(f"✅  {label}")
            else:
                results.append(f"❌  {label}  — не найден")
                all_ok = False

        status = "Все файлы на месте." if all_ok else "Обнаружены отсутствующие файлы."
        QMessageBox.information(
            self, "Проверка целостности",
            f"{status}\n\n" + "\n".join(results)
        )

    # ──────────────────────────────────────────────────────────────
    #  Безопасность: ротация токенов и проверка валидности VK
    # ──────────────────────────────────────────────────────────────

    def _check_token_reminder(self) -> None:
        """Показывает balloon если VK токен не менялся 10+ дней."""
        if not self._last_token_rotation:
            # Первый запуск — запоминаем сегодня, не беспокоим пользователя
            self._last_token_rotation = datetime.now().strftime("%Y-%m-%d")
            self._save_timer.start()
            return
        try:
            last = datetime.strptime(self._last_token_rotation, "%Y-%m-%d")
            days = (datetime.now() - last).days
        except (ValueError, TypeError):
            self._last_token_rotation = datetime.now().strftime("%Y-%m-%d")
            self._save_timer.start()
            return
        if days >= _TOKEN_ROTATION_DAYS:
            self._tray_notify(
                "Безопасность — смените токен",
                f"VK_USER_TOKEN не обновлялся {days} дн.\n"
                "Смените токен в настройках .env,\n"
                "затем нажмите «Действия → 🔑 Сменил токены VK».",
                QSystemTrayIcon.MessageIcon.Warning,
                10000,
            )

    def _check_vk_token(self) -> None:
        """Проверяет валидность VK_USER_TOKEN при старте (тихо, в фоне)."""
        from env_utils import get_env_path, load_env_safe
        load_env_safe(get_env_path())
        token = os.getenv("VK_USER_TOKEN", "")
        if not token:
            return
        worker = _VkTokenCheckWorker(token, self)
        worker.result.connect(self._on_vk_token_check)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_vk_token_check(self, valid: bool, message: str) -> None:
        if valid:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_vk_invalid_warning == today:
            return  # уже показывали сегодня — не беспокоить снова
        self._last_vk_invalid_warning = today
        self._save_timer.start()
        self._tray_notify(
            "VK токен устарел",
            "VK_USER_TOKEN недействителен.\nОбновите токен в .env",
            QSystemTrayIcon.MessageIcon.Warning,
            8000,
        )

    def _mark_tokens_rotated(self) -> None:
        """Сохраняет сегодняшнюю дату как дату последней смены токенов."""
        self._last_token_rotation = datetime.now().strftime("%Y-%m-%d")
        self._save_timer.start()
        self._tray_notify("Безопасность", "Дата обновления токенов сохранена.")
        QMessageBox.information(
            self, "Токены обновлены",
            f"Дата сохранена: {self._last_token_rotation}\n"
            f"Следующее напоминание через {_TOKEN_ROTATION_DAYS} дней."
        )

    # ------------------------------------------------------------------
    # Умная рассылка
    # ------------------------------------------------------------------

    def _start_smart_send(self) -> None:
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Умная рассылка", "Поле текста пустое.")
            return
        if not self.chk_max.isChecked():
            QMessageBox.warning(self, "Умная рассылка", "Умная рассылка работает только для MAX.")
            return
        if self._matcher is None and self.excel_path.exists():
            self._matcher = ExcelMatcher(self.excel_path)
        if self._matcher is None:
            QMessageBox.warning(self, "Умная рассылка", "Реестр адресов не загружен.")
            return

        blocks, header, footer = _parse_smart_blocks(text, self._matcher)
        if not blocks:
            QMessageBox.warning(
                self,
                "Умная рассылка",
                "Не удалось найти блоки с адресами.\n"
                "Проверьте что блоки разделены пустой строкой.",
            )
            return

        for b in blocks:
            if header:
                b.text = header + "\n\n" + b.text
            if footer:
                b.text = b.text + "\n\n" + footer

        dlg = _SmartSendPreviewDialog(blocks, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        confirmed_blocks = dlg.accepted_blocks()
        if not confirmed_blocks:
            return

        chat_ids_per_block = [
            (b.text, [m.chat_id for m in b.matches if m.chat_id])
            for b in confirmed_blocks
        ]

        # Лог отправки — только адреса с chat_id (те, что реально будут отправлены)
        # Порядок должен совпадать с global_idx в _SmartSendWorker.run()
        self._send_log_results = []
        self._send_log_list.clear()
        for b in confirmed_blocks:
            for m in b.matches:
                if not m.chat_id:
                    continue
                log_item = QListWidgetItem(f"⏳  {m.address}")
                log_item.setData(Qt.ItemDataRole.UserRole, m.address)
                log_item.setData(_LOG_MATCH_ROLE, m)
                self._send_log_list.addItem(log_item)
        self._addr_list.hide()
        self._send_log_list.show()

        self.send_button.hide()
        self._smart_send_btn.hide()
        self._cancel_button.setEnabled(True)
        self._cancel_button.setText("✕  Отменить рассылку")
        self._cancel_button.show()

        worker = _SmartSendWorker(
            self.max_sender,
            chat_ids_per_block,
            str(self.image_path) if self.image_path else None,
            send_max=True,
            dry_run=dlg.dry_run,
            parent=self,
        )
        worker.progress.connect(self._on_send_progress)
        worker.address_result.connect(self._on_address_result)
        worker.all_done.connect(self._on_smart_send_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._smart_worker = worker

    def _on_smart_send_done(self, success: bool, summary: str) -> None:
        is_dry_run = self._smart_worker.dry_run if self._smart_worker else False
        self._cancel_button.hide()
        self.send_button.show()
        self.send_button.setText("Опубликовать")
        self._smart_send_btn.show()
        self._smart_send_btn.setEnabled(True)
        self.setWindowTitle("MAX POST")
        self._send_log_list.hide()
        self._addr_list.show()
        if self._smart_worker is not None:
            self._smart_worker = None
        if is_dry_run:
            QMessageBox.information(self, "Тест завершён", "Пробный прогон умной рассылки выполнен.\nРеальных отправок не было.")
            return
        icon = "✅" if success else "⚠️"
        QMessageBox.information(self, "Умная рассылка завершена", f"{icon} {summary}")


def _backup_address_file() -> None:
    """При запуске сохраняет резервную копию max_address.xlsx (до 3 штук, ротация)."""
    import shutil as _shutil
    src = Path(__file__).parent / "max_address.xlsx"
    if not src.exists():
        return
    backup_dir = src.parent / "_backups"
    backup_dir.mkdir(exist_ok=True)
    dst = backup_dir / f"max_address_{time.strftime('%Y%m%d')}.xlsx"
    if not dst.exists():
        _shutil.copy2(src, dst)
        # Оставляем только последние 3 резервных копии
        backups = sorted(backup_dir.glob("max_address_*.xlsx"))
        for old in backups[:-3]:
            old.unlink(missing_ok=True)


def main() -> None:
    tg_notify.install_excepthook()
    tg_notify.send_startup()


    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # не выходить при hide() в трей
    _backup_address_file()  # резервная копия перед стартом
    window = MainWindow()
    window.showMaximized()
    # Проверка обновлений через 2 сек после запуска (чтобы окно успело отрисоваться)
    QTimer.singleShot(UPDATE_CHECK_DELAY_MS, lambda: check_for_updates(window))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
