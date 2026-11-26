# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import PySide6
qt_dir = Path(PySide6.__file__).parent
runtime_names = ['msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'vcruntime140.dll', 'vcruntime140_1.dll']
qt_runtimes = [(str(qt_dir / name), '.') for name in runtime_names if (qt_dir / name).exists()]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=qt_runtimes,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'IPython', 'numba', 'llvmlite'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VibrationMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=os.environ.get('VIBRATION_MONITOR_CONSOLE') == '1',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
