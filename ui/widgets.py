"""Базовые виджеты: LineNumberedEdit, SpellCheckTextEdit, _NumberedItemDelegate."""

import re

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import (
    QColor, QPainter,
    QSyntaxHighlighter, QTextCharFormat,
)
from PyQt6.QtWidgets import QMenu, QPlainTextEdit, QStyledItemDelegate, QTextEdit


# ── Проверка орфографии через pymorphy2 (lazy singleton) ──────
_morph_analyzer = None


def _get_morph():
    """Возвращает MorphAnalyzer или None если pymorphy2 не установлен."""
    global _morph_analyzer
    if _morph_analyzer is None:
        try:
            import pymorphy3
            _morph_analyzer = pymorphy3.MorphAnalyzer()
        except Exception:
            _morph_analyzer = False
    return _morph_analyzer or None


_word_known_cache: dict[str, bool] = {}


def _is_known(morph, word: str) -> bool:
    """True если слово есть в словаре. Результат кэшируется на всю сессию."""
    if word not in _word_known_cache:
        _word_known_cache[word] = morph.word_is_known(word)
    return _word_known_cache[word]


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
        self._morph = _get_morph()  # кэшируем один раз при создании хайлайтера

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
        morph = self._morph
        if morph:
            for m in self._WORD_RE.finditer(text):
                if not _is_known(morph, m.group().lower()):
                    fmt = self.format(m.start())  # берём текущий формат (с фоном если есть)
                    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                    fmt.setUnderlineColor(QColor("#e03e3e"))
                    self.setFormat(m.start(), len(m.group()), fmt)


class _SpellMixin:
    """Mixin: добавляет CombinedHighlighter к полю ввода."""

    def _init_spellcheck(self) -> None:
        self._spell_hl = _CombinedHighlighter(self.document())

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
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


class LineNumberedEdit(_SpellMixin, QPlainTextEdit):
    """QPlainTextEdit с чередующимися полосками, номерами строк и проверкой орфографии."""

    _COLOR_ODD = QColor("#f8f9fb")
    _COLOR_EVEN = QColor("#ffffff")
    _NUM_COLOR = QColor("#c4cdd8")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_spellcheck()

    def set_address_marks(self, marks: dict[int, bool]) -> None:
        """Подсветить строки с адресами: True=найден (зелёный), False=не найден (оранжевый)."""
        self._spell_hl.set_line_marks(marks)

    def paintEvent(self, event) -> None:
        # Полоски фона — до вызова super (super рисует текст поверх)
        painter = QPainter(self.viewport())
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        vp_w = self.viewport().width()
        clip_bottom = event.rect().bottom()
        clip_top = event.rect().top()
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > clip_bottom:
                break
            if rect.bottom() >= clip_top:
                color = self._COLOR_ODD if block.blockNumber() % 2 == 0 else self._COLOR_EVEN
                painter.fillRect(QRect(0, int(rect.top()), vp_w, int(rect.height())), color)
            block = block.next()
        painter.end()
        super().paintEvent(event)

        # Номера строк — один проход поверх текста, шрифт и ширина вычисляются один раз
        painter2 = QPainter(self.viewport())
        font = painter2.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter2.setFont(font)
        painter2.setPen(self._NUM_COLOR)
        num_rect_w = vp_w - 8
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        offset = self.contentOffset()
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > clip_bottom:
                break
            if block.isVisible() and rect.bottom() >= clip_top:
                painter2.drawText(
                    QRect(0, int(rect.top()), num_rect_w, int(rect.height())),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block_num + 1),
                )
            block = block.next()
            block_num += 1
        painter2.end()


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
