"""Виджеты фонового изображения: _OverlayWidget и _BgWidget."""

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget


class _OverlayWidget(QWidget):
    """Полупрозрачное изображение поверх всех дочерних виджетов."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._bg_pixmap: QPixmap | None = None
        self._opacity_pct: int = 30
        self._cached_scaled: QPixmap | None = None
        self._cached_for_size: QSize | None = None

    def set_background(self, pixmap: QPixmap | None, opacity_pct: int = 30) -> None:
        self._bg_pixmap = pixmap
        self._opacity_pct = opacity_pct
        self._cached_scaled = None
        self._cached_for_size = None
        self.update()

    def paintEvent(self, event) -> None:
        if not self._bg_pixmap or self._bg_pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._opacity_pct / 100)
        current_size = self.size()
        if self._cached_scaled is None or self._cached_for_size != current_size:
            self._cached_scaled = self._bg_pixmap.scaled(
                current_size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._cached_for_size = current_size
        scaled = self._cached_scaled
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()


class _BgWidget(QWidget):
    """Центральный виджет с фоновым изображением."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bg_pixmap: QPixmap | None = None
        self._mode: int = 0  # 0 = фон (за элементами), 1 = наложение (поверх)
        self._opacity_pct: int = 50
        self._overlay = _OverlayWidget(self)
        self._overlay.hide()
        self._cached_scaled: QPixmap | None = None
        self._cached_for_size: QSize | None = None

    def set_background(self, pixmap: QPixmap | None, mode: int = 0, opacity_pct: int = 50) -> None:
        self._bg_pixmap = pixmap
        self._mode = mode
        self._opacity_pct = opacity_pct
        self._cached_scaled = None
        self._cached_for_size = None
        if mode == 1 and pixmap and not pixmap.isNull():
            self._overlay.set_background(pixmap, opacity_pct)
            self._overlay.setGeometry(self.rect())
            self._overlay.show()
            self._overlay.raise_()
        else:
            self._overlay.set_background(None)
            self._overlay.hide()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())
        if self._mode == 1:
            self._overlay.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f3f4f6"))
        if self._mode == 0 and self._bg_pixmap and not self._bg_pixmap.isNull():
            painter.setOpacity(self._opacity_pct / 100)
            current_size = self.size()
            if self._cached_scaled is None or self._cached_for_size != current_size:
                self._cached_scaled = self._bg_pixmap.scaled(
                    current_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._cached_for_size = current_size
            scaled = self._cached_scaled
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
