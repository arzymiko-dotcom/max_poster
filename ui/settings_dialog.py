"""Диалог настроек подключений (токены MAX, ВКонтакте)."""
import re
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from env_utils import get_env_path, load_env_safe, read_env_text


# ── Воркеры для проверки токенов ──────────────────────────────

class _VkCheckWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, token: str, parent=None):
        super().__init__(parent)
        self._token = token

    def run(self):
        import requests
        try:
            r = requests.post(
                "https://api.vk.com/method/users.get",
                data={"access_token": self._token, "v": "5.199"},
                timeout=8,
            )
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                code = data["error"].get("error_code", "?")
                msg  = data["error"].get("error_msg", "")
                self.done.emit(False, f"Ошибка [{code}]: {msg}")
            else:
                name = data["response"][0].get("first_name", "")
                self.done.emit(True, f"OK — {name}")
        except Exception as e:
            self.done.emit(False, str(e))


class _MaxCheckWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, instance: str, token: str, parent=None):
        super().__init__(parent)
        self._instance = instance
        self._token = token

    def run(self):
        import requests
        try:
            url = f"https://api.green-api.com/waInstance{self._instance}/getStateInstance/{self._token}"
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            data = r.json()
            state = data.get("stateInstance", "")
            if state == "authorized":
                self.done.emit(True, "OK — авторизован")
            else:
                self.done.emit(False, f"Статус: {state or 'неизвестен'}")
        except Exception as e:
            self.done.emit(False, str(e))


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = get_env_path()
    if not path.exists():
        return values
    for line in read_env_text(path).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" in s:
            k, _, v = s.partition("=")
            values[k.strip()] = v.strip()
    return values


def _write_env(updates: dict[str, str]) -> None:
    import tempfile
    path = get_env_path()
    lines = read_env_text(path).splitlines() if path.exists() else []
    done: set[str] = set()
    result = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            result.append(line)
            continue
        if "=" in s:
            k = s.partition("=")[0].strip()
            if k in updates:
                result.append(f"{k}={updates[k]}")
                done.add(k)
                continue
        result.append(line)
    for k, v in updates.items():
        if k not in done:
            result.append(f"{k}={v}")
    content = "\n".join(result) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


class _SecretEdit(QWidget):
    """Поле ввода токена с кнопкой показа/скрытия."""

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._edit = QLineEdit(value)
        self._edit.setEchoMode(QLineEdit.EchoMode.Password)

        btn = QPushButton("👁")
        btn.setFixedWidth(30)
        btn.setCheckable(True)
        btn.setToolTip("Показать / скрыть")
        btn.setStyleSheet("QPushButton { border: 1px solid #ccc; border-radius: 4px; }")
        btn.toggled.connect(
            lambda on: self._edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )

        layout.addWidget(self._edit)
        layout.addWidget(btn)

    def text(self) -> str:
        return self._edit.text()


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #888; font-size: 11px;")
    lbl.setWordWrap(True)
    lbl.setOpenExternalLinks(True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    return lbl


class SettingsDialog(QDialog):
    """Диалог настроек подключений."""

    settings_saved = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки подключений")
        self.setMinimumWidth(520)

        vals = _read_env()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        # ── MAX / GREEN-API ──────────────────────────────────────
        max_group = QGroupBox("MAX / GREEN-API")
        max_form = QFormLayout(max_group)
        max_form.setContentsMargins(12, 8, 12, 12)
        max_form.setSpacing(8)
        self._max_instance = QLineEdit(vals.get("MAX_ID_INSTANCE", ""))
        self._max_instance.setPlaceholderText("например: 3100545725")
        self._max_token = _SecretEdit(vals.get("MAX_API_TOKEN", ""))
        max_form.addRow("ID инстанса:", self._max_instance)
        max_form.addRow("API токен:", self._max_token)

        # Кнопка проверки MAX токена
        self._max_check_btn = QPushButton("✓ Проверить")
        self._max_check_btn.setFixedWidth(110)
        self._max_check_btn.setToolTip("Проверить подключение к GREEN-API")
        self._max_check_btn.clicked.connect(self._check_max_token)
        self._max_check_status = QLabel("")
        self._max_check_status.setStyleSheet("font-size: 11px;")
        max_check_row = QHBoxLayout()
        max_check_row.addWidget(self._max_check_btn)
        max_check_row.addWidget(self._max_check_status)
        max_check_row.addStretch()
        max_form.addRow("", max_check_row)

        self._send_delay = QSpinBox()
        self._send_delay.setRange(0, 30)
        self._send_delay.setSuffix(" сек")
        self._send_delay.setValue(int(vals.get("SEND_DELAY_SEC", "0") or 0))
        self._send_delay.setToolTip(
            "Дополнительная пауза между отправками поверх случайной задержки 5–12 сек"
        )
        max_form.addRow("Пауза между сообщениями:", self._send_delay)

        max_form.addRow(
            "", _hint_label(
                "Получить данные: личный кабинет "
                "<a href='https://console.green-api.com/'>console.green-api.com</a> "
                "→ выберите инстанс → скопируйте ID и API токен."
            )
        )

        # ── ВКонтакте ────────────────────────────────────────────
        vk_group = QGroupBox("ВКонтакте")
        vk_form = QFormLayout(vk_group)
        vk_form.setContentsMargins(12, 8, 12, 12)
        vk_form.setSpacing(8)
        self._vk_group_id = QLineEdit(vals.get("VK_GROUP_ID", ""))
        self._vk_group_id.setPlaceholderText("только цифры, без минуса")
        self._vk_group_token = _SecretEdit(vals.get("VK_GROUP_TOKEN", ""))
        self._vk_user_token = _SecretEdit(vals.get("VK_USER_TOKEN", ""))
        vk_form.addRow("ID группы:", self._vk_group_id)
        vk_form.addRow("Токен группы:", self._vk_group_token)
        vk_form.addRow("Токен пользователя:", self._vk_user_token)

        # Кнопки: получить токен + проверить
        _VK_TOKEN_URL = (
            "https://oauth.vk.com/authorize?client_id=2685278"
            "&scope=wall,photos,offline"
            "&redirect_uri=https://oauth.vk.com/blank.html"
            "&response_type=token&v=5.199"
        )
        get_token_btn = QPushButton("🔗 Получить токен VK")
        get_token_btn.setToolTip("Открыть браузер для получения токена пользователя")
        get_token_btn.clicked.connect(lambda: __import__("webbrowser").open(_VK_TOKEN_URL))

        self._vk_check_btn = QPushButton("✓ Проверить")
        self._vk_check_btn.setFixedWidth(110)
        self._vk_check_btn.setToolTip("Проверить валидность токена пользователя VK")
        self._vk_check_btn.clicked.connect(self._check_vk_token)
        self._vk_check_status = QLabel("")
        self._vk_check_status.setStyleSheet("font-size: 11px;")

        vk_btn_row = QHBoxLayout()
        vk_btn_row.addWidget(get_token_btn)
        vk_btn_row.addWidget(self._vk_check_btn)
        vk_btn_row.addWidget(self._vk_check_status)
        vk_btn_row.addStretch()
        vk_form.addRow("", vk_btn_row)

        vk_form.addRow(
            "", _hint_label(
                "<b>Токен группы</b> (для постов от имени группы): "
                "vk.com → Управление → API → Создать ключ доступа.<br>"
                "<b>Токен пользователя</b> (для загрузки фото к постам): "
                "нажмите «Получить токен VK» выше."
            )
        )

        # ── Основная группа МАХ ──────────────────────────────────
        pin_group = QGroupBox("Основная группа МАХ (📌 закреплена в списке адресов)")
        pin_form = QFormLayout(pin_group)
        pin_form.setContentsMargins(12, 8, 12, 12)
        pin_form.setSpacing(8)
        self._pin_group_id   = QLineEdit(vals.get("MAX_MAIN_GROUP_ID", ""))
        self._pin_group_id.setPlaceholderText("например: -68787567064560")
        self._pin_group_name = QLineEdit(vals.get("MAX_MAIN_GROUP_NAME", ""))
        self._pin_group_name.setPlaceholderText("Название для отображения")
        self._pin_group_link = QLineEdit(vals.get("MAX_MAIN_GROUP_LINK", ""))
        self._pin_group_link.setPlaceholderText("https://max.ru/gks2vyb")
        pin_form.addRow("ID группы:", self._pin_group_id)
        pin_form.addRow("Название:", self._pin_group_name)
        pin_form.addRow("Ссылка:", self._pin_group_link)
        pin_form.addRow("", _hint_label(
            "Оставьте ID пустым, чтобы убрать закреплённую группу."
        ))

        layout.addWidget(max_group)
        layout.addWidget(pin_group)
        layout.addWidget(vk_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self._error = QLabel("")
        self._error.setStyleSheet("color: #e05555;")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> str | None:
        """Возвращает текст ошибки или None если всё ок."""
        vk_id = self._vk_group_id.text().strip()
        if vk_id and not re.fullmatch(r"\d+", vk_id):
            return "VK ID группы — только цифры (например: 236573184)"

        max_instance = self._max_instance.text().strip()
        if max_instance and not re.fullmatch(r"\d+", max_instance):
            return "MAX ID инстанса — только цифры (например: 3100545725)"

        return None

    def _save(self) -> None:
        error = self._validate()
        if error:
            self._error.setText(error)
            return
        self._error.setText("")
        _write_env({
            "MAX_ID_INSTANCE":    self._max_instance.text().strip(),
            "MAX_API_TOKEN":      self._max_token.text().strip(),
            "MAX_MAIN_GROUP_ID":  self._pin_group_id.text().strip(),
            "MAX_MAIN_GROUP_NAME": self._pin_group_name.text().strip(),
            "MAX_MAIN_GROUP_LINK": self._pin_group_link.text().strip(),
            "VK_GROUP_ID":        self._vk_group_id.text().strip(),
            "VK_GROUP_TOKEN":     self._vk_group_token.text().strip(),
            "VK_USER_TOKEN":      self._vk_user_token.text().strip(),
            "SEND_DELAY_SEC":     str(self._send_delay.value()),
        })
        load_env_safe(get_env_path(), override=True)
        self.settings_saved.emit()
        self.accept()

    def _check_vk_token(self) -> None:
        token = self._vk_user_token.text().strip()
        if not token:
            self._vk_check_status.setText("Токен не задан")
            self._vk_check_status.setStyleSheet("color: #e05555; font-size: 11px;")
            return
        self._vk_check_btn.setEnabled(False)
        self._vk_check_btn.setText("Проверяем…")
        self._vk_check_status.setText("")
        worker = _VkCheckWorker(token, self)
        worker.done.connect(self._on_vk_check_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_vk_check_done(self, ok: bool, msg: str) -> None:
        self._vk_check_btn.setEnabled(True)
        self._vk_check_btn.setText("✓ Проверить")
        color = "#2a7a2a" if ok else "#e05555"
        self._vk_check_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._vk_check_status.setText(msg)

    def _check_max_token(self) -> None:
        instance = self._max_instance.text().strip()
        token = self._max_token.text().strip()
        if not instance or not token:
            self._max_check_status.setText("ID инстанса и токен обязательны")
            self._max_check_status.setStyleSheet("color: #e05555; font-size: 11px;")
            return
        self._max_check_btn.setEnabled(False)
        self._max_check_btn.setText("Проверяем…")
        self._max_check_status.setText("")
        worker = _MaxCheckWorker(instance, token, self)
        worker.done.connect(self._on_max_check_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_max_check_done(self, ok: bool, msg: str) -> None:
        self._max_check_btn.setEnabled(True)
        self._max_check_btn.setText("✓ Проверить")
        color = "#2a7a2a" if ok else "#e05555"
        self._max_check_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._max_check_status.setText(msg)
