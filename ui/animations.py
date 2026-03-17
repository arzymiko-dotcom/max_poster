"""Анимации успешной отправки: _SuccessAnimWidget и SuccessOverlay."""

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from constants import ANIM_FRAME_INTERVAL_MS, ANIM_STEP, ANIM_WIDGET_SIZE


class _SuccessAnimWidget(QWidget):
    """Векторная анимация: белый круг масштабируется, затем рисуется зелёная галочка."""

    _SIZE = ANIM_WIDGET_SIZE

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(ANIM_FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._step)

    def start(self) -> None:
        self._t = 0.0
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self._t = 0.0

    def _step(self) -> None:
        self._t = min(1.0, self._t + ANIM_STEP)
        self.update()
        if self._t >= 1.0:
            self._timer.stop()

    @staticmethod
    def _ease_out(t: float) -> float:
        return 1.0 - (1.0 - t) ** 3

    def paintEvent(self, event) -> None:
        if self._t <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        s = self._SIZE
        cx, cy, r = s / 2.0, s / 2.0, 54.0

        # ── Фаза 1: круг (t 0 → 0.55) ───────────────────────────────
        ct = self._ease_out(min(1.0, self._t / 0.55))
        cr = r * ct
        if cr > 1:
            # Мягкая тень
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(28 * ct)))
            p.drawEllipse(int(cx - cr + 2), int(cy - cr + 5),
                          int(cr * 2), int(cr * 2))
            # Белый круг
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(int(cx - cr), int(cy - cr),
                          int(cr * 2), int(cr * 2))
            # Зелёный ободок
            border_w = max(0.5, 4.5 * ct)
            pen = QPen(QColor("#22c55e"), border_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            inn = cr - border_w / 2
            p.drawEllipse(int(cx - inn), int(cy - inn),
                          int(inn * 2), int(inn * 2))

        # ── Фаза 2: галочка (t 0.40 → 1.0) ─────────────────────────
        if self._t > 0.40:
            ck = self._ease_out(min(1.0, (self._t - 0.40) / 0.60))
            pen = QPen(QColor("#16a34a"), 5.5,
                       Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap,
                       Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            # Опорные точки галочки
            ax, ay = cx - 18, cy + 1
            bx, by = cx - 4,  cy + 16
            ex, ey = cx + 19, cy - 10

            path = QPainterPath()
            path.moveTo(ax, ay)
            split = 0.38
            if ck <= split:
                frac = ck / split
                path.lineTo(ax + (bx - ax) * frac, ay + (by - ay) * frac)
            else:
                frac = (ck - split) / (1.0 - split)
                path.lineTo(bx, by)
                path.lineTo(bx + (ex - bx) * frac, by + (ey - by) * frac)
            p.drawPath(path)

        p.end()


class SuccessOverlay(QWidget):
    """Полупрозрачный оверлей с векторной анимацией успешной отправки."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._anim = _SuccessAnimWidget()
        layout.addWidget(self._anim)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._do_hide)

        self.hide()

    def show_success(self, duration_ms: int = 2000) -> None:
        if self.parent():
            self.setGeometry(self.parent().rect())  # type: ignore[union-attr]
        self._anim.start()
        self.raise_()
        self.show()
        self._hide_timer.start(duration_ms)

    def _do_hide(self) -> None:
        self._anim.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 85))
        painter.end()

    def mousePressEvent(self, event) -> None:
        self._hide_timer.stop()
        self._do_hide()
