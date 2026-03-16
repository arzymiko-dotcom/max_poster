"""
qr_panel.py — адаптер для встраивания QR Generator в unified shell.

Извлекает centralWidget из QMainWindow и помещает его в QWidget-обёртку,
чтобы избежать проблем с QMainWindow внутри QStackedWidget.
"""

import importlib.util
import sys
from pathlib import Path

from PyQt6.QtWidgets import QVBoxLayout, QWidget


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


def create_qr_widget() -> QWidget:
    """Создаёт виджет QR Generator для встраивания в QStackedWidget.

    Берёт centralWidget из QMainWindow и помещает его в обёртку QWidget.
    Ссылка на исходный QMainWindow сохраняется, чтобы предотвратить GC.
    """
    module = _load_qr_module()
    win = module.MainWindow()
    win.hide()

    # Извлекаем centralWidget (BgWidget со всем UI)
    central = win.centralWidget()

    # Обёртка с нулевыми отступами
    wrapper = QWidget()
    wrapper.setObjectName("qrContent")
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Перемещаем central в обёртку (setParent выполняется автоматически)
    layout.addWidget(central)

    # Сохраняем ссылку на win, чтобы не допустить GC и сохранить
    # все связанные сигналы, QSettings и потоки обновления
    wrapper._qr_main_window = win

    return wrapper
