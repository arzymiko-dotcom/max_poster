"""
app.py — точка входа unified MAX POST shell.

Запускает VS Code-style контейнер с сайдбаром,
объединяющий MAX POST и QR Generator в одном окне.
"""

import signal
import sys

from PyQt6.QtCore import QLibraryInfo, QLocale, QTimer, QTranslator
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

# PyQt6 + VS Code terminal конфликтуют по SIGINT — используем стандартный C-обработчик
signal.signal(signal.SIGINT, signal.SIG_DFL)

import tg_notify
from crash_dialog import install_crash_hook
from log_setup import setup_logging
from updater import check_for_updates

_SERVER_NAME = "MAXPOSTSingleInstance"


def _try_activate_existing() -> bool:
    """Пробует подключиться к уже запущенному экземпляру и активировать его.
    Возвращает True если экземпляр найден (этому процессу надо выйти)."""
    sock = QLocalSocket()
    sock.connectToServer(_SERVER_NAME)
    if sock.waitForConnected(500):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True
    return False


def _setup_single_instance_server(window) -> QLocalServer:
    """Создаёт сервер одиночного экземпляра. При входящем соединении — разворачивает окно."""
    QLocalServer.removeServer(_SERVER_NAME)  # чистим стale-сокет после краша
    server = QLocalServer(window)
    server.listen(_SERVER_NAME)

    def _on_connection():
        conn = server.nextPendingConnection()
        if conn:
            conn.waitForReadyRead(300)
            conn.deleteLater()
        # Разворачиваем главное окно
        window.showNormal()
        window.activateWindow()
        window.raise_()

    server.newConnection.connect(_on_connection)
    return server


def main() -> None:
    setup_logging()
    install_crash_hook()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Переводим стандартные Qt-меню и диалоги на русский (QLineEdit, QFileDialog и т.д.)
    _tr = QTranslator(app)
    _tr_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if _tr.load(QLocale(QLocale.Language.Russian, QLocale.Country.Russia),
                "qtbase", "_", _tr_path):
        app.installTranslator(_tr)

    # Проверяем: уже запущен?
    if _try_activate_existing():
        sys.exit(0)

    tg_notify.send_startup()

    from shell_window import ShellWindow
    window = ShellWindow()
    _setup_single_instance_server(window)
    window.showMaximized()

    # Проверка обновлений через 2 сек после запуска
    QTimer.singleShot(2000, lambda: check_for_updates(window))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
