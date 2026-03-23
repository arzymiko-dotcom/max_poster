import io
import sys
import os
import json
import zipfile
import tempfile
import subprocess
import time
import urllib.request
from datetime import date
from pathlib import Path
import qrcode
import requests
from PIL import Image as PilImage, ImageDraw
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QLineEdit, QComboBox, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QFileDialog, QFrame, QCheckBox,
    QGraphicsOpacityEffect, QMessageBox
)
from PyQt6.QtGui import (
    QPixmap, QFont, QFontDatabase, QPainter, QColor,
    QFontMetrics, QLinearGradient, QBrush, QIcon
)
from PyQt6.QtCore import (
    Qt, QRect, QSettings, QThread, pyqtSignal,
    QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup,
    QBuffer, QIODevice,
)

if __name__ == '__main__' and sys.platform == "win32":
    import ctypes
    ctypes.windll.user32.ShowWindow(
        ctypes.windll.kernel32.GetConsoleWindow(), 0)

# ========== TELEGRAM ERROR REPORTING ==========
from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_base_dir, '.env'))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(text):
    """Отправляет сообщение в Telegram, обрезая при необходимости."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:4000] + "\n... (обрезано)"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=data, timeout=5)
    except Exception:
        pass  # не мешаем работе программы

def global_excepthook(exc_type, exc_value, exc_traceback):
    """Глобальный обработчик необработанных исключений."""
    import traceback
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
    tb_text = ''.join(tb_lines)
    send_telegram_message(f"❌ <b>Необработанная ошибка</b>\n<pre>{tb_text}</pre>")
    # Вызываем стандартный обработчик для завершения программы
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

if __name__ == '__main__':
    sys.excepthook = global_excepthook
# ==============================================

# ══════════════════════════════════════════════════════════════════
#  АВТООБНОВЛЕНИЕ
# ══════════════════════════════════════════════════════════════════
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN")
GITHUB_USER     = "arzymiko-dotcom"
GITHUB_REPO     = "qr-generator-updates"
YADISK_PUBLIC_URL = "https://disk.yandex.ru/d/9fwFZoSshbcqNg"


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_current_version() -> str:
    try:
        return _read_text_file(os.path.join(_base_dir, "version.txt"))
    except Exception:
        return "0.0"


def _get_yadisk_direct_link(public_url):
    api = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={public_url}"
    req = urllib.request.Request(api, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise Exception(f"Яндекс Диск вернул неверный ответ: {e}") from e
    if "href" not in data:
        raise Exception(f"Яндекс Диск не вернул ссылку для скачивания: {data}")
    return data["href"]


def _download_file(url, dest_path, progress_callback=None):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total_size = None
        if 'Content-Length' in resp.headers:
            total_size = int(resp.headers['Content-Length'])
        downloaded = 0
        chunk_size = 1048576
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total_size:
                    percent = int(downloaded * 100 / total_size)
                    progress_callback(percent)


class UpdateChecker(QThread):
    """Проверяет версию в фоне, не блокируя GUI."""
    update_available = pyqtSignal(str, str)  # (latest_version, current_version)

    def run(self):
        try:
            current = get_current_version()
            if GITHUB_TOKEN:
                url = (f"https://api.github.com/repos/{GITHUB_USER}/"
                       f"{GITHUB_REPO}/contents/version.txt")
                req = urllib.request.Request(url, headers={
                    "Authorization": f"token {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3.raw"
                })
            else:
                url = (f"https://raw.githubusercontent.com/{GITHUB_USER}/"
                       f"{GITHUB_REPO}/main/version.txt")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                latest = resp.read().decode("utf-8", errors="replace").strip()
            if latest != current:
                self.update_available.emit(latest, current)
        except Exception:
            pass  # обновление не обязательно для работы программы


def _download_and_install(parent=None):
    """Скачивает и запускает installer. Вызывается из главного потока."""
    from PyQt6.QtWidgets import QMessageBox, QProgressDialog
    progress = QProgressDialog("Скачиваем обновление...", None, 0, 100, parent)
    progress.setWindowTitle("Обновление")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setCancelButton(None)
    progress.show()
    QApplication.processEvents()

    def update_progress(percent):
        progress.setValue(percent)
        progress.setLabelText(f"Скачиваем обновление... {percent}%")
        QApplication.processEvents()

    try:
        direct_url = _get_yadisk_direct_link(YADISK_PUBLIC_URL)
        tmp_dir  = tempfile.gettempdir()
        tmp_file = os.path.join(tmp_dir, "QR_update_download.tmp")
        _download_file(direct_url, tmp_file, update_progress)

        progress.setLabelText("Распаковка...")
        QApplication.processEvents()

        if zipfile.is_zipfile(tmp_file):
            with zipfile.ZipFile(tmp_file, "r") as z:
                exe_names = [n for n in z.namelist() if n.lower().endswith(".exe")]
                if not exe_names:
                    raise Exception("EXE не найден в архиве")
                # Проверяем путь ДО извлечения (защита от path traversal)
                safe_path = os.path.abspath(os.path.join(tmp_dir, exe_names[0]))
                if not safe_path.startswith(os.path.abspath(tmp_dir)):
                    raise Exception("Небезопасный путь в архиве")
                z.extract(exe_names[0], tmp_dir)
                exe_path = safe_path
        else:
            exe_path = os.path.join(tmp_dir, "QR_Generator_MAX_Setup.exe")
            if os.path.exists(exe_path):
                os.remove(exe_path)
            os.rename(tmp_file, exe_path)

        progress.close()
        # Удаляем временный файл загрузки
        try:
            if os.path.exists(tmp_file) and tmp_file != exe_path:
                os.remove(tmp_file)
        except OSError:
            pass
        time.sleep(1)
        subprocess.Popen([exe_path])
        QApplication.quit()
    except Exception as e:
        progress.close()
        QMessageBox.warning(parent, "Ошибка", f"Не удалось скачать обновление:\n{e}")
        send_telegram_message(f"⚠️ Ошибка при скачивании обновления:\n{str(e)}")
# ══════════════════════════════════════════════════════════════════


def res(filename):
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'assets', filename)


def wrap_text_by_px(text, font, max_px):
    fm = QFontMetrics(font)
    result = []
    current = ''
    for ch in text:
        if ch == '\n':
            result.append(current)
            current = ''
            continue
        if fm.horizontalAdvance(current + ch) > max_px and current:
            result.append(current)
            current = ch
        else:
            current += ch
    result.append(current)
    return '\n'.join(result)


def make_qr_with_logo(url, logo_path, out_path):
    qr = qrcode.QRCode(
        version=3,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color='black', back_color='white').convert('RGBA')
    qr_w, qr_h = qr_img.size

    zone = int(qr_w * 0.27)
    px = (qr_w - zone) // 2
    py = (qr_h - zone) // 2

    draw = ImageDraw.Draw(qr_img)
    draw.rectangle([px - 4, py - 4, px + zone + 4, py + zone + 4],
                   fill=(255, 255, 255, 255))

    if os.path.exists(logo_path):
        try:
            logo = PilImage.open(logo_path).convert('RGBA')
            pad = int(zone * 0.06)
            logo_size = zone - pad * 2
            logo = logo.resize((logo_size, logo_size), PilImage.Resampling.LANCZOS)
            qr_img.paste(logo, (px + pad, py + pad), logo)
        except Exception as e:
            print(f"Ошибка вставки логотипа: {e}")

    qr_img.save(out_path)
    return qr_img


def crop_transparent(pixmap):
    """Обрезает прозрачные края. PIL в ~100× быстрее чем per-pixel Qt API."""
    ba = pixmap.toImage()
    if ba.isNull():
        return pixmap
    # Сохраняем QImage → bytes → PIL
    qbuf = QBuffer()
    qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
    ba.save(qbuf, "PNG")
    qbuf.close()
    pil_img = PilImage.open(io.BytesIO(qbuf.data().data()))
    bbox = pil_img.getchannel('A').getbbox()  # bbox по альфа-каналу (~4× быстрее split)
    if bbox is None:
        return pixmap
    cropped = pil_img.crop(bbox)
    out = io.BytesIO()
    cropped.save(out, "PNG")
    result = QPixmap()
    result.loadFromData(out.getvalue())
    return result


class TitleTextEdit(QTextEdit):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText('\n')
            else:
                pass
        else:
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QR Генератор")
        self.setMinimumSize(900, 600)
        self.showMaximized()
        self.tmp_qr = os.path.join(tempfile.gettempdir(), '_qr_gen_tmp.png')
        self.font_size = 18
        self._last_save_folder: str | None = None

        self.settings = QSettings("MAX", "QRGeneratorMAX")

        self.setWindowIcon(QIcon(res('max.targetsize-256.png')))

        try:
            QFontDatabase.addApplicationFont(res('vk-sans-display-medium-ldNirGrX.ttf'))
            QFontDatabase.addApplicationFont(res('VKSansDisplay-Regular-CTMBbjTz.ttf'))
        except Exception:
            pass  # шрифты не критичны — упадём на системный

        self._build_ui()

        self.inp_title.setPlainText(self.settings.value("title", ""))
        self.inp_url.setText(self.settings.value("url", ""))
        self.inp_region.setCurrentText(self.settings.value("region", "Введите название региона из списка"))
        self.inp_org.setCurrentText(self.settings.value("org", "Введите наименование организации"))
        self.chk_show_org.setChecked(self.settings.value("show_org", False, type=bool))

        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    def _build_ui(self):
        root_widget = BgWidget(res('background_2.png'))
        self.setCentralWidget(root_widget)

        outer = QHBoxLayout(root_widget)
        outer.setContentsMargins(30, 30, 30, 30)
        outer.setSpacing(24)

        card = QFrame()
        card.setStyleSheet("QFrame { background-color: white; border-radius: 18px; }")
        card.setFixedWidth(500)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(34, 28, 34, 28)
        cl.setSpacing(8)

        # Поле 1 + выбор размера шрифта
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        icon_lbl = QLabel()
        px = QPixmap(res('number-one.png'))
        if not px.isNull():
            icon_lbl.setPixmap(px.scaled(26, 26,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        icon_lbl.setFixedSize(26, 26)
        icon_lbl.setStyleSheet('background: transparent;')
        lbl_title = QLabel('Введите заголовок')
        lbl_title.setFont(QFont('VK Sans Display', 13, QFont.Weight.Bold))
        lbl_title.setStyleSheet('color: #111133; background: transparent;')
        lbl_size = QLabel('Размер:')
        lbl_size.setFont(QFont('VK Sans Display', 10))
        lbl_size.setStyleSheet('color: #555577; background: transparent;')
        self.font_combo = QComboBox()
        self.font_combo.addItems([str(s) for s in range(12, 101, 2)])
        self.font_combo.setCurrentText('18')
        self.font_combo.setFixedWidth(70)
        self.font_combo.setFixedHeight(30)
        self.font_combo.setStyleSheet("""
            QComboBox {
                background: #f0eeff; border: 1.5px solid #d0cfe8;
                border-radius: 8px; padding: 2px 8px;
                color: #333355; font-size: 12px; font-weight: bold;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background: white; border: 1px solid #d0cfe8;
                selection-background-color: #eeeeff; color: #222244;
            }
        """)
        self.font_combo.currentTextChanged.connect(self._on_font_size_changed)
        title_row.addWidget(icon_lbl)
        title_row.addWidget(lbl_title)
        title_row.addStretch()
        title_row.addWidget(lbl_size)
        title_row.addWidget(self.font_combo)
        cl.addLayout(title_row)

        cl.addWidget(self._hint('текст до :: — градиент,  после :: — белый'))
        cl.addWidget(self._hint('Shift+Enter — перенос строки'))

        self.inp_title = TitleTextEdit()
        self.inp_title.setPlaceholderText('Введите текст')
        self.inp_title.setFixedHeight(85)
        self.inp_title.setFont(QFont('VK Sans Display', 11))
        self.inp_title.setStyleSheet("""
            QTextEdit {
                background: white; border: 1.5px solid #d8d6f0;
                border-radius: 10px; padding: 8px 14px;
                color: #222244; font-style: italic;
            }
            QTextEdit:focus { border: 1.5px solid #4a6cf7; }
        """)
        self.inp_title.textChanged.connect(
            lambda: self.preview.set_title(self.inp_title.toPlainText()))
        cl.addWidget(self.inp_title)
        cl.addSpacing(8)

        cl.addLayout(self._label_row('number-2.png', 'Вставьте ссылку на чат'))
        cl.addWidget(self._hint('нажмите на аватар чата или канала и скопируйте ссылку'))
        self.inp_url = self._input('Введите ссылку на чат или чат бот')
        self.inp_url.textChanged.connect(self._set_btn_create_state)
        cl.addWidget(self.inp_url)
        cl.addSpacing(8)

        cl.addLayout(self._label_row('number-3.png', 'Выберите регион'))
        cl.addWidget(self._hint('список регионов'))
        self.inp_region = self._combo([
            'Введите название региона из списка',
            'Санкт-Петербург',
        ])
        cl.addWidget(self.inp_region)
        cl.addSpacing(8)

        cl.addLayout(self._label_row('number-4.png', 'Укажите наименование учреждения'))
        self.inp_org = self._combo([
            'Введите наименование организации',
            'ООО "ЖКС №2 ВЫБОРГСКОГО РАЙОНА"',
        ])
        cl.addWidget(self.inp_org)

        self.chk_show_org = QCheckBox('Показывать наименование на карточке')
        self.chk_show_org.setFont(QFont('VK Sans Display', 10))
        self.chk_show_org.setStyleSheet("""
            QCheckBox {
                color: #444466;
                background: transparent;
                margin-left: 4px;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1.5px solid #aaaacc;
                border-radius: 4px;
                background: white;
            }
            QCheckBox::indicator:checked {
                background: #4a6cf7;
                border: 1.5px solid #4a6cf7;
            }
        """)
        self.chk_show_org.toggled.connect(self._update_preview_org)
        self.inp_org.currentTextChanged.connect(self._update_preview_org)
        cl.addWidget(self.chk_show_org)
        cl.addStretch()

        # Версия внизу карточки
        self.lbl_version = QLabel(f'version {get_current_version()}')
        self.lbl_version.setFont(QFont('VK Sans Display', 9))
        self.lbl_version.setStyleSheet(
            'color: #bbbbcc; background: transparent; margin-left: 4px;')
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.addWidget(self.lbl_version)
        bottom_row.addStretch(2)
        self.lbl_copyright = QLabel(f'Все права защищены MAX © {date.today().year}')
        self.lbl_copyright.setFont(QFont('VK Sans Display', 8))
        self.lbl_copyright.setStyleSheet('color: #bbbbcc; background: transparent;')
        bottom_row.addWidget(self.lbl_copyright)
        bottom_row.addStretch()
        cl.addLayout(bottom_row)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.preview = PreviewCard(
            frame_path=res('editable-frame.png'),
            default_qr_path=res('preview.png')
        )
        self.preview.setMinimumSize(400, 380)
        right.addWidget(self.preview, stretch=1)

        lbl_prev = QLabel('Предпросмотр')
        lbl_prev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_prev.setStyleSheet(
            'color: rgba(255,255,255,0.6); font-size: 13px; background: transparent;')
        right.addWidget(lbl_prev)

        self._btn_state = 'create'
        self.btn_main = QPushButton('Создать')
        self.btn_main.setFixedHeight(60)
        self.btn_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_main.setFont(QFont('VK Sans Display', 20, QFont.Weight.Bold))
        self._btn_main_effect = QGraphicsOpacityEffect(self.btn_main)
        self._btn_main_effect.setOpacity(1.0)
        self.btn_main.setGraphicsEffect(self._btn_main_effect)
        self._apply_btn_create_style()
        self.btn_main.clicked.connect(self._on_main_btn_clicked)
        right.addWidget(self.btn_main)

        # Строка с результатом сохранения
        self._save_result_widget = QWidget()
        self._save_result_widget.setStyleSheet('background: transparent;')
        self._save_result_widget.setFixedHeight(36)
        _row = QHBoxLayout(self._save_result_widget)
        _row.setContentsMargins(0, 0, 0, 0)
        _row.setSpacing(6)
        self._lbl_saved = QLabel('')
        self._lbl_saved.setFont(QFont('VK Sans Display', 10))
        self._lbl_saved.setStyleSheet('color: rgba(255,255,255,0.85); background: transparent;')
        self._btn_open_folder = QPushButton('📂 Открыть папку')
        self._btn_open_folder.setFixedHeight(30)
        self._btn_open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_folder.setFont(QFont('VK Sans Display', 10))
        self._btn_open_folder.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.12);
                color: rgba(255,255,255,0.85);
                border: 1px solid rgba(255,255,255,0.25);
                border-radius: 8px; padding: 0 10px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.22); color: white; }
        """)
        self._btn_open_folder.clicked.connect(self._open_save_folder)
        _row.addWidget(self._lbl_saved, stretch=1)
        _row.addWidget(self._btn_open_folder)
        self._save_result_widget.setVisible(False)
        right.addWidget(self._save_result_widget)

        outer.addWidget(card)
        outer.addLayout(right, stretch=1)

    def _on_font_size_changed(self, val):
        try:
            self.font_size = int(val)
            self.preview.set_font_size(self.font_size)
        except ValueError:
            pass

    def _apply_btn_create_style(self):
        self.btn_main.setText('Создать')
        self.btn_main.setFont(QFont('VK Sans Display', 20, QFont.Weight.Bold))
        self.btn_main.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #4a6cf7, stop:1 #8b3cf7);
                color: white; border-radius: 14px; border: none;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #5a7cff, stop:1 #9b4cff);
            }
        """)

    def _apply_btn_save_style(self):
        self.btn_main.setText('⬇  Сохранить карточку (PNG)')
        self.btn_main.setFont(QFont('VK Sans Display', 15, QFont.Weight.Bold))
        self.btn_main.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #16a34a, stop:1 #15803d);
                color: white; border-radius: 14px; border: none;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #22c55e, stop:1 #16a34a);
            }
        """)

    def _on_main_btn_clicked(self):
        if self._btn_state == 'create':
            self.generate_qr()
        else:
            self.save_full_card()

    def _set_btn_save_state(self):
        """Плавный переход кнопки: Создать → Сохранить."""
        if self._btn_state == 'save':
            return
        anim_out = QPropertyAnimation(self._btn_main_effect, b"opacity")
        anim_out.setDuration(180)
        anim_out.setStartValue(1.0)
        anim_out.setEndValue(0.0)
        anim_out.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _swap():
            self._btn_state = 'save'
            self._apply_btn_save_style()
            anim_in = QPropertyAnimation(self._btn_main_effect, b"opacity")
            anim_in.setDuration(220)
            anim_in.setStartValue(0.0)
            anim_in.setEndValue(1.0)
            anim_in.setEasingCurve(QEasingCurve.Type.InCubic)
            anim_in.start()
            self._btn_anim_in = anim_in  # держим ссылку

        anim_out.finished.connect(_swap)
        anim_out.start()
        self._btn_anim_out = anim_out  # держим ссылку

    def _set_btn_create_state(self):
        """Мгновенный сброс кнопки обратно в «Создать»."""
        if self._btn_state == 'create':
            return
        self._btn_state = 'create'
        self._btn_main_effect.setOpacity(1.0)
        self._apply_btn_create_style()
        self._save_result_widget.setVisible(False)

    def _update_preview_org(self, *_):
        org = self.inp_org.currentText()
        show = self.chk_show_org.isChecked()
        self.preview.set_org(org, show)

    def _label_row(self, icon_file, text):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        px = QPixmap(res(icon_file))
        if not px.isNull():
            icon.setPixmap(px.scaled(26, 26,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        icon.setFixedSize(26, 26)
        icon.setStyleSheet('background: transparent;')
        lbl = QLabel(text)
        lbl.setFont(QFont('VK Sans Display', 13, QFont.Weight.Bold))
        lbl.setStyleSheet('color: #111133; background: transparent;')
        row.addWidget(icon)
        row.addWidget(lbl)
        row.addStretch()
        return row

    def _hint(self, text):
        lbl = QLabel(text)
        lbl.setFont(QFont('VK Sans Display', 10))
        lbl.setStyleSheet('color: #888aaa; background: transparent; margin-left: 34px;')
        return lbl

    def _input_style(self) -> str:
        return """
            QLineEdit {
                background: white; border: 1.5px solid #d8d6f0;
                border-radius: 10px; padding: 0 14px;
                color: #222244; font-style: italic;
            }
            QLineEdit:focus { border: 1.5px solid #4a6cf7; }
        """

    def _input(self, placeholder):
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        w.setFixedHeight(44)
        w.setFont(QFont('VK Sans Display', 11))
        w.setStyleSheet(self._input_style())
        return w

    def _combo(self, items):
        w = QComboBox()
        w.addItems(items)
        w.setFixedHeight(44)
        w.setFont(QFont('VK Sans Display', 11))
        icon_path = res('select-icon.png').replace('\\', '/')
        w.setStyleSheet(f"""
            QComboBox {{
                background: white; border: 1.5px solid #d8d6f0;
                border-radius: 10px; padding: 0 14px;
                color: #888aaa; font-style: italic;
            }}
            QComboBox:focus {{ border: 1.5px solid #4a6cf7; }}
            QComboBox::drop-down {{ border: none; width: 30px; }}
            QComboBox::down-arrow {{ image: url({icon_path}); width: 14px; height: 14px; }}
            QComboBox QAbstractItemView {{
                background: white; border: 1px solid #d8d6f0;
                selection-background-color: #eeeeff;
                color: #222244; font-style: normal;
            }}
        """)
        return w

    def generate_qr(self):
        url = self.inp_url.text().strip()
        if not url:
            self.inp_url.setStyleSheet(
                self._input_style() + 'QLineEdit { border: 2px solid #e03e3e; }')
            return
        if not url.startswith('http'):
            url = 'https://' + url

        self.inp_url.setStyleSheet(self._input_style())  # сброс красной рамки
        try:
            logo_path = res('max.targetsize-256.png')
            make_qr_with_logo(url, logo_path, self.tmp_qr)
            self.preview.set_qr(self.tmp_qr)
            self._set_btn_save_state()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось создать QR-код:\n{e}")

    @staticmethod
    def _get_save_folder() -> str:
        """Возвращает путь к папке 'Адреса с QR' на рабочем столе, создаёт если нет."""
        desktop = Path.home() / 'Desktop'
        if not desktop.exists():
            # На русской Windows Desktop может быть переименован, используем shell-папку
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.shell32.SHGetFolderPathW(0, 0x0000, 0, 0, buf)
            desktop = Path(buf.value)
        folder = desktop / 'Адреса с QR'
        folder.mkdir(exist_ok=True)
        return str(folder)

    def save_full_card(self):
        full_text = self.inp_title.toPlainText().strip()
        if '::' in full_text:
            base_title = full_text.split('::', 1)[1].strip()
        else:
            base_title = full_text
        if not base_title:
            base_title = "card_result"
        base_title = base_title.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
        base_title = ' '.join(base_title.split())
        invalid_chars = '<>:"/\\|?*'
        for ch in invalid_chars:
            base_title = base_title.replace(ch, '_')
        if len(base_title) > 50:
            base_title = base_title[:50]

        try:
            save_folder = self._get_save_folder()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось создать папку:\n{e}")
            return

        path = os.path.join(save_folder, f"{base_title}.png")

        FIXED_W = 1400
        FIXED_H = 1050

        tmp_preview = PreviewCard(
            frame_path=res('editable-frame.png'),
            default_qr_path=self.tmp_qr if os.path.exists(self.tmp_qr) else res('preview.png')
        )
        tmp_preview.set_title(self.preview.title_text)
        tmp_preview.set_font_size(self.preview.font_size)
        tmp_preview.set_org(self.preview.org_text, self.preview.show_org)
        tmp_preview.set_qr(self.tmp_qr if os.path.exists(self.tmp_qr) else res('preview.png'))
        tmp_preview.resize(FIXED_W, FIXED_H)

        pixmap = QPixmap(FIXED_W, FIXED_H)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        tmp_preview.render(painter)
        painter.end()

        try:
            cropped = crop_transparent(pixmap)
            cropped.save(path, 'PNG')
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")
            return

        self._last_save_folder = save_folder
        filename = os.path.basename(path)
        self._lbl_saved.setText(f'✓  {filename}')
        self._save_result_widget.setVisible(True)
        self._animate_save_pulse()

    def _open_save_folder(self):
        if self._last_save_folder:
            subprocess.Popen(['explorer', self._last_save_folder])

    def _animate_save_pulse(self):
        """Пульс на кнопке: быстро гаснет → вспыхивает обратно."""
        if hasattr(self, '_save_pulse_anim'):
            self._save_pulse_anim.stop()
        fade_out = QPropertyAnimation(self._btn_main_effect, b"opacity")
        fade_out.setDuration(110)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.25)
        fade_out.setEasingCurve(QEasingCurve.Type.OutQuad)

        fade_in = QPropertyAnimation(self._btn_main_effect, b"opacity")
        fade_in.setDuration(300)
        fade_in.setStartValue(0.25)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutQuart)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(fade_out)
        group.addAnimation(fade_in)
        group.start()
        self._save_pulse_anim = group  # держим ссылку чтобы GC не удалил

    def _on_update_available(self, latest_version, current_version):
        msg = QMessageBox(self)
        msg.setWindowTitle("Доступно обновление")
        msg.setText(
            f"Доступна новая версия {latest_version}.\n"
            f"Сейчас у вас версия {current_version}.\n\n"
            f"Обновить сейчас?"
        )
        yes_btn = msg.addButton("Да", QMessageBox.ButtonRole.YesRole)
        msg.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        msg.exec()
        if msg.clickedButton() == yes_btn:
            _download_and_install(self)

    def closeEvent(self, event):
        self.settings.setValue("title", self.inp_title.toPlainText())
        self.settings.setValue("url", self.inp_url.text())
        self.settings.setValue("region", self.inp_region.currentText())
        self.settings.setValue("org", self.inp_org.currentText())
        self.settings.setValue("show_org", self.chk_show_org.isChecked())
        if hasattr(self, "_update_checker") and self._update_checker.isRunning():
            self._update_checker.quit()
            self._update_checker.wait(2000)
        event.accept()


class BgWidget(QWidget):
    def __init__(self, bg_path):
        super().__init__()
        self.bg = QPixmap(bg_path)
        self._bg_cache: QPixmap | None = None
        self._bg_cache_size: tuple[int, int] = (0, 0)

    def paintEvent(self, event):
        p = QPainter(self)
        if not self.bg.isNull():
            size = (self.width(), self.height())
            if self._bg_cache is None or self._bg_cache_size != size:
                self._bg_cache = self.bg.scaled(
                    size[0], size[1],
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self._bg_cache_size = size
            p.drawPixmap(0, 0, self._bg_cache)
        else:
            p.fillRect(self.rect(), QColor('#0d0b1a'))
        p.end()


class PreviewCard(QWidget):
    def __init__(self, frame_path, default_qr_path):
        super().__init__()
        self.frame_px   = QPixmap(frame_path)
        self.qr_px      = QPixmap(default_qr_path)
        self.title_text = ''
        self.font_size  = 18
        self.org_text   = ''
        self.show_org   = False
        self._frame_cache: QPixmap | None = None
        self._frame_cache_size: tuple[int, int] = (0, 0)
        self._qr_cache: QPixmap | None = None
        self._qr_cache_size: int = 0
        self._has_vk_font: bool = 'VK Sans Display' in QFontDatabase.families()

    def set_qr(self, path):
        self.qr_px = QPixmap(path)
        self._qr_cache = None  # сброс кэша при смене QR
        self.update()

    def set_title(self, text):
        self.title_text = text
        self.update()

    def set_font_size(self, size):
        self.font_size = size
        self.update()

    def set_org(self, text: str, show: bool):
        self.org_text = text
        self.show_org = show
        self.update()

    def _draw_gradient_line(self, painter, text, font, x, y, max_w, line_h):
        fm = QFontMetrics(font)
        text_w = min(fm.horizontalAdvance(text), max_w)
        tmp = QPixmap(max_w, line_h + 6)
        tmp.fill(QColor(0, 0, 0, 0))
        tp = QPainter(tmp)
        tp.setRenderHint(QPainter.RenderHint.Antialiasing)
        tp.setFont(font)
        tp.setPen(QColor(255, 255, 255))
        tp.drawText(QRect(0, 0, max_w, line_h + 6),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        grad = QLinearGradient(0, 0, text_w, 0)
        grad.setColorAt(0.0, QColor('#4f8fff'))
        grad.setColorAt(1.0, QColor('#a855f7'))
        tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tp.fillRect(tmp.rect(), QBrush(grad))
        tp.end()
        painter.drawPixmap(x, y, tmp)

    def _render_text_block(self, painter, text, font, text_x, cur_y,
                           max_text_w, line_h, qr_y, sh, gradient=True):
        for paragraph in text.split('\n'):
            if paragraph.strip():
                wrapped = wrap_text_by_px(paragraph.strip(), font, max_text_w)
                for ln in wrapped.split('\n'):
                    if gradient:
                        self._draw_gradient_line(
                            painter, ln, font, text_x, cur_y, max_text_w, line_h)
                    else:
                        avail_h = qr_y - cur_y - int(sh * 0.02)
                        if avail_h > 0:
                            painter.drawText(
                                QRect(text_x, cur_y, max_text_w, line_h + 4),
                                Qt.AlignmentFlag.AlignTop |
                                Qt.AlignmentFlag.AlignLeft, ln)
                    cur_y += line_h
            else:
                cur_y += int(line_h * 0.5)
        return cur_y

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        if self.frame_px.isNull():
            p.fillRect(self.rect(), QColor('#2a1464'))
            p.end()
            return

        size = (w, h)
        if self._frame_cache is None or self._frame_cache_size != size:
            self._frame_cache = self.frame_px.scaled(w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._frame_cache_size = size
        scaled = self._frame_cache
        fx = (w - scaled.width()) // 2
        fy = (h - scaled.height()) // 2
        p.drawPixmap(fx, fy, scaled)

        sw = scaled.width()
        sh = scaled.height()

        left_zone_w = int(sw * 0.50)
        padding_x   = int(sw * 0.04)
        max_text_w  = left_zone_w - padding_x

        qr_size = int(sw * 0.18)
        qr_x    = fx + int(sw * 0.04)
        qr_y    = fy + int(sh * 0.65)

        if self._has_vk_font:
            font = QFont('VK Sans Display', self.font_size, QFont.Weight.Bold)
        else:
            font = QFont('Arial', self.font_size, QFont.Weight.Bold)
        line_h = QFontMetrics(font).height()

        if self.title_text:
            if '::' in self.title_text:
                grad_part, white_part = self.title_text.split('::', 1)
            else:
                grad_part  = self.title_text
                white_part = ''

            text_x = fx + padding_x
            cur_y  = fy + int(sh * 0.06)

            if grad_part:
                cur_y = self._render_text_block(
                    p, grad_part, font, text_x, cur_y,
                    max_text_w, line_h, qr_y, sh, gradient=True)

            if white_part:
                p.setFont(font)
                p.setPen(QColor(255, 255, 255))
                cur_y = self._render_text_block(
                    p, white_part, font, text_x, cur_y,
                    max_text_w, line_h, qr_y, sh, gradient=False)

        if not self.qr_px.isNull():
            p.setBrush(QColor(255, 255, 255))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(qr_x - 6, qr_y - 6,
                              qr_size + 12, qr_size + 12, 10, 10)
            if self._qr_cache is None or self._qr_cache_size != qr_size:
                self._qr_cache = self.qr_px.scaled(qr_size, qr_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self._qr_cache_size = qr_size
            p.drawPixmap(qr_x, qr_y, self._qr_cache)

        if self.show_org and self.org_text:
            org_font_size = 9
            org_font = QFont(
                'VK Sans Display' if self._has_vk_font else 'Arial',
                org_font_size)
            p.setFont(org_font)
            p.setPen(QColor(180, 180, 180))
            org_y = fy + int(sh * 0.945)
            org_h = sh - int(sh * 0.945)
            p.drawText(
                QRect(fx + padding_x, org_y, sw - padding_x * 2, org_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.org_text)

        p.end()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    sys.exit(app.exec())