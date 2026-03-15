import sys
from pathlib import Path

from PyQt6.QtCore import QSize, QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPixmap, QTextCursor
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
    QShortcut,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Роль для пометки вручную добавленных адресов
_MANUAL_ROLE: int = Qt.ItemDataRole.UserRole + 1

import tg_notify
from address_parser import extract_address, extract_all_addresses
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


def _assets_dir() -> Path:
    """Папка assets (работает и в dev, и в exe)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    return base / "assets"


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
    result_ready = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(
        self,
        max_sender: MaxSender,
        vk_sender: VkSender,
        chat_ids: list,
        text: str,
        image_path: str | None,
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

    def run(self) -> None:
        lines: list[str] = []
        success = True

        if self.send_max:
            total = len(self.chat_ids)
            for i, chat_id in enumerate(self.chat_ids, 1):
                self.progress.emit(f"MAX {i}/{total}…")
                r = self.max_sender.send_post(
                    chat_link=chat_id,
                    text=self.text,
                    image_path=self.image_path,
                )
                lines.append(f"MAX [{i}/{total}]: {r.message}")
                if not r.success:
                    success = False

        if self.send_vk:
            r = self.vk_sender.send_post(
                text=self.text,
                image_path=self.image_path,
                progress=lambda msg: self.progress.emit(f"ВК: {msg}"),
            )
            lines.append(f"ВК: {r.message}")
            if not r.success:
                success = False

        self.result_ready.emit(success, "\n".join(lines))



class _OverlayWidget(QWidget):
    """Полупрозрачное изображение поверх всех дочерних виджетов."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._bg_pixmap: QPixmap | None = None
        self._opacity_pct: int = 30

    def set_background(self, pixmap: QPixmap | None, opacity_pct: int = 30) -> None:
        self._bg_pixmap = pixmap
        self._opacity_pct = opacity_pct
        self.update()

    def paintEvent(self, event) -> None:
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            painter = QPainter(self)
            painter.setOpacity(self._opacity_pct / 100)
            scaled = self._bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)


class _BgWidget(QWidget):
    """Центральный виджет с фоновым изображением."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bg_pixmap: QPixmap | None = None
        self._mode: int = 0  # 0 = фон (за элементами), 1 = наложение (поверх)
        self._opacity_pct: int = 50
        self._overlay = _OverlayWidget(self)
        self._overlay.hide()

    def set_background(self, pixmap: QPixmap | None, mode: int = 0, opacity_pct: int = 50) -> None:
        self._bg_pixmap = pixmap
        self._mode = mode
        self._opacity_pct = opacity_pct
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
            scaled = self._bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)


class ThemePickerDialog(QDialog):
    """Диалог выбора фонового изображения."""

    _COLS = 4
    preview_changed = pyqtSignal(object, int, int)  # (int | None index, int mode, int opacity_pct)

    def __init__(
        self,
        assets_dir: Path,
        current_index: int | None,
        current_mode: int = 0,
        current_opacity: int = 50,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Тема оформления")
        self._selected: int | None = current_index
        self._mode: int = current_mode
        self._opacity_pct: int = current_opacity
        self._btns: list[tuple[int | None, QPushButton]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Выберите фоновое изображение:"))

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(8)

        items: list[int | None] = [None] + list(range(1, 11))
        for pos, idx in enumerate(items):
            if idx is None:
                btn = QPushButton("Без фона")
            else:
                btn = QPushButton()
                img_path = assets_dir / f"fon_{idx}.jpg"
                if img_path.exists():
                    pix = QPixmap(str(img_path)).scaled(
                        96, 64,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    btn.setIcon(QIcon(pix))
                    btn.setIconSize(QSize(96, 64))
                else:
                    btn.setText(f"Фон {idx}")
            btn.setFixedSize(108, 76)
            btn.setCheckable(True)
            btn.setChecked(current_index == idx)
            btn.setObjectName("themeThumb")
            btn.clicked.connect(lambda _, x=idx: self._select(x))
            self._btns.append((idx, btn))
            grid.addWidget(btn, pos // self._COLS, pos % self._COLS)

        layout.addWidget(grid_widget)

        # ── Переключатель режима ────────────────────────────────────
        mode_frame = QFrame()
        mode_frame.setObjectName("modeFrame")
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(4, 6, 4, 2)
        mode_layout.setSpacing(16)

        mode_layout.addWidget(QLabel("Режим:"))

        self._rb_bg = QRadioButton("Фон  (за элементами)")
        self._rb_overlay = QRadioButton("Наложение  (поверх всего)")
        self._rb_bg.setChecked(current_mode == 0)
        self._rb_overlay.setChecked(current_mode == 1)

        btn_group = QButtonGroup(self)
        btn_group.addButton(self._rb_bg, 0)
        btn_group.addButton(self._rb_overlay, 1)
        btn_group.idClicked.connect(self._set_mode)

        mode_layout.addWidget(self._rb_bg)
        mode_layout.addWidget(self._rb_overlay)
        mode_layout.addStretch()
        layout.addWidget(mode_frame)

        # ── Слайдер прозрачности ────────────────────────────────────
        opacity_frame = QFrame()
        opacity_layout = QHBoxLayout(opacity_frame)
        opacity_layout.setContentsMargins(4, 0, 4, 4)
        opacity_layout.setSpacing(10)

        opacity_layout.addWidget(QLabel("Прозрачность:"))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(10, 90)
        self._opacity_slider.setValue(current_opacity)
        self._opacity_slider.setTickInterval(10)
        self._opacity_slider.setFixedWidth(180)
        self._opacity_label = QLabel(f"{current_opacity}%")
        self._opacity_label.setFixedWidth(34)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)

        opacity_layout.addWidget(self._opacity_slider)
        opacity_layout.addWidget(self._opacity_label)
        opacity_layout.addStretch()
        layout.addWidget(opacity_frame)

        # ── Кнопки Применить / Отмена ───────────────────────────────
        buttons = QDialogButtonBox()
        buttons.addButton("Применить", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select(self, index: int | None) -> None:
        self._selected = index
        for idx, btn in self._btns:
            btn.setChecked(idx == index)
        self.preview_changed.emit(index, self._mode, self._opacity_pct)

    def _set_mode(self, mode: int) -> None:
        self._mode = mode
        self.preview_changed.emit(self._selected, self._mode, self._opacity_pct)

    def _on_opacity_changed(self, value: int) -> None:
        self._opacity_pct = value
        self._opacity_label.setText(f"{value}%")
        self.preview_changed.emit(self._selected, self._mode, value)


class AddAddressDialog(QDialog):
    """Диалог ручного добавления адреса в список рассылки."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Добавить адрес")
        self.setFixedWidth(440)

        layout = QFormLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(10)

        self._address_edit = QLineEdit()
        self._address_edit.setPlaceholderText("г. Санкт-Петербург, ул. Примерная, д. 1")

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://web.max.ru/-123456789")

        self._id_edit = QLineEdit()
        self._id_edit.setPlaceholderText("-123456789  (необязательно, если указана ссылка)")

        layout.addRow("Адрес:*", self._address_edit)
        layout.addRow("Ссылка (url):", self._url_edit)
        layout.addRow("ID чата:", self._id_edit)

        note = QLabel("Если ссылка содержит web.max.ru/, ID будет извлечён автоматически.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 12px;")
        layout.addRow(note)

        buttons = QDialogButtonBox()
        buttons.addButton("Применить", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _on_accept(self) -> None:
        if not self._address_edit.text().strip():
            QMessageBox.warning(self, "Добавить адрес", "Поле «Адрес» обязательно.")
            return
        self.accept()

    def result_match(self) -> "MatchResult":
        address = self._address_edit.text().strip()
        url = self._url_edit.text().strip()
        chat_id = self._id_edit.text().strip()
        if url and not chat_id and "web.max.ru/" in url:
            chat_id = url.split("web.max.ru/")[-1].strip("/")
        return MatchResult(address=address, score=0, chat_link=url, chat_id=chat_id)


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
        self.setWindowIcon(QIcon(str(_assets_dir() / "max_poster.ico")))
        self.resize(1280, 760)

        # Версия — читаем один раз
        _ver_file = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "version.txt"
        self._app_version: str = _ver_file.read_text(encoding="utf-8").strip() if _ver_file.exists() else "?"

        self.excel_path: Path = self._resolve_excel_path()
        self.image_path: Path | None = None
        self._worker: SendWorker | None = None
        self._bg_index: int | None = None
        self._bg_mode: int = 0  # 0 = фон, 1 = наложение
        self._bg_opacity: int = 50
        self._bg_widget: _BgWidget | None = None

        # Кэшированный ExcelMatcher — читает Excel только один раз
        self._matcher: ExcelMatcher | None = (
            ExcelMatcher(self.excel_path) if self.excel_path.exists() else None
        )

        import os
        _appdata = Path(os.environ.get("APPDATA", Path.home())) / "max_poster" if getattr(sys, "frozen", False) else Path(__file__).parent
        _appdata.mkdir(parents=True, exist_ok=True)
        self.state_manager = StateManager(_appdata / "app_state.json")
        self.max_sender = MaxSender()
        self.vk_sender = VkSender()
        self._pending_history: dict = {}

        self._parse_timer = QTimer(self)
        self._parse_timer.setSingleShot(True)
        self._parse_timer.setInterval(700)
        self._parse_timer.timeout.connect(self._auto_check_addresses)

        # Дебаунс сохранения состояния — не пишем на каждый символ
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._do_save_state)

        self.setAcceptDrops(True)

        self._build_menu()
        self._build_ui()
        self._apply_styles()
        self.load_state()

        # Горячие клавиши
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.send_post)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.select_image)

    @staticmethod
    def _resolve_excel_path() -> Path:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        return base / "max_address.xlsx"

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("Файл")
        actions_menu = menu.addMenu("Действия")
        view_menu = menu.addMenu("Вид")
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

        send_action = QAction("Опубликовать", self)
        send_action.triggered.connect(self.send_post)
        actions_menu.addAction(send_action)

        theme_action = QAction("Тема оформления…", self)
        theme_action.triggered.connect(self._open_theme_picker)
        view_menu.addAction(theme_action)

        about_action = QAction("О программе", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _build_ui(self) -> None:
        central = _BgWidget()
        self._bg_widget = central
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(16)

        left_box = QGroupBox("Ввод данных")
        left_layout = QVBoxLayout(left_box)
        left_layout.setSpacing(12)

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
        tc_layout.addWidget(self.text_input)
        tc_layout.addWidget(bottom_bar)

        self._addr_list = QListWidget()
        self._addr_list.setMinimumHeight(80)
        self._addr_list.setObjectName("addrList")
        self._addr_list.itemChanged.connect(self._update_checklist)
        self._addr_list.itemChanged.connect(self.save_state)

        left_layout.addWidget(QLabel("Текст публикации"))
        left_layout.addWidget(text_container, 1)
        addr_header = QHBoxLayout()
        addr_header.setContentsMargins(0, 0, 0, 0)
        addr_lbl = QLabel("Адреса для рассылки MAX (☑ — выбрать)")
        self._add_addr_btn = QPushButton("+")
        self._add_addr_btn.setObjectName("addAddrBtn")
        self._add_addr_btn.setFixedSize(24, 24)
        self._add_addr_btn.setToolTip("Добавить адрес вручную")
        self._add_addr_btn.clicked.connect(self._add_address_manually)
        addr_header.addWidget(addr_lbl)
        addr_header.addStretch()
        addr_header.addWidget(self._add_addr_btn)
        left_layout.addLayout(addr_header)
        left_layout.addWidget(self._addr_list, 1)

        buttons_row = QGridLayout()
        buttons_row.setSpacing(8)

        self.check_button = QPushButton("Проверить адрес")
        self.check_button.clicked.connect(self.check_post)

        self.photo_button = QPushButton("Загрузить фото")
        self.photo_button.clicked.connect(self.select_image)

        self.clear_button = QPushButton("Очистить")
        self.clear_button.setObjectName("clearButton")
        self.clear_button.clicked.connect(self.clear_form)

        self.send_button = QPushButton("Опубликовать")
        self.send_button.clicked.connect(self.send_post)
        self.send_button.setObjectName("primaryButton")

        # Чекбоксы выбора платформы — под кнопкой Отправить
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
        platforms_row.addWidget(self.chk_max)
        platforms_row.addWidget(self.chk_vk)
        platforms_row.addStretch()

        send_col = QWidget()
        send_col_layout = QVBoxLayout(send_col)
        send_col_layout.setContentsMargins(0, 0, 0, 0)
        send_col_layout.setSpacing(4)
        send_col_layout.addWidget(self.send_button)
        send_col_layout.addLayout(platforms_row)

        # Row 0: вспомогательные кнопки
        buttons_row.addWidget(self.check_button, 0, 0)
        buttons_row.addWidget(self.photo_button, 0, 1)
        # Row 1: кнопка отправки на всю ширину
        buttons_row.addWidget(send_col, 1, 0, 1, 2)

        # Прогресс-бар (скрыт в режиме ожидания)
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("sendProgress")
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()

        # Кнопка "Очистить" — отдельно, мелко, справа
        bottom_actions = QHBoxLayout()
        bottom_actions.setContentsMargins(0, 2, 0, 0)
        bottom_actions.addStretch()
        bottom_actions.addWidget(self.clear_button)

        left_layout.addLayout(buttons_row)
        left_layout.addWidget(self._progress_bar)
        left_layout.addLayout(bottom_actions)

        version_label = QLabel(f"Version {self._app_version}")
        version_label.setObjectName("versionLabel")
        left_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignLeft)

        right_box = QGroupBox()
        right_layout = QVBoxLayout(right_box)
        right_layout.setSpacing(12)
        right_layout.setContentsMargins(12, 10, 12, 12)

        preview_header = QHBoxLayout()
        preview_title = QLabel("Предпросмотр")
        preview_title.setObjectName("groupBoxTitle")
        self._theme_btn = QPushButton("Тема")
        self._theme_btn.setObjectName("themeMiniBtn")
        self._theme_btn.setFixedHeight(24)
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.clicked.connect(self._open_theme_picker)
        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        preview_header.addWidget(self._theme_btn)
        right_layout.addLayout(preview_header)

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
            addrs = entry["max"]
            if isinstance(addrs, list):
                addr_text = ", ".join(addrs[:2]) + ("…" if len(addrs) > 2 else "")
            else:
                addr_text = addrs
            parts.append(f"<b style='color:#2d6cdf;'>MAX</b> · {addr_text}")
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
        self.setStyleSheet("""
            QMainWindow { background: #f3f4f6; }
            QGroupBox {
                font-size: 15px;
                font-weight: 600;
                border: 1px solid #cfd6df;
                border-radius: 10px;
                margin-top: 12px;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QPlainTextEdit, QTextEdit, QLineEdit, QComboBox {
                border: 1px solid #c7d0db;
                border-radius: 8px;
                padding: 8px;
                background: #ffffff;
                font-size: 14px;
            }
            QPushButton {
                min-height: 42px;
                border-radius: 8px;
                border: 1px solid #bfc8d4;
                background: #eef2f7;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton#primaryButton {
                background: #2d6cdf;
                color: white;
                border: none;
                font-weight: 600;
            }
            #previewCard {
                border: 1px solid #cfd6df;
                border-radius: 10px;
                background: #ffffff;
                overflow: hidden;
            }
            #postCard {
                background: #ffffff;
            }
            #postText {
                border: none;
                background: #ffffff;
                font-size: 14px;
                color: #1a1a1a;
                padding: 14px 16px;
            }
            #textContainer {
                border: 1px solid #c7d0db;
                border-radius: 8px;
                background: #ffffff;
            }
            #textContainer QPlainTextEdit {
                border: none;
                border-radius: 8px 8px 0 0;
                padding: 8px;
                background: #ffffff;
                font-size: 14px;
            }
            #emojiButton {
                min-height: 24px;
                border: none;
                background: transparent;
                font-size: 16px;
                padding: 0;
            }
            #emojiButton:hover { background: #eef2f7; border-radius: 4px; }
            #charCounter { font-size: 12px; color: #888; }
            #emojiPicker {
                border: 1px solid #c7d0db;
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
            #versionLabel { font-size: 11px; color: #aab0bb; padding: 2px 0; }
            QPushButton#photoButtonDone {
                background: #eaf7ef;
                color: #1a7a3f;
                border: 1px solid #a8dfc0;
                font-size: 13px;
            }
            #checklistFrame {
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #f8fafc;
            }
            #checklistTitle {
                font-size: 12px;
                font-weight: 600;
                color: #7a8799;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            #checklistItem { font-size: 13px; color: #444; padding: 1px 0; }
            #historyFrame {
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #f8fafc;
            }
            #histEntry {
                background: #ffffff;
                border: 1px solid #eaeff5;
                border-radius: 7px;
                padding: 5px 8px;
            }
            #histEmpty { font-size: 12px; color: #c0c8d4; padding: 4px 0; }
            QPushButton#histClearBtn {
                min-height: 0;
                font-size: 11px;
                color: #a0a8b4;
                background: transparent;
                border: 1px solid #dde3ea;
                border-radius: 5px;
                padding: 1px 8px;
            }
            QPushButton#histClearBtn:hover { background: #f0f4f8; color: #e05555; }
            #addrList {
                border: 1px solid #c7d0db;
                border-radius: 8px;
                background: #ffffff;
                font-size: 13px;
            }
            #addrList::item { padding: 3px 6px; }
            #addrList::item:selected { background: #eef4ff; color: #1a1a1a; }
            QPushButton#clearButton {
                min-height: 28px;
                font-size: 12px;
                color: #a0a8b4;
                background: transparent;
                border: 1px solid #dde3ea;
                border-radius: 6px;
                padding: 2px 16px;
            }
            QPushButton#clearButton:hover {
                background: #fff0f0;
                color: #d94040;
                border-color: #f0b0b0;
            }
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
            QLabel#groupBoxTitle {
                font-size: 15px;
                font-weight: 600;
                color: #1a1a2e;
                padding-left: 4px;
            }
            QPushButton#themeMiniBtn {
                min-height: 0;
                font-size: 12px;
                padding: 2px 10px;
                border-radius: 6px;
                border: 1px solid #c7d0db;
                background: #f3f4f6;
                color: #555;
            }
            QPushButton#themeMiniBtn:hover {
                background: #eef4ff;
                border-color: #2d6cdf;
                color: #2d6cdf;
            }
            QPushButton#themeThumb {
                min-height: 0;
                border: 2px solid #dde3ea;
                border-radius: 6px;
                background: #f0f2f5;
                padding: 0;
                font-size: 12px;
                color: #555;
            }
            QPushButton#themeThumb:hover { border-color: #2d6cdf; }
            QPushButton#themeThumb:checked { border: 2px solid #2d6cdf; background: #eef4ff; }
            QProgressBar#sendProgress {
                border: none;
                border-radius: 2px;
                background: #e8edf3;
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
        self._char_counter.setText(f"{count}/4000")
        self._char_counter.setStyleSheet("color: #cc0000; font-weight: 600;" if count > 4000 else "color: #888;")
        self._update_checklist()
        self.save_state()
        self._parse_timer.start()

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
            except Exception:
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

    def _get_all_matches(self) -> list[MatchResult]:
        results = []
        for i in range(self._addr_list.count()):
            item = self._addr_list.item(i)
            if item:
                match = item.data(Qt.ItemDataRole.UserRole)
                if match:
                    results.append(match)
        return results

    def _open_theme_picker(self) -> None:
        prev_index = self._bg_index
        prev_mode = self._bg_mode
        prev_opacity = self._bg_opacity
        dlg = ThemePickerDialog(
            _assets_dir(), self._bg_index, self._bg_mode, self._bg_opacity, parent=self
        )
        dlg.preview_changed.connect(lambda idx, m, o: self._apply_theme(idx, m, o, save=False))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_theme(dlg._selected, dlg._mode, dlg._opacity_pct)
        else:
            self._apply_theme(prev_index, prev_mode, prev_opacity, save=False)

    def _apply_theme(self, index: int | None, mode: int = 0, opacity_pct: int = 50, save: bool = True) -> None:
        self._bg_index = index
        self._bg_mode = mode
        self._bg_opacity = opacity_pct
        if index is None or self._bg_widget is None:
            if self._bg_widget:
                self._bg_widget.set_background(None)
        else:
            path = _assets_dir() / f"fon_{index}.jpg"
            pix = QPixmap(str(path)) if path.exists() else QPixmap()
            self._bg_widget.set_background(pix, mode, opacity_pct)
        if save:
            self.save_state()

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

        self.send_button.setEnabled(False)
        self.send_button.setText("Публикуется…")
        self.check_button.setEnabled(False)
        self._progress_bar.show()

        self._pending_history = {
            "addresses": [m.address for m in checked],
            "send_max": send_max,
            "send_vk": send_vk,
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

    def _on_send_finished(self, success: bool, message: str) -> None:
        self.send_button.setEnabled(True)
        self.send_button.setText("Опубликовать")
        self.check_button.setEnabled(True)
        self._progress_bar.hide()
        if success:
            h = self._pending_history
            history_manager.add_entry(
                addresses=h.get("addresses", []),
                sent_max=h.get("send_max", False),
                sent_vk=h.get("send_vk", False),
            )
            self._refresh_history()
            QMessageBox.information(self, "Отправка", message)
        else:
            tg_notify.send_error("Ошибка отправки поста", message)
            QMessageBox.critical(self, "Отправка", message)

    def _auto_check_addresses(self) -> None:
        """Тихий автопарсинг адресов при изменении текста (без диалогов)."""
        text = self.text_input.toPlainText().strip()
        if not text or not self.excel_path.exists():
            return
        parsed_list = extract_all_addresses(text)
        if not parsed_list:
            return
        if self._matcher is None:
            self._matcher = ExcelMatcher(self.excel_path)
        matcher = self._matcher

        new_items: list[MatchResult] = []
        seen_ids: set[str] = set()
        for parsed in parsed_list:
            try:
                matches = matcher.find_matches(parsed)
            except Exception:
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
        if {b.chat_id for b in new_items} == existing_auto_ids:
            return

        manual_ids = {m.chat_id for m, _ in manual_entries}

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
            if m.chat_id not in {b.chat_id for b in new_items} | manual_ids - {m.chat_id}:
                pass
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
        })

    def load_state(self) -> None:
        data = self.state_manager.load()
        self.resize(data.get("width", 1280), data.get("height", 760))

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

    def closeEvent(self, event) -> None:
        self._save_timer.stop()
        self._do_save_state()  # сохраняем сразу, не через таймер
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
    tg_notify.install_excepthook()
    tg_notify.send_startup()

    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    # Проверка обновлений через 2 сек после запуска (чтобы окно успело отрисоваться)
    QTimer.singleShot(2000, lambda: check_for_updates(window))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
