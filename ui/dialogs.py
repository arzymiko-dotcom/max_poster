"""Диалоговые окна: ThemePickerDialog, FontPickerDialog, AddAddressDialog, VkEditDialog."""

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from excel_matcher import MatchResult


class ThemePickerDialog(QDialog):
    """Диалог выбора фонового изображения."""

    _COLS = 4
    preview_changed = pyqtSignal(object, int, int)  # (int | None index, int mode, int opacity_pct)

    def __init__(
        self,
        assets_dir: Path,
        current_index: "int | None",
        current_mode: int = 0,
        current_opacity: int = 50,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Тема оформления")
        self._selected: "int | None" = current_index
        self._mode: int = current_mode
        self._opacity_pct: int = current_opacity
        self._btns: "list[tuple[int | None, QPushButton]]" = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Выберите фоновое изображение:"))

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(8)

        items: "list[int | None]" = [None] + list(range(1, 12))
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

    def selected_index(self) -> "int | None":
        return self._selected

    def selected_mode(self) -> int:
        return self._mode

    def selected_opacity(self) -> int:
        return self._opacity_pct

    def _select(self, index: "int | None") -> None:
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

    def result_match(self) -> MatchResult:
        address = self._address_edit.text().strip()
        url = self._url_edit.text().strip()
        chat_id = self._id_edit.text().strip()
        if url and not chat_id and "web.max.ru/" in url:
            extracted = url.split("web.max.ru/")[-1].strip("/")
            if extracted:
                chat_id = extracted
        return MatchResult(address=address, score=0, chat_link=url, chat_id=chat_id)


class VkEditDialog(QDialog):
    """Диалог редактирования опубликованного поста ВКонтакте."""

    def __init__(self, post_id: int, current_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Редактировать пост ВКонтакте  (ID {post_id})")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self._post_id = post_id
        self._new_image_path: "Path | None" = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # Подсказка
        hint = QLabel(
            "Текст загружен из истории (может быть сокращён).\n"
            "Нажмите «Загрузить из ВК» чтобы получить оригинал."
        )
        hint.setStyleSheet("color: #7a8a9a; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Поле текста
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlainText(current_text)
        self._text_edit.setPlaceholderText("Введите новый текст поста…")
        layout.addWidget(self._text_edit, 1)

        # Строка фото
        photo_row = QHBoxLayout()
        self._photo_lbl = QLabel("Фото: не выбрано (существующее останется)")
        self._photo_lbl.setStyleSheet("color: #7a8a9a; font-size: 11px;")
        photo_btn = QPushButton("Заменить фото…")
        photo_btn.setFixedHeight(28)
        photo_btn.clicked.connect(self._select_photo)
        photo_row.addWidget(self._photo_lbl, 1)
        photo_row.addWidget(photo_btn)
        layout.addLayout(photo_row)

        # Кнопки диалога
        btn_box = QDialogButtonBox()
        self._load_btn = btn_box.addButton("Загрузить из ВК", QDialogButtonBox.ButtonRole.ResetRole)
        self._load_btn.setToolTip("Загрузить текущий текст поста из ВКонтакте")
        btn_box.addButton("Сохранить изменения", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        # «Загрузить из ВК» — обрабатываем снаружи через сигнал
        self._load_btn.clicked.connect(self._request_load)
        layout.addWidget(btn_box)

    def _select_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите фото", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self._new_image_path = Path(path)
            self._photo_lbl.setText(f"Фото: {Path(path).name}")
            self._photo_lbl.setStyleSheet("color: #22a35a; font-size: 11px;")

    def _request_load(self) -> None:
        """Сигнализирует главному окну загрузить оригинальный текст из ВК."""
        self._load_btn.setEnabled(False)
        self._load_btn.setText("Загружается…")
        self.load_requested.emit()

    load_requested = pyqtSignal()

    def set_loaded_text(self, text: str) -> None:
        """Вызывается после успешной загрузки текста из ВК."""
        self._text_edit.setPlainText(text)
        self._load_btn.setEnabled(True)
        self._load_btn.setText("Загружено ✓")

    def post_id(self) -> int:
        return self._post_id

    def new_text(self) -> str:
        return self._text_edit.toPlainText().strip()

    def new_image_path(self) -> "Path | None":
        return self._new_image_path
