# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.building.datastruct import Tree


block_cipher = None


# Collect dynamic modules that PyInstaller may miss
hiddenimports = []
try:
    hiddenimports += collect_submodules('playwright')
except Exception:
    pass


# Datas will be added after Analysis using Tree() to avoid tuple unpack errors
datas = []


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)


pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


# Include Playwright browsers folder (if present) into the bundle contents
if os.path.isdir('ms-playwright'):
    a.datas += Tree('ms-playwright', prefix='ms-playwright')

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AvitoSellerParser',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    onefile=True,
)

