"""Базовые виджеты: LineNumberedEdit, SpellCheckTextEdit, _NumberedItemDelegate."""

import re

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import (
    QAction, QColor, QPainter,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor,
)
from PyQt6.QtWidgets import QPlainTextEdit, QStyledItemDelegate, QTextEdit


# ── Проверка орфографии (lazy singleton) ──────────────────────
_spell_checker = None

def _get_spell():
    """Возвращает SpellChecker(ru) или None если библиотека не установлена."""
    global _spell_checker
    if _spell_checker is None:
        try:
            from spellchecker import SpellChecker
            _spell_checker = SpellChecker(language="ru")
        except Exception:
            _spell_checker = False
    return _spell_checker or None


class _SpellHighlighter(QSyntaxHighlighter):
    """Подчёркивает красной волной орфографические ошибки."""
    _WORD_RE = re.compile(r"[а-яёА-ЯЁ]{2,}")

    def __init__(self, document):
        super().__init__(document)
        self._fmt = QTextCharFormat()
        self._fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        self._fmt.setUnderlineColor(QColor("#e03e3e"))

    def highlightBlock(self, text: str) -> None:
        spell = _get_spell()
        if not spell:
            return
        for m in self._WORD_RE.finditer(text):
            if spell.unknown([m.group().lower()]):
                self.setFormat(m.start(), len(m.group()), self._fmt)


class _SpellMixin:
    """Mixin: добавляет SpellHighlighter и контекстное меню с подсказками."""

    def _init_spellcheck(self) -> None:
        self._spell_hl = _SpellHighlighter(self.document())

    def contextMenuEvent(self, event) -> None:
        menu = self.createStandardContextMenu()
        spell = _get_spell()
        if spell:
            cursor = self.cursorForPosition(event.pos())
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
            word = cursor.selectedText()
            if word and re.fullmatch(r"[а-яёА-ЯЁ]+", word) and spell.unknown([word.lower()]):
                candidates = sorted(spell.candidates(word.lower()) or [])[:5]
                if candidates:
                    first = menu.actions()[0] if menu.actions() else None
                    menu.insertSeparator(first)
                    for s in reversed(candidates):
                        act = QAction(s, menu)
                        act.triggered.connect(
                            lambda checked, w=s, c=cursor: c.insertText(w)
                        )
                        menu.insertAction(first, act)
        menu.exec(event.globalPos())


class LineNumberedEdit(_SpellMixin, QPlainTextEdit):
    """QPlainTextEdit с чередующимися полосками, номерами строк и проверкой орфографии."""

    _COLOR_ODD = QColor("#f8f9fb")
    _COLOR_EVEN = QColor("#ffffff")
    _NUM_COLOR = QColor("#c4cdd8")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_spellcheck()

    def paintEvent(self, event) -> None:
        # Полоски — один проход, вычисляем ширину один раз
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

        # Номера справа (поверх текста) — шрифт вычисляем один раз
        painter2 = QPainter(self.viewport())
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        offset = self.contentOffset()
        font = painter2.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter2.setFont(font)
        painter2.setPen(self._NUM_COLOR)
        num_rect_w = vp_w - 8
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
