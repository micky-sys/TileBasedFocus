# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: Windows onedir bundle for ``tools/bay14_reader_gui.py``.
#
# Build (from tools/):
#   py -3 -m pip install -r ../host/requirements.txt -r ../host/requirements-build.txt
#   py -3 -m PyInstaller --noconfirm --clean Bay14Reader.spec
#
# Output: dist/Bay14Reader/Bay14Reader.exe + dist/Bay14Reader/_internal/

import pathlib

from PyInstaller.utils.hooks import collect_all, collect_submodules

TOOLS_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = TOOLS_DIR.parent
HOST_DIR = REPO_ROOT / "host"

ICON_ICO = HOST_DIR / "assets" / "Iron661_logo.ico"
main_script = TOOLS_DIR / "bay14_reader_gui.py"

datas = []
binaries = []
hiddenimports = [
    # First-party reader modules (resolved via pathex on tools/ and host/).
    "bay14_export_core",
    "capture",
    "capture.bayer_demosaic",
    "capture.gps_fix",
    "capture.raw_bay_writer",
    "capture.tiff_writer",
]

# collect_all for packages that ship native .pyd/.dll, plugins, or heavy
# submodule trees that PyInstaller's static analysis misses. Skipping this
# is the most common source of runtime "PKG-00 / ModuleNotFoundError" /
# "DLL load failed" crashes from frozen apps using these libraries.
for pkg in (
    "PySide6",
    "shiboken6",
    "cv2",
    "numpy",
    "blosc2",
    "imagecodecs",
    "tifffile",
    "numba",
    "llvmlite",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Numba lazily resolves typed.* / core.types.* names from strings — make sure
# they end up in the PYZ even though collect_all already pulls most of them.
hiddenimports += collect_submodules("numba.core")
hiddenimports += collect_submodules("numba.typed")

a = Analysis(
    [str(main_script)],
    pathex=[str(TOOLS_DIR), str(REPO_ROOT), str(HOST_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Tk / GenICam aren't used by the reader GUI; keep them out to shrink
    # the bundle (also avoids tkinter PKG load attempts at startup).
    excludes=["tkinter", "harvesters", "genicam"],
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
    name="Bay14Reader",
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
    name="Bay14Reader",
)
