"""
shell_window.py — VS Code-style контейнер для всех модулей MAX POST.
"""

import sys
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer,
)
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QMainWindow, QMenu, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)


# ──────────────────────────────────────────────────────────────
#  Стили
# ──────────────────────────────────────────────────────────────
_SIDEBAR_STYLE = """
QFrame#sidebar {
    background: #1e1e2e;
    border-right: 1px solid #2d2d3f;
}
QPushButton#sideBtn {
    border: none;
    border-left: 3px solid transparent;
    background: transparent;
    border-radius: 0px;
    min-width: 0px;
    min-height: 0px;
    color: #888aaa;
    font-size: 9px;
}
QPushButton#sideBtn:checked {
    background: rgba(74, 108, 247, 0.18);
    border-left: 3px solid #4a6cf7;
    color: #ffffff;
}
QPushButton#sideBtn:hover:!checked {
    background: rgba(255, 255, 255, 0.07);
    color: #cccccc;
}
QPushButton#settingsBtn {
    border: none;
    background: transparent;
    color: #666888;
    font-size: 20px;
    min-width: 0px;
    min-height: 0px;
}
QPushButton#settingsBtn:hover {
    color: #aaaacc;
    background: rgba(255,255,255,0.07);
}
"""

_SHELL_STYLE = """
QMainWindow { background: #1e1e2e; }
"""


def _assets(name: str) -> str:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return str(base / "assets" / name)


# ──────────────────────────────────────────────────────────────
#  Кнопка сайдбара с иконкой + подписью
# ──────────────────────────────────────────────────────────────
class _SideButton(QPushButton):
    def __init__(self, icon_path: str, tooltip: str, fallback: str):
        super().__init__()
        self.setObjectName("sideBtn")
        self.setCheckable(True)
        self.setFixedSize(60, 60)
        self.setToolTip(tooltip)
        icon = QIcon(icon_path)
        if not icon.isNull():
            self.setIcon(icon)
            self.setIconSize(QSize(26, 26))
        else:
            self.setText(fallback)


# ──────────────────────────────────────────────────────────────
#  Сайдбар
# ──────────────────────────────────────────────────────────────
class _SideBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(60)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)

        # Логотип вверху
        logo = QLabel()
        icon = QIcon(_assets("MAX POST.ico"))
        if not icon.isNull():
            logo.setPixmap(icon.pixmap(32, 32))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        logo.setFixedHeight(44)
        layout.addWidget(logo)

        # Тонкий разделитель
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background:#2d2d3f; max-height:1px; border:none;")
        layout.addWidget(sep)
        layout.addSpacing(10)

        # Кнопки модулей
        self.btn_max = _SideButton(_assets("MAX POST.ico"), "MAX POST — отправка сообщений", "MP")
        self.btn_qr  = _SideButton(_assets("max.ico"),      "QR Generator — генератор карточек", "QR")
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_qr)

        layout.addStretch()

        # Кнопка настроек внизу
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("settingsBtn")
        self.btn_settings.setFixedSize(60, 50)
        self.btn_settings.setToolTip("Настройки")
        layout.addWidget(self.btn_settings)

        # Группа — только одна кнопка активна
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self.btn_max, 0)
        self._group.addButton(self.btn_qr,  1)
        self.btn_max.setChecked(True)


# ──────────────────────────────────────────────────────────────
#  Плавное переключение панелей
# ──────────────────────────────────────────────────────────────
class _FadeStack(QStackedWidget):
    """QStackedWidget с fade-анимацией при переключении панелей."""

    DURATION = 180  # ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animating = False
        # Хранить ссылки, чтобы Python GC не удалил объекты до конца анимации
        self._eff_out = None
        self._anim_out = None
        self._eff_in = None
        self._anim_in = None

    def switch_to(self, index: int) -> None:
        if self._animating or index == self.currentIndex():
            return
        current = self.currentWidget()
        if current is None:
            self.setCurrentIndex(index)
            return

        self._animating = True

        # Fade out текущей панели
        self._eff_out = QGraphicsOpacityEffect(current)
        current.setGraphicsEffect(self._eff_out)
        self._anim_out = QPropertyAnimation(self._eff_out, b"opacity", self)
        self._anim_out.setDuration(self.DURATION)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _do_switch():
            self.setCurrentIndex(index)
            new = self.currentWidget()
            current.setGraphicsEffect(None)
            if new:
                self._eff_in = QGraphicsOpacityEffect(new)
                new.setGraphicsEffect(self._eff_in)
                self._anim_in = QPropertyAnimation(self._eff_in, b"opacity", self)
                self._anim_in.setDuration(self.DURATION)
                self._anim_in.setStartValue(0.0)
                self._anim_in.setEndValue(1.0)
                self._anim_in.setEasingCurve(QEasingCurve.Type.InCubic)
                self._anim_in.finished.connect(lambda: new.setGraphicsEffect(None))
                self._anim_in.finished.connect(lambda: setattr(self, "_animating", False))
                self._anim_in.start()
            else:
                self._animating = False

        self._anim_out.finished.connect(_do_switch)
        self._anim_out.start()


# ──────────────────────────────────────────────────────────────
#  Главное окно-контейнер
# ──────────────────────────────────────────────────────────────
class ShellWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MAX POST")
        self.setWindowIcon(QIcon(_assets("MAX POST.ico")))
        self.setStyleSheet(_SIDEBAR_STYLE + _SHELL_STYLE)
        self.menuBar().setVisible(False)  # убираем стандартное меню

        # ── Инициализируем MAX POST ─────────────────────────────
        from main import MainWindow as MaxWindow
        self._max_win = MaxWindow()

        # ── Инициализируем QR Generator ────────────────────────
        try:
            from qr_panel import create_qr_widget
            self._qr_win = create_qr_widget()
            qr_widget = self._qr_win
            self._qr_available = True
        except Exception as e:
            self._qr_win = None
            self._qr_available = False
            qr_widget = QLabel(f"QR Generator недоступен:\n{e}")
            qr_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            qr_widget.setStyleSheet("color:#888; font-size:14px; background:#252535;")

        # ── FadeStack ───────────────────────────────────────────
        self._stack = _FadeStack()

        # Переносим стили MAX POST на его centralWidget, потому что после
        # reparent в stack стили от _max_win перестают каскадироваться
        max_widget = self._max_win.centralWidget()
        max_widget.setStyleSheet(self._max_win.styleSheet())

        self._stack.addWidget(max_widget)   # index 0
        self._stack.addWidget(qr_widget)    # index 1

        # ── Компоновка ──────────────────────────────────────────
        self._sidebar = _SideBar()
        body = QWidget()
        body.setObjectName("shellBody")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self._sidebar)
        bl.addWidget(self._stack)
        self.setCentralWidget(body)

        # ── Сигналы ─────────────────────────────────────────────
        self._sidebar._group.idClicked.connect(self._switch_panel)
        self._sidebar.btn_settings.clicked.connect(self._show_settings_menu)

    # ──────────────────────────────────────────────────────────
    def _switch_panel(self, index: int) -> None:
        self._stack.switch_to(index)

    # ──────────────────────────────────────────────────────────
    def _show_settings_menu(self) -> None:
        """Показывает выпадающее меню со всеми пунктами MAX POST."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #252535;
                color: #ccccdd;
                border: 1px solid #3a3a55;
                border-radius: 6px;
                padding: 4px;
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: rgba(74,108,247,0.25);
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #3a3a55;
                margin: 4px 8px;
            }
        """)

        # Копируем все пункты из меню MAX POST
        inner_menu = self._max_win.menuBar()
        for top_action in inner_menu.actions():
            top_menu = top_action.menu()
            if top_menu:
                sub = menu.addMenu(top_action.text())
                sub.setStyleSheet(menu.styleSheet())
                for action in top_menu.actions():
                    sub.addAction(action)
            else:
                menu.addAction(top_action)

        # Показываем над кнопкой настроек
        btn = self._sidebar.btn_settings
        pos = btn.mapToGlobal(btn.rect().topRight())
        menu.exec(pos)

    # ──────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        self._max_win.close()
        if self._qr_win is not None:
            try:
                self._qr_win.close()
            except Exception:
                pass
        super().closeEvent(event)
