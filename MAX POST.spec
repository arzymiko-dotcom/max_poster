# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

bs4_datas, bs4_binaries, bs4_hiddenimports = collect_all('bs4')

a = Analysis(
    ['app.py'],
    pathex=[],
    datas=[
        ('twemoji', 'twemoji'),
        ('assets', 'assets'),
        ('fonts', 'fonts'),
        # QR Generator — только исходники и ассеты, без .venv/build/dist
        ('app/my_qr_app/main.py',  'app/my_qr_app'),
        ('app/my_qr_app/assets',   'app/my_qr_app/assets'),
        ('app/my_qr_app/version.txt', 'app/my_qr_app'),
        # pyspellchecker — русский словарь
        ('.venv/Lib/site-packages/pymorphy3_dicts_ru/data', 'pymorphy3_dicts_ru/data'),
        # История изменений
        ('changelog.json', '.'),
        *bs4_datas,
    ],
    binaries=[
        *bs4_binaries,
    ],
    hiddenimports=[
        *bs4_hiddenimports,
        'dotenv', 'dotenv.main',
        'qrcode', 'qrcode.image.pil',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        'pandas', 'pandas.core', 'pandas.io.excel',
        'openpyxl', 'openpyxl.reader.excel',
        # Новые модули
        'stats_panel',
        'html', 'html.parser',       # парсер отчёта в stats_panel
        'bs4', 'bs4.builder', 'bs4.builder._htmlparser',
        'soupsieve',
        'log_setup', 'constants', 'crash_dialog', 'template_manager', 'vk_utils',
        # ui-пакет — импортируются внутри try/except, PyInstaller может пропустить
        'ui', 'ui.paths', 'ui.widgets', 'ui.emoji_picker',
        'ui.background', 'ui.animations', 'ui.preview_card',
        'ui.dialogs', 'ui.styles', 'ui.settings_dialog',
        'env_utils',
        # морфологический анализатор
        'pymorphy3', 'pymorphy3_dicts_ru', 'dawg2_python',
        # DeepSeek AI панель
        'claude_panel', 'openai',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebEngineQuick',
        'PyQt6.QtWebChannel',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MAX POST',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\MAX POST.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MAX POST',
)
