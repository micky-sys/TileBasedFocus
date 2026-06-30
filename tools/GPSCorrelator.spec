# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: Windows onedir bundle for ``tools/sbf_geotag_gui.py``.
#
# Build (from tools/):
#   py -3 -m pip install pyinstaller PySide6 matplotlib tifffile numpy
#   py -3 -m PyInstaller --noconfirm --clean \
#       --workpath %LOCALAPPDATA%\GPSCorrelatorBuild\build \
#       --distpath "C:\Parallax Apps" \
#       GPSCorrelator.spec
#
# Output: C:\Parallax Apps\GPS Correlator\GPS Correlator.exe (+ _internal\)

import pathlib

from PyInstaller.utils.hooks import collect_all

TOOLS_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = TOOLS_DIR.parent
HOST_DIR = REPO_ROOT / "host"

ICON_ICO = HOST_DIR / "assets" / "Iron661_logo.ico"
main_script = TOOLS_DIR / "sbf_geotag_gui.py"

datas = []
binaries = []
hiddenimports = [
    # First-party Python modules reached only via sys.path tricks.
    "sbf_geotag_exports",
    "geotag_attitude_exports",
    "capture",
    "capture.attitude",
    "capture.gps_correlator",
    "capture.gps_fix",
    "capture.tiff_writer",
    # ``capture.raw_bay_writer`` is imported lazily by
    # ``iter_bay14line_frame_meta`` when the user picks the
    # "Raw line files" source in the GUI. PyInstaller can't see
    # the lazy import from the static analysis pass, so list it
    # here -- otherwise the frozen build raises ModuleNotFoundError
    # the moment the operator switches to bay14line mode.
    "capture.raw_bay_writer",
]

# Pull every native plugin / data file the GUI needs at startup. Skipping
# this for any of these packages causes "PKG-00 / DLL load failed /
# ModuleNotFoundError" runtime crashes on the frozen exe.
for pkg in (
    "PySide6",
    "shiboken6",
    "matplotlib",
    "numpy",
    "tifffile",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(main_script)],
    pathex=[str(TOOLS_DIR), str(REPO_ROOT), str(HOST_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The geotagger never touches the GenICam stack, OpenCV, the numba
    # JIT, or the blosc2 compressor; excluding them keeps the bundle
    # small and dodges their Windows DLL footprint.
    excludes=[
        "tkinter",
        "harvesters",
        "genicam",
        "cv2",
        "numba",
        "llvmlite",
        "blosc2",
        "imagecodecs",
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
    name="GPS Correlator",
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
    name="GPS Correlator",
)
