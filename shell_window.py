"""
shell_window.py — VS Code-style контейнер для всех модулей MAX POST.
"""

import ctypes
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)


def _ui_prefs_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST"
    else:
        base = Path(__file__).parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "ui_prefs.json"


def _load_ui_prefs() -> dict:
    try:
        return json.loads(_ui_prefs_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        _log.warning("Не удалось загрузить ui_prefs.json", exc_info=True)
        return {}


def _save_ui_prefs(prefs: dict) -> None:
    try:
        _ui_prefs_path().write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        _log.warning("Не удалось сохранить ui_prefs.json", exc_info=True)


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
    min-width: 0px;
    min-height: 0px;
}
QPushButton#settingsBtn:hover {
    background: rgba(255,255,255,0.07);
}
QPushButton#sideBtn:disabled {
    color: #444455;
    background: transparent;
    border-left: 3px solid transparent;
}
"""

_SHELL_STYLE = """
QMainWindow { background: #1e1e2e; }
"""


def _assets(name: str) -> str:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return str(base / "assets" / name)


# ──────────────────────────────────────────────────────────────
#  Пароль для настроек подключений
# ──────────────────────────────────────────────────────────────
def _hash_pw(password: str) -> str:
    """PBKDF2-HMAC-SHA256 с random salt. Формат: 'pbkdf2:<salt_hex>:<hash_hex>'."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
    return f"pbkdf2:{salt.hex()}:{key.hex()}"


def _verify_pw(password: str, stored: str) -> bool:
    """Проверяет пароль против PBKDF2 или устаревшего SHA256 хэша."""
    if stored.startswith("pbkdf2:"):
        try:
            _, salt_hex, hash_hex = stored.split(":", 2)
            salt = bytes.fromhex(salt_hex)
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
            return key.hex() == hash_hex
        except (ValueError, IndexError):
            return False
    # Устаревший SHA256 — принимаем, но при следующем сохранении обновится
    return hashlib.sha256(password.encode("utf-8")).hexdigest() == stored


class _SetPasswordDialog(QDialog):
    """Первичная установка пароля."""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Создать пароль для настроек")
        self.setMinimumWidth(360)
        self._hash: str = ""
        self._hint: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Защитите настройки паролем.\nОн потребуется при каждом открытии."))

        form = QFormLayout()
        form.setSpacing(8)
        self._pw = QLineEdit()
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw.setPlaceholderText("Придумайте пароль")
        self._pw2 = QLineEdit()
        self._pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw2.setPlaceholderText("Повторите пароль")
        self._hint_edit = QLineEdit()
        self._hint_edit.setPlaceholderText("Необязательно — подсказка если забудете")
        form.addRow("Пароль:", self._pw)
        form.addRow("Повтор:", self._pw2)
        form.addRow("Подсказка:", self._hint_edit)
        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #e05555;")
        layout.addWidget(self._error)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._check)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _check(self) -> None:
        pw = self._pw.text()
        if not pw:
            self._error.setText("Пароль не может быть пустым.")
            return
        if pw != self._pw2.text():
            self._error.setText("Пароли не совпадают.")
            return
        self._hash = _hash_pw(pw)
        self._hint = self._hint_edit.text().strip()
        self.accept()

    def result_data(self) -> tuple[str, str]:
        return self._hash, self._hint


class _EnterPasswordDialog(QDialog):
    """Ввод пароля для входа в настройки."""
    def __init__(self, hint: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Вход в настройки")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        if hint:
            hint_label = QLabel(f"Подсказка: {hint}")
            hint_label.setStyleSheet("color: #888aaa; font-style: italic;")
            layout.addWidget(hint_label)

        form = QFormLayout()
        self._pw = QLineEdit()
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw.setPlaceholderText("Введите пароль")
        self._pw.returnPressed.connect(self._try_accept)
        form.addRow("Пароль:", self._pw)
        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #e05555;")
        layout.addWidget(self._error)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Войти")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._try_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _try_accept(self) -> None:
        if not self._pw.text():
            self._error.setText("Введите пароль.")
            return
        self.accept()

    def entered_password(self) -> str:
        return self._pw.text()


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
            self.setIconSize(QSize(28, 28))
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
        self.btn_max   = _SideButton(_assets("MAX POST.ico"), "MAX POST — отправка сообщений",    "MP")
        self.btn_qr    = _SideButton(_assets("qr.ico"),       "QR Generator — генератор карточек", "QR")
        self.btn_stats = _SideButton(_assets("state.ico"),    "Статистика групп",                  "📊")
        self.btn_mkd   = _SideButton(_assets("mkd.ico"),      "СУПЕР МКД+ — в разработке",         "МКД+")

        # ВКонтакте — кнопка с бейджем непрочитанных
        self._vk_container = QWidget(self)
        self._vk_container.setFixedSize(60, 60)
        self.btn_vk = _SideButton(_assets("vk_2.ico"), "Сообщения ВКонтакте", "VK")
        self.btn_vk.setParent(self._vk_container)
        self.btn_vk.setGeometry(0, 0, 60, 60)
        self._vk_badge = QLabel("", self._vk_container)
        self._vk_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vk_badge.setFixedSize(18, 18)
        self._vk_badge.setStyleSheet(
            "background:#e74c3c; color:white; border-radius:9px; font-size:10px; font-weight:bold;"
        )
        self._vk_badge.move(38, 4)
        self._vk_badge.setVisible(False)

        layout.addWidget(self.btn_max)
        layout.addSpacing(8)
        layout.addWidget(self.btn_qr)
        layout.addSpacing(8)
        layout.addWidget(self.btn_stats)
        layout.addSpacing(8)
        layout.addWidget(self._vk_container)
        layout.addSpacing(8)
        layout.addWidget(self.btn_mkd)

        layout.addStretch()

        # Кнопка авторизации (токены)
        self.btn_auth = QPushButton()
        self.btn_auth.setObjectName("settingsBtn")
        self.btn_auth.setFixedSize(60, 50)
        self.btn_auth.setToolTip("Настройки подключений")
        _auth_icon = QIcon(_assets("authorization.ico"))
        if not _auth_icon.isNull():
            self.btn_auth.setIcon(_auth_icon)
            self.btn_auth.setIconSize(QSize(28, 28))
        else:
            self.btn_auth.setText("🔑")
        layout.addWidget(self.btn_auth)
        layout.addSpacing(6)

        # Кнопка настроек внизу — иконка settings.ico
        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("settingsBtn")
        self.btn_settings.setFixedSize(60, 50)
        self.btn_settings.setToolTip("Настройки")
        _settings_icon = QIcon(_assets("settings.ico"))
        if not _settings_icon.isNull():
            self.btn_settings.setIcon(_settings_icon)
            self.btn_settings.setIconSize(QSize(28, 28))
        else:
            self.btn_settings.setText("⚙")
        layout.addWidget(self.btn_settings)

        # Группа — только одна кнопка активна
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self.btn_max,   0)
        self._group.addButton(self.btn_qr,    1)
        self._group.addButton(self.btn_stats, 2)
        self._group.addButton(self.btn_vk,    3)
        self.btn_max.setChecked(True)

    def set_vk_badge(self, count: int) -> None:
        if count > 0:
            self._vk_badge.setText(str(min(count, 99)))
            self._vk_badge.setVisible(True)
        else:
            self._vk_badge.setVisible(False)


# ──────────────────────────────────────────────────────────────
#  Плавное переключение панелей
# ──────────────────────────────────────────────────────────────
class _FadeStack(QStackedWidget):
    """QStackedWidget с мгновенным переключением панелей."""

    def switch_to(self, index: int) -> None:
        if index == self.currentIndex():
            return
        self.setCurrentIndex(index)


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

        # ── Инициализируем Статистику групп ────────────────────
        try:
            from stats_panel import StatsPanel
            self._stats_panel = StatsPanel()
        except Exception as e:
            self._stats_panel = QLabel(f"Статистика недоступна:\n{e}")
            self._stats_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)  # type: ignore[union-attr]
            self._stats_panel.setStyleSheet("color:#888; font-size:14px; background:#f3f4f6;")  # type: ignore[union-attr]

        # ── Инициализируем панель VK-сообщений ─────────────────
        try:
            from vk_messages_panel import VkMessagesPanel
            self._vk_panel = VkMessagesPanel()
            self._vk_available = True
        except Exception as e:
            self._vk_panel = QLabel(f"VK Сообщения недоступны:\n{e}")
            self._vk_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)  # type: ignore[union-attr]
            self._vk_panel.setStyleSheet("color:#888; font-size:14px; background:#1e1e2e;")  # type: ignore[union-attr]
            self._vk_available = False

        # ── FadeStack ───────────────────────────────────────────
        self._stack = _FadeStack()

        # Переносим стили MAX POST на его centralWidget, потому что после
        # reparent в stack стили от _max_win перестают каскадироваться
        max_widget = self._max_win.centralWidget()
        max_widget.setStyleSheet(self._max_win.styleSheet())

        self._stack.addWidget(max_widget)        # index 0
        self._stack.addWidget(qr_widget)         # index 1
        self._stack.addWidget(self._stats_panel) # index 2
        self._stack.addWidget(self._vk_panel)    # index 3

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
        self._sidebar.btn_auth.clicked.connect(self._open_settings_dialog)
        self._sidebar.btn_mkd.clicked.connect(self._show_mkd_coming_soon)
        self._sidebar.btn_stats.clicked.connect(self._on_stats_clicked)
        self._sidebar.btn_vk.clicked.connect(self._on_vk_clicked)
        if self._vk_available:
            self._vk_panel.unread_changed.connect(self._sidebar.set_vk_badge)  # type: ignore[union-attr]

        # ── Тёмная тема — восстанавливаем состояние ─────────────
        prefs = _load_ui_prefs()
        self._dark_mode: bool = prefs.get("dark_mode", False)
        if self._dark_mode:
            self._apply_dark_mode(self._dark_mode)

    # ──────────────────────────────────────────────────────────
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_dark_titlebar()

    # ──────────────────────────────────────────────────────────
    def _apply_dark_titlebar(self) -> None:
        """Включает тёмный заголовок окна через Windows DWM API."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 20H1+, Windows 11)
            # значение 19 — для старых сборок Windows 10
            for attr in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr,
                    ctypes.byref(ctypes.c_int(1)),
                    ctypes.sizeof(ctypes.c_int),
                )
                if result == 0:  # S_OK
                    break
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────
    def _open_settings_dialog(self) -> None:
        prefs = _load_ui_prefs()
        pw_hash = prefs.get("settings_password_hash", "")
        hint    = prefs.get("settings_password_hint", "")

        if not pw_hash:
            # Первый вход — предложить установить пароль
            set_dlg = _SetPasswordDialog(self)
            if set_dlg.exec() != QDialog.DialogCode.Accepted:
                return
            pw_hash, hint = set_dlg.result_data()
            prefs["settings_password_hash"] = pw_hash
            prefs["settings_password_hint"] = hint
            _save_ui_prefs(prefs)
        else:
            enter_dlg = _EnterPasswordDialog(hint, self)
            if enter_dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if not _verify_pw(enter_dlg.entered_password(), pw_hash):
                QMessageBox.warning(self, "Неверный пароль", "Пароль неверный. Попробуйте ещё раз.")
                return

        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.settings_saved.connect(self._max_win.reload_senders)
        dlg.exec()

    # ──────────────────────────────────────────────────────────
    def _toggle_dark_mode(self) -> None:
        self._dark_mode = not self._dark_mode
        self._apply_dark_mode(self._dark_mode)
        prefs = _load_ui_prefs()
        prefs["dark_mode"] = self._dark_mode
        _save_ui_prefs(prefs)

    def _apply_dark_mode(self, dark: bool) -> None:
        from ui.styles import get_stylesheet, get_dark_stylesheet
        ss = get_dark_stylesheet() if dark else get_stylesheet()
        max_widget = self._stack.widget(0)
        if max_widget:
            max_widget.setStyleSheet(ss)
        if hasattr(self._stats_panel, "set_dark"):
            self._stats_panel.set_dark(dark)

    # ──────────────────────────────────────────────────────────
    def _on_stats_clicked(self) -> None:
        """Переключает на панель статистики."""
        self._stack.switch_to(2)

    # ──────────────────────────────────────────────────────────
    def _on_vk_clicked(self) -> None:
        """Переключает на панель VK-сообщений."""
        self._stack.switch_to(3)

    # ──────────────────────────────────────────────────────────
    def _show_mkd_coming_soon(self) -> None:
        """Показывает сообщение о том, что модуль МКД ещё в разработке."""
        QMessageBox.information(
            self,
            "СУПЕР МКД+",
            "Этот модуль находится в разработке и будет доступен в следующих версиях.",
        )
        # Кнопка не должна оставаться «нажатой» — возвращаем фокус на активную панель
        self._sidebar.btn_mkd.setChecked(False)

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

        # Тёмная тема
        dark_label = "☀️  Светлая тема" if self._dark_mode else "🌙  Тёмная тема"
        dark_action = menu.addAction(dark_label)
        dark_action.triggered.connect(self._toggle_dark_mode)
        menu.addSeparator()

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
