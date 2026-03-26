"""
shell_window.py — VS Code-style контейнер для всех модулей MAX POST.
"""

import ctypes
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from pathlib import Path

_log = logging.getLogger(__name__)

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer, pyqtProperty
from PyQt6.QtGui import QCursor, QIcon
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
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
QFrame#sidebarSep { background: #2d2d3f; max-height: 1px; border: none; }
QPushButton#sideBtn {
    border: none;
    border-left: 3px solid transparent;
    background: transparent;
    border-radius: 0px;
    min-width: 0px;
    min-height: 0px;
    color: #888aaa;
    font-size: 12px;
    text-align: left;
    padding-left: 14px;
}
QPushButton#expandBtn {
    background: transparent;
    border: none;
    color: #888aaa;
    min-width: 20px;
    max-width: 20px;
    min-height: 36px;
    max-height: 36px;
    text-align: center;
    padding: 0px;
}
QPushButton#expandBtn:hover {
    color: #ccccee;
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
    border-left: 3px solid transparent;
    background: transparent;
    min-width: 0px;
    min-height: 0px;
    color: #888aaa;
    font-size: 12px;
    text-align: left;
    padding-left: 14px;
}
QPushButton#settingsBtn:hover {
    background: rgba(255,255,255,0.07);
    color: #cccccc;
}
QPushButton#sideBtn:disabled {
    color: #444455;
    background: transparent;
    border-left: 3px solid transparent;
}
QPushButton#updBtn {
    border: none;
    border-left: 3px solid transparent;
    background: transparent;
    min-width: 0px;
    min-height: 0px;
    color: #888aaa;
    font-size: 12px;
    text-align: left;
    padding-left: 14px;
}
QPushButton#updBtn:hover {
    background: rgba(255,255,255,0.07);
    color: #cccccc;
}
"""

_CHANGELOG_POPUP_DARK = """
QFrame#changelogPopup {
    background: #252535;
    border: 1px solid #3a3a55;
    border-radius: 10px;
}
QLabel#changelogTitle  { color: #4a6cf7; font-size: 13px; font-weight: 700; }
QLabel#changelogVersion{ color: #aaaacc; font-size: 11px; }
QLabel#changelogItem   { color: #d0d0e8; font-size: 12px; }
QLabel#changelogSep    { color: #3a3a55; font-size: 10px; }
"""

_CHANGELOG_POPUP_LIGHT = """
QFrame#changelogPopup {
    background: #ffffff;
    border: 1px solid #c7d0db;
    border-radius: 10px;
}
QLabel#changelogTitle  { color: #2563eb; font-size: 13px; font-weight: 700; }
QLabel#changelogVersion{ color: #6b7280; font-size: 11px; }
QLabel#changelogItem   { color: #1a1a2e; font-size: 12px; }
QLabel#changelogSep    { color: #c7d0db; font-size: 10px; }
"""

_SHELL_STYLE       = "QMainWindow { background: #1e1e2e; }"
_SHELL_STYLE_LIGHT = "QMainWindow { background: #f3f4f6; } QWidget#shellBody { background: #f3f4f6; }"


def _assets(name: str) -> str:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return str(base / "assets" / name)


# ──────────────────────────────────────────────────────────────
#  Пароль для настроек подключений
# ──────────────────────────────────────────────────────────────
_PW_MAX_ATTEMPTS = 5
_PW_LOCKOUT_SEC  = 5 * 60  # 5 минут
_pw_fail_count: int   = 0
_pw_locked_until: float = 0.0


def _pw_check_locked() -> str | None:
    """Возвращает сообщение если доступ заблокирован, иначе None."""
    global _pw_locked_until
    if _pw_locked_until and time.monotonic() < _pw_locked_until:
        remaining = int(_pw_locked_until - time.monotonic())
        mins, secs = divmod(remaining, 60)
        return f"Слишком много неверных попыток.\nПовторите через {mins}:{secs:02d}."
    return None


def _pw_record_fail() -> str:
    """Фиксирует неудачную попытку. Возвращает текст ошибки для пользователя."""
    global _pw_fail_count, _pw_locked_until
    _pw_fail_count += 1
    remaining_attempts = _PW_MAX_ATTEMPTS - _pw_fail_count
    if _pw_fail_count >= _PW_MAX_ATTEMPTS:
        _pw_locked_until = time.monotonic() + _PW_LOCKOUT_SEC
        _pw_fail_count = 0
        return "Пароль неверный.\nДоступ заблокирован на 5 минут."
    return f"Пароль неверный. Осталось попыток: {remaining_attempts}."


def _pw_reset_fails() -> None:
    global _pw_fail_count, _pw_locked_until
    _pw_fail_count = 0
    _pw_locked_until = 0.0


def _verify_pw(password: str, stored: str) -> bool:
    """Проверяет пароль против PBKDF2-хэша."""
    if stored.startswith("pbkdf2:"):
        try:
            _, salt_hex, hash_hex = stored.split(":", 2)
            salt = bytes.fromhex(salt_hex)
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
            return hmac.compare_digest(key.hex(), hash_hex)
        except (ValueError, IndexError, TypeError):
            return False
    return False


def _get_admin_pw_hash() -> str:
    """Читает хэш пароля из .env (SETTINGS_PASSWORD_HASH).
    Пароль задаётся разработчиком — пользователь изменить не может."""
    from env_utils import get_env_path, load_env_safe
    path = get_env_path()
    load_env_safe(path)
    return os.getenv("SETTINGS_PASSWORD_HASH", "")



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
#  Попап changelog при наведении на кнопку upd
# ──────────────────────────────────────────────────────────────

def _app_base_dir() -> Path:
    """Возвращает папку рядом с EXE (frozen) или скриптом (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _load_changelog() -> list[dict]:
    """Читает changelog.json рядом с EXE или скриптом."""
    p = _app_base_dir() / "changelog.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []



class _ChangelogPopup(QFrame):
    """Попап с историей изменений — появляется при наведении на кнопку upd."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("changelogPopup")
        self.setFixedWidth(360)

        # Polling-таймер: вместо enter/leave проверяем позицию курсора каждые 60мс.
        # Это полностью исключает мигание, т.к. popup-окно может перехватывать mouse-события.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(60)
        self._poll_timer.timeout.connect(self._check_cursor)
        self._btn_ref: "QPushButton | None" = None  # задаётся из _UpdBtn
        self.setStyleSheet(_CHANGELOG_POPUP_LIGHT)  # корректируется через set_dark()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Скролл-область — фиксирует высоту попапа, устраняет наложение текста
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(480)
        outer.addWidget(scroll)

        # Контент-виджет с явной шириной (нужно для корректного wordWrap в QLabel)
        content = QWidget()
        content.setFixedWidth(360)
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        changelog = _load_changelog()
        from updater import _local_version  # noqa: PLC0415 — circular import avoided
        current_ver = _local_version()

        if not changelog:
            layout.addWidget(QLabel("Нет данных об обновлениях"))
        else:
            for idx, entry in enumerate(changelog[:3]):
                ver = entry.get("version", "")
                changes = entry.get("changes", [])

                if idx > 0:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setObjectName("changelogSep")
                    layout.addWidget(sep)

                header = QHBoxLayout()
                title = QLabel(f"Версия {ver}")
                title.setObjectName("changelogTitle" if ver == current_ver else "changelogVersion")
                header.addWidget(title)
                if ver == current_ver:
                    badge = QLabel("  текущая")
                    badge.setObjectName("changelogVersion")
                    header.addWidget(badge)
                header.addStretch()
                layout.addLayout(header)

                for change in changes:
                    lbl = QLabel(f"• {change}")
                    lbl.setObjectName("changelogItem")
                    lbl.setWordWrap(True)
                    layout.addWidget(lbl)

        layout.addStretch()

    def set_dark(self, dark: bool) -> None:
        self.setStyleSheet(_CHANGELOG_POPUP_DARK if dark else _CHANGELOG_POPUP_LIGHT)

    def show_near(self, btn: "QPushButton") -> None:
        """Показать попап справа от кнопки, выровненный по нижнему краю кнопки."""
        self._btn_ref = btn
        self.adjustSize()
        btn_br = btn.mapToGlobal(btn.rect().bottomRight())
        x = btn_br.x() + 6
        y = btn_br.y() - self.height()

        # Держим попап в пределах доступного экрана
        screen = QApplication.primaryScreen()
        if screen:
            ag = screen.availableGeometry()
            x = max(ag.left(), min(x, ag.right() - self.width()))
            y = max(ag.top(), min(y, ag.bottom() - self.height()))

        self.move(x, y)
        self.show()
        self._poll_timer.start()

    def _check_cursor(self) -> None:
        """Скрыть если курсор ушёл и с кнопки, и с попапа."""
        cursor = QCursor.pos()

        # Проверяем попап
        popup_rect = QRect(self.pos(), self.size())
        if popup_rect.contains(cursor):
            return

        # Проверяем кнопку
        if self._btn_ref is not None:
            btn_tl = self._btn_ref.mapToGlobal(QPoint(0, 0))
            btn_rect = QRect(btn_tl, self._btn_ref.size())
            if btn_rect.contains(cursor):
                return

        # Курсор ни там ни там — скрываем
        self._poll_timer.stop()
        self.hide()


class _UpdBtn(QPushButton):
    """Кнопка обновлений с попапом changelog при наведении."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updBtn")
        self.setFixedHeight(50)
        _icon = QIcon(_assets("upd.ico"))
        if not _icon.isNull():
            self._has_icon = True
            self.setIcon(_icon)
            self.setIconSize(QSize(28, 28))
        else:
            self._has_icon = False
            self.setText("🔄")

        self._popup = _ChangelogPopup()

    def set_expanded(self, expanded: bool) -> None:
        if self._has_icon:
            self.setText("Обновления" if expanded else "")

    def set_dark(self, dark: bool) -> None:
        self._popup.set_dark(dark)

    def enterEvent(self, event) -> None:
        if not self._popup.isVisible():
            self._popup.show_near(self)
        super().enterEvent(event)


# ──────────────────────────────────────────────────────────────
class _SideButton(QPushButton):
    def __init__(self, icon_path: str, tooltip: str, fallback: str, label: str = ""):
        super().__init__()
        self.setObjectName("sideBtn")
        self.setCheckable(True)
        self.setFixedHeight(60)
        self.setToolTip(tooltip)
        self._label = label
        icon = QIcon(icon_path)
        if not icon.isNull():
            self._has_icon = True
            self.setIcon(icon)
            self.setIconSize(QSize(28, 28))
        else:
            self._has_icon = False
            self.setText(fallback)

    def set_expanded(self, expanded: bool) -> None:
        if self._has_icon:
            self.setText(self._label if expanded else "")


class _ExpandBtn(QPushButton):
    """Кнопка нижнего сайдбара с поддержкой текстовой метки при раскрытии."""
    def __init__(self, icon_path: str, fallback: str, label: str, obj_name: str, height: int = 50):
        super().__init__()
        self.setObjectName(obj_name)
        self.setFixedHeight(height)
        self._label = label
        icon = QIcon(icon_path)
        if not icon.isNull():
            self._has_icon = True
            self.setIcon(icon)
            self.setIconSize(QSize(28, 28))
        else:
            self._has_icon = False
            self.setText(fallback)

    def set_expanded(self, expanded: bool) -> None:
        if self._has_icon:
            self.setText(self._label if expanded else "")


# ──────────────────────────────────────────────────────────────
#  Сайдбар
# ──────────────────────────────────────────────────────────────
_SIDEBAR_COLLAPSED_W = 60
_SIDEBAR_EXPANDED_W  = 210
_SIDEBAR_ANIM_MS     = 220


class _SideBar(QFrame):

    # Qt-свойство для анимации ширины
    def _get_sidebar_w(self) -> int:
        return self.width()

    def _set_sidebar_w(self, w: int) -> None:
        self.setFixedWidth(int(w))

    sidebar_w = pyqtProperty(int, _get_sidebar_w, _set_sidebar_w)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(_SIDEBAR_COLLAPSED_W)
        self._sb_expanded = False
        self._dark = False

        # Анимация ширины
        self._width_anim = QPropertyAnimation(self, b"sidebar_w", self)
        self._width_anim.setDuration(_SIDEBAR_ANIM_MS)
        self._width_anim.setEasingCurve(QEasingCurve.Type.InOutQuart)

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
        sep.setObjectName("sidebarSep")
        layout.addWidget(sep)
        layout.addSpacing(10)

        # Кнопки модулей
        self.btn_max    = _SideButton(_assets("post.ico"),  "MAX POST — отправка сообщений",    "MP",   "MAX POST")
        self.btn_qr     = _SideButton(_assets("qr.ico"),   "QR Generator — генератор карточек", "QR",   "QR")
        self.btn_stats  = _SideButton(_assets("state.ico"),"Статистика групп",                  "📊",  "Статистика")
        self.btn_mkd    = _SideButton(_assets("mkd.ico"),   "СУПЕР МКД+ — в разработке",        "МКД+", "МКД+")
        self.btn_claude = _SideButton(_assets("chat.ico"), "AI Чат — помощник",                 "✨",  "AI Чат")
        self.btn_shared = _SideButton(_assets("dwnld.ico"), "Общие файлы — фото и документы",  "📁",  "Файлы")

        # ВКонтакте — кнопка с бейджем непрочитанных
        self.btn_vk = _SideButton(_assets("vk_2.ico"), "Сообщения ВКонтакте", "VK", "ВКонтакте")
        self._vk_badge = QLabel("", self.btn_vk)
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
        layout.addWidget(self.btn_vk)
        layout.addSpacing(8)
        layout.addWidget(self.btn_claude)
        layout.addSpacing(8)
        layout.addWidget(self.btn_shared)
        layout.addSpacing(8)
        layout.addWidget(self.btn_mkd)
        layout.addSpacing(6)

        # Кнопка развернуть/свернуть — по центру между навигацией и нижними кнопками
        self.btn_expand = QPushButton()
        self.btn_expand.setObjectName("expandBtn")
        self._apply_expand_icon(expanded=False)
        self.btn_expand.setToolTip("Развернуть панель")
        self.btn_expand.clicked.connect(self._toggle_expand)
        # btn_expand не добавляется в layout — его позиционирует _BodyWidget
        layout.addStretch(1)

        # Кнопка changelog
        self.btn_upd = _UpdBtn(self)
        layout.addWidget(self.btn_upd)
        layout.addSpacing(2)

        # Кнопка переключения темы
        self.btn_theme = QPushButton("🌙")
        self.btn_theme.setObjectName("updBtn")
        self.btn_theme.setFixedHeight(40)
        self.btn_theme.setToolTip("Переключить тему (тёмная / светлая)")
        layout.addWidget(self.btn_theme)
        layout.addSpacing(2)

        # Кнопка авторизации (токены)
        self.btn_auth = _ExpandBtn(_assets("authorization.ico"), "🔑", "Токены",    "settingsBtn", 50)
        self.btn_auth.setToolTip("Настройки подключений")
        layout.addWidget(self.btn_auth)
        layout.addSpacing(6)

        # Кнопка настроек внизу
        self.btn_settings = _ExpandBtn(_assets("settings.ico"), "⚙", "Настройки", "settingsBtn", 50)
        self.btn_settings.setToolTip("Настройки")
        layout.addWidget(self.btn_settings)

        # Группа — только одна кнопка активна
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self.btn_max,    0)
        self._group.addButton(self.btn_qr,    1)
        self._group.addButton(self.btn_stats, 2)
        self._group.addButton(self.btn_vk,    3)
        self._group.addButton(self.btn_claude, 4)
        self._group.addButton(self.btn_shared, 5)
        self.btn_max.setChecked(True)

    # ── Иконка кнопки expand/collapse ──────────────────────────
    def _apply_expand_icon(self, expanded: bool) -> None:
        from PyQt6.QtGui import QPixmap, QPainter, QFont, QColor, QTransform
        from PyQt6.QtCore import Qt
        px = QPixmap(20, 24)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setFont(QFont("Segoe UI Symbol", 15))
        p.setPen(QColor("#a855f7"))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "\u27a7")
        p.end()
        if expanded:
            px = px.transformed(QTransform().scale(-1, 1))
        self.btn_expand.setIcon(QIcon(px))
        self.btn_expand.setIconSize(QSize(20, 24))
        self.btn_expand.setText("")

    # ── Переключить expand/collapse по клику ───────────────────
    def _toggle_expand(self) -> None:
        if self._sb_expanded:
            self._do_collapse()
        else:
            self._do_expand()

    # ── Раскрыть ────────────────────────────────────────────────
    def _do_expand(self) -> None:
        self._sb_expanded = True
        self._set_btn_labels(True)
        self._apply_expand_icon(expanded=True)
        self.btn_expand.setToolTip("Свернуть панель")
        self._width_anim.stop()
        self._width_anim.setStartValue(self.width())
        self._width_anim.setEndValue(_SIDEBAR_EXPANDED_W)
        self._width_anim.start()

    # ── Свернуть ────────────────────────────────────────────────
    def _do_collapse(self) -> None:
        self._sb_expanded = False
        self._set_btn_labels(False)
        self._apply_expand_icon(expanded=False)
        self.btn_expand.setToolTip("Развернуть панель")
        self._width_anim.stop()
        self._width_anim.setStartValue(self.width())
        self._width_anim.setEndValue(_SIDEBAR_COLLAPSED_W)
        self._width_anim.start()

    # ── Обновить текстовые метки всех кнопок ───────────────────
    def _set_btn_labels(self, show: bool) -> None:
        for btn in (self.btn_max, self.btn_qr, self.btn_stats, self.btn_vk,
                    self.btn_claude, self.btn_shared, self.btn_mkd,
                    self.btn_upd, self.btn_auth, self.btn_settings):
            btn.set_expanded(show)
        emoji = "☀️" if self._dark else "🌙"
        self.btn_theme.setText(f"{emoji}  Тема" if show else emoji)

    # ── Бейдж ВК ────────────────────────────────────────────────
    def set_vk_badge(self, count: int) -> None:
        if count > 0:
            self._vk_badge.setText(str(min(count, 99)))
            self._vk_badge.setVisible(True)
        else:
            self._vk_badge.setVisible(False)

    # ── Тема ─────────────────────────────────────────────────────
    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        emoji = "☀️" if dark else "🌙"
        suffix = "  Тема" if self._sb_expanded else ""
        self.btn_theme.setText(emoji + suffix)
        self.btn_theme.setToolTip("Переключить на светлую тему" if dark else "Переключить на тёмную тему")
        self.btn_upd.set_dark(dark)


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
#  Контейнер сайдбар+стек; позиционирует кнопку expand на границе
# ──────────────────────────────────────────────────────────────
class _BodyWidget(QWidget):
    """shellBody: кнопка expand плавает на правом краю сайдбара, по центру высоты."""

    def __init__(self) -> None:
        super().__init__()
        self._eb: QPushButton | None = None
        self._sb: QFrame | None = None

    def init_expand(self, sidebar: QFrame, btn: QPushButton) -> None:
        self._sb = sidebar
        self._eb = btn
        btn.setParent(self)
        btn.show()
        btn.raise_()
        self._repos()

    def resizeEvent(self, ev) -> None:  # type: ignore[override]
        super().resizeEvent(ev)
        self._repos()

    def _repos(self) -> None:
        if not self._eb or not self._sb:
            return
        bw = self._eb.width() or 20
        bh = self._eb.height() or 36
        sw = self._sb.width()
        self._eb.move(sw - bw // 2, (self.height() - bh) // 2)
        self._eb.raise_()


# ──────────────────────────────────────────────────────────────
#  Главное окно-контейнер
# ──────────────────────────────────────────────────────────────
class ShellWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MAX POST")
        self.setWindowIcon(QIcon(_assets("MAX POST.ico")))
        self.menuBar().setVisible(False)  # убираем стандартное меню

        # ── Инициализируем MAX POST ─────────────────────────────
        from main import MainWindow as MaxWindow
        self._max_win = MaxWindow()
        self._max_win._shell_window = self  # чтобы трей мог управлять ShellWindow

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

        # ── Инициализируем панель Claude AI ─────────────────────────
        try:
            from claude_panel import ClaudePanel
            self._claude_panel = ClaudePanel()
            self._claude_available = True
        except Exception as e:
            self._claude_panel = QLabel(f"Claude AI недоступен:\n{e}")
            self._claude_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)  # type: ignore[union-attr]
            self._claude_panel.setStyleSheet("color:#888; font-size:14px; background:#1e1e2e;")  # type: ignore[union-attr]
            self._claude_available = False

        # ── Инициализируем панель Общих файлов ─────────────────
        try:
            from shared_files_panel import SharedFilesPanel
            self._shared_panel = SharedFilesPanel()
            self._shared_available = True
        except Exception as e:
            self._shared_panel = QLabel(f"Общие файлы недоступны:\n{e}")
            self._shared_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)  # type: ignore[union-attr]
            self._shared_panel.setStyleSheet("color:#888; font-size:14px; background:#1e1e2e;")  # type: ignore[union-attr]
            self._shared_available = False

        # ── FadeStack ───────────────────────────────────────────
        self._stack = _FadeStack()

        # Переносим стили MAX POST на его centralWidget, потому что после
        # reparent в stack стили от _max_win перестают каскадироваться
        max_widget = self._max_win.centralWidget()
        max_widget.setStyleSheet(self._max_win.styleSheet())

        self._stack.addWidget(max_widget)           # index 0
        self._stack.addWidget(qr_widget)            # index 1
        self._stack.addWidget(self._stats_panel)    # index 2
        self._stack.addWidget(self._vk_panel)       # index 3
        self._stack.addWidget(self._claude_panel)   # index 4
        self._stack.addWidget(self._shared_panel)   # index 5

        # ── Компоновка ──────────────────────────────────────────
        self._sidebar = _SideBar()
        body = _BodyWidget()
        body.setObjectName("shellBody")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self._sidebar)
        bl.addWidget(self._stack)
        body.init_expand(self._sidebar, self._sidebar.btn_expand)
        self._sidebar._width_anim.valueChanged.connect(lambda _: body._repos())
        self.setCentralWidget(body)

        # ── Сигналы ─────────────────────────────────────────────
        self._sidebar._group.idClicked.connect(self._switch_panel)
        self._sidebar.btn_settings.clicked.connect(self._show_settings_menu)
        self._sidebar.btn_auth.clicked.connect(self._open_settings_dialog)
        self._sidebar.btn_mkd.clicked.connect(self._show_mkd_coming_soon)
        self._sidebar.btn_stats.clicked.connect(self._on_stats_clicked)
        self._sidebar.btn_vk.clicked.connect(self._on_vk_clicked)
        self._sidebar.btn_claude.clicked.connect(self._on_claude_clicked)
        self._sidebar.btn_shared.clicked.connect(self._on_shared_clicked)
        if self._shared_available:
            self._shared_panel.photo_for_post.connect(self._on_shared_photo_for_post)  # type: ignore[union-attr]
        if self._vk_available:
            self._vk_panel.unread_changed.connect(self._sidebar.set_vk_badge)  # type: ignore[union-attr]
        if self._claude_available:
            self._claude_panel.set_post_text_getter(  # type: ignore[union-attr]
                lambda: self._max_win.text_input.toPlainText()
            )

        self._sidebar.btn_theme.clicked.connect(self._toggle_dark_mode)

        # ── Тема — восстанавливаем состояние ────────────────────
        prefs = _load_ui_prefs()
        self._dark_mode: bool = prefs.get("dark_mode", False)
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
        # Проверка блокировки
        lock_msg = _pw_check_locked()
        if lock_msg:
            QMessageBox.warning(self, "Доступ заблокирован", lock_msg)
            return

        pw_hash = _get_admin_pw_hash()
        if not pw_hash:
            QMessageBox.warning(self, "Настройки недоступны",
                                "Пароль администратора не задан.\n"
                                "Добавьте SETTINGS_PASSWORD_HASH в .env.")
            return

        enter_dlg = _EnterPasswordDialog("", self)
        if enter_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not _verify_pw(enter_dlg.entered_password(), pw_hash):
            msg = _pw_record_fail()
            QMessageBox.warning(self, "Неверный пароль", msg)
            return

        _pw_reset_fails()
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
        self.setStyleSheet(_SIDEBAR_STYLE + (_SHELL_STYLE if dark else _SHELL_STYLE_LIGHT))
        ss = get_dark_stylesheet() if dark else get_stylesheet()
        max_widget = self._stack.widget(0)
        if max_widget:
            max_widget.setStyleSheet(ss)
        if hasattr(self._stats_panel, "set_dark"):
            self._stats_panel.set_dark(dark)
        if self._vk_panel is not None and hasattr(self._vk_panel, "set_dark"):
            self._vk_panel.set_dark(dark)
        if hasattr(self._claude_panel, "set_dark"):
            self._claude_panel.set_dark(dark)
        if hasattr(self._shared_panel, "set_dark"):
            self._shared_panel.set_dark(dark)
        self._sidebar.btn_upd.set_dark(dark)
        self._sidebar.set_dark(dark)

    # ──────────────────────────────────────────────────────────
    def _on_stats_clicked(self) -> None:
        """Переключает на панель статистики."""
        self._stack.switch_to(2)

    # ──────────────────────────────────────────────────────────
    def _on_vk_clicked(self) -> None:
        """Переключает на панель VK-сообщений."""
        self._stack.switch_to(3)

    # ──────────────────────────────────────────────────────────
    def _on_claude_clicked(self) -> None:
        """Переключает на панель Claude AI."""
        self._stack.switch_to(4)

    # ──────────────────────────────────────────────────────────
    def _on_shared_clicked(self) -> None:
        """Переключает на панель Общих файлов."""
        self._stack.switch_to(5)

    # ──────────────────────────────────────────────────────────
    def _on_shared_photo_for_post(self, path: str) -> None:
        """Вставляет фото из Общих файлов в пост и переключает на панель MAX POST."""
        self._max_win.set_photo_from_external(path)
        self._stack.switch_to(0)
        self._sidebar._group.button(0).setChecked(True)

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
        # Если нажали X (не «Выход» из трея) — сворачиваем в трей
        _tray = getattr(self._max_win, "_tray", None)
        if not self._max_win._real_quit and _tray is not None and _tray.isVisible():
            event.ignore()
            self.hide()
            n_sched = sum(
                1 for v in self._max_win._scheduled_posts.values()
                if v.get("timer") and v["timer"].isActive()
            )
            if n_sched:
                self._max_win._tray_notify(
                    "MAX POST свёрнут",
                    f"Программа работает в фоне.\nОтложенных постов: {n_sched}.",
                )
            else:
                self._max_win._tray_notify("MAX POST свёрнут", "Программа работает в фоне.")
            return

        # Полное закрытие
        self._sidebar._width_anim.stop()
        self._max_win.close()
        for panel in (self._qr_win, self._stats_panel, self._vk_panel, self._claude_panel):
            if panel is not None:
                try:
                    panel.close()
                except Exception:
                    pass
        super().closeEvent(event)
        # QSystemTrayIcon может удерживать event loop — явно завершаем
        QApplication.quit()
