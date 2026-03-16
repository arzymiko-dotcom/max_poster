"""
qr_panel.py — адаптер для встраивания QR Generator в unified shell.
"""

import importlib.util
import sys
from pathlib import Path

from PyQt6.QtWidgets import QWidget


def _load_qr_module():
    qr_path = Path("D:/my_qr_app/main.py")
    if not qr_path.exists():
        raise FileNotFoundError(f"QR Generator не найден: {qr_path}")
    qr_dir = str(qr_path.parent)
    if qr_dir not in sys.path:
        sys.path.insert(0, qr_dir)
    spec = importlib.util.spec_from_file_location("qr_main", str(qr_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_qr_window():
    """Создаёт QR MainWindow, скрывает его и возвращает (win, central_widget)."""
    module = _load_qr_module()
    win = module.MainWindow()
    win.hide()  # showMaximized() вызывается в __init__ — немедленно скрываем

    widget = win.centralWidget()
    if widget is None:
        raise RuntimeError("QR Generator: centralWidget() вернул None")

    # Правильно отцепляем виджет от QMainWindow перед встраиванием в stack
    win.setCentralWidget(QWidget())  # даём QMainWindow пустой заглушку
    widget.setParent(None)           # чисто отцепляем от старого родителя
    widget.setObjectName("qrContent")
    return win, widget
