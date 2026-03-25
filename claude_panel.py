"""
claude_panel.py — Панель чата с Groq AI для MAX POST.
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget,
)

from env_utils import get_env_path, load_env_safe
from ui.widgets import _GripSplitter, _RuLabel

_log = logging.getLogger(__name__)

_MODEL    = "llama-3.3-70b-versatile"
_BASE_URL = "https://api.groq.com/openai/v1"

_SYSTEM_PROMPT = (
    "Ты — встроенный помощник программы MAX POST. "
    "MAX POST отправляет объявления жильцам многоквартирных домов "
    "в мессенджер MAX и ВКонтакте. "
    "Пользователь — сотрудник управляющей компании ЖКС №2 Выборгского района Санкт-Петербурга.\n\n"
    "Помогай:\n"
    "- Составлять и редактировать тексты объявлений для жильцов\n"
    "- Исправлять ошибки и улучшать формулировки\n"
    "- Предлагать варианты текстов (отключение воды, ремонт, собрание и т.д.)\n"
    "- Отвечать на вопросы о работе программы\n\n"
    "Стиль: деловой, вежливый, понятный для жильцов. "
    "Язык — русский. Ответы короткие и по делу."
)

# ── Цветовые схемы ───────────────────────────────────────────────
_DARK = dict(
    bg="#1e1e2e", bg_header="#16162a", border="#2d2d3f",
    text_primary="#e0e0f0", text_secondary="#6e6e9e",
    bubble_user="#3a4cf7", bubble_ai="#252540",
    text_user="#ffffff", text_ai="#e0e0f0",
    input_bg="#2d2d3f", input_border="#3a3a5f",
    btn_send="#3a4cf7", btn_send_hover="#4a5cff",
    btn_clear_bg="#2d2d3f", btn_clear_fg="#8888b0",
    scrollbar="#3d3d5f",
    _menu_ss="QMenu{background:#252535;color:#ccccdd;border:1px solid #3a3a55;border-radius:6px;padding:4px;font-size:13px;}QMenu::item{padding:5px 18px 5px 10px;border-radius:3px;}QMenu::item:selected{background:rgba(74,108,247,0.25);}QMenu::item:disabled{color:#555570;}QMenu::separator{height:1px;background:#3a3a55;margin:3px 6px;}",
)
_LIGHT = dict(
    bg="#f5f5fc", bg_header="#ededf8", border="#d0d0e8",
    text_primary="#1a1a3a", text_secondary="#6060a0",
    bubble_user="#4a6cf7", bubble_ai="#ffffff",
    text_user="#ffffff", text_ai="#1a1a3a",
    input_bg="#ffffff", input_border="#c0c0d8",
    btn_send="#4a6cf7", btn_send_hover="#5a7cff",
    btn_clear_bg="#e8e8f5", btn_clear_fg="#6060a0",
    scrollbar="#c0c0d8",
    _menu_ss="QMenu{background:#ffffff;color:#1a1a3a;border:1px solid #c7d0db;border-radius:6px;padding:4px;font-size:13px;}QMenu::item{padding:5px 18px 5px 10px;border-radius:3px;}QMenu::item:selected{background:rgba(74,108,247,0.15);}QMenu::item:disabled{color:#aaaacc;}QMenu::separator{height:1px;background:#d0d0e8;margin:3px 6px;}",
)
_colors: dict = dict(_DARK)


# ── Worker ───────────────────────────────────────────────────────
class _StreamWorker(QThread):
    chunk = pyqtSignal(str)
    done  = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, api_key: str, messages: list, parent=None):
        super().__init__(parent)
        self._api_key  = api_key
        self._messages = messages
        self._stop     = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import openai
            client = openai.OpenAI(api_key=self._api_key, base_url=_BASE_URL, timeout=30.0)
            stream = client.chat.completions.create(
                model=_MODEL,
                max_tokens=1024,
                messages=[{"role": "system", "content": _SYSTEM_PROMPT}] + self._messages,
                stream=True,
            )
            for chunk in stream:
                if self._stop:
                    return
                content = chunk.choices[0].delta.content
                if content:
                    self.chunk.emit(content)
            if not self._stop:
                self.done.emit()
        except Exception as exc:
            if self._stop:
                return
            try:
                import openai as _o
                if isinstance(exc, _o.AuthenticationError):
                    msg = "Неверный GROQ_API_KEY в .env"
                elif isinstance(exc, _o.APIConnectionError):
                    msg = "Нет подключения к серверам Groq"
                elif isinstance(exc, _o.RateLimitError):
                    msg = "Превышен лимит запросов Groq"
                else:
                    msg = str(exc)
            except ImportError:
                msg = str(exc)
            self.error.emit(msg)


# ── Пузырь сообщения ─────────────────────────────────────────────
class _Bubble(QWidget):
    def __init__(self, text: str, is_user: bool, parent=None):
        super().__init__(parent)
        c = _colors
        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 3, 10, 3)
        outer.setSpacing(6)

        self._lbl = _RuLabel(text)
        self._lbl.setWordWrap(True)
        self._lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._lbl.setMaximumWidth(500)
        self._lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        if is_user:
            self._lbl.setStyleSheet(f"""
                background: {c['bubble_user']}; color: {c['text_user']};
                border-radius: 12px; padding: 8px 12px; font-size: 13px;
            """)
            outer.addStretch()
            outer.addWidget(self._lbl)
        else:
            icon = QLabel("✨")
            icon.setFixedWidth(20)
            icon.setStyleSheet("background: transparent; font-size: 14px;")
            self._lbl.setStyleSheet(f"""
                background: {c['bubble_ai']}; color: {c['text_ai']};
                border-radius: 12px; padding: 8px 12px; font-size: 13px;
            """)
            outer.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
            outer.addWidget(self._lbl)
            outer.addStretch()

    def append(self, text: str):
        self._lbl.setText(self._lbl.text() + text)


# ── QTextEdit с русским контекстным меню ─────────────────────────
class _RuTextEdit(QTextEdit):
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(_colors.get("_menu_ss", ""))

        cursor = self.textCursor()
        has_sel = cursor.hasSelection()

        undo = menu.addAction("Отменить")
        undo.setEnabled(self.document().isUndoAvailable())
        undo.triggered.connect(self.undo)

        redo = menu.addAction("Повторить")
        redo.setEnabled(self.document().isRedoAvailable())
        redo.triggered.connect(self.redo)

        menu.addSeparator()

        cut = menu.addAction("Вырезать")
        cut.setEnabled(has_sel)
        cut.triggered.connect(self.cut)

        copy = menu.addAction("Копировать")
        copy.setEnabled(has_sel)
        copy.triggered.connect(self.copy)

        paste = menu.addAction("Вставить")
        paste.setEnabled(self.canPaste())
        paste.triggered.connect(self.paste)

        delete = menu.addAction("Удалить")
        delete.setEnabled(has_sel)
        delete.triggered.connect(lambda: cursor.removeSelectedText())

        menu.addSeparator()

        select_all = menu.addAction("Выделить всё")
        select_all.triggered.connect(self.selectAll)

        menu.exec(event.globalPos())


# ── Фильтр Ctrl+Enter ────────────────────────────────────────────
class _SendFilter(QObject):
    triggered = pyqtSignal()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            ke: QKeyEvent = event  # type: ignore[assignment]
            if (ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.triggered.emit()
                return True
        return False


# ── Главная панель ───────────────────────────────────────────────
class ClaudePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[dict] = []
        self._worker: _StreamWorker | None = None
        self._current_bubble: _Bubble | None = None
        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────
    def _setup_ui(self):
        c = _colors
        self.setStyleSheet(f"background: {c['bg']};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Заголовок
        self._header = QWidget()
        self._header.setFixedHeight(54)
        self._header.setStyleSheet(
            f"background: {c['bg_header']}; border-bottom: 1px solid {c['border']};"
        )
        h_lay = QHBoxLayout(self._header)
        h_lay.setContentsMargins(16, 0, 10, 0)

        self._title_lbl = QLabel("⚡ Groq AI")
        self._title_lbl.setStyleSheet(
            f"color: {c['text_primary']}; font-size: 15px; font-weight: 700;"
        )
        h_lay.addWidget(self._title_lbl)
        h_lay.addStretch()

        self._clear_btn = QPushButton("Очистить")
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_clear_bg']}; color: {c['btn_clear_fg']};
                border-radius: 5px; font-size: 11px; padding: 0 10px;
                border: none;
            }}
            QPushButton:hover {{ color: {c['text_primary']}; }}
        """)
        self._clear_btn.clicked.connect(self._clear_chat)
        h_lay.addWidget(self._clear_btn)
        root.addWidget(self._header)

        # Статус (ошибка / «печатает…»)
        self._status_lbl = QLabel("")
        self._status_lbl.setFixedHeight(20)
        self._status_lbl.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 11px; padding-left: 14px;"
        )
        root.addWidget(self._status_lbl)

        # Область сообщений
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: {c['bg']}; }}
            QScrollBar:vertical {{ width: 5px; background: transparent; }}
            QScrollBar::handle:vertical {{
                background: {c['scrollbar']}; border-radius: 2px;
            }}
        """)

        self._msgs_widget = QWidget()
        self._msgs_widget.setStyleSheet(f"background: {c['bg']};")
        self._msgs_lay = QVBoxLayout(self._msgs_widget)
        self._msgs_lay.setContentsMargins(0, 8, 0, 8)
        self._msgs_lay.setSpacing(4)
        self._msgs_lay.addStretch()

        self._scroll.setWidget(self._msgs_widget)

        # Область ввода
        self._input_box = QWidget()
        self._input_box.setStyleSheet(
            f"background: {c['bg']}; border-top: 1px solid {c['border']};"
        )
        self._input_box.setMinimumHeight(80)
        in_lay = QVBoxLayout(self._input_box)
        in_lay.setContentsMargins(12, 8, 12, 10)
        in_lay.setSpacing(6)

        self._input = _RuTextEdit()
        self._input.setPlaceholderText("Напишите запрос… (Ctrl+Enter — отправить)")
        self._input.setStyleSheet(f"""
            QTextEdit {{
                background: {c['input_bg']}; color: {c['text_primary']};
                border: 1px solid {c['input_border']}; border-radius: 8px;
                padding: 6px 10px; font-size: 13px;
            }}
            QTextEdit:focus {{ border-color: {c['btn_send']}; }}
        """)
        self._send_filter = _SendFilter()
        self._send_filter.triggered.connect(self._on_send)
        self._input.installEventFilter(self._send_filter)
        in_lay.addWidget(self._input, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._ctx_btn = QPushButton("📋 Вставить текст поста")
        self._ctx_btn.setFixedHeight(28)
        self._ctx_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_clear_bg']}; color: {c['btn_clear_fg']};
                border-radius: 5px; font-size: 11px; padding: 0 10px; border: none;
            }}
            QPushButton:hover {{ color: {c['text_primary']}; }}
        """)
        self._ctx_btn.setToolTip("Вставить текущий текст объявления в поле ввода")
        self._ctx_btn.clicked.connect(self._insert_post_context)
        btn_row.addWidget(self._ctx_btn)
        btn_row.addStretch()

        self._send_btn = QPushButton("Отправить")
        self._send_btn.setFixedHeight(32)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_send']}; color: white;
                border-radius: 7px; font-size: 13px;
                font-weight: 600; padding: 0 18px; border: none;
            }}
            QPushButton:hover {{ background: {c['btn_send_hover']}; }}
            QPushButton:disabled {{ background: {c['btn_clear_bg']}; color: {c['text_secondary']}; }}
        """)
        self._send_btn.clicked.connect(self._on_send)
        btn_row.addWidget(self._send_btn)
        in_lay.addLayout(btn_row)

        # Сплиттер между сообщениями и полем ввода
        self._splitter = _GripSplitter(Qt.Orientation.Vertical)
        self._splitter.setHandleWidth(8)
        self._splitter.addWidget(self._scroll)
        self._splitter.addWidget(self._input_box)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([400, 120])
        self._splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {c['border']}; }}"
        )
        root.addWidget(self._splitter, 1)

        # Приветственное сообщение
        self._add_bubble(
            "Привет! Я помогу составить текст объявления, исправить ошибки "
            "или ответить на вопросы о работе MAX POST. Что нужно?",
            is_user=False,
        )

    # ── Тема ────────────────────────────────────────────────────
    def set_dark(self, dark: bool) -> None:
        _colors.clear()
        _colors.update(_DARK if dark else _LIGHT)
        self._rebuild_styles()

    def _rebuild_styles(self):
        """Обновляет стили всех элементов под текущую тему."""
        c = _colors
        self.setStyleSheet(f"background: {c['bg']};")
        self._header.setStyleSheet(
            f"background: {c['bg_header']}; border-bottom: 1px solid {c['border']};"
        )
        self._title_lbl.setStyleSheet(
            f"color: {c['text_primary']}; font-size: 15px; font-weight: 700;"
        )
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_clear_bg']}; color: {c['btn_clear_fg']};
                border-radius: 5px; font-size: 11px; padding: 0 10px; border: none;
            }}
            QPushButton:hover {{ color: {c['text_primary']}; }}
        """)
        self._status_lbl.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 11px; padding-left: 14px;"
        )
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: {c['bg']}; }}
            QScrollBar:vertical {{ width: 5px; background: transparent; }}
            QScrollBar::handle:vertical {{
                background: {c['scrollbar']}; border-radius: 2px;
            }}
        """)
        self._msgs_widget.setStyleSheet(f"background: {c['bg']};")
        self._input_box.setStyleSheet(
            f"background: {c['bg']}; border-top: 1px solid {c['border']};"
        )
        self._input.setStyleSheet(f"""
            QTextEdit {{
                background: {c['input_bg']}; color: {c['text_primary']};
                border: 1px solid {c['input_border']}; border-radius: 8px;
                padding: 6px 10px; font-size: 13px;
            }}
            QTextEdit:focus {{ border-color: {c['btn_send']}; }}
        """)
        self._ctx_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_clear_bg']}; color: {c['btn_clear_fg']};
                border-radius: 5px; font-size: 11px; padding: 0 10px; border: none;
            }}
            QPushButton:hover {{ color: {c['text_primary']}; }}
        """)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_send']}; color: white;
                border-radius: 7px; font-size: 13px;
                font-weight: 600; padding: 0 18px; border: none;
            }}
            QPushButton:hover {{ background: {c['btn_send_hover']}; }}
            QPushButton:disabled {{ background: {c['btn_clear_bg']}; color: {c['text_secondary']}; }}
        """)
        self._splitter.setStyleSheet(f"""
            QSplitter::handle {{ background: {c['border']}; }}
        """)

    # ── Контекст поста ──────────────────────────────────────────
    # Устанавливается из ShellWindow чтобы не создавать жёсткую зависимость
    def set_post_text_getter(self, getter) -> None:
        """Принимает callable() → str, возвращающий текущий текст объявления."""
        self._get_post_text = getter

    def _insert_post_context(self):
        getter = getattr(self, "_get_post_text", None)
        text = getter() if getter else ""
        if text.strip():
            cur = self._input.toPlainText().strip()
            prefix = f"Текст объявления:\n{text}\n\n"
            self._input.setPlainText(prefix + cur)
        else:
            self._status_lbl.setText("Поле объявления пустое")

    # ── Отправка ────────────────────────────────────────────────
    def _on_send(self):
        text = self._input.toPlainText().strip()
        if not text:
            return

        # Проверяем API-ключ
        load_env_safe(get_env_path(), override=True)
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            self._status_lbl.setText("⚠ GROQ_API_KEY не задан в .env")
            return

        self._input.clear()
        self._status_lbl.setText("")
        self._add_bubble(text, is_user=True)
        self._messages.append({"role": "user", "content": text})
        # Скользящее окно истории — не более 40 сообщений (20 пар)
        if len(self._messages) > 40:
            self._messages = self._messages[-40:]

        # Пузырь для ответа (пустой, заполняется по мере стриминга)
        self._current_bubble = self._add_bubble("", is_user=False)
        self._send_btn.setEnabled(False)
        self._status_lbl.setText("Groq печатает…")

        if self._worker and self._worker.isRunning():
            self._worker.stop()

        _w = _StreamWorker(api_key, list(self._messages))
        self._worker = _w
        _w.chunk.connect(self._on_chunk)
        _w.done.connect(self._on_done)
        _w.error.connect(self._on_error)
        _w.finished.connect(self._on_worker_finished)
        _w.finished.connect(_w.deleteLater)
        _w.start()

    def _on_worker_finished(self) -> None:
        self._worker = None

    def _on_chunk(self, text: str):
        if self._current_bubble:
            self._current_bubble.append(text)
            # Скроллим вниз по мере появления текста
            sb = self._scroll.verticalScrollBar()
            if sb.value() >= sb.maximum() - 40:
                sb.setValue(sb.maximum())

    def _on_done(self):
        self._send_btn.setEnabled(True)
        self._status_lbl.setText("")
        # Сохраняем ответ в историю
        if self._current_bubble:
            reply = self._current_bubble._lbl.text()
            self._messages.append({"role": "assistant", "content": reply})
        self._current_bubble = None
        self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        )

    def _on_error(self, msg: str):
        self._send_btn.setEnabled(True)
        self._status_lbl.setText(f"⚠ {msg}")
        if self._current_bubble:
            self._current_bubble._lbl.setText(f"[Ошибка: {msg}]")
        self._current_bubble = None

    # ── Очистка ─────────────────────────────────────────────────
    def _clear_chat(self):
        if self._worker is not None:
            self._worker.stop()
        self._messages.clear()
        self._current_bubble = None
        while self._msgs_lay.count() > 1:
            item = self._msgs_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._status_lbl.setText("")
        self._send_btn.setEnabled(True)
        self._add_bubble(
            "Чат очищен. Чем могу помочь?", is_user=False
        )

    # ── Helpers ─────────────────────────────────────────────────
    def _add_bubble(self, text: str, is_user: bool) -> _Bubble:
        bubble = _Bubble(text, is_user)
        self._msgs_lay.insertWidget(self._msgs_lay.count() - 1, bubble)
        return bubble

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.stop()
            self._worker.quit()
            self._worker.wait(2000)
        super().closeEvent(event)
