# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: Windows one-file CLI bundle for
# ``tools/rename_exports_to_new_scheme.py``.
#
# Build (from tools/):
#   py -3 -m pip install pyinstaller
#   py -3 -m PyInstaller --noconfirm --clean RenameExports.spec
#
# Output: dist/RenameExports.exe (single self-contained executable)

import pathlib

TOOLS_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = TOOLS_DIR.parent
HOST_DIR = REPO_ROOT / "host"

ICON_ICO = HOST_DIR / "assets" / "Iron661_logo.ico"
main_script = TOOLS_DIR / "rename_exports_to_new_scheme.py"


a = Analysis(
    [str(main_script)],
    pathex=[str(TOOLS_DIR), str(REPO_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Pure-Python CLI: no GUI / camera / numeric stack needed.
        "tkinter",
        "PySide6",
        "shiboken6",
        "cv2",
        "numpy",
        "tifffile",
        "imagecodecs",
        "blosc2",
        "numba",
        "llvmlite",
        "harvesters",
        "genicam",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

_icon = str(ICON_ICO) if ICON_ICO.is_file() else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RenameExports",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **({"icon": _icon} if _icon else {}),
)
