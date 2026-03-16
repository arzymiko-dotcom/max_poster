"""
shell_window.py — VS Code-style контейнер для всех модулей MAX POST.

Структура:
  ┌──────┬──────────────────────────────┐
  │Sidebar│   QStackedWidget             │
  │ 60px  │   (панели модулей)           │
  └──────┴──────────────────────────────┘

Сайдбар содержит иконки-кнопки. Клик переключает активную панель.
"""

from pathlib import Path
import sys

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


# ──────────────────────────────────────────────────────────────
#  Стиль сайдбара
# ──────────────────────────────────────────────────────────────
_SIDEBAR_STYLE = """
QFrame#sidebar {
    background: #1e1e2e;
    border-right: 1px solid #2a2a3e;
}
QPushButton#sideBtn {
    border: none;
    border-left: 3px solid transparent;
    background: transparent;
    border-radius: 0px;
    min-width: 0px;
    min-height: 0px;
}
QPushButton#sideBtn:checked {
    background: rgba(74, 108, 247, 0.18);
    border-left: 3px solid #4a6cf7;
}
QPushButton#sideBtn:hover:!checked {
    background: rgba(255, 255, 255, 0.07);
}
QLabel#sideLabel {
    color: #6272a4;
    font-size: 9px;
    background: transparent;
}
"""

_SHELL_STYLE = """
QMainWindow {
    background: #1e1e2e;
}
"""


def _assets(name: str) -> str:
    """Возвращает путь к иконке из assets/ MAX POST."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return str(base / "assets" / name)


# ──────────────────────────────────────────────────────────────
#  Кнопка сайдбара
# ──────────────────────────────────────────────────────────────
class _SideButton(QPushButton):
    def __init__(self, icon_path: str, tooltip: str, label: str):
        super().__init__()
        self.setObjectName("sideBtn")
        self.setCheckable(True)
        self.setFixedSize(60, 64)
        self.setToolTip(tooltip)
        icon = QIcon(icon_path)
        if not icon.isNull():
            self.setIcon(icon)
            self.setIconSize(QSize(28, 28))
        else:
            self.setText(label[:2])


# ──────────────────────────────────────────────────────────────
#  Сайдбар
# ──────────────────────────────────────────────────────────────
class _SideBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(60)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(0)

        # Логотип вверху
        logo_label = QLabel()
        logo_label.setObjectName("sideLabel")
        logo_px_path = _assets("MAX POST.ico")
        logo_icon = QIcon(logo_px_path)
        if not logo_icon.isNull():
            logo_label.setPixmap(logo_icon.pixmap(32, 32))
        logo_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        logo_label.setFixedHeight(48)
        layout.addWidget(logo_label)

        # Разделитель
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #2a2a3e; max-height: 1px;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)
        layout.addSpacing(8)

        # Кнопки модулей
        self.btn_max  = _SideButton(_assets("MAX POST.ico"), "MAX POST — отправка сообщений", "MP")
        self.btn_qr   = _SideButton(_assets("max.ico"),      "QR Generator — генератор карточек", "QR")

        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_qr)
        layout.addStretch()

        # Группа — только одна кнопка активна
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self.btn_max, 0)
        self._group.addButton(self.btn_qr,  1)
        self.btn_max.setChecked(True)


# ──────────────────────────────────────────────────────────────
#  Главное окно-контейнер
# ──────────────────────────────────────────────────────────────
class ShellWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MAX POST")
        self.setWindowIcon(QIcon(_assets("MAX POST.ico")))
        self.setStyleSheet(_SIDEBAR_STYLE + _SHELL_STYLE)

        # ── Создаём панели ──────────────────────────────────────
        from main import MainWindow as MaxWindow
        self._max_win = MaxWindow()

        try:
            from qr_panel import create_qr_window
            self._qr_win, qr_widget = create_qr_window()
            self._qr_available = True
        except Exception as e:
            self._qr_win = None
            self._qr_available = False
            qr_widget = QLabel(f"QR Generator недоступен:\n{e}")
            qr_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            qr_widget.setStyleSheet("color: #888; font-size: 14px;")

        # ── QStackedWidget ──────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.addWidget(self._max_win.centralWidget())   # index 0
        self._stack.addWidget(qr_widget)                       # index 1

        # ── Компоновка: sidebar + stack ─────────────────────────
        self._sidebar = _SideBar()
        body = QWidget()
        body.setObjectName("shellBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._sidebar)
        body_layout.addWidget(self._stack)
        self.setCentralWidget(body)

        # ── Меню MAX POST переносим на ShellWindow ───────────────
        self._attach_max_menu()

        # ── Сигналы сайдбара ────────────────────────────────────
        self._sidebar._group.idClicked.connect(self._switch_panel)

    # ──────────────────────────────────────────────────────────
    def _attach_max_menu(self) -> None:
        """Копирует QAction-ы из меню MAX POST в меню ShellWindow."""
        outer = self.menuBar()
        inner = self._max_win.menuBar()
        for menu in inner.findChildren(type(inner.addMenu(""))):
            pass  # не используем этот подход
        # Переносим меню целиком — Qt позволяет перепривязать QMenu к другому menubar
        for action in inner.actions():
            outer.addAction(action)
        inner.setVisible(False)

    # ──────────────────────────────────────────────────────────
    def _switch_panel(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        if index == 0:
            self.menuBar().setVisible(True)
        else:
            self.menuBar().setVisible(False)

    # ──────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        self._max_win.close()
        if self._qr_win is not None:
            self._qr_win.close()
        super().closeEvent(event)
