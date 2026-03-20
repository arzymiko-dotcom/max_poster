"""
crash_dialog.py — диалог отчёта об ошибке.

Показывается при необработанном исключении.
Позволяет отправить отчёт в Telegram и/или скопировать трейсбек.
"""

import sys
import traceback

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class CrashDialog(QDialog):
    def __init__(self, tb_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Произошла ошибка")
        self.setMinimumSize(560, 380)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowStaysOnTopHint
        )

        self._tb_text = tb_text
        self._sent = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        header = QLabel("В программе возникла непредвиденная ошибка.")
        header.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(header)

        sub = QLabel(
            "Нажмите «Отправить отчёт», чтобы сообщить разработчику.\n"
            "Отчёт будет отправлен автоматически — без персональных данных."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #555;")
        layout.addWidget(sub)

        self._tb_edit = QPlainTextEdit(tb_text)
        self._tb_edit.setReadOnly(True)
        self._tb_edit.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 11px; "
            "background: #f5f5f5; border: 1px solid #ddd;"
        )
        self._tb_edit.setMaximumHeight(200)
        layout.addWidget(self._tb_edit)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #2a7a2a; font-size: 11px;")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._send_btn = QPushButton("Отправить отчёт")
        self._send_btn.setStyleSheet(
            "QPushButton { background: #2d6cdf; color: white; "
            "border-radius: 5px; padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #1a55c4; }"
            "QPushButton:disabled { background: #9ab2e0; }"
        )
        self._send_btn.clicked.connect(self._send_report)

        copy_btn = QPushButton("Скопировать")
        copy_btn.setStyleSheet(
            "QPushButton { border: 1px solid #ccc; border-radius: 5px; padding: 6px 14px; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        copy_btn.clicked.connect(self._copy)

        close_btn = QPushButton("Закрыть")
        close_btn.setStyleSheet(
            "QPushButton { border: 1px solid #ccc; border-radius: 5px; padding: 6px 14px; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._send_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _send_report(self) -> None:
        self._send_btn.setEnabled(False)
        self._send_btn.setText("Отправляем…")
        try:
            import tg_notify
            tg_notify.send_error("Необработанная ошибка (отчёт пользователя)", self._tb_text)
            self._status.setText("Отчёт отправлен. Спасибо!")
            self._send_btn.setText("Отправлено")
            self._sent = True
        except Exception:
            self._status.setStyleSheet("color: #c0392b; font-size: 11px;")
            self._status.setText("Не удалось отправить отчёт. Скопируйте текст ошибки вручную.")
            self._send_btn.setText("Ошибка отправки")

    def _copy(self) -> None:
        app = QApplication.instance()
        if app:
            app.clipboard().setText(self._tb_text)
            self._status.setText("Текст ошибки скопирован в буфер обмена.")


def show_crash_dialog(tb_text: str) -> None:
    """Показывает диалог краша если есть активный QApplication."""
    app = QApplication.instance()
    if app is None:
        return
    dlg = CrashDialog(tb_text)
    dlg.exec()


def install_crash_hook() -> None:
    """
    Устанавливает sys.excepthook: при необработанной ошибке показывает
    диалог с кнопкой «Отправить отчёт» и тихо отправляет в Telegram.
    """
    _orig = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            _orig(exc_type, exc_value, exc_tb)
            return

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        # Тихая отправка в Telegram (если токены заданы)
        try:
            import tg_notify
            tg_notify.send_error("Необработанная ошибка", tb_text)
        except Exception:
            pass

        # Показываем диалог
        show_crash_dialog(tb_text)

        _orig(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
