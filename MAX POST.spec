# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('twemoji', 'twemoji'),
        ('assets', 'assets'),
        ('fonts', 'fonts'),
        # QR Generator — только исходники и ассеты, без .venv/build/dist
        ('app/my_qr_app/main.py',  'app/my_qr_app'),
        ('app/my_qr_app/assets',   'app/my_qr_app/assets'),
        ('app/my_qr_app/version.txt', 'app/my_qr_app'),
    ],
    hiddenimports=[
        'dotenv', 'dotenv.main',
        'qrcode', 'qrcode.image.pil',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        'pandas', 'pandas.core', 'pandas.io.excel',
        'openpyxl', 'openpyxl.reader.excel',
        # Новые модули
        'stats_panel',
        'html', 'html.parser',       # парсер отчёта в stats_panel
        'log_setup', 'constants',
        # ui-пакет — импортируются внутри try/except, PyInstaller может пропустить
        'ui', 'ui.paths', 'ui.widgets', 'ui.emoji_picker',
        'ui.background', 'ui.animations', 'ui.preview_card',
        'ui.dialogs', 'ui.styles',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
