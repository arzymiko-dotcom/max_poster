"""Базовые виджеты: LineNumberedEdit и _NumberedItemDelegate."""

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QPlainTextEdit, QStyledItemDelegate


class LineNumberedEdit(QPlainTextEdit):
    """QPlainTextEdit с чередующимися полосками и номером строки справа (как в списке адресов)."""

    _COLOR_ODD = QColor("#f8f9fb")
    _COLOR_EVEN = QColor("#ffffff")
    _NUM_COLOR = QColor("#c4cdd8")

    def paintEvent(self, event) -> None:
        # Полоски
        painter = QPainter(self.viewport())
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > event.rect().bottom():
                break
            if rect.bottom() >= event.rect().top():
                color = self._COLOR_ODD if block.blockNumber() % 2 == 0 else self._COLOR_EVEN
                painter.fillRect(QRect(0, int(rect.top()), self.viewport().width(), int(rect.height())), color)
            block = block.next()
        painter.end()
        super().paintEvent(event)

        # Номера справа (поверх текста)
        painter2 = QPainter(self.viewport())
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        offset = self.contentOffset()
        font = painter2.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter2.setFont(font)
        painter2.setPen(self._NUM_COLOR)
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > event.rect().bottom():
                break
            if block.isVisible() and rect.bottom() >= event.rect().top():
                painter2.drawText(
                    QRect(0, int(rect.top()), self.viewport().width() - 8, int(rect.height())),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block_num + 1),
                )
            block = block.next()
            block_num += 1
        painter2.end()


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
