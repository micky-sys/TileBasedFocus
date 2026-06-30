# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: Windows **onedir** bundle (recommended).
#
# One-file builds embed a PKG blob that Windows must unpack to ``%TEMP%`` at
# every launch; that path often triggers ``PKG`` / extraction errors (AV,
# locked temp, cloud-synced folders). Onedir avoids embedded PKG entirely:
# ``dist/Iron661Capture/Iron661Capture.exe`` plus ``dist/Iron661Capture/_internal/``.
#
# Build (from host/):
#   py -3 -m pip install -r requirements.txt -r requirements-build.txt
#   py -3 -m PyInstaller --noconfirm --clean Iron661Capture.spec

import pathlib

from PyInstaller.utils.hooks import collect_all, collect_submodules

HOST_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = HOST_DIR.parent

ICON_ICO = HOST_DIR / "assets" / "Iron661_logo.ico"

main_script = HOST_DIR / "main_capture.py"

datas = [
    (str(REPO_ROOT / "serial_comm.py"), "."),
    (str(REPO_ROOT / "tile_geometry.py"), "."),
    (str(REPO_ROOT / "sharpness.py"), "."),
]

binaries = []
hiddenimports = [
    "serial_comm",
    "tile_geometry",
    "sharpness",
    "blosc2",
    "imagecodecs",
    "numba",
    "llvmlite",
    "tifffile",
    "psutil",
    "serial",
    "cv2",
    "pynmea2",
]

hiddenimports += collect_submodules("capture")

for pkg in ("PySide6", "cv2", "blosc2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(main_script)],
    pathex=[str(HOST_DIR), str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Iron661Capture",
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
    icon=str(ICON_ICO),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Iron661Capture",
)
