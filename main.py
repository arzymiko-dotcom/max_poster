import atexit
import json
import logging
import os
import random
import sys
import time
import uuid
from pathlib import Path

from PyQt6.QtCore import QDateTime, QSize, QTime, QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyledItemDelegate,
    QSlider,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Роль для пометки вручную добавленных адресов
_MANUAL_ROLE: int = Qt.ItemDataRole.UserRole + 1
# Роль для закреплённой основной группы МАХ
_PINNED_ROLE: int = Qt.ItemDataRole.UserRole + 2

import tg_notify
from address_parser import extract_all_addresses
from excel_matcher import ExcelMatcher, MatchResult
import history_manager
from max_sender import MaxSender
from state_manager import StateManager
from updater import check_for_updates
from vk_sender import VkSender

from ui.paths import _assets_dir, _fonts_dir
from ui.widgets import LineNumberedEdit, _NumberedItemDelegate
from ui.emoji_picker import EmojiPicker
from ui.background import _BgWidget
from ui.animations import SuccessOverlay
from ui.preview_card import PreviewCard
from ui.dialogs import ThemePickerDialog, FontPickerDialog, AddAddressDialog, VkEditDialog
from ui.styles import get_stylesheet
from constants import (
    PARSE_DEBOUNCE_MS,
    SAVE_DEBOUNCE_MS,
    UPDATE_CHECK_DELAY_MS,
    TEXT_CHAR_LIMIT,
)

_log = logging.getLogger(__name__)


class _ConnCheckWorker(QThread):
    """Проверяет соединение с GREEN-API в фоне, чтобы не блокировать UI."""
    done = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, max_sender, parent=None) -> None:
        super().__init__(parent)
        self._sender = max_sender

    def run(self) -> None:
        result = self._sender.open_max_for_login()
        self.done.emit(result.success, result.message)


class SendWorker(QThread):
    result_ready = pyqtSignal(bool, str)
    progress = pyqtSignal(str)
    progress_step = pyqtSignal(int, int)   # (current, total)

    def __init__(
        self,
        max_sender: MaxSender,
        vk_sender: VkSender,
        chat_ids: list,
        text: str,
        image_path: "str | None",
        send_max: bool,
        send_vk: bool,
    ) -> None:
        super().__init__()
        self.max_sender = max_sender
        self.vk_sender = vk_sender
        self.chat_ids = chat_ids
        self.text = text
        self.image_path = image_path
        self.send_max = send_max
        self.send_vk = send_vk
        self._cancelled = False
        self.vk_post_id: int | None = None  # заполняется в run() при успешной отправке в ВК

    def cancel(self) -> None:
        """Запрашивает отмену — поток остановится после текущей отправки."""
        self._cancelled = True

    def run(self) -> None:
        lines: list[str] = []
        success = True

        if self.send_max:
            # Проверяем авторизацию перед стартом рассылки
            self.progress.emit("Проверка авторизации MAX…")
            if not self.max_sender.is_authorized():
                self.result_ready.emit(False,
                    "❌ Аккаунт MAX не авторизован. "
                    "Проверьте подключение в настройках (🔑) и попробуйте снова.")
                return

            total = len(self.chat_ids)
            for i, chat_id in enumerate(self.chat_ids, 1):
                if self._cancelled:
                    lines.append(f"⛔ Отменено после {i - 1}/{total} отправок.")
                    self.result_ready.emit(False, "\n".join(lines))
                    return
                # Случайная пауза между отправками — имитирует живого человека
                if i > 1:
                    delay = random.randint(5, 12)
                    for sec in range(delay):
                        if self._cancelled:
                            break
                        self.progress.emit(f"MAX {i}/{total} · пауза {delay - sec}с…")
                        time.sleep(1)
                self.progress.emit(f"MAX {i}/{total}…")
                self.progress_step.emit(i, total)
                r = self.max_sender.send_post(
                    chat_link=chat_id,
                    text=self.text,
                    image_path=self.image_path,
                )
                lines.append(f"MAX [{i}/{total}]: {r.message}")
                if not r.success:
                    success = False

        if not self._cancelled and self.send_vk:
            r = self.vk_sender.send_post(
                text=self.text,
                image_path=self.image_path,
                progress=lambda msg: self.progress.emit(f"ВК: {msg}"),
            )
            lines.append(f"ВК: {r.message}")
            if not r.success:
                success = False
            elif r.post_id:
                self.vk_post_id = r.post_id

        self.result_ready.emit(success, "\n".join(lines))


class VkScheduleWorker(QThread):
    """Регистрирует отложенный пост на стороне ВКонтакте (wall.post с publish_date).
    После успеха ВК сам опубликует пост в нужное время — программа может быть выключена.
    """
    done = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, vk_sender: "VkSender", text: str, image_path: "str | None",
                 publish_date: int, parent=None) -> None:
        super().__init__(parent)
        self._sender = vk_sender
        self._text = text
        self._image_path = image_path
        self._publish_date = publish_date

    def run(self) -> None:
        r = self._sender.send_post(
            text=self._text,
            image_path=self._image_path,
            publish_date=self._publish_date,
        )
        self.done.emit(r.success, r.message)


class VkEditWorker(QThread):
    """Редактирует пост ВКонтакте в фоновом потоке."""
    done = pyqtSignal(bool, str)

    def __init__(self, vk_sender: "VkSender", post_id: int,
                 text: str, image_path: "str | None", parent=None) -> None:
        super().__init__(parent)
        self._sender = vk_sender
        self._post_id = post_id
        self._text = text
        self._image_path = image_path

    def run(self) -> None:
        r = self._sender.edit_post(
            post_id=self._post_id,
            text=self._text,
            image_path=self._image_path,
        )
        self.done.emit(r.success, r.message)


class VkLoadTextWorker(QThread):
    """Загружает оригинальный текст поста из ВКонтакте."""
    done = pyqtSignal(str)  # текст поста (пустая строка при ошибке)

    def __init__(self, vk_sender: "VkSender", post_id: int, parent=None) -> None:
        super().__init__(parent)
        self._sender = vk_sender
        self._post_id = post_id

    def run(self) -> None:
        text = self._sender.get_post_text(self._post_id)
        self.done.emit(text)


class SendResultDialog(QDialog):
    """Диалог с детальными результатами отправки."""

    def __init__(self, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Результаты отправки")
        self.setMinimumWidth(520)
        self.setMinimumHeight(300)

        lines = [l for l in message.strip().splitlines() if l.strip()]
        ok_count = sum(1 for l in lines if "Отправлено" in l or "отправлено" in l.lower())
        fail_count = sum(1 for l in lines if "Ошибка" in l or "ошибка" in l.lower() or "⛔" in l)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # Summary row
        summary_row = QHBoxLayout()
        ok_lbl = QLabel(f"✅  Успешно: {ok_count}")
        ok_lbl.setStyleSheet("font-size:14px; font-weight:600; color:#16a34a;")
        fail_lbl = QLabel(f"❌  Ошибок: {fail_count}")
        fail_lbl.setStyleSheet("font-size:14px; font-weight:600; color:#dc2626;")
        summary_row.addWidget(ok_lbl)
        summary_row.addSpacing(24)
        summary_row.addWidget(fail_lbl)
        summary_row.addStretch()
        layout.addLayout(summary_row)

        # Results list
        list_w = QListWidget()
        list_w.setAlternatingRowColors(True)
        list_w.setStyleSheet("""
            QListWidget { border:1px solid #e4eaf0; border-radius:8px;
                          font-size:13px; background:#ffffff;
                          alternate-background-color:#f8fafc; }
            QListWidget::item { padding:5px 8px; }
        """)
        for line in lines:
            item = QListWidgetItem(line)
            low = line.lower()
            if "ошибка" in low or "⛔" in line or "error" in low:
                item.setForeground(QColor("#dc2626"))
                item.setBackground(QColor("#fff5f5"))
            elif "отправлено" in low or "ok" in low:
                item.setForeground(QColor("#16a34a"))
            list_w.addItem(item)
        layout.addWidget(list_w)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("MAX POST")
        self.setWindowIcon(QIcon(str(_assets_dir() / "MAX POST.ico")))
        self.resize(1280, 760)

        # Версия — читаем один раз
        _ver_file = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "version.txt"
        _ver_lines = _ver_file.read_text(encoding="utf-8").splitlines() if _ver_file.exists() else []
        self._app_version: str = _ver_lines[0].strip() if _ver_lines else "?"

        self.excel_path: Path = self._resolve_excel_path()
        self.image_path: "Path | None" = None
        self._worker: "SendWorker | None" = None
        self._bg_index: "int | None" = None
        self._bg_mode: int = 0  # 0 = фон, 1 = наложение
        self._bg_opacity: int = 50
        self._bg_widget: "_BgWidget | None" = None

        # Шрифты интерфейса — загружаются отложено после показа окна
        self._ui_font_family: str = ""
        self._ui_font_size: int = 13
        self._ui_font_families: list[str] = []
        self._pending_font_family: str = ""
        self._pending_font_size: int = 0

        # Кэшированный ExcelMatcher — читает Excel только один раз
        self._matcher: "ExcelMatcher | None" = (
            ExcelMatcher(self.excel_path) if self.excel_path.exists() else None
        )

        _appdata = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST" if getattr(sys, "frozen", False) else Path(__file__).parent
        if getattr(sys, "frozen", False):
            _old_appdata = Path(os.environ.get("APPDATA", Path.home())) / "max_poster"
            if not _appdata.exists() and _old_appdata.exists():
                try:
                    _old_appdata.rename(_appdata)
                except OSError:
                    pass  # если переименовать не удалось — просто создадим новую папку ниже
        _appdata.mkdir(parents=True, exist_ok=True)
        self.state_manager = StateManager(_appdata / "app_state.json")
        self.max_sender = MaxSender()
        self.vk_sender = VkSender()
        atexit.register(self._do_save_state)  # сохраняем состояние и при крэше
        self._pending_history: dict = {}
        self._scheduled_posts: dict = {}  # entry_id -> {"timer": QTimer, "data": dict}

        self._parse_timer = QTimer(self)
        self._parse_timer.setSingleShot(True)
        self._parse_timer.setInterval(PARSE_DEBOUNCE_MS)
        self._parse_timer.timeout.connect(self._auto_check_addresses)

        # Дебаунс сохранения состояния — не пишем на каждый символ
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._do_save_state)

        self.setAcceptDrops(True)
        self._real_quit = False  # True → полное закрытие; False → сворачивание в трей

        self._build_menu()
        self._build_ui()
        self._apply_styles()
        self._setup_tray()
        self.load_state()
        # Загружаем шрифты и применяем сохранённый шрифт после показа окна
        QTimer.singleShot(0, self._deferred_font_load)
        QTimer.singleShot(500, self._load_scheduled_from_disk)

        # Горячие клавиши (Ctrl+Return и Ctrl+L заданы через QAction в меню)

    @staticmethod
    def _resolve_excel_path() -> Path:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        return base / "max_address.xlsx"

    @staticmethod
    def _load_font_families() -> list[str]:
        """Загрузить шрифты из папки fonts/ и вернуть отсортированный список семейств."""
        fonts_dir = _fonts_dir()
        seen: set[str] = set()
        families: list[str] = []
        for f in sorted(list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf"))):
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid >= 0:
                for fam in QFontDatabase.applicationFontFamilies(fid):
                    if fam not in seen:
                        seen.add(fam)
                        families.append(fam)
        return sorted(families)

    def _deferred_font_load(self) -> None:
        """Загружает шрифты и применяет сохранённый шрифт. Вызывается после показа окна."""
        self._ui_font_families = self._load_font_families()
        fam = self._pending_font_family
        sz = self._pending_font_size
        if fam and fam in self._ui_font_families:
            self._apply_ui_font(fam, sz or self._ui_font_size)

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("Файл")
        actions_menu = menu.addMenu("Действия")
        view_menu = menu.addMenu("Вид")
        help_menu = menu.addMenu("Справка")

        open_image_action = QAction("Открыть фото", self)
        open_image_action.setShortcut(QKeySequence("Ctrl+L"))
        open_image_action.triggered.connect(self.select_image)
        file_menu.addAction(open_image_action)

        clear_photo_action = QAction("Очистить фото", self)
        clear_photo_action.triggered.connect(self._clear_photo)
        file_menu.addAction(clear_photo_action)

        file_menu.addSeparator()

        reload_excel_action = QAction("Обновить реестр адресов", self)
        reload_excel_action.triggered.connect(self._reload_excel)
        file_menu.addAction(reload_excel_action)

        file_menu.addSeparator()

        clear_action = QAction("Очистить форму", self)
        clear_action.triggered.connect(self.clear_form)
        file_menu.addAction(clear_action)

        file_menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self._quit_app)
        file_menu.addAction(exit_action)

        check_action = QAction("Проверить адрес", self)
        check_action.triggered.connect(self.check_post)
        actions_menu.addAction(check_action)

        send_action = QAction("Опубликовать", self)
        send_action.setShortcut(QKeySequence("Ctrl+Return"))
        send_action.triggered.connect(self.send_post)
        actions_menu.addAction(send_action)

        actions_menu.addSeparator()

        conn_action = QAction("Проверить соединение MAX…", self)
        conn_action.triggered.connect(self._check_max_connection)
        actions_menu.addAction(conn_action)

        theme_action = QAction("Тема оформления…", self)
        theme_action.triggered.connect(self._open_theme_picker)
        view_menu.addAction(theme_action)

        font_action = QAction("Шрифт интерфейса…", self)
        font_action.triggered.connect(self._open_font_picker)
        view_menu.addAction(font_action)

        about_action = QAction("О программе", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        shortcuts_action = QAction("Горячие клавиши", self)
        shortcuts_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts_action)

        help_menu.addSeparator()

        update_action = QAction("Проверить обновления…", self)
        update_action.triggered.connect(lambda: check_for_updates(self, silent=False))
        help_menu.addAction(update_action)

        integrity_action = QAction("Проверка целостности программы…", self)
        integrity_action.triggered.connect(self._check_integrity)
        help_menu.addAction(integrity_action)

    def _build_ui(self) -> None:
        central = _BgWidget()
        central.setObjectName("maxPosterContent")
        self._bg_widget = central
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left_box = QGroupBox()
        left_box.setObjectName("sidePanel")
        left_layout = QVBoxLayout(left_box)
        left_layout.setSpacing(12)
        left_layout.setContentsMargins(12, 10, 12, 12)

        # ── Заголовок левой панели ───────────────────────────────────
        left_header = QFrame()
        left_header.setObjectName("checklistFrame")
        lh_layout = QHBoxLayout(left_header)
        lh_layout.setContentsMargins(14, 10, 14, 10)
        lh_title = QLabel("Ввод данных")
        lh_title.setObjectName("checklistTitle")
        lh_layout.addWidget(lh_title)
        lh_layout.addStretch()
        left_layout.addWidget(left_header)

        self.text_input = LineNumberedEdit()
        self.text_input.textChanged.connect(self.sync_preview)

        # Нижняя панель: смайлик + счётчик символов
        self._emoji_picker: "EmojiPicker | None" = None

        self._emoji_btn = QPushButton("😊")
        self._emoji_btn.setObjectName("emojiButton")
        self._emoji_btn.setFixedSize(28, 28)
        self._emoji_btn.clicked.connect(self._toggle_emoji_picker)

        self._char_counter = QLabel("0/4000")
        self._char_counter.setObjectName("charCounter")

        right_bar = QWidget()
        rb_layout = QVBoxLayout(right_bar)
        rb_layout.setContentsMargins(0, 2, 2, 2)
        rb_layout.setSpacing(2)
        rb_layout.addWidget(self._emoji_btn, alignment=Qt.AlignmentFlag.AlignRight)
        rb_layout.addWidget(self._char_counter, alignment=Qt.AlignmentFlag.AlignRight)

        bottom_bar = QWidget()
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(0, 0, 0, 0)
        bb_layout.addStretch()
        bb_layout.addWidget(right_bar)

        self.text_input.setPlaceholderText(
            "Введите текст объявления…\n\nАдрес будет найден автоматически"
        )

        text_container = QFrame()
        text_container.setObjectName("textContainer")
        self._text_container = text_container
        tc_layout = QVBoxLayout(text_container)
        tc_layout.setContentsMargins(0, 0, 0, 0)
        tc_layout.setSpacing(0)
        tc_layout.addWidget(self.text_input)
        tc_layout.addWidget(bottom_bar)

        self._addr_list = QListWidget()
        self._addr_list.setMinimumHeight(80)
        self._addr_list.setObjectName("addrList")
        self._addr_list.setAlternatingRowColors(True)
        self._addr_list.setItemDelegate(_NumberedItemDelegate(self._addr_list))
        self._addr_list.itemChanged.connect(self._update_checklist)
        self._addr_list.itemChanged.connect(self.save_state)
        self._insert_pinned_group()

        left_layout.addWidget(text_container, 1)
        addr_header_frame = QFrame()
        addr_header_frame.setObjectName("checklistFrame")
        ah_layout = QHBoxLayout(addr_header_frame)
        ah_layout.setContentsMargins(14, 10, 14, 10)
        self._addr_count_lbl = QLabel("Адреса для рассылки MAX")
        self._addr_count_lbl.setObjectName("checklistTitle")
        addr_lbl = self._addr_count_lbl
        self._add_addr_btn = QPushButton("+")
        self._add_addr_btn.setObjectName("addAddrBtn")
        self._add_addr_btn.setFixedSize(24, 24)
        self._add_addr_btn.setToolTip("Добавить адрес вручную")
        self._add_addr_btn.clicked.connect(self._add_address_manually)
        ah_layout.addWidget(addr_lbl)
        ah_layout.addStretch()
        ah_layout.addWidget(self._add_addr_btn)
        left_layout.addWidget(addr_header_frame)

        self._addr_search = QLineEdit()
        self._addr_search.setPlaceholderText("🔍 Поиск в max_address.xlsx…")
        self._addr_search.setObjectName("addrSearch")
        self._addr_search.setFixedHeight(26)
        self._addr_search.textChanged.connect(self._on_addr_search_changed)
        if self._matcher is not None:
            self._addr_search.show()
        else:
            self._addr_search.hide()
        left_layout.addWidget(self._addr_search)

        self._addr_search_results = QListWidget()
        self._addr_search_results.setObjectName("addrSearchResults")
        self._addr_search_results.setMaximumHeight(180)
        self._addr_search_results.hide()
        self._addr_search_results.itemClicked.connect(self._on_addr_search_item_clicked)
        left_layout.addWidget(self._addr_search_results)

        # Таймер debounce для поиска (300 мс)
        self._addr_search_timer = QTimer(self)
        self._addr_search_timer.setSingleShot(True)
        self._addr_search_timer.timeout.connect(self._do_addr_search)

        addr_hint = QLabel("⚠️ Рекомендуется не более 10 групп за раз в 5 минут во избежание бана МАХ")
        addr_hint.setObjectName("addrHintLbl")
        left_layout.addWidget(addr_hint)

        left_layout.addWidget(self._addr_list, 1)

        # Создаём заранее — используется в строке платформ
        self.clear_button = QPushButton("Очистить")
        self.clear_button.setObjectName("clearButton")
        self.clear_button.clicked.connect(self.clear_form)

        # ── Платформы ────────────────────────────────────────────────
        platforms_section = QWidget()
        platforms_section.setObjectName("platformsSection")
        pl_layout = QVBoxLayout(platforms_section)
        pl_layout.setContentsMargins(0, 0, 0, 0)
        pl_layout.setSpacing(6)

        pl_title = QLabel("Платформы")
        pl_title.setObjectName("sectionTitle")
        pl_layout.addWidget(pl_title)

        self.chk_max = QCheckBox("MAX")
        self.chk_max.setChecked(True)
        self.chk_vk = QCheckBox("ВКонтакте")
        self.chk_vk.setChecked(False)

        # Иконки платформ
        _assets = _assets_dir()
        _max_icon_path = _assets / "max.ico"
        _vk_icon_path = _assets / "vk_2.ico"
        if _max_icon_path.exists():
            self.chk_max.setIcon(QIcon(str(_max_icon_path)))
            self.chk_max.setIconSize(QSize(18, 18))
        if _vk_icon_path.exists():
            self.chk_vk.setIcon(QIcon(str(_vk_icon_path)))
            self.chk_vk.setIconSize(QSize(18, 18))

        platforms_row = QHBoxLayout()
        platforms_row.setContentsMargins(0, 0, 0, 0)
        platforms_row.setSpacing(8)

        chk_max_frame = QFrame()
        chk_max_frame.setObjectName("platformChip")
        chk_max_fl = QHBoxLayout(chk_max_frame)
        chk_max_fl.setContentsMargins(10, 6, 10, 6)
        chk_max_fl.addWidget(self.chk_max)

        chk_vk_frame = QFrame()
        chk_vk_frame.setObjectName("platformChip")
        chk_vk_fl = QHBoxLayout(chk_vk_frame)
        chk_vk_fl.setContentsMargins(10, 6, 10, 6)
        chk_vk_fl.addWidget(self.chk_vk)

        platforms_row.addWidget(chk_max_frame)
        platforms_row.addWidget(chk_vk_frame)
        platforms_row.addStretch()
        platforms_row.addWidget(self.clear_button)
        pl_layout.addLayout(platforms_row)

        # ── Кнопки действий ─────────────────────────────────────────
        buttons_row = QGridLayout()
        buttons_row.setSpacing(8)

        self.check_button = QPushButton("Проверить адрес")
        self.check_button.clicked.connect(self.check_post)
        self.check_button.setToolTip("Найти адреса из текста в реестре Excel")

        self.photo_button = QPushButton("Загрузить фото")
        self.photo_button.clicked.connect(self.select_image)
        self.photo_button.setToolTip("Выбрать фото (Ctrl+L)")

        self.send_button = QPushButton("Опубликовать")
        self.send_button.clicked.connect(self.send_post)
        self.send_button.setObjectName("primaryButton")
        self.send_button.setToolTip("Опубликовать пост (Ctrl+Return)")

        # Row 0: вспомогательные кнопки
        buttons_row.addWidget(self.check_button, 0, 0)
        buttons_row.addWidget(self.photo_button, 0, 1)
        # Row 1: отложенный пост
        sched_frame = QFrame()
        sched_frame.setObjectName("scheduleRow")
        sched_fl = QHBoxLayout(sched_frame)
        sched_fl.setContentsMargins(0, 0, 0, 0)
        sched_fl.setSpacing(8)
        self._chk_schedule = QCheckBox("Отложить")
        self._chk_schedule.setObjectName("scheduleChk")
        self._chk_schedule.toggled.connect(self._on_schedule_toggled)

        _now30 = QDateTime.currentDateTime().addSecs(30 * 60)
        self._sched_date = QDateEdit()
        self._sched_date.setObjectName("scheduleDate")
        self._sched_date.setDisplayFormat("dd.MM.yyyy")
        self._sched_date.setCalendarPopup(True)
        self._sched_date.setDate(_now30.date())
        self._sched_date.setMinimumDate(QDateTime.currentDateTime().date())

        self._sched_hour = QSpinBox()
        self._sched_hour.setObjectName("scheduleHour")
        self._sched_hour.setRange(0, 23)
        self._sched_hour.setValue(_now30.time().hour())
        self._sched_hour.setWrapping(True)
        self._sched_hour.setFixedWidth(42)
        self._sched_hour.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._sched_min = QSpinBox()
        self._sched_min.setObjectName("scheduleMin")
        self._sched_min.setRange(0, 59)
        self._sched_min.setValue(_now30.time().minute())
        self._sched_min.setWrapping(True)
        self._sched_min.setFixedWidth(42)
        self._sched_min.setAlignment(Qt.AlignmentFlag.AlignCenter)

        _sep = QLabel(":")
        _sep.setObjectName("scheduleTimeSep")

        self._sched_widget = QFrame()
        self._sched_widget.setObjectName("scheduleRow")
        _sw = QHBoxLayout(self._sched_widget)
        _sw.setContentsMargins(0, 0, 0, 0)
        _sw.setSpacing(4)
        _sw.addWidget(self._sched_date, 1)
        _sw.addWidget(self._sched_hour)
        _sw.addWidget(_sep)
        _sw.addWidget(self._sched_min)
        self._sched_widget.hide()

        sched_fl.addWidget(self._chk_schedule)
        sched_fl.addWidget(self._sched_widget, 1)
        buttons_row.addWidget(sched_frame, 1, 0, 1, 2)

        # Row 2: подсказка про режим отложенной публикации (скрыта пока "Отложить" не выбрано)
        self._sched_hint_lbl = QLabel()
        self._sched_hint_lbl.setObjectName("schedHintLbl")
        self._sched_hint_lbl.setWordWrap(True)
        self._sched_hint_lbl.hide()
        buttons_row.addWidget(self._sched_hint_lbl, 2, 0, 1, 2)

        # Row 3: кнопка отправки / кнопка отмены (переключаются)
        self._cancel_button = QPushButton("✕  Отменить отправку")
        self._cancel_button.setObjectName("cancelSendBtn")
        self._cancel_button.hide()
        self._cancel_button.clicked.connect(self._cancel_send)

        send_area = QFrame()
        sa_layout = QVBoxLayout(send_area)
        sa_layout.setContentsMargins(0, 0, 0, 0)
        sa_layout.setSpacing(0)
        sa_layout.addWidget(self.send_button)
        sa_layout.addWidget(self._cancel_button)
        buttons_row.addWidget(send_area, 3, 0, 1, 2)

        left_layout.addWidget(platforms_section)

        # Прогресс-бар + кнопка отмены (скрыты в режиме ожидания)
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("sendProgress")
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()

        left_layout.addLayout(buttons_row)
        left_layout.addWidget(self._progress_bar)

        version_label = QLabel(f"Version {self._app_version}")
        version_label.setObjectName("versionLabel")
        left_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignLeft)

        right_box = QGroupBox()
        right_box.setObjectName("sidePanel")
        right_layout = QVBoxLayout(right_box)
        right_layout.setSpacing(12)
        right_layout.setContentsMargins(12, 10, 12, 12)

        preview_header_frame = QFrame()
        preview_header_frame.setObjectName("checklistFrame")
        ph_layout = QHBoxLayout(preview_header_frame)
        ph_layout.setContentsMargins(14, 10, 14, 10)

        preview_title = QLabel("Предпросмотр")
        preview_title.setObjectName("previewTitle")

        ph_layout.addWidget(preview_title)
        ph_layout.addStretch()
        right_layout.addWidget(preview_header_frame)

        self.preview = PreviewCard()
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout.addWidget(self.preview)

        # ── Чеклист готовности ──────────────────────────────────────
        checklist_frame = QFrame()
        checklist_frame.setObjectName("checklistFrame")
        cl_layout = QVBoxLayout(checklist_frame)
        cl_layout.setContentsMargins(14, 10, 14, 10)
        cl_layout.setSpacing(6)

        cl_title = QLabel("Готовность к отправке")
        cl_title.setObjectName("checklistTitle")
        cl_layout.addWidget(cl_title)

        self._cl_text     = QLabel()
        self._cl_photo    = QLabel()
        self._cl_address  = QLabel()
        self._cl_platform = QLabel()

        for lbl in (self._cl_text, self._cl_photo, self._cl_address, self._cl_platform):
            lbl.setObjectName("checklistItem")
            cl_layout.addWidget(lbl)

        right_layout.addWidget(checklist_frame)

        # подключаем чекбоксы к обновлению чеклиста и аватара предпросмотра
        self.chk_max.stateChanged.connect(self._update_checklist)
        self.chk_vk.stateChanged.connect(self._update_checklist)
        self.chk_max.stateChanged.connect(self._sync_preview_avatar)
        self.chk_vk.stateChanged.connect(self._sync_preview_avatar)
        self.chk_max.stateChanged.connect(lambda _: self._update_sched_hint())
        self.chk_vk.stateChanged.connect(lambda _: self._update_sched_hint())

        # ── История публикаций ───────────────────────────────────────
        right_layout.addWidget(self._build_history_panel())

        root.addWidget(left_box, 5)
        root.addWidget(right_box, 6)

        # Оверлей успеха — поверх всего
        self._success_overlay = SuccessOverlay(central)

        # Начальный аватар предпросмотра
        self._sync_preview_avatar()


    # ──────────────────────────────────────────────────────────────────
    #  История публикаций
    # ──────────────────────────────────────────────────────────────────

    def _build_history_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("historyFrame")
        frame.setFixedHeight(190)

        outer = QVBoxLayout(frame)
        outer.setContentsMargins(14, 10, 14, 8)
        outer.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel("История публикаций")
        title.setObjectName("checklistTitle")
        clear_btn = QPushButton("Очистить")
        clear_btn.setObjectName("histClearBtn")
        clear_btn.setFixedHeight(22)
        clear_btn.clicked.connect(self._clear_history)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(clear_btn)
        outer.addLayout(title_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._hist_container = QWidget()
        self._hist_layout = QVBoxLayout(self._hist_container)
        self._hist_layout.setContentsMargins(0, 0, 4, 0)
        self._hist_layout.setSpacing(3)
        self._hist_layout.addStretch()

        scroll.setWidget(self._hist_container)
        outer.addWidget(scroll)
        self._hist_scroll = scroll

        # Кэшируем иконки один раз
        _ico_size = QSize(16, 16)
        def _load_icon(name: str) -> "QPixmap | None":
            p = _assets_dir() / name
            if not p.exists():
                return None
            pix = QPixmap(str(p))
            return pix.scaled(_ico_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation) if not pix.isNull() else None

        self._hist_max_pix = _load_icon("max.ico")
        self._hist_vk_pix = _load_icon("vk_2.ico")

        self._refresh_history()
        return frame

    def _refresh_history(self) -> None:
        # удаляем все виджеты кроме последнего stretch
        while self._hist_layout.count() > 1:
            item = self._hist_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        entries = history_manager.load()
        if not entries:
            lbl = QLabel("Нет записей")
            lbl.setObjectName("histEmpty")
            self._hist_layout.insertWidget(0, lbl)
            return

        max_pix = self._hist_max_pix
        vk_pix = self._hist_vk_pix

        for entry in entries:
            row = self._make_history_row(entry, max_pix, vk_pix)
            self._hist_layout.insertWidget(self._hist_layout.count() - 1, row)

        # Прокручиваем к началу (новые записи — вверху)
        QTimer.singleShot(0, lambda: self._hist_scroll.verticalScrollBar().setValue(0))

    def _make_history_row(self, entry: dict, max_pix: "QPixmap | None", vk_pix: "QPixmap | None") -> QFrame:
        row = QFrame()
        status = entry.get("status", "")
        entry_id = entry.get("id", "")

        if status in ("scheduled", "scheduled_vk"):
            row.setObjectName("histEntryScheduled")
        elif status == "publishing":
            row.setObjectName("histEntryPublishing")
        elif status == "failed":
            row.setObjectName("histEntryFailed")
        else:
            row.setObjectName("histEntry")

        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Дата/время
        sched_at = entry.get("scheduled_at", "")
        ts = entry.get("ts", "").replace("  ", " ").strip()
        if status in ("scheduled", "scheduled_vk") and sched_at:
            ts_display = sched_at[:16]
        elif status == "publishing":
            ts_display = "Публикуется…"
        else:
            ts_display = ts[:16] if len(ts) > 16 else ts

        date_lbl = QLabel(ts_display)
        date_lbl.setObjectName("histDate")
        layout.addWidget(date_lbl)

        # Бейдж для отложенных постов
        if status == "scheduled_vk":
            badge = QLabel("ВК: в очереди")
            badge.setObjectName("histBadgeScheduled")
            layout.addWidget(badge)
        elif status == "scheduled":
            badge = QLabel("Отложен")
            badge.setObjectName("histBadgeScheduled")
            layout.addWidget(badge)

        # Иконки платформ
        for has, pix, fallback_text in (
            ("max" in entry, max_pix, "MAX"),
            (bool(entry.get("vk")), vk_pix, "VK"),
        ):
            if not has:
                continue
            ico_lbl = QLabel()
            if pix:
                ico_lbl.setPixmap(pix)
                ico_lbl.setFixedSize(16, 16)
            else:
                ico_lbl.setText(fallback_text)
                ico_lbl.setObjectName("histPlatformFallback")
            layout.addWidget(ico_lbl)

        # Текст публикации
        snippet = entry.get("text", "")
        if not snippet and "max" in entry:
            addrs = entry["max"]
            snippet = ", ".join(addrs[:1]) if isinstance(addrs, list) else str(addrs)
        text_lbl = QLabel(snippet or "—")
        text_lbl.setObjectName("histText")
        text_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(text_lbl)

        # Кнопка отмены только для "scheduled"
        if status == "scheduled" and entry_id:
            cancel_btn = QPushButton("✕")
            cancel_btn.setObjectName("histCancelScheduled")
            cancel_btn.setFixedSize(18, 18)
            cancel_btn.setToolTip("Отменить отложенный пост")
            cancel_btn.clicked.connect(lambda _=False, eid=entry_id: self._cancel_scheduled(eid))
            layout.addWidget(cancel_btn)
        elif status == "scheduled_vk" and entry_id:
            info_lbl = QLabel("отмена — через ВК")
            info_lbl.setObjectName("histDate")
            layout.addWidget(info_lbl)

        # Кнопка редактирования — для опубликованных ВК-постов с post_id
        vk_post_id = entry.get("vk_post_id")
        if vk_post_id and entry.get("vk") and status not in ("scheduled", "scheduled_vk", "publishing"):
            edit_btn = QPushButton("✏")
            edit_btn.setObjectName("histEditBtn")
            edit_btn.setFixedSize(22, 22)
            edit_btn.setToolTip("Редактировать пост в ВКонтакте")
            edit_btn.clicked.connect(
                lambda _=False, pid=vk_post_id, t=entry.get("text", ""): self._edit_vk_post(pid, t)
            )
            layout.addWidget(edit_btn)

        # Информация об отсутствии редактирования для MAX
        if entry.get("max") and not entry.get("vk") and status not in ("scheduled", "publishing", "failed"):
            info_btn = QPushButton("ℹ")
            info_btn.setObjectName("histInfoBtn")
            info_btn.setFixedSize(22, 22)
            info_btn.setToolTip("Редактирование MAX")
            info_btn.clicked.connect(self._show_max_edit_info)
            layout.addWidget(info_btn)

        return row

    # ──────────────────────────────────────────────────────────────────
    #  Редактирование постов
    # ──────────────────────────────────────────────────────────────────

    def _edit_vk_post(self, post_id: int, current_text: str) -> None:
        """Открывает диалог редактирования поста ВКонтакте."""
        dlg = VkEditDialog(post_id=post_id, current_text=current_text, parent=self)
        dlg.load_requested.connect(lambda: self._load_vk_post_text(dlg, post_id))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_text = dlg.new_text()
        if not new_text:
            QMessageBox.warning(self, "Редактирование", "Текст не может быть пустым.")
            return
        image_path = str(dlg.new_image_path()) if dlg.new_image_path() else None
        worker = VkEditWorker(
            vk_sender=self.vk_sender,
            post_id=post_id,
            text=new_text,
            image_path=image_path,
            parent=self,
        )
        worker.done.connect(self._on_vk_edit_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.send_button.setEnabled(False)
        self.send_button.setText("Обновление ВК…")

    def _load_vk_post_text(self, dlg: "VkEditDialog", post_id: int) -> None:
        """Загружает текст поста из ВК и вставляет в диалог."""
        worker = VkLoadTextWorker(vk_sender=self.vk_sender, post_id=post_id, parent=self)
        worker.done.connect(lambda text: dlg.set_loaded_text(text) if text else
                            QMessageBox.warning(dlg, "Ошибка", "Не удалось загрузить текст из ВКонтакте."))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_vk_edit_done(self, success: bool, message: str) -> None:
        self.send_button.setEnabled(True)
        self.send_button.setText("Опубликовать")
        if success:
            self._tray_notify("ВКонтакте: пост обновлён ✓", message)
            QMessageBox.information(self, "Готово", message)
        else:
            tg_notify.send_error("Ошибка редактирования ВК", message)
            QMessageBox.critical(self, "Ошибка", f"Не удалось обновить пост:\n\n{message}")

    def _show_max_edit_info(self) -> None:
        QMessageBox.information(
            self, "Редактирование MAX",
            "Редактирование сообщений в MAX через API не поддерживается.\n\n"
            "Чтобы исправить пост:\n"
            "1. Зайдите в группу MAX вручную\n"
            "2. Удалите старое сообщение\n"
            "3. Отправьте исправленную версию через программу",
        )

    def _clear_history(self) -> None:
        history_manager.clear()
        self._refresh_history()

    # ──────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet(get_stylesheet())

    def _update_checklist(self) -> None:
        def row(ok: bool, label: str) -> str:
            if ok:
                return f'<span style="color:#22a35a;">&#10003;</span>  {label}'
            return f'<span style="color:#c0c8d4;">&#9679;</span>  <span style="color:#aab0bb;">{label}</span>'

        has_text    = bool(self.text_input.toPlainText().strip())
        has_photo   = self.image_path is not None
        checked     = self._get_checked_matches()
        has_address = len(checked) > 0
        has_platform = self.chk_max.isChecked() or self.chk_vk.isChecked()

        # Счётчик выбранных адресов в заголовке
        n = len(checked)
        if n > 0:
            self._addr_count_lbl.setText(f"Адреса для рассылки MAX  ({n})")
        else:
            self._addr_count_lbl.setText("Адреса для рассылки MAX")

        # Поиск по Excel — показываем если файл адресов доступен
        if self._matcher is None and self.excel_path.exists():
            self._matcher = ExcelMatcher(self.excel_path)
        if self._matcher is not None:
            self._addr_search.show()

        self._cl_text.setText(row(has_text, "Текст введён"))
        if has_photo:
            self._cl_photo.setText(
                '<span style="color:#22a35a;">&#10003;</span>  Фото загружено'
            )
        else:
            self._cl_photo.setText(
                '<span style="color:#c0c8d4;">&#9675;</span>'
                '  <span style="color:#aab0bb;">Без фото (опционально)</span>'
            )
        self._cl_address.setText(row(has_address, "Адрес найден"))
        self._cl_platform.setText(row(has_platform, "Платформа выбрана"))

    def sync_preview(self) -> None:
        text = self.text_input.toPlainText()
        self.preview.set_preview_text(text)
        count = len(text)
        self._char_counter.setText(f"{count}/{TEXT_CHAR_LIMIT}")
        if count > TEXT_CHAR_LIMIT:
            self._char_counter.setStyleSheet("color: #cc0000; font-weight: 700;")
        elif count > 3500:
            self._char_counter.setStyleSheet("color: #e07800; font-weight: 600;")
        else:
            self._char_counter.setStyleSheet("color: #888;")
        self._update_checklist()
        self.save_state()
        self._parse_timer.start()

    def _toggle_emoji_picker(self) -> None:
        if self._emoji_picker is None:
            self._emoji_picker = EmojiPicker(self)
            self._emoji_picker.emoji_selected.connect(self._insert_emoji)
        if self._emoji_picker.isVisible():
            self._emoji_picker.hide()
        else:
            self._emoji_picker.show_near(self._emoji_btn)

    def _insert_emoji(self, emoji: str) -> None:
        cursor = self.text_input.textCursor()
        cursor.insertText(emoji)
        self.text_input.setTextCursor(cursor)
        self.text_input.setFocus()

    def select_image(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Выберите изображение", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not file_name:
            return
        self.image_path = Path(file_name)
        self.preview.set_image(str(self.image_path))
        self._set_photo_button_name(self.image_path.name)
        self._update_checklist()
        self.save_state()

    def _set_photo_button_name(self, name: str) -> None:
        short = name if len(name) <= 22 else name[:19] + "…"
        self.photo_button.setText(f"✓  {short}")
        self.photo_button.setObjectName("photoButtonDone")
        self.photo_button.setStyle(self.photo_button.style())

    def _on_addr_search_changed(self, text: str) -> None:
        """Запускает debounce-таймер при изменении текста в поиске."""
        self._addr_search_timer.start(300)
        if not text.strip():
            self._addr_search_results.hide()
            self._addr_search_results.clear()

    def _do_addr_search(self) -> None:
        """Ищет адреса в Excel и показывает результаты под полем поиска."""
        q = self._addr_search.text().strip()
        self._addr_search_results.clear()
        if not q or len(q) < 2:
            self._addr_search_results.hide()
            return

        if self._matcher is None:
            if self.excel_path.exists():
                self._matcher = ExcelMatcher(self.excel_path)
            else:
                return

        results = self._matcher.search(q)
        if not results:
            self._addr_search_results.hide()
            return

        for match in results:
            item = QListWidgetItem(match.address)
            item.setData(Qt.ItemDataRole.UserRole, match)
            self._addr_search_results.addItem(item)
        self._addr_search_results.show()

    def _on_addr_search_item_clicked(self, item: QListWidgetItem) -> None:
        """Добавляет выбранный из поиска адрес в список рассылки."""
        match: MatchResult | None = item.data(Qt.ItemDataRole.UserRole)
        if not match:
            return

        # Проверяем, нет ли уже такого адреса/chat_id в списке
        for i in range(self._addr_list.count()):
            existing = self._addr_list.item(i)
            if not existing:
                continue
            ex_m = existing.data(Qt.ItemDataRole.UserRole)
            if ex_m and (ex_m.address == match.address or
                         (match.chat_id and ex_m.chat_id == match.chat_id)):
                self._addr_search.clear()
                self._addr_search_results.hide()
                return

        new_item = QListWidgetItem(match.address)
        new_item.setFlags(new_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        new_item.setCheckState(Qt.CheckState.Checked)
        new_item.setData(Qt.ItemDataRole.UserRole, match)
        new_item.setData(_MANUAL_ROLE, True)
        self._addr_list.addItem(new_item)
        self._update_checklist()
        self.save_state()

        self._addr_search.clear()
        self._addr_search_results.hide()

    def clear_form(self) -> None:
        self.text_input.clear()
        self._addr_search.clear()
        self._addr_list.clear()
        self._insert_pinned_group(checked=True)
        self.preview.set_preview_text("")
        self.preview.set_image(None)
        self.image_path = None
        self.photo_button.setText("Загрузить фото")
        self.photo_button.setObjectName("photoButton")
        self.photo_button.setStyle(self.photo_button.style())
        self._update_checklist()
        self.save_state()

    def check_post(self) -> None:
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Проверка", "Введите текст публикации.")
            return

        if not self.excel_path.exists():
            QMessageBox.critical(self, "Ошибка", f"Файл не найден: {self.excel_path}")
            return

        parsed_list = extract_all_addresses(text)
        if not parsed_list:
            QMessageBox.warning(self, "Проверка", "Не удалось извлечь адреса из текста.")
            return

        if self._matcher is None:
            self._matcher = ExcelMatcher(self.excel_path)
        matcher = self._matcher

        pinned_checked = self._get_pinned_state()
        self._addr_list.blockSignals(True)
        seen_ids: set[str] = set()
        found = 0
        try:
            self._addr_list.clear()
            for parsed in parsed_list:
                try:
                    matches = matcher.find_matches(parsed)
                except Exception as exc:
                    _log.warning("find_matches failed for %r: %s", parsed, exc)
                    continue
                if not matches:
                    continue
                best = matches[0]
                if best.chat_id and best.chat_id in seen_ids:
                    continue
                if best.chat_id:
                    seen_ids.add(best.chat_id)
                item = QListWidgetItem(best.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                item.setData(Qt.ItemDataRole.UserRole, best)
                self._addr_list.addItem(item)
                found += 1
        finally:
            self._addr_list.blockSignals(False)
        self._insert_pinned_group(pinned_checked)
        self._update_checklist()
        self.save_state()

        if found == 0:
            QMessageBox.warning(self, "Проверка", "Адреса из текста не найдены в Excel.")

    def _get_checked_matches(self) -> list[MatchResult]:
        results = []
        for i in range(self._addr_list.count()):
            item = self._addr_list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                match = item.data(Qt.ItemDataRole.UserRole)
                if match:
                    results.append(match)
        return results

    def _open_font_picker(self) -> None:
        prev_family = self._ui_font_family
        prev_size   = self._ui_font_size
        families = self._ui_font_families or ["Sans Serif"]
        dlg = FontPickerDialog(
            families, prev_family, prev_size, parent=self
        )
        dlg.font_changed.connect(self._apply_ui_font)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.reset_requested():
                self._apply_ui_font("", 13)
            else:
                self._apply_ui_font(dlg.selected_family(), dlg.selected_size())
            self.save_state()
        else:
            self._apply_ui_font(prev_family, prev_size)  # откат к сохранённому

    def _apply_ui_font(self, family: str, size: int) -> None:
        """Применить шрифт ко всему интерфейсу. family='' — сброс на системный."""
        self._ui_font_family = family
        self._ui_font_size = size if size > 0 else 13
        app = QApplication.instance()
        if app:
            app.setFont(QFont(family, self._ui_font_size) if family else QFont())
        self._apply_styles()   # перегенерировать stylesheet с новым font-family

    def _open_theme_picker(self) -> None:
        prev_index = self._bg_index
        prev_mode = self._bg_mode
        prev_opacity = self._bg_opacity
        dlg = ThemePickerDialog(
            _assets_dir(), self._bg_index, self._bg_mode, self._bg_opacity, parent=self
        )
        dlg.preview_changed.connect(lambda idx, m, o: self._apply_theme(idx, m, o, save=False))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_theme(dlg.selected_index(), dlg.selected_mode(), dlg.selected_opacity())
        else:
            self._apply_theme(prev_index, prev_mode, prev_opacity, save=False)

    def _apply_theme(self, index: "int | None", mode: int = 0, opacity_pct: int = 50, save: bool = True) -> None:
        self._bg_index = index
        self._bg_mode = mode
        self._bg_opacity = opacity_pct
        if self._bg_widget is not None:
            if index is None:
                self._bg_widget.set_background(None)
            else:
                assets = _assets_dir()
                path = next(
                    (assets / f"fon_{index}{ext}" for ext in (".jpg", ".png")
                     if (assets / f"fon_{index}{ext}").exists()),
                    assets / f"fon_{index}.jpg",
                )
                pix = QPixmap(str(path)) if path.exists() else QPixmap()
                self._bg_widget.set_background(pix, mode, opacity_pct)
        if save:
            self.save_state()

    # ──────────────────────────────────────────────────────────────────
    #  Аватар предпросмотра
    # ──────────────────────────────────────────────────────────────────

    def _sync_preview_avatar(self, _state=None) -> None:
        """Обновить шапку платформы в зависимости от выбранной платформы."""
        if self.chk_vk.isChecked() and not self.chk_max.isChecked():
            self.preview.set_platform_avatar("vk", _assets_dir())
        else:
            self.preview.set_platform_avatar("max", _assets_dir())

    # ── Закреплённая основная группа МАХ ──────────────────────────────────

    def _insert_pinned_group(self, checked: bool = True) -> None:
        """Вставляет закреплённую основную группу МАХ в начало списка адресов."""
        chat_id = os.getenv("MAX_MAIN_GROUP_ID", "-68787567064560")
        if not chat_id:
            return
        name     = os.getenv("MAX_MAIN_GROUP_NAME", "ЖКС №2 Выборгского")
        link     = os.getenv("MAX_MAIN_GROUP_LINK", "https://max.ru/gks2vyb")
        match    = MatchResult(address=name, score=0, chat_link=link, chat_id=chat_id)
        item     = QListWidgetItem(f"📌  {name}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, match)
        item.setData(_PINNED_ROLE, True)
        item.setForeground(QColor("#4a6cf7"))
        self._addr_list.blockSignals(True)
        try:
            self._addr_list.insertItem(0, item)
        finally:
            self._addr_list.blockSignals(False)

    def _get_pinned_state(self) -> bool:
        """Возвращает текущее состояние галочки закреплённой группы (по умолчанию True)."""
        for i in range(self._addr_list.count()):
            item = self._addr_list.item(i)
            if item and item.data(_PINNED_ROLE):
                return item.checkState() == Qt.CheckState.Checked
        return True

    def _add_address_manually(self) -> None:
        dlg = AddAddressDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        match = dlg.result_match()
        item = QListWidgetItem(match.address)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, match)
        item.setData(_MANUAL_ROLE, True)
        self._addr_list.addItem(item)
        self._update_checklist()
        self.save_state()

    def send_post(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # отправка уже идёт — игнорируем повторное нажатие
        if self._chk_schedule.isChecked():
            self._schedule_post()
            return
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Отправка", "Нельзя отправить пустой текст.")
            return

        send_max = self.chk_max.isChecked()
        send_vk = self.chk_vk.isChecked()

        if not send_max and not send_vk:
            QMessageBox.warning(self, "Отправка", "Выбери хотя бы одну платформу (MAX или ВКонтакте).")
            return

        checked = self._get_checked_matches()
        chat_ids = list(dict.fromkeys(m.chat_id for m in checked if m.chat_id))

        if send_max and not chat_ids:
            QMessageBox.warning(self, "Отправка", "Нет отмеченных адресов. Нажми «Проверить адрес».")
            return

        # Проверяем наличие токенов
        if send_max and not (os.getenv("MAX_ID_INSTANCE") and os.getenv("MAX_API_TOKEN")):
            QMessageBox.warning(
                self, "Отправка",
                "Не заданы токены MAX (ID инстанса / API токен).\n"
                "Откройте Настройки подключений (🔑) и заполните данные."
            )
            return
        if send_vk and not os.getenv("VK_GROUP_TOKEN"):
            QMessageBox.warning(
                self, "Отправка",
                "Не задан токен ВКонтакте (VK_GROUP_TOKEN).\n"
                "Откройте Настройки подключений (🔑) и заполните данные."
            )
            return

        # Подтверждение при массовой рассылке (> 5 групп)
        if send_max and len(chat_ids) > 5:
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Подтверждение отправки")
            dlg.setText(
                f"Публикация будет отправлена в <b>{len(chat_ids)}</b> групп MAX."
                f"<br><br>Продолжить?"
            )
            dlg.setIcon(QMessageBox.Icon.Question)
            btn_yes = dlg.addButton("Отправить", QMessageBox.ButtonRole.AcceptRole)
            dlg.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            dlg.exec()
            if dlg.clickedButton() != btn_yes:
                return

        self.send_button.hide()
        self.check_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        self._cancel_button.setText("✕  Отменить отправку")
        self._cancel_button.show()
        self._progress_bar.show()

        self._pending_history = {
            "addresses": [m.address for m in checked],
            "send_max": send_max,
            "send_vk": send_vk,
            "text": text,
        }

        self._worker = SendWorker(
            max_sender=self.max_sender,
            vk_sender=self.vk_sender,
            chat_ids=chat_ids,
            text=text,
            image_path=str(self.image_path) if self.image_path else None,
            send_max=send_max,
            send_vk=send_vk,
        )
        self._worker.progress.connect(self._on_send_progress)
        self._worker.progress_step.connect(self._on_send_step)
        self._worker.result_ready.connect(self._on_send_finished)
        self._progress_bar.setRange(0, len(chat_ids) if send_max else 0)
        self._progress_bar.setValue(0)
        self._worker.start()

    def _on_send_progress(self, step: str) -> None:
        self.send_button.setText(step)
        self.setWindowTitle(f"MAX POST — {step}")

    def _on_send_step(self, current: int, total: int) -> None:
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)

    def _cancel_send(self) -> None:
        """Запрашивает отмену текущей рассылки."""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._cancel_button.setEnabled(False)
            self._cancel_button.setText("✕  Отменяется…")
            self.setWindowTitle("MAX POST — Отменяется…")

    def _on_send_finished(self, success: bool, message: str) -> None:
        self._cancel_button.hide()
        self.send_button.show()
        self.send_button.setEnabled(True)
        self.send_button.setText("Опубликовать")
        self.check_button.setEnabled(True)
        self._progress_bar.hide()
        self.setWindowTitle("MAX POST")
        vk_post_id = getattr(self._worker, "vk_post_id", None) if self._worker else None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if success:
            self._success_overlay.show_success()
            h = self._pending_history
            try:
                history_manager.add_entry(
                    addresses=h.get("addresses", []),
                    sent_max=h.get("send_max", False),
                    sent_vk=h.get("send_vk", False),
                    text=h.get("text", ""),
                    vk_post_id=vk_post_id,
                )
                self._refresh_history()
            except Exception as exc:
                _log.warning("Не удалось сохранить историю: %s", exc)
            tg_notify.send_post_done(
                addresses=h.get("addresses", []),
                send_max=h.get("send_max", False),
                send_vk=h.get("send_vk", False),
                text=h.get("text", ""),
            )
        else:
            tg_notify.send_error("Ошибка отправки поста", message)
            SendResultDialog(message, self).exec()

    # ──────────────────────────────────────────────────────────────────
    #  Отложенные посты
    # ──────────────────────────────────────────────────────────────────

    def _on_schedule_toggled(self, checked: bool) -> None:
        if checked:
            _now30 = QDateTime.currentDateTime().addSecs(30 * 60)
            self._sched_date.setMinimumDate(QDateTime.currentDateTime().date())
            sel_dt = self._get_sched_datetime()
            if sel_dt <= QDateTime.currentDateTime():
                self._sched_date.setDate(_now30.date())
                self._sched_hour.setValue(_now30.time().hour())
                self._sched_min.setValue(_now30.time().minute())
            self._sched_widget.show()
            self.send_button.setText("Запланировать")
            self._update_sched_hint()
            self._sched_hint_lbl.show()
        else:
            self._sched_widget.hide()
            self._sched_hint_lbl.hide()
            self.send_button.setText("Опубликовать")

    def _update_sched_hint(self) -> None:
        """Обновляет подсказку под строкой расписания в зависимости от выбранных платформ."""
        if not self._chk_schedule.isChecked():
            return
        vk = self.chk_vk.isChecked()
        mx = self.chk_max.isChecked()
        if vk and mx:
            self._sched_hint_lbl.setText(
                "ВК: пост зарегистрирован в очереди ВКонтакте — компьютер можно выключить.\n"
                "MAX: таймер работает только пока программа запущена (держите в трее)."
            )
        elif vk:
            self._sched_hint_lbl.setText(
                "Пост будет зарегистрирован в очереди ВКонтакте.\n"
                "Компьютер можно выключить — ВК опубликует сам."
            )
        elif mx:
            self._sched_hint_lbl.setText(
                "MAX: таймер работает только пока программа запущена.\n"
                "Сверните в трей — не выключайте компьютер до времени отправки."
            )

    def _get_sched_datetime(self) -> QDateTime:
        d = self._sched_date.date()
        t = QTime(self._sched_hour.value(), self._sched_min.value())
        return QDateTime(d, t)

    def _schedule_post(self) -> None:
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Отправка", "Нельзя отправить пустой текст.")
            return

        send_max = self.chk_max.isChecked()
        send_vk = self.chk_vk.isChecked()
        if not send_max and not send_vk:
            QMessageBox.warning(self, "Отправка", "Выбери хотя бы одну платформу (MAX или ВКонтакте).")
            return

        checked = self._get_checked_matches()
        chat_ids = list(dict.fromkeys(m.chat_id for m in checked if m.chat_id))
        if send_max and not chat_ids:
            QMessageBox.warning(self, "Отправка", "Нет отмеченных адресов. Нажми «Проверить адрес».")
            return

        if send_max and not (os.getenv("MAX_ID_INSTANCE") and os.getenv("MAX_API_TOKEN")):
            QMessageBox.warning(self, "Отправка",
                "Не заданы токены MAX.\nОткройте Настройки подключений (🔑).")
            return
        if send_vk and not os.getenv("VK_GROUP_TOKEN"):
            QMessageBox.warning(self, "Отправка",
                "Не задан токен ВКонтакте.\nОткройте Настройки подключений (🔑).")
            return

        sched_dt = self._get_sched_datetime()
        now = QDateTime.currentDateTime()
        ms = now.msecsTo(sched_dt)
        if ms <= 0:
            QMessageBox.warning(self, "Отложенный пост", "Выберите время в будущем.")
            return

        entry_id = uuid.uuid4().hex[:8]
        sched_str = sched_dt.toString("dd.MM.yyyy  HH:mm")
        addresses = [m.address for m in checked]
        unix_ts = sched_dt.toSecsSinceEpoch()

        try:
            history_manager.add_scheduled_entry(
                entry_id=entry_id,
                addresses=addresses,
                sent_max=send_max,
                sent_vk=send_vk,
                text=text,
                scheduled_at=sched_str,
            )
            self._refresh_history()
        except Exception as exc:
            _log.warning("Не удалось сохранить отложенную запись: %s", exc)

        # ── ВК: отправляем API-запрос сразу, ВК сам опубликует в нужное время ──────
        if send_vk:
            image_path_str = str(self.image_path) if self.image_path else None
            vk_worker = VkScheduleWorker(
                vk_sender=self.vk_sender,
                text=text,
                image_path=image_path_str,
                publish_date=unix_ts,
                parent=self,
            )
            vk_worker.done.connect(
                lambda ok, msg, eid=entry_id, s=sched_str: self._on_vk_schedule_done(ok, msg, eid, s)
            )
            vk_worker.finished.connect(vk_worker.deleteLater)
            vk_worker.start()
            self.send_button.setEnabled(False)
            self.send_button.setText("Регистрация в ВК…")

        # ── MAX: локальный таймер, работает только пока программа запущена ──────────
        if send_max:
            sched_data = {
                "entry_id": entry_id,
                "scheduled_at_iso": sched_dt.toString(Qt.DateFormat.ISODate),
                "addresses": addresses,
                "chat_ids": chat_ids,
                "send_max": True,
                "send_vk": False,   # VK уже обработан выше
                "text": text,
                "image_path": str(self.image_path) if self.image_path else None,
            }
            self._save_scheduled_to_disk(sched_data)
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda eid=entry_id: self._fire_scheduled(eid))
            timer.start(int(ms))
            self._scheduled_posts[entry_id] = {"timer": timer, "data": sched_data}

        if not send_vk:
            # VK не выбран — сразу показываем подтверждение для MAX
            QMessageBox.information(
                self, "Запланировано",
                f"MAX-пост будет опубликован\n{sched_dt.toString('dd.MM.yyyy  в  HH:mm')}\n\n"
                "Держите программу запущенной (трей).",
            )
            self._chk_schedule.setChecked(False)
        # Если VK выбран — подтверждение покажет _on_vk_schedule_done

    def _on_vk_schedule_done(self, success: bool, message: str, entry_id: str, sched_str: str) -> None:
        """Вызывается когда VkScheduleWorker завершил регистрацию поста в ВКонтакте."""
        self.send_button.setEnabled(True)
        self.send_button.setText("Запланировать" if self._chk_schedule.isChecked() else "Опубликовать")

        if success:
            try:
                history_manager.update_entry_status(entry_id, "scheduled_vk")
                self._refresh_history()
            except Exception as exc:
                _log.warning("Ошибка обновления статуса VK scheduled: %s", exc)
            self._tray_notify(
                "ВКонтакте: пост запланирован",
                f"Пост зарегистрирован в очереди ВК на {sched_str}.\n"
                "Компьютер можно выключить.",
            )
            QMessageBox.information(
                self, "Запланировано в ВКонтакте",
                f"Пост зарегистрирован в очереди ВКонтакте\nна {sched_str}.\n\n"
                "ВКонтакте опубликует его сам — компьютер можно выключить.",
            )
            self._chk_schedule.setChecked(False)
        else:
            tg_notify.send_error("Ошибка планирования ВК", message)
            QMessageBox.critical(
                self, "Ошибка планирования",
                f"Не удалось зарегистрировать пост в ВКонтакте:\n\n{message}",
            )

    def _fire_scheduled(self, entry_id: str) -> None:
        scheduled = self._scheduled_posts.pop(entry_id, None)
        if not scheduled:
            return
        data = scheduled["data"]

        try:
            history_manager.update_entry_status(entry_id, "publishing")
            self._refresh_history()
        except Exception as exc:
            _log.warning("Ошибка обновления статуса: %s", exc)

        self._remove_scheduled_from_disk(entry_id)

        worker = SendWorker(
            max_sender=self.max_sender,
            vk_sender=self.vk_sender,
            chat_ids=data["chat_ids"],
            text=data["text"],
            image_path=data.get("image_path"),
            send_max=data["send_max"],
            send_vk=data["send_vk"],
        )
        worker.result_ready.connect(
            lambda ok, msg, eid=entry_id, d=data: self._on_scheduled_finished(eid, d, ok, msg)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()
        # Сохраняем ссылку, чтобы GC не удалил поток
        self._scheduled_posts[f"_running_{entry_id}"] = {"timer": None, "worker": worker, "data": data}

    def _on_scheduled_finished(self, entry_id: str, data: dict, success: bool, message: str) -> None:
        running_key = f"_running_{entry_id}"
        info = self._scheduled_posts.pop(running_key, None)
        if info and info.get("worker"):
            info["worker"].deleteLater()

        new_status = "done" if success else "failed"
        try:
            history_manager.update_entry_status(entry_id, new_status)
            self._refresh_history()
        except Exception as exc:
            _log.warning("Ошибка обновления статуса: %s", exc)

        if success:
            if self.isVisible():
                self._success_overlay.show_success()
            else:
                self._tray_notify(
                    "Отложенный пост отправлен ✓",
                    data.get("text", "")[:80] or "Публикация успешно отправлена.",
                )
        else:
            tg_notify.send_error("Ошибка отложенной отправки", message)
            if self.isVisible():
                SendResultDialog(message, self).exec()
            else:
                self._tray_notify(
                    "Ошибка отправки",
                    message[:120],
                    QSystemTrayIcon.MessageIcon.Critical,
                )

    def _cancel_scheduled(self, entry_id: str) -> None:
        reply = QMessageBox.question(
            self, "Отмена поста",
            "Отменить этот отложенный пост?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        scheduled = self._scheduled_posts.pop(entry_id, None)
        if scheduled and scheduled.get("timer"):
            scheduled["timer"].stop()
        self._remove_scheduled_from_disk(entry_id)
        try:
            history_manager.update_entry_status(entry_id, "cancelled")
            self._refresh_history()
        except Exception as exc:
            _log.warning("Ошибка отмены: %s", exc)

    def _save_scheduled_to_disk(self, data: dict) -> None:
        path = history_manager._data_dir() / "scheduled.json"
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            items = []
        items.append(data)
        try:
            history_manager._atomic_write(path, json.dumps(items, ensure_ascii=False, indent=2))
        except Exception as exc:
            _log.warning("Ошибка сохранения scheduled.json: %s", exc)

    def _remove_scheduled_from_disk(self, entry_id: str) -> None:
        path = history_manager._data_dir() / "scheduled.json"
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            items = [i for i in items if i.get("entry_id") != entry_id]
            history_manager._atomic_write(path, json.dumps(items, ensure_ascii=False, indent=2))
        except Exception as exc:
            _log.warning("Ошибка удаления из scheduled.json: %s", exc)

    def _load_scheduled_from_disk(self) -> None:
        path = history_manager._data_dir() / "scheduled.json"
        if not path.exists():
            return
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        now = QDateTime.currentDateTime()
        overdue: list[dict] = []
        for item in items:
            try:
                sched_dt = QDateTime.fromString(item["scheduled_at_iso"], Qt.DateFormat.ISODate)
                ms = now.msecsTo(sched_dt)
                entry_id = item["entry_id"]
                if ms <= 0:
                    overdue.append(item)
                    continue
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda eid=entry_id: self._fire_scheduled(eid))
                timer.start(int(ms))
                self._scheduled_posts[entry_id] = {"timer": timer, "data": item}
                _log.info("Восстановлен отложенный пост %s, запуск через %d мс", entry_id, ms)
            except Exception as exc:
                _log.warning("Ошибка загрузки отложенного поста: %s", exc)

        if overdue:
            # Просроченные посты отправляем автоматически — диалог не нужен,
            # т.к. программа обычно работает в трее и посты должны уходить без участия пользователя.
            n = len(overdue)
            label = "пост" if n == 1 else ("поста" if n < 5 else "постов")
            _log.info("Найдено %d просроченных отложенных постов — отправляем автоматически", n)
            for delay_idx, item in enumerate(overdue):
                entry_id = item["entry_id"]
                timer = QTimer(self)
                timer.setSingleShot(True)
                # Разносим запуски по 2 сек, чтобы не запустить все одновременно
                timer.timeout.connect(lambda eid=entry_id: self._fire_scheduled(eid))
                timer.start(1000 + delay_idx * 2000)
                self._scheduled_posts[entry_id] = {"timer": timer, "data": item}
                _log.info("Просроченный пост %s: будет отправлен через %d сек", entry_id, 1 + delay_idx * 2)
            self._tray_notify(
                "Отправка просроченных постов",
                f"Найдено {n} отложенных {label}, пропущенных во время паузы.\n"
                "Отправляем автоматически.",
            )

    def _auto_check_addresses(self) -> None:
        """Тихий автопарсинг адресов при изменении текста (без диалогов)."""
        text = self.text_input.toPlainText().strip()

        def _clear_auto_items() -> None:
            """Удаляет из списка все автоматически найденные адреса."""
            pinned_checked = self._get_pinned_state()
            manual_entries = []
            for i in range(self._addr_list.count()):
                itm = self._addr_list.item(i)
                if not itm:
                    continue
                if itm.data(_PINNED_ROLE):
                    continue  # обрабатываем отдельно
                if itm.data(_MANUAL_ROLE):
                    m = itm.data(Qt.ItemDataRole.UserRole)
                    manual_entries.append((m, itm.checkState()))
            if manual_entries or self._addr_list.count() > 0:
                self._addr_list.blockSignals(True)
                try:
                    self._addr_list.clear()
                    for m, state in manual_entries:
                        item = QListWidgetItem(m.address)
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(state)
                        item.setData(Qt.ItemDataRole.UserRole, m)
                        item.setData(_MANUAL_ROLE, True)
                        self._addr_list.addItem(item)
                finally:
                    self._addr_list.blockSignals(False)
                self._insert_pinned_group(pinned_checked)
                self._update_checklist()
                self.save_state()

        if not text or not self.excel_path.exists():
            _clear_auto_items()
            return
        parsed_list = extract_all_addresses(text)
        if not parsed_list:
            _clear_auto_items()
            return
        if self._matcher is None:
            self._matcher = ExcelMatcher(self.excel_path)
        matcher = self._matcher

        new_items: list[MatchResult] = []
        seen_ids: set[str] = set()
        for parsed in parsed_list:
            try:
                matches = matcher.find_matches(parsed)
            except Exception as exc:
                _log.warning("find_matches failed for %r: %s", parsed, exc)
                continue
            if not matches:
                continue
            best = matches[0]
            if best.chat_id and best.chat_id in seen_ids:
                continue
            if best.chat_id:
                seen_ids.add(best.chat_id)
            new_items.append(best)

        if not new_items:
            _clear_auto_items()
            return

        # Собираем вручную добавленные адреса — они НЕ перезаписываются автопарсингом
        pinned_checked = self._get_pinned_state()
        manual_entries: list[tuple[MatchResult, Qt.CheckState]] = []
        checked_ids: set[str] = set()
        existing_auto_ids: set[str] = set()
        for i in range(self._addr_list.count()):
            itm = self._addr_list.item(i)
            if not itm:
                continue
            m = itm.data(Qt.ItemDataRole.UserRole)
            if not m:
                continue
            if itm.data(_PINNED_ROLE):
                continue  # pinned обрабатываем отдельно
            if itm.data(_MANUAL_ROLE):
                manual_entries.append((m, itm.checkState()))
            else:
                existing_auto_ids.add(m.chat_id)
            if itm.checkState() == Qt.CheckState.Checked:
                checked_ids.add(m.chat_id)

        # Не перерисовываем если автоадреса не изменились
        new_ids = {b.chat_id for b in new_items}
        if new_ids == existing_auto_ids:
            return

        self._addr_list.blockSignals(True)
        try:
            self._addr_list.clear()
            for best in new_items:
                item = QListWidgetItem(best.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                state = (Qt.CheckState.Unchecked
                         if (checked_ids and best.chat_id not in checked_ids)
                         else Qt.CheckState.Checked)
                item.setCheckState(state)
                item.setData(Qt.ItemDataRole.UserRole, best)
                self._addr_list.addItem(item)
            # Возвращаем ручные адреса (если их нет среди автопарсинга)
            for m, state in manual_entries:
                if m.chat_id in new_ids:
                    continue  # уже есть в авто-результатах — не дублируем
                item = QListWidgetItem(m.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(state)
                item.setData(Qt.ItemDataRole.UserRole, m)
                item.setData(_MANUAL_ROLE, True)
                self._addr_list.addItem(item)
        finally:
            self._addr_list.blockSignals(False)
        self._insert_pinned_group(pinned_checked)
        self._update_checklist()
        self.save_state()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            if any(
                u.toLocalFile().lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                self._text_container.setStyleSheet(
                    "QFrame#textContainer { border: 2px solid #2d6cdf; border-radius: 8px; background: #eef3ff; }"
                )
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._text_container.setStyleSheet("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._text_container.setStyleSheet("")
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                self.image_path = Path(path)
                self.preview.set_image(str(self.image_path))
                self._set_photo_button_name(self.image_path.name)
                self._update_checklist()
                self.save_state()
                break

    def reload_senders(self) -> None:
        """Пересоздаёт sender-объекты после обновления токенов в .env."""
        self.max_sender = MaxSender()
        self.vk_sender = VkSender()
        # Обновляем закреплённую группу, если её настройки изменились
        pinned_checked = self._get_pinned_state()
        self._addr_list.blockSignals(True)
        try:
            for i in range(self._addr_list.count()):
                item = self._addr_list.item(i)
                if item and item.data(_PINNED_ROLE):
                    self._addr_list.takeItem(i)
                    break
        finally:
            self._addr_list.blockSignals(False)
        self._insert_pinned_group(pinned_checked)

    def _reload_excel(self) -> None:
        """Перезагружает Excel-реестр адресов с диска."""
        self._matcher = None
        if self.excel_path.exists():
            self._matcher = ExcelMatcher(self.excel_path)
            QMessageBox.information(
                self, "Реестр обновлён",
                f"Файл {self.excel_path.name} перезагружен."
            )
        else:
            QMessageBox.warning(
                self, "Файл не найден",
                f"Файл {self.excel_path.name} не найден.\nПоложите его рядом с программой."
            )

    def save_state(self) -> None:
        """Запускает таймер — реальная запись через 400мс после последнего вызова."""
        self._save_timer.start()

    def _do_save_state(self) -> None:
        try:
            _ = self._addr_list.count()  # проверяем, живы ли C++ объекты
        except RuntimeError:
            return  # Qt уже уничтожил объекты (вызов через atexit при завершении)
        checked_ids = {m.chat_id for m in self._get_checked_matches()}
        pinned_checked = self._get_pinned_state()
        addresses = []
        for i in range(self._addr_list.count()):
            item = self._addr_list.item(i)
            if not item:
                continue
            if item.data(_PINNED_ROLE):
                continue  # pinned сохраняется отдельно в pinned_checked
            m = item.data(Qt.ItemDataRole.UserRole)
            if m:
                addresses.append({
                    "address": m.address,
                    "chat_id": m.chat_id,
                    "chat_link": m.chat_link,
                    "manual": bool(item.data(_MANUAL_ROLE)),
                })
        self.state_manager.save({
            "image_path": str(self.image_path) if self.image_path else "",
            "text": self.text_input.toPlainText(),
            "width": self.width(),
            "height": self.height(),
            "bg_index": self._bg_index,
            "bg_mode": self._bg_mode,
            "bg_opacity": self._bg_opacity,
            "addresses": addresses,
            "checked_ids": list(checked_ids),
            "pinned_checked": pinned_checked,
            "ui_font_family": self._ui_font_family,
            "ui_font_size": self._ui_font_size,
        })

    def load_state(self) -> None:
        data = self.state_manager.load()
        try:
            w = int(data.get("width", 1280) or 1280)
            h = int(data.get("height", 760) or 760)
        except (ValueError, TypeError):
            w, h = 1280, 760
        self.resize(w, h)

        # Шрифт интерфейса — применяется в _deferred_font_load после загрузки шрифтов
        self._pending_font_family = data.get("ui_font_family", "")
        try:
            self._pending_font_size = int(data.get("ui_font_size", 0) or 0)
        except (ValueError, TypeError):
            self._pending_font_size = 0

        bg_index = data.get("bg_index", None)
        bg_mode = data.get("bg_mode", 0)
        bg_opacity = data.get("bg_opacity", 50)
        if bg_index is not None:
            self._apply_theme(bg_index, bg_mode, bg_opacity)

        text = data.get("text", "")
        if text:
            self.text_input.setPlainText(text)

        image_path = data.get("image_path", "")
        if image_path and Path(image_path).exists():
            self.image_path = Path(image_path)
            self.preview.set_image(str(self.image_path))
            self._set_photo_button_name(self.image_path.name)

        pinned_checked = bool(data.get("pinned_checked", True))
        addresses = data.get("addresses", [])
        checked_ids = set(data.get("checked_ids", []))
        self._addr_list.blockSignals(True)
        try:
            self._addr_list.clear()
            for a in addresses:
                match = MatchResult(
                    address=a.get("address", ""),
                    score=0,
                    chat_link=a.get("chat_link", ""),
                    chat_id=a.get("chat_id", ""),
                )
                item = QListWidgetItem(match.address)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                state = Qt.CheckState.Checked if (not checked_ids or match.chat_id in checked_ids) else Qt.CheckState.Unchecked
                item.setCheckState(state)
                item.setData(Qt.ItemDataRole.UserRole, match)
                if a.get("manual"):
                    item.setData(_MANUAL_ROLE, True)
                self._addr_list.addItem(item)
        finally:
            self._addr_list.blockSignals(False)
        self._insert_pinned_group(pinned_checked)

        self.sync_preview()
        self.text_input.setFocus()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_success_overlay") and self._bg_widget:
            self._success_overlay.setGeometry(self._bg_widget.rect())

    # ──────────────────────────────────────────────────────────────────
    #  Системный трей
    # ──────────────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        """Создаёт иконку в системном трее."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = QIcon(str(_assets_dir() / "MAX POST.ico"))
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("MAX POST")

        menu = QMenu()
        show_action = menu.addAction("Показать окно")
        show_action.triggered.connect(self._show_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("Выход")
        quit_action.triggered.connect(self._quit_app)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        shell = getattr(self, "_shell_window", None)
        target = shell if shell is not None else self
        target.showNormal()
        target.activateWindow()
        target.raise_()

    def _quit_app(self) -> None:
        """Полное закрытие приложения (из меню трея или Файл → Выход)."""
        self._real_quit = True
        shell = getattr(self, "_shell_window", None)
        if shell is not None:
            shell.close()
        else:
            self.close()

    def _tray_notify(self, title: str, message: str,
                     icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
                     duration_ms: int = 5000) -> None:
        """Показывает balloon-уведомление из трея (только если есть трей)."""
        tray = getattr(self, "_tray", None)
        if tray and tray.isVisible():
            tray.showMessage(title, message, icon, duration_ms)

    def closeEvent(self, event) -> None:
        # Если нажали X (не «Выход») — сворачиваем в трей
        _tray = getattr(self, "_tray", None)
        if not self._real_quit and _tray is not None and _tray.isVisible():
            event.ignore()
            self.hide()
            n_sched = sum(
                1 for v in self._scheduled_posts.values()
                if v.get("timer") and v["timer"].isActive()
            )
            if n_sched:
                self._tray_notify(
                    "MAX POST свёрнут",
                    f"Программа работает в фоне.\n"
                    f"Отложенных постов: {n_sched}.",
                )
            else:
                self._tray_notify("MAX POST свёрнут", "Программа работает в фоне.")
            return

        # Полное закрытие
        self._save_timer.stop()
        self._do_save_state()

        for entry in self._scheduled_posts.values():
            t = entry.get("timer")
            if t:
                t.stop()
            w = entry.get("worker")
            if w and w.isRunning():
                w.quit()
                w.wait(2000)

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.quit()
            self._worker.wait(3000)

        conn = getattr(self, "_conn_worker", None)
        if conn and conn.isRunning():
            conn.quit()
            conn.wait(1000)

        tray = getattr(self, "_tray", None)
        if tray:
            tray.hide()

        self.max_sender.close()
        super().closeEvent(event)

    def _clear_photo(self) -> None:
        """Очищает только фото, не трогая текст и адреса."""
        self.image_path = None
        self.preview.set_image(None)
        self.photo_button.setText("Загрузить фото")
        self.photo_button.setObjectName("photoButton")
        self.photo_button.setStyle(self.photo_button.style())
        self._update_checklist()
        self.save_state()

    def _check_max_connection(self) -> None:
        """Запускает проверку соединения с GREEN-API в фоновом потоке."""
        if hasattr(self, "_conn_worker") and self._conn_worker and self._conn_worker.isRunning():
            return
        self._conn_worker = _ConnCheckWorker(self.max_sender, self)
        self._conn_worker.done.connect(self._on_conn_check_done)
        self._conn_worker.done.connect(self._conn_worker.deleteLater)
        self._conn_worker.start()

    def _on_conn_check_done(self, success: bool, message: str) -> None:
        self._conn_worker = None
        if success:
            QMessageBox.information(self, "Соединение MAX", message)
        else:
            QMessageBox.warning(self, "Соединение MAX", message)

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self, "Горячие клавиши",
            "Ctrl + Enter  —  Опубликовать\n"
            "Ctrl + L       —  Загрузить фото\n"
        )

    def show_about(self) -> None:
        QMessageBox.information(
            self, "О программе",
            "MAX POST\n\n"
            "Отправка сообщений в группы MAX через GREEN-API.\n\n"
            "Emoji provided free by Twitter (Twemoji) under CC BY 4.0\n"
            "https://creativecommons.org/licenses/by/4.0/"
        )

    def _check_integrity(self) -> None:
        """Проверяет наличие ключевых файлов программы."""
        from env_utils import get_env_path
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        results: list[str] = []

        checks = [
            (base / "version.txt",               "version.txt"),
            (_assets_dir() / "MAX POST.ico",     "Иконка приложения"),
            (get_env_path(),                      "Файл настроек .env"),
        ]
        if self.excel_path:
            checks.append((self.excel_path, f"Excel-файл ({self.excel_path.name})"))

        all_ok = True
        for path, label in checks:
            if path.exists():
                results.append(f"✅  {label}")
            else:
                results.append(f"❌  {label}  — не найден")
                all_ok = False

        status = "Все файлы на месте." if all_ok else "Обнаружены отсутствующие файлы."
        QMessageBox.information(
            self, "Проверка целостности",
            f"{status}\n\n" + "\n".join(results)
        )


def _backup_address_file() -> None:
    """При запуске сохраняет резервную копию max_address.xlsx (до 3 штук, ротация)."""
    import shutil as _shutil
    src = Path(__file__).parent / "max_address.xlsx"
    if not src.exists():
        return
    backup_dir = src.parent / "_backups"
    backup_dir.mkdir(exist_ok=True)
    dst = backup_dir / f"max_address_{time.strftime('%Y%m%d')}.xlsx"
    if not dst.exists():
        _shutil.copy2(src, dst)
        # Оставляем только последние 3 резервных копии
        backups = sorted(backup_dir.glob("max_address_*.xlsx"))
        for old in backups[:-3]:
            old.unlink(missing_ok=True)


def main() -> None:
    tg_notify.install_excepthook()
    tg_notify.send_startup()

    try:
        from PyQt6.QtWebEngineQuick import QtWebEngineQuick
        QtWebEngineQuick.initialize()
    except Exception:
        pass
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: F401 — должен быть до QApplication
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # не выходить при hide() в трей
    _backup_address_file()  # резервная копия перед стартом
    window = MainWindow()
    window.showMaximized()
    # Проверка обновлений через 2 сек после запуска (чтобы окно успело отрисоваться)
    QTimer.singleShot(UPDATE_CHECK_DELAY_MS, lambda: check_for_updates(window))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
