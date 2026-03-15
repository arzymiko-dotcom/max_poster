import sys
from pathlib import Path

from PyQt6.QtCore import QSize, QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from address_parser import extract_address
from excel_matcher import ExcelMatcher, MatchResult
import history_manager
from max_sender import MaxSender
from state_manager import StateManager
from updater import check_for_updates
from vk_sender import VkSender



def _twemoji_dir() -> Path:
    """Папка с PNG-иконками Twemoji (работает и в dev, и в exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "twemoji"
    return Path(__file__).parent / "twemoji"


def _emoji_icon(emoji: str) -> QIcon | None:
    """Возвращает QIcon из Twemoji PNG или None если файл не найден."""
    codepoints = "-".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
    path = _twemoji_dir() / f"{codepoints}.png"
    if path.exists():
        return QIcon(str(path))
    return None


class EmojiPicker(QFrame):
    emoji_selected = pyqtSignal(str)

    _EMOJIS = {
        "😊 Смайлики": [
            "😀","😃","😄","😁","😆","😅","😂","🤣","😊","😇","🙂","🙃","😉","😌",
            "😍","🥰","😘","😗","😙","😚","😋","😛","😝","😜","🤪","🤨","🧐","🤓",
            "😎","🤩","🥳","😏","😒","😞","😔","😟","😕","🙁","☹️","😣","😖","😫",
            "😩","🥺","😢","😭","😤","😠","😡","🤬","🤯","😳","🥵","🥶","😱","😨",
            "😰","😥","😓","🤗","🤔","🤭","🤫","🤥","😶","😐","😑","😬","🙄","😯",
            "😦","😧","😮","😲","🥱","😴","🤤","😪","😵","🤐","🥴","🤢","🤮","🤧",
            "😷","🤒","🤕","🤑","🤠","😈","👿","👹","👺","🤡","💩","👻","💀","☠️",
        ],
        "👋 Жесты": [
            "👍","👎","👌","🤌","🤏","✌️","🤞","🤟","🤘","🤙","👈","👉","👆","👇",
            "☝️","✋","🖐","🖖","👋","🤚","💪","🦾","🙏","👏","🙌","🤝","✍️","🤳",
            "💅","🦵","🦶","👂","👃","👀","👁","👅","👄","🫀","🫁","🧠","🦷","🦴",
        ],
        "🐶 Природа": [
            "🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯","🦁","🐮","🐷","🐸",
            "🐵","🙈","🙉","🙊","🐔","🐧","🐦","🦆","🦅","🦉","🦇","🐺","🐗","🐴",
            "🦄","🐝","🐛","🦋","🐌","🐞","🐜","🦟","🕷","🐢","🐍","🦎","🐙","🦑",
            "🐡","🐠","🐟","🐬","🐳","🐋","🦈","🌸","🌺","🌻","🌹","🍀","🌿","🍃",
            "🌳","🌲","🌴","🌵","🌾","🍄","🌍","🌊","🌈","☀️","🌙","⭐","❄️","🔥",
        ],
        "🍕 Еда": [
            "🍎","🍊","🍋","🍇","🍓","🫐","🍒","🍑","🥭","🍍","🥝","🍅","🥑","🍆",
            "🥕","🌽","🌶","🥒","🥬","🧅","🧄","🥔","🍠","🥐","🥖","🍞","🧀","🥚",
            "🍳","🥞","🥓","🥩","🍗","🍖","🌭","🍔","🍟","🍕","🌮","🌯","🥪","🍜",
            "🍝","🍣","🍱","🍤","🍙","🍚","🧁","🍰","🎂","🍭","🍬","🍫","🍿","🍩",
            "🍪","☕","🍵","🧃","🥤","🍺","🥂","🍾","🧋",
        ],
        "❤️ Символы": [
            "❤️","🧡","💛","💚","💙","💜","🖤","🤍","🤎","💔","❣️","💕","💞","💓",
            "💗","💖","💘","💝","✨","💫","⚡","🌟","💥","🎉","🎊","🎈","🎁","🏆",
            "🥇","💯","🔔","📢","📣","💬","✅","❌","⭕","🔴","🟢","🔵","🟡","🟠",
            "♻️","🆕","🆒","🆓","🔝","🔜","💡","🔍","📍","📌","🗓","⏰","🚀","🌐",
            # Знаки восклицания и внимания
            "❗","❕","‼️","⁉️","❓","❔","🚨","⚠️","🔴","📛","🆘","⛔","🚫","🔞",
        ],
    }

    # Флаги отдельным словарём: код страны → эмодзи (вставляется при клике)
    _FLAGS = {
        "RU":"🇷🇺","BY":"🇧🇾","KZ":"🇰🇿","UZ":"🇺🇿","AZ":"🇦🇿","AM":"🇦🇲",
        "GE":"🇬🇪","MD":"🇲🇩","TJ":"🇹🇯","TM":"🇹🇲","KG":"🇰🇬",
        "UA":"🇺🇦","DE":"🇩🇪","FR":"🇫🇷","IT":"🇮🇹","ES":"🇪🇸","PL":"🇵🇱",
        "NL":"🇳🇱","BE":"🇧🇪","AT":"🇦🇹","CH":"🇨🇭","SE":"🇸🇪","NO":"🇳🇴",
        "DK":"🇩🇰","FI":"🇫🇮","CZ":"🇨🇿","SK":"🇸🇰","HU":"🇭🇺","RO":"🇷🇴",
        "BG":"🇧🇬","HR":"🇭🇷","RS":"🇷🇸","GR":"🇬🇷","PT":"🇵🇹","GB":"🇬🇧",
        "IE":"🇮🇪","LV":"🇱🇻","LT":"🇱🇹","EE":"🇪🇪","SI":"🇸🇮",
        "US":"🇺🇸","CN":"🇨🇳","JP":"🇯🇵","KR":"🇰🇷","IN":"🇮🇳","BR":"🇧🇷",
        "AU":"🇦🇺","CA":"🇨🇦","MX":"🇲🇽","AR":"🇦🇷","IL":"🇮🇱","TR":"🇹🇷",
        "SA":"🇸🇦","AE":"🇦🇪","EG":"🇪🇬","ZA":"🇿🇦","TH":"🇹🇭","VN":"🇻🇳",
        "🚩":"🚩","🏁":"🏁","🌍":"🌍","🌎":"🌎","🌏":"🌏",
    }
    

    _COLS = 8  # количество колонок в сетке эмодзи

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("emojiPicker")
        self.setFixedSize(310, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Заголовок с кнопкой закрытия
        header = QHBoxLayout()
        title = QLabel("Эмодзи")
        title.setStyleSheet("font-size:12px; color:#666;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet("border:none; background:transparent; color:#888; font-size:12px;")
        close_btn.clicked.connect(self.hide)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        for category, emojis in self._EMOJIS.items():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

            grid_widget = QWidget()
            grid = QGridLayout(grid_widget)
            grid.setSpacing(1)
            grid.setContentsMargins(2, 2, 2, 2)
            grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

            for i, emoji in enumerate(emojis):
                btn = QPushButton()
                icon = _emoji_icon(emoji)
                if icon:
                    btn.setIcon(icon)
                    btn.setIconSize(QSize(24, 24))
                else:
                    btn.setText(emoji)
                btn.setFixedSize(32, 32)
                btn.setObjectName("emojiBtn")
                btn.setToolTip(emoji)
                btn.clicked.connect(lambda _, e=emoji: self._pick(e))
                grid.addWidget(btn, i // self._COLS, i % self._COLS)

            scroll.setWidget(grid_widget)
            tabs.addTab(scroll, category.split()[0])

        # Вкладка флагов: кнопки с кодом страны (флаги не рендерятся на Windows)
        flag_scroll = QScrollArea()
        flag_scroll.setWidgetResizable(True)
        flag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        flag_grid_widget = QWidget()
        flag_grid = QGridLayout(flag_grid_widget)
        flag_grid.setSpacing(2)
        flag_grid.setContentsMargins(2, 2, 2, 2)
        flag_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        for i, (code, emoji) in enumerate(self._FLAGS.items()):
            btn = QPushButton()
            icon = _emoji_icon(emoji)
            if icon:
                btn.setIcon(icon)
                btn.setIconSize(QSize(24, 24))
                btn.setToolTip(code)
            else:
                btn.setText(code)
            btn.setFixedSize(40, 28)
            btn.setObjectName("emojiBtn")
            btn.clicked.connect(lambda _, e=emoji: self._pick(e))
            flag_grid.addWidget(btn, i // 7, i % 7)
        flag_scroll.setWidget(flag_grid_widget)
        tabs.addTab(flag_scroll, "🚩")

        layout.addWidget(tabs)

    def _pick(self, emoji: str) -> None:
        self.emoji_selected.emit(emoji)
        # окно не закрывается — можно выбирать несколько эмодзи подряд

    def show_near(self, widget: QWidget) -> None:
        pos = widget.mapToGlobal(widget.rect().topLeft())
        x = pos.x() - self.width() + widget.width()
        y = pos.y() - self.height() - 4
        self.move(x, y)
        self.show()
        self.raise_()


class SendWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        max_sender: MaxSender,
        vk_sender: VkSender,
        chat_id: str,
        text: str,
        image_path: str | None,
        send_max: bool,
        send_vk: bool,
    ) -> None:
        super().__init__()
        self.max_sender = max_sender
        self.vk_sender = vk_sender
        self.chat_id = chat_id
        self.text = text
        self.image_path = image_path
        self.send_max = send_max
        self.send_vk = send_vk

    def run(self) -> None:
        lines: list[str] = []
        success = True

        if self.send_max:
            r = self.max_sender.send_post(
                chat_link=self.chat_id,
                text=self.text,
                image_path=self.image_path,
            )
            lines.append(f"MAX: {r.message}")
            if not r.success:
                success = False

        if self.send_vk:
            r = self.vk_sender.send_post(
                text=self.text,
                image_path=self.image_path,
            )
            lines.append(f"ВК: {r.message}")
            if not r.success:
                success = False

        self.finished.emit(success, "\n".join(lines))



class PreviewCard(QFrame):
    """Карточка предпросмотра поста — картинка + текст единым блоком, как в соцсети."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("previewCard")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Весь пост прокручивается целиком — нет конфликтующих скроллов
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._post_widget = QWidget()
        self._post_widget.setObjectName("postCard")
        post_layout = QVBoxLayout(self._post_widget)
        post_layout.setContentsMargins(0, 0, 0, 0)
        post_layout.setSpacing(0)

        # Блок изображения
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_placeholder_style()

        # Текст поста: растёт с контентом, скролл отключён (скролл — снаружи)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setObjectName("postText")
        self.preview_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.preview_text.document().contentsChanged.connect(self._adjust_text_height)

        post_layout.addWidget(self.image_label)
        post_layout.addWidget(self.preview_text)
        post_layout.addStretch()

        scroll.setWidget(self._post_widget)
        outer.addWidget(scroll)

        self._original_pixmap = QPixmap()

    def _apply_placeholder_style(self) -> None:
        self.image_label.setStyleSheet(
            "background:#f0f2f5; color:#b0b8c1; font-size:13px;"
        )
        self.image_label.setFixedHeight(200)
        self.image_label.setText("Изображение не выбрано")
        self.image_label.setPixmap(QPixmap())

    def _adjust_text_height(self) -> None:
        doc_h = int(self.preview_text.document().size().height()) + 20
        self.preview_text.setMinimumHeight(doc_h)

    def set_preview_text(self, text: str) -> None:
        self.preview_text.setPlainText(text)

    def set_image(self, file_path: str | None) -> None:
        if not file_path:
            self._original_pixmap = QPixmap()
            self._apply_placeholder_style()
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            self._original_pixmap = QPixmap()
            self.image_label.setText("Не удалось загрузить изображение")
            return

        self._original_pixmap = pixmap
        self.image_label.setStyleSheet("")
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._original_pixmap.isNull():
            return
        w = max(100, self._post_widget.width())
        scaled = self._original_pixmap.scaled(
            w, 420,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setFixedHeight(scaled.height())
        self.image_label.setPixmap(scaled)
        self.image_label.setText("")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("MAX Poster")
        self.resize(1280, 760)

        self.excel_path: Path = self._resolve_excel_path()
        self.image_path: Path | None = None
        self.current_matches: list[MatchResult] = []
        self._current_chat_id: str = ""
        self._worker: SendWorker | None = None

        self.state_manager = StateManager()
        self.max_sender = MaxSender()
        self.vk_sender = VkSender()
        self._pending_history: dict = {}

        self._build_menu()
        self._build_ui()
        self._apply_styles()
        self.load_state()

    @staticmethod
    def _resolve_excel_path() -> Path:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        return base / "max_address.xlsx"

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("Файл")
        actions_menu = menu.addMenu("Действия")
        help_menu = menu.addMenu("Справка")

        open_image_action = QAction("Открыть фото", self)
        open_image_action.triggered.connect(self.select_image)
        file_menu.addAction(open_image_action)

        clear_action = QAction("Очистить форму", self)
        clear_action.triggered.connect(self.clear_form)
        file_menu.addAction(clear_action)

        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        check_action = QAction("Проверить", self)
        check_action.triggered.connect(self.check_post)
        actions_menu.addAction(check_action)

        send_action = QAction("Отправить", self)
        send_action.triggered.connect(self.send_post)
        actions_menu.addAction(send_action)

        about_action = QAction("О программе", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(16)

        left_box = QGroupBox()
        left_box.setObjectName("leftCard")
        left_layout = QVBoxLayout(left_box)
        left_layout.setSpacing(12)
        left_layout.setContentsMargins(20, 20, 20, 16)

        page_title = QLabel("Создание публикации")
        page_title.setObjectName("pageTitle")
        left_layout.addWidget(page_title)

        self.text_input = QPlainTextEdit()
        self.text_input.textChanged.connect(self.sync_preview)

        # Нижняя панель: смайлик + счётчик символов
        self._emoji_picker = EmojiPicker(self)
        self._emoji_picker.emoji_selected.connect(self._insert_emoji)

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
        text_section_lbl = QLabel("Текст публикации")
        text_section_lbl.setObjectName("sectionLabel")
        tc_layout.addWidget(text_section_lbl)
        tc_layout.addWidget(self.text_input)
        tc_layout.addWidget(bottom_bar)

        self.detected_address = QLineEdit()
        self.detected_address.setReadOnly(True)
        self.detected_address.setPlaceholderText("Здесь появится найденный адрес")

        self.match_selector = QComboBox()
        self.match_selector.setEnabled(False)
        self.match_selector.currentIndexChanged.connect(self.apply_selected_match)

        left_layout.addWidget(text_container, 1)
        left_layout.addWidget(QLabel("Найденный адрес"))
        left_layout.addWidget(self.detected_address)
        left_layout.addWidget(QLabel("Варианты совпадений из Excel"))
        left_layout.addWidget(self.match_selector)

        buttons_row = QGridLayout()

        self.check_button = QPushButton("Проверить адрес")
        self.check_button.clicked.connect(self.check_post)

        self.photo_button = QPushButton("Загрузить фото")
        self.photo_button.clicked.connect(self.select_image)

        self.clear_button = QPushButton("Очистить")
        self.clear_button.clicked.connect(self.clear_form)

        self.send_button = QPushButton("Отправить")
        self.send_button.clicked.connect(self.send_post)
        self.send_button.setObjectName("primaryButton")

        # Чекбоксы выбора платформы — под кнопкой Отправить
        self.chk_max = QCheckBox("MAX")
        self.chk_max.setChecked(True)
        self.chk_vk = QCheckBox("ВКонтакте")
        self.chk_vk.setChecked(False)

        platforms_row = QHBoxLayout()
        platforms_row.setContentsMargins(0, 0, 0, 0)
        platforms_row.addWidget(self.chk_max)
        platforms_row.addWidget(self.chk_vk)
        platforms_row.addStretch()

        send_col = QWidget()
        send_col_layout = QVBoxLayout(send_col)
        send_col_layout.setContentsMargins(0, 0, 0, 0)
        send_col_layout.setSpacing(4)
        send_col_layout.addWidget(self.send_button)
        send_col_layout.addLayout(platforms_row)

        buttons_row.addWidget(self.check_button, 0, 0)
        buttons_row.addWidget(self.photo_button, 0, 1)
        buttons_row.addWidget(self.clear_button, 1, 0)
        buttons_row.addWidget(send_col, 1, 1)

        left_layout.addLayout(buttons_row)

        version_label = QLabel("Version 1.1.0")
        version_label.setObjectName("versionLabel")
        left_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignLeft)

        right_box = QGroupBox()
        right_box.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        self.preview = PreviewCard()
        right_layout.addWidget(self.preview, 1)

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

        # подключаем чекбоксы к обновлению чеклиста
        self.chk_max.stateChanged.connect(self._update_checklist)
        self.chk_vk.stateChanged.connect(self._update_checklist)

        # ── История публикаций ───────────────────────────────────────
        right_layout.addWidget(self._build_history_panel())

        root.addWidget(left_box, 5)
        root.addWidget(right_box, 6)


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

        for entry in entries:
            lbl = QLabel(self._entry_html(entry))
            lbl.setObjectName("histEntry")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(True)
            self._hist_layout.insertWidget(self._hist_layout.count() - 1, lbl)

    @staticmethod
    def _entry_html(entry: dict) -> str:
        ts = entry.get("ts", "")
        parts = []
        if "max" in entry:
            addr = entry["max"]
            parts.append(f"<b style='color:#2d6cdf;'>MAX</b> · {addr}")
        if entry.get("vk"):
            parts.append("<b style='color:#4a76a8;'>ВКонтакте</b> · паблик")
        platforms = " &nbsp;│&nbsp; ".join(parts)
        return (
            f'<span style="color:#b0b8c4;font-size:11px;">{ts}</span><br>'
            f'<span style="font-size:12px;color:#2c3340;">{platforms}</span>'
        )

    def _clear_history(self) -> None:
        history_manager.clear()
        self._refresh_history()

    # ──────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        family = getattr(self, "_ui_font_family", "")
        size   = getattr(self, "_ui_font_size", 13)
        font_rule = f'* {{ font-family: "{family}"; font-size: {size}px; }}' if family else ""
        self.setStyleSheet(font_rule + """
            QMainWindow { background: #e8eaf2; }
            QMenuBar { background: #ffffff; border-bottom: 1px solid #e4e6ef; }
            QMenuBar::item:selected { background: #eef4ff; border-radius: 4px; }
            QMenu { background: #ffffff; border: 1px solid #e4e6ef; border-radius: 8px; }
            QMenu::item:selected { background: #eef4ff; }

            /* ── Левая карточка ──────────────────────────────────── */
            QGroupBox#leftCard {
                background: #ffffff;
                border: 1px solid #e0e2ea;
                border-radius: 14px;
                margin-top: 0;
                padding: 0;
            }
            QGroupBox#leftCard::title { width: 0; height: 0; }

            /* ── Правая панель (прозрачный контейнер) ───────────── */
            QGroupBox#rightPanel {
                background: transparent;
                border: none;
                margin-top: 0;
                padding: 0;
            }
            QGroupBox#rightPanel::title { width: 0; height: 0; }

            /* ── Типографика ─────────────────────────────────────── */
            #pageTitle {
                font-size: 17px;
                font-weight: 700;
                color: #111827;
                padding-bottom: 4px;
            }
            QLabel#groupBoxTitle {
                font-size: 15px;
                font-weight: 600;
                color: #111827;
                padding-left: 2px;
            }
            #sectionTitle {
                font-size: 12px;
                font-weight: 600;
                color: #6b7280;
                letter-spacing: 0.3px;
            }
            #sectionLabel {
                font-size: 11px;
                color: #aab4bf;
                padding: 7px 10px 2px 10px;
            }
            #versionLabel { font-size: 11px; color: #aab0bb; padding: 2px 0; }

            /* ── Поля ввода ──────────────────────────────────────── */
            QPlainTextEdit, QTextEdit, QLineEdit, QComboBox {
                border: 1px solid #d1d5e0;
                border-radius: 8px;
                padding: 8px;
                background: #ffffff;
            }
            #textContainer {
                border: 1px solid #d1d5e0;
                border-radius: 10px;
                background: #ffffff;
            }
            #textContainer QPlainTextEdit {
                border: none;
                border-radius: 0 0 8px 8px;
                padding: 6px 10px;
                background: #ffffff;
            }
            #emojiButton {
                min-height: 24px;
                border: none;
                background: transparent;
                font-size: 16px;
                padding: 0;
            }
            #emojiButton:hover { background: #eef2f7; border-radius: 4px; }
            #charCounter { font-size: 11px; color: #9ca3af; }

            /* ── Список адресов ──────────────────────────────────── */
            #addrList {
                border: 1px solid #d1d5e0;
                border-radius: 10px;
                background: #ffffff;
                alternate-background-color: #f8f9fb;
            }
            #addrList::item { padding: 4px 8px; }
            #addrList::item:selected { background: #eef4ff; color: #1a1a1a; }
            QPushButton#addAddrBtn {
                min-height: 0;
                font-size: 16px;
                font-weight: 600;
                color: #2d6cdf;
                background: transparent;
                border: 1px solid #b0c4e8;
                border-radius: 6px;
                padding: 0;
            }
            QPushButton#addAddrBtn:hover { background: #eef4ff; }

            /* ── Платформы ───────────────────────────────────────── */
            #platformChip {
                border: 1px solid #d1d9e0;
                border-radius: 10px;
                background: #f8fafc;
            }
            #platformChip QCheckBox {
                font-size: 13px;
                color: #1f2937;
                spacing: 6px;
            }

            /* ── Кнопки ──────────────────────────────────────────── */
            QPushButton {
                min-height: 40px;
                border-radius: 10px;
                border: 1px solid #d1d5e0;
                background: #f3f4f8;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #e8eaf4; border-color: #b8bcd0; }
            QPushButton#primaryButton {
                background: #2d6cdf;
                color: white;
                border: none;
                font-weight: 600;
                min-height: 44px;
                border-radius: 10px;
            }
            QPushButton#primaryButton:hover { background: #2560cc; }
            QPushButton#clearButton {
                min-height: 28px;
                font-size: 12px;
                color: #9ca3af;
                background: transparent;
                border: 1px solid #d1d5e0;
                border-radius: 8px;
                padding: 2px 16px;
            }
            QPushButton#clearButton:hover { background: #fff0f0; color: #d94040; border-color: #f0b0b0; }
            QPushButton#photoButtonDone {
                background: #eaf7ef;
                color: #1a7a3f;
                border: 1px solid #a8dfc0;
                font-size: 13px;
            }

            /* ── Мини-кнопки шапки ───────────────────────────────── */
            QPushButton#themeMiniBtn, QPushButton#fontMiniBtn {
                min-height: 0;
                font-size: 12px;
                padding: 3px 12px;
                border-radius: 7px;
                border: 1px solid #d1d5e0;
                background: #f3f4f8;
                color: #555;
            }
            QPushButton#fontMiniBtn { font-weight: 700; }
            QPushButton#themeMiniBtn:hover, QPushButton#fontMiniBtn:hover {
                background: #eef4ff;
                border-color: #2d6cdf;
                color: #2d6cdf;
            }

            /* ── Карточка предпросмотра поста ────────────────────── */
            #previewCard {
                border: 1px solid #e0e2ea;
                border-radius: 14px;
                background: #ffffff;
            }
            #postCard { background: #ffffff; }
            #postText {
                border: none;
                background: #ffffff;
                color: #1a1a1a;
                padding: 12px 16px;
            }
            #postAvatar {
                background: #3b82f6;
                color: white;
                font-size: 16px;
                font-weight: 700;
                border-radius: 18px;
            }
            #postAuthor { font-size: 13px; font-weight: 600; color: #1f2937; }
            #postDate { font-size: 11px; color: #9ca3af; }
            QPushButton#postMoreBtn {
                min-height: 0;
                font-size: 16px;
                color: #9ca3af;
                background: transparent;
                border: none;
                padding: 0;
            }
            #postReactions { border-top: 1px solid #f0f4f8; }
            #reactionItem { font-size: 13px; color: #9ca3af; }

            /* ── Чеклист готовности ──────────────────────────────── */
            #checklistFrame {
                border: 1px solid #e0e2ea;
                border-radius: 14px;
                background: #ffffff;
            }
            #checklistTitle {
                font-size: 12px;
                font-weight: 600;
                color: #6b7280;
                letter-spacing: 0.3px;
            }
            #checklistItem { font-size: 13px; color: #374151; padding: 1px 0; }

            /* ── История публикаций ──────────────────────────────── */
            #historyFrame {
                border: 1px solid #e0e2ea;
                border-radius: 14px;
                background: #ffffff;
            }
            #histEntry {
                background: #f8fafc;
                border: 1px solid #eaeff5;
                border-radius: 8px;
                padding: 5px 8px;
            }
            #histEmpty { font-size: 12px; color: #c0c8d4; padding: 4px 0; }
            #histDate { font-size: 11px; color: #b0b8c4; }
            #histText { font-size: 12px; color: #4a5568; }
            #histPlatformFallback { font-size: 11px; color: #6b7280; font-weight: 600; }
            QPushButton#histClearBtn {
                min-height: 0;
                font-size: 11px;
                color: #9ca3af;
                background: transparent;
                border: 1px solid #dde3ea;
                border-radius: 5px;
                padding: 1px 8px;
            }
            QPushButton#histClearBtn:hover { background: #fff0f0; color: #e05555; }

            /* ── Эмодзи и разное ─────────────────────────────────── */
            #emojiPicker {
                border: 1px solid #d1d5e0;
                border-radius: 10px;
                background: #ffffff;
            }
            #emojiBtn {
                min-height: 0;
                border: none;
                background: transparent;
                font-size: 18px;
                padding: 0;
            }
            #emojiBtn:hover { background: #eef2f7; border-radius: 4px; }
            QPushButton#themeThumb {
                min-height: 0;
                border: 1px solid #d1d5e0;
                border-radius: 6px;
                background: #f0f2f5;
                padding: 0;
                font-size: 12px;
                color: #555;
            }
            QPushButton#themeThumb:hover { border-color: #2d6cdf; }

            /* ── Прогресс-бар ────────────────────────────────────── */
            QProgressBar#sendProgress {
                border: none;
                background: #e0e4ef;
                border-radius: 2px;
            }
            QProgressBar#sendProgress::chunk {
                background: #2d6cdf;
                border-radius: 2px;
            }
        """)

    def _update_checklist(self) -> None:
        def row(ok: bool, label: str) -> str:
            if ok:
                return f'<span style="color:#22a35a;">&#10003;</span>  {label}'
            return f'<span style="color:#c0c8d4;">&#9679;</span>  <span style="color:#aab0bb;">{label}</span>'

        has_text    = bool(self.text_input.toPlainText().strip())
        has_photo   = self.image_path is not None
        has_address = bool(self._current_chat_id)
        has_platform = self.chk_max.isChecked() or self.chk_vk.isChecked()

        self._cl_text.setText(row(has_text, "Текст введён"))
        self._cl_photo.setText(row(has_photo, "Фото загружено"))
        self._cl_address.setText(row(has_address, "Адрес найден"))
        self._cl_platform.setText(row(has_platform, "Платформа выбрана"))

    def sync_preview(self) -> None:
        text = self.text_input.toPlainText()
        self.preview.set_preview_text(text)
        count = len(text)
        self._char_counter.setText(f"{count}/4000")
        self._char_counter.setStyleSheet("color: #cc0000; font-weight: 600;" if count > 4000 else "color: #888;")
        self._update_checklist()
        self.save_state()

    def _toggle_emoji_picker(self) -> None:
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
        self.detected_address.clear()
        self._current_chat_id = ""
        self.match_selector.clear()
        self.match_selector.setEnabled(False)
        self.preview.set_preview_text("")
        self.preview.set_image(None)
        self.image_path = None
        self.current_matches = []
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

        parsed = extract_address(text)
        if not parsed.street:
            QMessageBox.warning(self, "Проверка", "Не удалось извлечь адрес из текста.")
            return

        if not self.excel_path.exists():
            QMessageBox.critical(self, "Ошибка", f"Файл не найден: {self.excel_path}")
            return

        try:
            matcher = ExcelMatcher(self.excel_path)
            matches = matcher.find_matches(parsed)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Ошибка при чтении Excel: {exc}")
            return

        self.current_matches = matches
        self.match_selector.blockSignals(True)
        self.match_selector.clear()

        for item in matches:
            self.match_selector.addItem(item.address, item)

        self.match_selector.blockSignals(False)
        self.match_selector.setEnabled(bool(matches))

        if not matches:
            self.detected_address.clear()
            return

        self.match_selector.setCurrentIndex(0)
        self.apply_selected_match()
        self.save_state()

    def apply_selected_match(self) -> None:
        data = self.match_selector.currentData()
        if not data:
            return
        self.detected_address.setText(data.address)
        self._current_chat_id = data.chat_id
        self._update_checklist()
        self.save_state()

    def send_post(self) -> None:
        text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Отправка", "Нельзя отправить пустой текст.")
            return

        send_max = self.chk_max.isChecked()
        send_vk = self.chk_vk.isChecked()

        if not send_max and not send_vk:
            QMessageBox.warning(self, "Отправка", "Выбери хотя бы одну платформу (MAX или ВКонтакте).")
            return

        chat_id = self._current_chat_id
        if send_max and not chat_id:
            QMessageBox.warning(self, "Отправка", "ID чата не заполнен. Заполни колонку ID в Excel и нажми 'Проверить адрес'.")
            return

        self.send_button.setEnabled(False)
        self.check_button.setEnabled(False)

        # сохраняем данные для записи в историю после отправки
        self._pending_history = {
            "address": self.detected_address.text(),
            "send_max": send_max,
            "send_vk": send_vk,
        }

        self._worker = SendWorker(
            max_sender=self.max_sender,
            vk_sender=self.vk_sender,
            chat_id=chat_id,
            text=text,
            image_path=str(self.image_path) if self.image_path else None,
            send_max=send_max,
            send_vk=send_vk,
        )
        self._worker.finished.connect(self._on_send_finished)
        self._worker.start()

    def _on_send_finished(self, success: bool, message: str) -> None:
        self.send_button.setEnabled(True)
        self.check_button.setEnabled(True)
        if success:
            h = self._pending_history
            history_manager.add_entry(
                address=h.get("address", ""),
                sent_max=h.get("send_max", False),
                sent_vk=h.get("send_vk", False),
            )
            self._refresh_history()
            QMessageBox.information(self, "Отправка", message)
        else:
            QMessageBox.critical(self, "Отправка", message)

    def save_state(self) -> None:
        self.state_manager.save({
            "image_path": str(self.image_path) if self.image_path else "",
            "text": self.text_input.toPlainText(),
            "width": self.width(),
            "height": self.height(),
            "selected_address": self.detected_address.text(),
            "chat_id": self._current_chat_id,
        })

    def load_state(self) -> None:
        data = self.state_manager.load()
        self.resize(data.get("width", 1280), data.get("height", 760))

        text = data.get("text", "")
        if text:
            self.text_input.setPlainText(text)

        image_path = data.get("image_path", "")
        if image_path and Path(image_path).exists():
            self.image_path = Path(image_path)
            self.preview.set_image(str(self.image_path))
            self._set_photo_button_name(self.image_path.name)

        selected_address = data.get("selected_address", "")
        if selected_address:
            self.detected_address.setText(selected_address)


        self._current_chat_id = data.get("chat_id", "")

        self.sync_preview()

    def closeEvent(self, event) -> None:
        self.save_state()
        self.max_sender.close()
        super().closeEvent(event)

    def show_about(self) -> None:
        QMessageBox.information(
            self, "О программе",
            "MAX Poster\n\n"
            "Отправка сообщений в группы MAX через GREEN-API.\n\n"
            "Emoji provided free by Twitter (Twemoji) under CC BY 4.0\n"
            "https://creativecommons.org/licenses/by/4.0/"
        )


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    # Проверка обновлений через 2 сек после запуска (чтобы окно успело отрисоваться)
    QTimer.singleShot(2000, lambda: check_for_updates(window))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
