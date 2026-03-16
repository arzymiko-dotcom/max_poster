"""
qr_panel.py — адаптер для встраивания QR Generator в unified shell.
"""

import importlib.util
import sys
from pathlib import Path


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
    """Создаёт QR MainWindow, скрывает его и возвращает (win, central_widget).

    ВАЖНО: НЕ вызываем setCentralWidget(QWidget()) — это может запустить
    deleteLater() на оригинальном виджете в PyQt6. Просто берём centralWidget()
    и позволяем addWidget() стека выполнить reparent.
    """
    module = _load_qr_module()
    win = module.MainWindow()
    win.hide()  # __init__ вызывает showMaximized() — немедленно скрываем

    widget = win.centralWidget()
    if widget is None:
        raise RuntimeError("QR Generator: centralWidget() вернул None")

    widget.setObjectName("qrContent")
    return win, widget
