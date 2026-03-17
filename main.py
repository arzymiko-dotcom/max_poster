import atexit
import logging
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QRect, QSize, QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Роль для пометки вручную добавленных адресов
_MANUAL_ROLE: int = Qt.ItemDataRole.UserRole + 1

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
from ui.dialogs import ThemePickerDialog, FontPickerDialog, AddAddressDialog
from ui.styles import get_stylesheet
from constants import (
    PARSE_DEBOUNCE_MS,
    SAVE_DEBOUNCE_MS,
    UPDATE_CHECK_DELAY_MS,
    TEXT_CHAR_LIMIT,
)

_log = logging.getLogger(__name__)


class SendWorker(QThread):
    result_ready = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

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

    def cancel(self) -> None:
        """Запрашивает отмену — поток остановится после текущей отправки."""
        self._cancelled = True

    def run(self) -> None:
        lines: list[str] = []
        success = True

        if self.send_max:
            total = len(self.chat_ids)
            for i, chat_id in enumerate(self.chat_ids, 1):
                if self._cancelled:
                    lines.append(f"⛔ Отменено после {i - 1}/{total} отправок.")
                    self.result_ready.emit(False, "\n".join(lines))
                    return
                self.progress.emit(f"MAX {i}/{total}…")
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

        self.result_ready.emit(success, "\n".join(lines))


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

        self._build_menu()
        self._build_ui()
        self._apply_styles()
        self.load_state()
        # Загружаем шрифты и применяем сохранённый шрифт после показа окна
        QTimer.singleShot(0, self._deferred_font_load)

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

        clear_action = QAction("Очистить форму", self)
        clear_action.triggered.connect(self.clear_form)
        file_menu.addAction(clear_action)

        file_menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.close)
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

        text_container = QFrame()
        text_container.setObjectName("textContainer")
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

        left_layout.addWidget(text_container, 1)
        addr_header_frame = QFrame()
        addr_header_frame.setObjectName("checklistFrame")
        ah_layout = QHBoxLayout(addr_header_frame)
        ah_layout.setContentsMargins(14, 10, 14, 10)
        addr_lbl = QLabel("Адреса для рассылки MAX")
        addr_lbl.setObjectName("checklistTitle")
        self._add_addr_btn = QPushButton("+")
        self._add_addr_btn.setObjectName("addAddrBtn")
        self._add_addr_btn.setFixedSize(24, 24)
        self._add_addr_btn.setToolTip("Добавить адрес вручную")
        self._add_addr_btn.clicked.connect(self._add_address_manually)
        ah_layout.addWidget(addr_lbl)
        ah_layout.addStretch()
        ah_layout.addWidget(self._add_addr_btn)
        left_layout.addWidget(addr_header_frame)
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
        _vk_icon_path = _assets / "vk.ico"
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

        self.photo_button = QPushButton("Загрузить фото")
        self.photo_button.clicked.connect(self.select_image)

        self.send_button = QPushButton("Опубликовать")
        self.send_button.clicked.connect(self.send_post)
        self.send_button.setObjectName("primaryButton")

        # Row 0: вспомогательные кнопки
        buttons_row.addWidget(self.check_button, 0, 0)
        buttons_row.addWidget(self.photo_button, 0, 1)
        # Row 1: кнопка отправки на всю ширину
        buttons_row.addWidget(self.send_button, 1, 0, 1, 2)

        left_layout.addWidget(platforms_section)

        # Прогресс-бар + кнопка отмены (скрыты в режиме ожидания)
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("sendProgress")
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()

        self._cancel_button = QPushButton("✕  Отмена")
        self._cancel_button.setObjectName("cancelSendBtn")
        self._cancel_button.setFixedHeight(22)
        self._cancel_button.hide()
        self._cancel_button.clicked.connect(self._cancel_send)

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(8)
        progress_row.addWidget(self._progress_bar, 1)
        progress_row.addWidget(self._cancel_button)

        left_layout.addLayout(buttons_row)
        left_layout.addLayout(progress_row)

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

        assets = _assets_dir()
        _ico_size = QSize(16, 16)

        def _load_icon(name: str) -> "QPixmap | None":
            p = assets / name
            if not p.exists():
                return None
            pix = QPixmap(str(p))
            return pix.scaled(_ico_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation) if not pix.isNull() else None

        max_pix = _load_icon("max.ico")
        vk_pix = _load_icon("vk.ico")

        for entry in entries:
            row = self._make_history_row(entry, max_pix, vk_pix)
            self._hist_layout.insertWidget(self._hist_layout.count() - 1, row)

    def _make_history_row(self, entry: dict, max_pix: "QPixmap | None", vk_pix: "QPixmap | None") -> QFrame:
        row = QFrame()
        row.setObjectName("histEntry")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Дата/время
        ts = entry.get("ts", "").replace("  ", " ").strip()
        date_lbl = QLabel(ts[:16] if len(ts) > 16 else ts)
        date_lbl.setObjectName("histDate")
        layout.addWidget(date_lbl)

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

        return row

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
        has_address = len(self._get_checked_matches()) > 0
        has_platform = self.chk_max.isChecked() or self.chk_vk.isChecked()

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
        self._char_counter.setStyleSheet("color: #cc0000; font-weight: 600;" if count > TEXT_CHAR_LIMIT else "color: #888;")
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

    def clear_form(self) -> None:
        self.text_input.clear()
        self._addr_list.clear()
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

        self._addr_list.blockSignals(True)
        self._addr_list.clear()
        seen_ids: set[str] = set()
        found = 0

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

        self._addr_list.blockSignals(False)
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
        chat_ids = [m.chat_id for m in checked if m.chat_id]

        if send_max and not chat_ids:
            QMessageBox.warning(self, "Отправка", "Нет отмеченных адресов. Нажми «Проверить адрес».")
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

        self.send_button.setEnabled(False)
        self.send_button.setText("Публикуется…")
        self.check_button.setEnabled(False)
        self._progress_bar.show()
        self._cancel_button.setEnabled(True)
        self._cancel_button.setText("✕  Отмена")
        self._cancel_button.show()

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
        self._worker.result_ready.connect(self._on_send_finished)
        self._worker.start()

    def _on_send_progress(self, step: str) -> None:
        self.send_button.setText(step)

    def _cancel_send(self) -> None:
        """Запрашивает отмену текущей рассылки."""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._cancel_button.setEnabled(False)
            self._cancel_button.setText("Отменяется…")

    def _on_send_finished(self, success: bool, message: str) -> None:
        self.send_button.setEnabled(True)
        self.send_button.setText("Опубликовать")
        self.check_button.setEnabled(True)
        self._progress_bar.hide()
        self._cancel_button.hide()
        if success:
            self._success_overlay.show_success()
            h = self._pending_history
            history_manager.add_entry(
                addresses=h.get("addresses", []),
                sent_max=h.get("send_max", False),
                sent_vk=h.get("send_vk", False),
                text=h.get("text", ""),
            )
            self._refresh_history()
        else:
            tg_notify.send_error("Ошибка отправки поста", message)
            SendResultDialog(message, self).exec()

    def _auto_check_addresses(self) -> None:
        """Тихий автопарсинг адресов при изменении текста (без диалогов)."""
        text = self.text_input.toPlainText().strip()

        def _clear_auto_items() -> None:
            """Удаляет из списка все автоматически найденные адреса."""
            manual_entries = []
            for i in range(self._addr_list.count()):
                itm = self._addr_list.item(i)
                if itm and itm.data(_MANUAL_ROLE):
                    m = itm.data(Qt.ItemDataRole.UserRole)
                    manual_entries.append((m, itm.checkState()))
            if manual_entries or self._addr_list.count() > 0:
                self._addr_list.blockSignals(True)
                self._addr_list.clear()
                for m, state in manual_entries:
                    item = QListWidgetItem(m.address)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(state)
                    item.setData(Qt.ItemDataRole.UserRole, m)
                    item.setData(_MANUAL_ROLE, True)
                    self._addr_list.addItem(item)
                self._addr_list.blockSignals(False)
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
        self._addr_list.blockSignals(False)
        self._update_checklist()
        self.save_state()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            if any(
                u.toLocalFile().lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                self.image_path = Path(path)
                self.preview.set_image(str(self.image_path))
                self._set_photo_button_name(self.image_path.name)
                self._update_checklist()
                self.save_state()
                break

    def save_state(self) -> None:
        """Запускает таймер — реальная запись через 400мс после последнего вызова."""
        self._save_timer.start()

    def _do_save_state(self) -> None:
        try:
            _ = self._addr_list.count()  # проверяем, живы ли C++ объекты
        except RuntimeError:
            return  # Qt уже уничтожил объекты (вызов через atexit при завершении)
        checked_ids = {m.chat_id for m in self._get_checked_matches()}
        addresses = []
        for i in range(self._addr_list.count()):
            item = self._addr_list.item(i)
            if not item:
                continue
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
            "ui_font_family": self._ui_font_family,
            "ui_font_size": self._ui_font_size,
        })

    def load_state(self) -> None:
        data = self.state_manager.load()
        self.resize(int(data.get("width", 1280)), int(data.get("height", 760)))

        # Шрифт интерфейса — применяется в _deferred_font_load после загрузки шрифтов
        self._pending_font_family = data.get("ui_font_family", "")
        self._pending_font_size = int(data.get("ui_font_size", 0))

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

        addresses = data.get("addresses", [])
        checked_ids = set(data.get("checked_ids", []))
        if addresses:
            self._addr_list.blockSignals(True)
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
            self._addr_list.blockSignals(False)

        self.sync_preview()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_success_overlay") and self._bg_widget:
            self._success_overlay.setGeometry(self._bg_widget.rect())

    def closeEvent(self, event) -> None:
        self._save_timer.stop()
        self._do_save_state()  # сохраняем сразу, не через таймер
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
        """Проверяет соединение с GREEN-API и показывает результат."""
        result = self.max_sender.open_max_for_login()
        if result.success:
            QMessageBox.information(self, "Соединение MAX", result.message)
        else:
            QMessageBox.warning(self, "Соединение MAX", result.message)

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
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        results: list[str] = []

        checks = [
            (base / "version.txt",               "version.txt"),
            (_assets_dir() / "MAX POST.ico",     "Иконка приложения"),
            (base / ".env",                       "Файл настроек .env"),
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


def main() -> None:
    tg_notify.install_excepthook()
    tg_notify.send_startup()

    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    # Проверка обновлений через 2 сек после запуска (чтобы окно успело отрисоваться)
    QTimer.singleShot(UPDATE_CHECK_DELAY_MS, lambda: check_for_updates(window))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
