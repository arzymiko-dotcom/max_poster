"""Виджет выбора эмодзи."""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.paths import _emoji_icon


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
