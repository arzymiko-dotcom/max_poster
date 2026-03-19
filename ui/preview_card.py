"""Карточка предпросмотра поста."""

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.paths import _assets_dir


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
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._refresh_pixmap)
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

    def set_image(self, file_path: "str | None") -> None:
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
        self._resize_timer.start()  # дебаунс 50 мс — не пересчитываем на каждый пиксель

    def _adjust_text_height(self) -> None:
        doc_h = int(self.preview_text.document().size().height()) + 20
        self.preview_text.setMinimumHeight(doc_h)

    def set_preview_text(self, text: str) -> None:
        self.preview_text.setPlainText(text)
