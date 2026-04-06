"""Базовые виджеты: LineNumberedEdit, SpellCheckTextEdit, _NumberedItemDelegate, _GripSplitter."""

import os
import re
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QPainter,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor,
)
from PyQt6.QtWidgets import QLabel, QMenu, QPlainTextEdit, QSplitter, QSplitterHandle, QStyledItemDelegate, QTextEdit


# ── Проверка орфографии через pymorphy3 (фоновая загрузка) ──────
_morph_analyzer = None

# ── Пользовательский словарь ──────────────────────────────────────
_USER_DICT_PATH = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST" / "user_dict.txt"
_user_dict: set[str] = set()


def _load_user_dict() -> None:
    try:
        raw = _USER_DICT_PATH.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        loaded = {w.strip().lower() for w in text.splitlines() if w.strip()}
        _user_dict.update(loaded)
        _word_known_cache.update((w, True) for w in loaded)
    except Exception:
        pass


def _add_to_user_dict(word: str) -> None:
    word = word.lower().strip()
    _user_dict.add(word)
    _word_known_cache[word] = True
    snapshot = sorted(_user_dict)
    def _save_dict():
        try:
            _USER_DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _USER_DICT_PATH.write_text("\n".join(snapshot), encoding="utf-8")
        except Exception:
            pass
    threading.Thread(target=_save_dict, daemon=True).start()


def _get_morph():
    """Возвращает MorphAnalyzer или None (None пока фоновая загрузка не завершилась)."""
    return _morph_analyzer if _morph_analyzer and _morph_analyzer is not False else None


def _load_morph_bg():
    """Загружает pymorphy3 в фоновом потоке — не блокирует главный поток."""
    global _morph_analyzer
    try:
        import pymorphy3
        _morph_analyzer = pymorphy3.MorphAnalyzer()
    except Exception:
        _morph_analyzer = False
    _load_user_dict()


_word_known_cache: dict[str, bool] = {}

threading.Thread(target=_load_morph_bg, daemon=True).start()

_RU_ALPHA = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"


def _is_known(morph, word: str) -> bool:
    """True если слово есть в словаре или пользовательском списке. Кэшируется."""
    if word not in _word_known_cache:
        _word_known_cache[word] = (word in _user_dict) or morph.word_is_known(word)
    return _word_known_cache[word]


def _get_suggestions(morph, word: str, max_results: int = 5) -> list[str]:
    """Варианты исправления через edit-distance 1 + проверку pymorphy3."""
    w = word.lower()
    n = len(w)
    candidates: set[str] = set()

    # Удаление символа
    for i in range(n):
        candidates.add(w[:i] + w[i + 1:])

    # Замена символа
    for i in range(n):
        for c in _RU_ALPHA:
            if c != w[i]:
                candidates.add(w[:i] + c + w[i + 1:])

    # Перестановка соседних символов
    for i in range(n - 1):
        s = list(w)
        s[i], s[i + 1] = s[i + 1], s[i]
        candidates.add("".join(s))

    candidates.discard(w)
    results = [c for c in candidates if len(c) >= 2 and (_is_known(morph, c))]
    return results[:max_results]


class _CombinedHighlighter(QSyntaxHighlighter):
    """Подчёркивает орфоошибки + подсвечивает строки с адресами (зелёный/оранжевый)."""
    _WORD_RE = re.compile(r"[а-яёА-ЯЁ]{3,}")

    def __init__(self, document):
        super().__init__(document)
        self._spell_fmt = QTextCharFormat()
        self._spell_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        self._spell_fmt.setUnderlineColor(QColor("#e03e3e"))
        self._addr_found_fmt = QTextCharFormat()
        self._addr_found_fmt.setBackground(QColor("#d6f5e3"))   # светло-зелёный
        self._addr_notfound_fmt = QTextCharFormat()
        self._addr_notfound_fmt.setBackground(QColor("#ffeeba"))  # светло-оранжевый
        self._line_marks: dict[int, bool] = {}  # line_idx → True=найден, False=не найден

    def set_line_marks(self, marks: dict[int, bool]) -> None:
        """Обновить подсветку строк. True=найден (зелёный), False=не найден (оранжевый)."""
        self._line_marks = marks
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        line_idx = self.currentBlock().blockNumber()
        # 1. Фон для адресных строк (целая строка)
        if line_idx in self._line_marks:
            bg_fmt = self._addr_found_fmt if self._line_marks[line_idx] else self._addr_notfound_fmt
            self.setFormat(0, len(text), bg_fmt)
        # 2. Орфография поверх фона (сохраняем цвет фона)
        morph = _get_morph()  # None пока грузится в фоне, быстро после загрузки
        if morph:
            for m in self._WORD_RE.finditer(text):
                if not _is_known(morph, m.group().lower()):
                    fmt = self.format(m.start())  # берём текущий формат (с фоном если есть)
                    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                    fmt.setUnderlineColor(QColor("#e03e3e"))
                    self.setFormat(m.start(), len(m.group()), fmt)


_RU_WORD_RE = re.compile(r'^[а-яёА-ЯЁ]{2,}$')


class _SpellMixin:
    """Mixin: добавляет CombinedHighlighter к полю ввода."""

    def _init_spellcheck(self) -> None:
        self._spell_hl = _CombinedHighlighter(self.document())

    def _add_word_to_dict(self, word: str) -> None:
        _add_to_user_dict(word)
        if hasattr(self, '_spell_hl'):
            self._spell_hl.rehighlight()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)

        # ── Spell check для слова под курсором ──
        morph = _get_morph()
        word_cursor = self.cursorForPosition(event.pos())
        word_cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        clicked_word = word_cursor.selectedText().strip()

        if morph and clicked_word and _RU_WORD_RE.match(clicked_word):
            if not _is_known(morph, clicked_word.lower()):
                suggestions = _get_suggestions(morph, clicked_word)
                if suggestions:
                    for s in suggestions:
                        # Сохраняем регистр: если слово с заглавной — исправление тоже
                        display = s.capitalize() if clicked_word[0].isupper() else s
                        act = menu.addAction(f"  → {display}")
                        act.triggered.connect(
                            lambda _, repl=display, wc=word_cursor: wc.insertText(repl)
                        )
                    menu.addSeparator()

                add_act = menu.addAction(f'Добавить «{clicked_word}» в словарь')
                add_act.triggered.connect(lambda: self._add_word_to_dict(clicked_word))
                menu.addSeparator()

        # ── Стандартные пункты ──
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

        self._extra_menu_actions(menu, cursor.selectedText() if has_sel else "")

        menu.exec(event.globalPos())

    def _extra_menu_actions(self, menu: QMenu, selected_text: str) -> None:
        """Hook для подклассов: добавить дополнительные пункты в контекстное меню."""


class LineNumberedEdit(_SpellMixin, QPlainTextEdit):
    """QPlainTextEdit с проверкой орфографии и подсветкой адресных строк."""

    send_selected_max = pyqtSignal(str)  # выделенный текст → отправить в MAX
    send_selected_vk  = pyqtSignal(str)  # выделенный текст → отправить в ВКонтакте

    # MainWindow устанавливает этот getter чтобы меню показывало счётчик адресов
    addr_count_getter: "callable | None" = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_spellcheck()

    def set_address_marks(self, marks: dict[int, bool]) -> None:
        """Подсветить строки с адресами: True=найден (зелёный), False=не найден (оранжевый)."""
        self._spell_hl.set_line_marks(marks)

    def _extra_menu_actions(self, menu: QMenu, selected_text: str) -> None:
        if not selected_text.strip():
            return
        menu.addSeparator()
        n = self.addr_count_getter() if callable(self.addr_count_getter) else None
        if n:
            max_label = f"📤 Отправить в MAX ({n} адр.)"
        else:
            max_label = "📤 Отправить в MAX — сначала выберите адреса"
        act_max = menu.addAction(max_label)
        act_max.setEnabled(bool(n))  # серый если нет адресов, но виден — напоминает выбрать
        act_max.triggered.connect(lambda: self.send_selected_max.emit(selected_text))
        act_vk = menu.addAction("📤 Отправить в ВКонтакте")
        act_vk.triggered.connect(lambda: self.send_selected_vk.emit(selected_text))


class SpellCheckTextEdit(_SpellMixin, QTextEdit):
    """QTextEdit с проверкой орфографии (для VK-панели и других полей ввода)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_spellcheck()


class _NumberedItemDelegate(QStyledItemDelegate):
    """Рисует серый номер строки справа в каждом элементе QListWidget."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        painter.save()
        font = painter.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        painter.setPen(QColor("#c4cdd8"))
        painter.drawText(
            option.rect.adjusted(0, 0, -8, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(index.row() + 1),
        )
        painter.restore()


# ── Сплиттер с точками-грипом ─────────────────────────────────

class _GripHandle(QSplitterHandle):
    """Ручка сплиттера с тремя точками — подсказывает что можно тянуть."""
    _DOT_R   = 2
    _SPACING = 7

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        cx = self.width() // 2
        cy = self.height() // 2
        for i in (-1, 0, 1):
            p.drawEllipse(cx - self._DOT_R, cy + i * self._SPACING - self._DOT_R,
                          self._DOT_R * 2, self._DOT_R * 2)
        p.end()


class _RuLabel(QLabel):
    """QLabel с русским контекстным меню (для выделяемых текстовых пузырей)."""

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        has_sel = bool(self.selectedText())
        copy = menu.addAction("Копировать")
        copy.setEnabled(has_sel)
        copy.triggered.connect(self.copy)
        menu.addSeparator()
        select_all = menu.addAction("Выделить всё")
        select_all.triggered.connect(lambda: self.setSelection(0, len(self.text())))
        menu.exec(event.globalPos())


class _GripSplitter(QSplitter):
    """QSplitter с кастомной ручкой (_GripHandle)."""
    def createHandle(self) -> QSplitterHandle:
        return _GripHandle(self.orientation(), self)
