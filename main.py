import atexit
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



_twemoji_dir_cache: "Path | None" = None


def _twemoji_dir() -> Path:
    """Папка с PNG-иконками Twemoji (работает и в dev, и в exe)."""
    global _twemoji_dir_cache
    if _twemoji_dir_cache is None:
        if getattr(sys, "frozen", False):
            _twemoji_dir_cache = Path(sys.executable).parent / "twemoji"
        else:
            _twemoji_dir_cache = Path(__file__).parent / "twemoji"
    return _twemoji_dir_cache


def _assets_dir() -> Path:
    """Папка assets (работает и в dev, и в exe)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    return base / "assets"


def _fonts_dir() -> Path:
    """Папка fonts (работает и в dev, и в exe)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    return base / "fonts"



def _emoji_icon(emoji: str) -> QIcon | None:
    """Возвращает QIcon из Twemoji PNG или None если файл не найден."""
    codepoints = "-".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
    path = _twemoji_dir() / f"{codepoints}.png"
    if path.exists():
        return QIcon(str(path))
    return None


class LineNumberedEdit(QPlainTextEdit):
    """QPlainTextEdit с чередующимися полосками и номером строки справа (как в списке адресов)."""

    _COLOR_ODD = QColor("#f8f9fb")
    _COLOR_EVEN = QColor("#ffffff")
    _NUM_COLOR = QColor("#c4cdd8")

    def paintEvent(self, event) -> None:
        # Полоски
        painter = QPainter(self.viewport())
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > event.rect().bottom():
                break
            if rect.bottom() >= event.rect().top():
                color = self._COLOR_ODD if block.blockNumber() % 2 == 0 else self._COLOR_EVEN
                painter.fillRect(QRect(0, int(rect.top()), self.viewport().width(), int(rect.height())), color)
            block = block.next()
        painter.end()
        super().paintEvent(event)

        # Номера справа (поверх текста)
        painter2 = QPainter(self.viewport())
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        offset = self.contentOffset()
        font = painter2.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter2.setFont(font)
        painter2.setPen(self._NUM_COLOR)
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > event.rect().bottom():
                break
            if block.isVisible() and rect.bottom() >= event.rect().top():
                painter2.drawText(
                    QRect(0, int(rect.top()), self.viewport().width() - 8, int(rect.height())),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block_num + 1),
                )
            block = block.next()
            block_num += 1
        painter2.end()


class _NumberedItemDelegate(QStyledItemDelegate):
    """Рисует серый номер строки справа в каждом элементе QListWidget."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        painter.save()
        font = painter.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        painter.setPen(QColor("#c4cdd8"))
        painter.drawText(
            option.rect.adjusted(0, 0, -8, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(index.row() + 1),
        )
        painter.restore()


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

        items: list[int | None] = [None] + list(range(1, 12))
        for pos, idx in enumerate(items):
            if idx is None:
                btn = QPushButton("Без фона")
            else:
                btn = QPushButton()
                img_path = next(
                    (assets_dir / f"fon_{idx}{ext}" for ext in (".jpg", ".png")
                     if (assets_dir / f"fon_{idx}{ext}").exists()),
                    None,
                )
                if img_path is not None and img_path.exists():
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

    def selected_index(self) -> int | None:
        return self._selected

    def selected_mode(self) -> int:
        return self._mode

    def selected_opacity(self) -> int:
        return self._opacity_pct

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


class FontPickerDialog(QDialog):
    """Диалог выбора шрифта интерфейса."""

    font_changed = pyqtSignal(str, int)

    def __init__(
        self,
        families: list,
        current_family: str,
        current_size: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Шрифт интерфейса")
        self.setMinimumWidth(360)
        self._reset_requested = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Выберите шрифт:"))

        self._list = QListWidget()
        for fam in families:
            item = QListWidgetItem(fam)
            item.setFont(QFont(fam, 13))
            self._list.addItem(item)
        if current_family and current_family in families:
            self._list.setCurrentRow(families.index(current_family))
        self._list.currentRowChanged.connect(self._on_changed)
        layout.addWidget(self._list)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Размер:"))
        self._spin = QSpinBox()
        self._spin.setRange(8, 24)
        self._spin.setValue(current_size if current_size else 13)
        self._spin.setSuffix(" pt")
        self._spin.valueChanged.connect(self._on_changed)
        size_row.addWidget(self._spin)
        size_row.addStretch()
        layout.addLayout(size_row)

        self._preview = QLabel("Пример: Abc 123")
        self._preview.setMinimumHeight(40)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            "border: 1px solid #c7d0db; border-radius: 6px; padding: 8px; background: #fff;"
        )
        layout.addWidget(self._preview)
        self._update_preview()

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Сбросить")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        buttons = QDialogButtonBox()
        buttons.addButton("Применить", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

    def _on_changed(self, _=None) -> None:
        self._update_preview()
        self.font_changed.emit(self.selected_family(), self.selected_size())

    def _update_preview(self) -> None:
        fam = self.selected_family()
        sz = self.selected_size()
        self._preview.setFont(QFont(fam, sz) if fam else QFont("", sz))
        self._preview.setText(f"{fam or 'Системный шрифт'}  Aa 123")

    def _on_reset(self) -> None:
        self._reset_requested = True
        self.accept()

    def reset_requested(self) -> bool:
        return self._reset_requested

    def selected_family(self) -> str:
        item = self._list.currentItem()
        return item.text() if item else ""

    def selected_size(self) -> int:
        return self._spin.value()


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
            extracted = url.split("web.max.ru/")[-1].strip("/")
            if extracted:
                chat_id = extracted
        return MatchResult(address=address, score=0, chat_link=url, chat_id=chat_id)


class _SuccessAnimWidget(QWidget):
    """Векторная анимация: белый круг масштабируется, затем рисуется зелёная галочка."""

    _SIZE = 148

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(14)          # ~70 fps
        self._timer.timeout.connect(self._step)

    def start(self) -> None:
        self._t = 0.0
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self._t = 0.0

    def _step(self) -> None:
        self._t = min(1.0, self._t + 0.030)  # 0→1 за ~33 кадра ≈ 470 мс
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


class PreviewCard(QFrame):
    """Карточка предпросмотра поста — шапка + изображение + текст + реакции."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("previewCard")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Шапка платформы (вне скролла) ────────────────────────────
        header = QWidget()
        header.setObjectName("postHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(10)

        self._avatar_lbl = QLabel()
        self._avatar_lbl.setFixedSize(28, 28)
        self._avatar_lbl.setScaledContents(True)
        self._avatar_lbl.setStyleSheet("background: transparent;")

        meta_col = QVBoxLayout()
        meta_col.setContentsMargins(0, 0, 0, 0)
        meta_col.setSpacing(1)
        self._name_lbl = QLabel("MAX Community")
        self._name_lbl.setObjectName("checklistTitle")
        self._date_lbl = QLabel("сейчас")
        self._date_lbl.setObjectName("postDate")
        meta_col.addWidget(self._name_lbl)
        meta_col.addWidget(self._date_lbl)

        more_btn = QPushButton("···")
        more_btn.setObjectName("postMoreBtn")
        more_btn.setFixedSize(28, 28)

        header_layout.addWidget(self._avatar_lbl)
        header_layout.addLayout(meta_col)
        header_layout.addStretch()
        header_layout.addWidget(more_btn, alignment=Qt.AlignmentFlag.AlignTop)

        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._post_widget = QWidget()
        self._post_widget.setObjectName("postCard")
        post_layout = QVBoxLayout(self._post_widget)
        post_layout.setContentsMargins(0, 0, 0, 0)
        post_layout.setSpacing(0)

        # ── Изображение ───────────────────────────────────────────────
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._original_pixmap = QPixmap()
        self._apply_placeholder_style()

        # ── Текст поста ───────────────────────────────────────────────
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setObjectName("postText")
        self.preview_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.preview_text.document().contentsChanged.connect(self._adjust_text_height)

        # ── Строка реакций ────────────────────────────────────────────
        reactions_widget = QWidget()
        reactions_widget.setObjectName("postReactions")
        reactions_widget.setFixedHeight(28)
        reactions_layout = QHBoxLayout(reactions_widget)
        reactions_layout.setContentsMargins(12, 2, 12, 2)
        reactions_layout.setSpacing(16)

        like_lbl = QLabel("♡  24")
        like_lbl.setObjectName("reactionItem")
        comment_lbl = QLabel("💬  3")
        comment_lbl.setObjectName("reactionItem")
        share_lbl = QLabel("↗")
        share_lbl.setObjectName("reactionItem")

        reactions_layout.addWidget(like_lbl)
        reactions_layout.addWidget(comment_lbl)
        reactions_layout.addStretch()
        reactions_layout.addWidget(share_lbl)

        post_layout.addWidget(self.image_label)
        post_layout.addWidget(self.preview_text)
        post_layout.addWidget(reactions_widget)

        scroll.setWidget(self._post_widget)
        outer.addWidget(scroll)

    def set_platform_avatar(self, platform: str, assets_dir: "Path") -> None:
        if platform == "vk":
            ico_path = assets_dir / "vk_group.ico"
            name = "ВКонтакте"
        else:
            ico_path = assets_dir / "max.ico"
            name = "MAX Community"
        self._name_lbl.setText(name)
        if ico_path.exists():
            self._avatar_lbl.setPixmap(QIcon(str(ico_path)).pixmap(QSize(28, 28)))
        else:
            self._avatar_lbl.setPixmap(QPixmap())

    def _apply_placeholder_style(self) -> None:
        nophoto = _assets_dir() / "nophoto.png"
        if nophoto.exists():
            self._original_pixmap = QPixmap(str(nophoto))
            self.image_label.setStyleSheet("background: #f0f2f5;")
            self.image_label.setText("")
            self._refresh_pixmap()
        else:
            self._original_pixmap = QPixmap()
            self.image_label.setStyleSheet("background:#f0f2f5; color:#b0b8c1; font-size:13px;")
            self.image_label.setText("Изображение не выбрано")
            self.image_label.setPixmap(QPixmap())

    def set_image(self, file_path: str | None) -> None:
        if not file_path:
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
            w, 110,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setFixedHeight(scaled.height())
        self.image_label.setPixmap(scaled)
        self.image_label.setText("")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _adjust_text_height(self) -> None:
        doc_h = int(self.preview_text.document().size().height()) + 20
        self.preview_text.setMinimumHeight(doc_h)

    def set_preview_text(self, text: str) -> None:
        self.preview_text.setPlainText(text)


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
        self.image_path: Path | None = None
        self._worker: SendWorker | None = None
        self._bg_index: int | None = None
        self._bg_mode: int = 0  # 0 = фон, 1 = наложение
        self._bg_opacity: int = 50
        self._bg_widget: _BgWidget | None = None

        # Шрифты интерфейса — загружаются отложено после показа окна
        self._ui_font_family: str = ""
        self._ui_font_size: int = 13
        self._ui_font_families: list[str] = []
        self._pending_font_family: str = ""
        self._pending_font_size: int = 0

        # Кэшированный ExcelMatcher — читает Excel только один раз
        self._matcher: ExcelMatcher | None = (
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

        # Прогресс-бар (скрыт в режиме ожидания)
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

        def _load_icon(name: str) -> QPixmap | None:
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
        self.setStyleSheet("""
            #maxPosterContent { background: #f3f4f6; }
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
                subcontrol-position: top center;
                padding: 0 6px;
            }
            #sidePanel {
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #ffffff;
                margin-top: 0;
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
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #ffffff;
            }
            #postCard {
                background: #ffffff;
            }
            #postText {
                border: none;
                background: #ffffff;
                font-size: 14px;
                color: #1a1a1a;
                padding: 10px 2px;
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
            #previewTitle {
                font-size: 13px;
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
            #histDate { font-size: 11px; color: #b0b8c4; }
            #histText { font-size: 12px; color: #4a5568; }
            #histPlatformFallback { font-size: 11px; color: #6b7280; font-weight: 600; }
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
                alternate-background-color: #f8f9fb;
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
            QPushButton#fontMiniBtn {
                min-height: 0;
                font-size: 12px;
                font-weight: 700;
                padding: 2px 10px;
                border-radius: 6px;
                border: 1px solid #c7d0db;
                background: #f3f4f6;
                color: #555;
            }
            QPushButton#fontMiniBtn:hover {
                background: #eef4ff;
                border-color: #2d6cdf;
                color: #2d6cdf;
            }
            /* ── Секция платформ ──────────────────────────────────── */
            #sectionTitle {
                font-size: 12px;
                font-weight: 600;
                color: #6b7280;
                letter-spacing: 0.3px;
            }
            #platformChip {
                border: 1px solid #d1d9e0;
                border-radius: 8px;
                background: #f8fafc;
            }
            #platformChip QCheckBox {
                font-size: 13px;
                color: #1f2937;
                spacing: 6px;
            }
            /* ── Шапка поста (preview) ───────────────────────────── */
            #postHeader {
                background: #f8fafc;
                border-bottom: 1px solid #f0f4f8;
            }
            #postAvatar {
                background: #3b82f6;
                color: white;
                font-size: 16px;
                font-weight: 700;
                border-radius: 18px;
            }
            #postAuthor {
                font-size: 13px;
                font-weight: 600;
                color: #1f2937;
            }
            #postDate {
                font-size: 11px;
                color: #9ca3af;
            }
            QPushButton#postMoreBtn {
                min-height: 0;
                font-size: 16px;
                color: #9ca3af;
                background: transparent;
                border: none;
                padding: 0;
                letter-spacing: 2px;
            }
            QPushButton#postMoreBtn:hover {
                color: #6b7280;
            }
            /* ── Реакции поста ────────────────────────────────────── */
            #postReactions {
                background: #ffffff;
                border-top: 1px solid #f0f4f8;
            }
            #reactionItem {
                font-size: 11px;
                color: #9ca3af;
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
                print(f"[warn] find_matches failed for {parsed!r}: {exc}", file=sys.stderr)
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

    def _apply_theme(self, index: int | None, mode: int = 0, opacity_pct: int = 50, save: bool = True) -> None:
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

    def _on_send_finished(self, success: bool, message: str) -> None:
        self.send_button.setEnabled(True)
        self.send_button.setText("Опубликовать")
        self.check_button.setEnabled(True)
        self._progress_bar.hide()
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
            QMessageBox.critical(self, "Отправка", message)

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
                print(f"[warn] find_matches failed for {parsed!r}: {exc}", file=sys.stderr)
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
    QTimer.singleShot(2000, lambda: check_for_updates(window))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
