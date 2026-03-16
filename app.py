"""
app.py — точка входа unified MAX POST shell.

Запускает VS Code-style контейнер с сайдбаром,
объединяющий MAX POST и QR Generator в одном окне.
"""

import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import tg_notify
from updater import check_for_updates


def main() -> None:
    tg_notify.install_excepthook()
    tg_notify.send_startup()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from shell_window import ShellWindow
    window = ShellWindow()
    window.showMaximized()

    # Проверка обновлений через 2 сек после запуска
    QTimer.singleShot(2000, lambda: check_for_updates(window))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
