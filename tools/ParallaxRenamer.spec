# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: Windows onedir bundle for ``tools/rename_exports_gui.py``.
#
# Build (from tools/):
#   py -3 -m pip install pyinstaller PySide6
#   py -3 -m PyInstaller --noconfirm --clean --distpath C:\ ParallaxRenamer.spec
#
# Output: C:\ParallaxRenamer\ParallaxRenamer.exe (+ _internal\)

import pathlib

from PyInstaller.utils.hooks import collect_all

TOOLS_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = TOOLS_DIR.parent
HOST_DIR = REPO_ROOT / "host"

ICON_ICO = HOST_DIR / "assets" / "Iron661_logo.ico"
main_script = TOOLS_DIR / "rename_exports_gui.py"

datas = []
binaries = []
hiddenimports = [
    "rename_exports_to_new_scheme",
]

# Pull in PySide6's native plugins and shiboken DLLs explicitly so the
# frozen GUI actually finds a Qt platform plugin at startup.
for pkg in ("PySide6", "shiboken6"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(main_script)],
    pathex=[str(TOOLS_DIR), str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The renamer is pure-stdlib over PySide6: no numerics or imaging.
        "tkinter",
        "numpy",
        "cv2",
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
    [],
    exclude_binaries=True,
    name="ParallaxRenamer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **({"icon": _icon} if _icon else {}),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ParallaxRenamer",
)
