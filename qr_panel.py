"""
qr_panel.py — адаптер для встраивания QR Generator в unified shell.

Импортирует MainWindow из D:/my_qr_app/main.py через importlib,
не показывает окно, возвращает centralWidget() для встраивания в QStackedWidget.
"""

import importlib.util
import os
import sys
from pathlib import Path


def _load_qr_module():
    qr_path = Path("D:/my_qr_app/main.py")
    if not qr_path.exists():
        raise FileNotFoundError(f"QR Generator не найден: {qr_path}")
    # Добавляем папку QR-проекта в sys.path чтобы его assets/dotenv нашлись
    qr_dir = str(qr_path.parent)
    if qr_dir not in sys.path:
        sys.path.insert(0, qr_dir)
    spec = importlib.util.spec_from_file_location("qr_main", str(qr_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_qr_window():
    """Создаёт экземпляр QR MainWindow (не показывает его). Возвращает (win, widget)."""
    module = _load_qr_module()
    win = module.MainWindow()
    # Не вызываем show() — окно используется только как контейнер логики
    widget = win.centralWidget()
    if widget is None:
        raise RuntimeError("QR Generator: centralWidget() вернул None")
    widget.setObjectName("qrContent")
    return win, widget
