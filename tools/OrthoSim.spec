# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: Windows onedir bundle for ``tools/orthosim_gui.py`` (OrthoSim).
#
# Build (from tools/; NEVER write dist/work onto Google Drive - see HANDOFF.md §4):
#   $r = Join-Path $env:LOCALAPPDATA 'OrthoSimBuild'
#   py -3 -m PyInstaller --noconfirm --clean `
#       --distpath "$r\dist" --workpath "$r\build" OrthoSim.spec
#   robocopy "$r\dist\OrthoSim" "C:\Parallax Apps\OrthoSim" /MIR
#
# Output: dist/OrthoSim/OrthoSim.exe + _internal/

import pathlib

from PyInstaller.utils.hooks import collect_all

TOOLS_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = TOOLS_DIR.parent
HOST_DIR = REPO_ROOT / "host"

ICON_ICO = HOST_DIR / "assets" / "Iron661_logo.ico"
main_script = TOOLS_DIR / "orthosim_gui.py"

datas = []
binaries = []
hiddenimports = [
    # First-party modules (resolved via pathex on tools/ and repo root).
    "orthosim_core",
    "orthosim_codec",
]

for pkg in (
    "PySide6",
    "shiboken6",
    "cv2",
    "numpy",
):
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
    excludes=["tkinter", "harvesters", "genicam", "matplotlib", "numba", "llvmlite"],
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
    name="OrthoSim",
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
    name="OrthoSim",
)
