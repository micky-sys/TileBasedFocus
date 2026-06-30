# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: Windows onedir bundle for ``tools/gps_export_studio_gui.py``.
#
# Build (from tools/; NEVER write dist/work onto Google Drive — see HANDOFF.md §4):
#   $r = Join-Path $env:LOCALAPPDATA 'ParallaxExportStudioBuild'
#   py -3 -m PyInstaller --noconfirm --clean `
#       --distpath "$r\dist" --workpath "$r\build" ParallaxExportStudio.spec
#   robocopy "$r\dist\Parallax Export Studio" "C:\Parallax Apps\Parallax Export Studio" /MIR
#
# Output: dist/Parallax Export Studio/Parallax Export Studio.exe + _internal/

import pathlib

from PyInstaller.utils.hooks import collect_all, collect_submodules

TOOLS_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = TOOLS_DIR.parent
HOST_DIR = REPO_ROOT / "host"

ICON_ICO = HOST_DIR / "assets" / "Iron661_logo.ico"
main_script = TOOLS_DIR / "gps_export_studio_gui.py"

datas = []
binaries = []
hiddenimports = [
    # First-party studio modules (resolved via pathex on tools/ and host/).
    "bay14_export_core",
    "bay14_reader_gui",
    "gps_export_studio_core",
    "sbf_geotag_exports",
    "geotag_attitude_exports",
    "capture",
    "capture.attitude",
    "capture.bayer_demosaic",
    "capture.gps_correlator",
    "capture.gps_fix",
    "capture.raw_bay_writer",
    "capture.tiff_writer",
]

for pkg in (
    "PySide6",
    "shiboken6",
    "matplotlib",
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
    name="Parallax Export Studio",
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
    name="Parallax Export Studio",
)
