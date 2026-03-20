"""
app.py — точка входа unified MAX POST shell.

Запускает VS Code-style контейнер с сайдбаром,
объединяющий MAX POST и QR Generator в одном окне.
"""

import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import tg_notify
from crash_dialog import install_crash_hook
from log_setup import setup_logging
from updater import check_for_updates


def main() -> None:
    setup_logging()
    install_crash_hook()
    tg_notify.send_startup()

    import os
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-direct-composition"
    )

    try:
        from PyQt6.QtWebEngineQuick import QtWebEngineQuick
        QtWebEngineQuick.initialize()
    except Exception:
        pass
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: F401 — должен быть до QApplication
    except Exception:
        pass

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
