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
try:
    hiddenimports += collect_submodules('bs4')
except Exception:
    pass


# Include Playwright browsers folder if it was installed in the repo root
datas = []
if os.path.isdir('ms-playwright'):
    datas += Tree('ms-playwright', prefix='ms-playwright')


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


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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

