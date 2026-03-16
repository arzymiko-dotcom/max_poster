"""
qr_panel.py — адаптер для встраивания QR Generator в unified shell.

Подавляет showMaximized() во время __init__, чтобы окно не создавалось
как top-level, затем конвертирует его в виджет через setWindowFlags.
"""

import importlib.util
import os
import sys
from pathlib import Path

from PyQt6.QtCore import Qt


def _qr_base() -> Path:
    """Путь к папке app/my_qr_app — работает и в dev, и в frozen exe."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "app" / "my_qr_app"
    return Path(__file__).parent / "app" / "my_qr_app"


def _load_qr_module():
    qr_base = _qr_base()
    qr_path = qr_base / "main.py"
    if not qr_path.exists():
        raise FileNotFoundError(f"QR Generator не найден: {qr_path}")
    qr_dir = str(qr_base)
    if qr_dir not in sys.path:
        sys.path.insert(0, qr_dir)
    spec = importlib.util.spec_from_file_location("qr_main", str(qr_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_qr_widget():
    """Создаёт QR Generator как встраиваемый виджет.

    Monkey-patch showMaximized на время __init__, чтобы окно никогда
    не создавалось как top-level window — без скрытого Qt WState.
    Затем setWindowFlags(Widget) делает его пригодным для QStackedWidget.
    """
    module = _load_qr_module()

    # В frozen-режиме res() ищет ассеты в sys._MEIPASS/assets/ — неверно.
    # Патчим её так, чтобы смотрела в app/my_qr_app/assets/.
    if getattr(sys, "frozen", False):
        qr_assets = _qr_base() / "assets"
        module.res = lambda filename: str(qr_assets / filename)

    # Подавляем showMaximized на время __init__
    _orig_show_maximized = module.MainWindow.showMaximized
    module.MainWindow.showMaximized = lambda self: None
    try:
        win = module.MainWindow()
    finally:
        module.MainWindow.showMaximized = _orig_show_maximized

    # Даём начальный размер — запускает layout-проход (без этого размер 0x0)
    win.resize(1200, 800)

    # Скрываем версию — она отображается только в MAX POST
    if hasattr(win, "lbl_version"):
        win.lbl_version.setVisible(False)

    # Конвертируем в встраиваемый виджет
    win.setWindowFlags(Qt.WindowType.Widget)
    win.setObjectName("qrContent")

    return win
