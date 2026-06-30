"""
Minimal frame grab using KAYA's official KYFGLib Python binding (no Harvesters / GenTL).

Requires:
  - KAYA Vision Point runtime DLLs in the bin directory (same folder as KYFGLib.py).
  - Environment variable KAYA_VISION_POINT_BIN_PATH is set automatically from --kaya-bin.

Usage (from repo root, adjust path if your KAYA install is elsewhere):
  python host/kyfglib_grab_smoke.py --list
  python host/kyfglib_grab_smoke.py --grabber 0 --camera 0 --wait 10
  python host/kyfglib_grab_smoke.py --kaya-bin "C:\\Program Files\\KAYA Instruments\\Common\\bin" ^
      --grabber 0 --free-run --wait 20 --output host\\_kyfg_first_frame.raw
  python host/kyfglib_grab_smoke.py --grabber 0 --camera 0 --software-pulse --wait 15

Close Vision Point before running if it locks the grabber.

Defaults for this project: Width=13400, Height=9528, PixelFormat=BayerRG14, TriggerSelector=FrameTrigger.
Override with --width / --height / --pixel-format / --trigger-selector if needed.
If StreamCreateAndAlloc returns 0x301B, try --free-run or match VP trigger; try --camera 1, 2, … if listed.

KYFG_Open expects the grabber *slot* (0, 1, …) after KYFG_Scan, not the numeric “scan id”
values printed beside each device (e.g. 1554).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
import warnings
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_kaya_bin() -> Path:
    return _repo_root() / "KAYA Instruments" / "Common" / "bin"


def _unwrap_status(x):
    return x[0] if isinstance(x, tuple) and len(x) >= 1 else x


def _fgstatus_name(k, st: int) -> str:
    d = getattr(k, "ERROR_STATUS", None)
    if isinstance(d, dict):
        return str(d.get(int(st), "?"))
    return "?"


def load_kyfblib(kaya_bin: Path, vp2_adapter: bool = False):
    kaya_bin = kaya_bin.resolve()
    ky_py = kaya_bin / "KYFGLib.py"
    if not ky_py.is_file():
        raise FileNotFoundError(f"KYFGLib.py not found at {ky_py}")
    if vp2_adapter:
        # Vision Point II: KYFGLib.py loads KYFGLibA_vc141.dll from KAYA_VISION_POINT_2_BIN_PATH.
        os.environ["WithAdapter"] = "1"
        os.environ["KAYA_VISION_POINT_2_BIN_PATH"] = str(kaya_bin)
    else:
        os.environ["KAYA_VISION_POINT_BIN_PATH"] = str(kaya_bin)
    spec = importlib.util.spec_from_file_location("_kaya_kyfglib_runtime", ky_py)
    if spec is None or spec.loader is None:
        raise RuntimeError("importlib could not load KYFGLib.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.disable_exceptions()
    mod.KYFG_Init()
    if not isinstance(getattr(mod, "kydll", None), mod.ctypes.CDLL):
        raise RuntimeError(
            "KYFG_Init did not load kydll (check KAYA_VISION_POINT_BIN_PATH / "
            "KAYA_VISION_POINT_2_BIN_PATH and DLL dependencies)."
        )
    return mod


# KAYA: KYFGLib_Initialize fails with this if KYService.exe is not running.
_FGSTATUS_SERVICE_NOTRUNNING = 0x80003601


def _kyfg_scan_pids(k):
    """Run legacy KYFG_Scan(); returns per-device IDs (not KYFG_Open arguments)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            st, n, pids = k.KYFG_Scan()
            return st, n, list(pids)
        except (IndexError, ValueError, OverflowError, MemoryError, TypeError) as e:
            print(
                f"KYFG_Scan raised {type(e).__name__}: {e}\n"
                "  Common cause: KYService is not running — the first scan call then returns a bogus count.\n"
                "  Start the service (often as Administrator), then retry:\n"
                '    "KAYA Instruments\\Common\\bin\\KYService_start.bat"'
            )
            return k.FGSTATUS_INVALID_VALUE, 0, []


def _maybe_kyfblib_initialize(k, want: bool) -> bool:
    """Optional KYFGLib_Initialize; required only for some API paths. Do not call by default."""
    if not want:
        return True
    init = k.KYFGLib_InitParameters()
    st = _unwrap_status(k.KYFGLib_Initialize(init))
    if st == k.FGSTATUS_OK:
        print("KYFGLib_Initialize OK.")
        return True
    name = k.ERROR_STATUS.get(st, "?")
    print(f"KYFGLib_Initialize failed: 0x{st:X} ({name})")
    if st == _FGSTATUS_SERVICE_NOTRUNNING:
        print(
            "  Start KAYA KYService (usually requires Administrator), then retry:\n"
            r'    "...\KAYA Instruments\Common\bin\KYService_start.bat"'
        )
    return False


def _prime_kyfg_runtime(k) -> None:
    """
    Run KYFG_Scan once so the internal device table matches KYFG_Open(slot).
    Does not call KYFGLib_Initialize by default — a failed Initialize (e.g. service stopped)
    can leave KYFG_Scan in a state that crashes Python's ctypes wrapper.
    """
    st_s, n_s, _pids = _kyfg_scan_pids(k)
    if st_s != k.FGSTATUS_OK:
        print(f"Note: KYFG_Scan status 0x{st_s:X} (KYFG_Open may still work if the driver stack is up)")


def cmd_list(k, args) -> int:
    if not _maybe_kyfblib_initialize(k, getattr(args, "ky_init", False)):
        return 1
    _prime_kyfg_runtime(k)

    st, n = k.KY_DeviceScan()
    if st != k.FGSTATUS_OK:
        print(f"KY_DeviceScan failed: 0x{st:X}")
        return 1
    print(f"KY_DeviceScan count: {n}")

    st_s, n_s, pids = _kyfg_scan_pids(k)
    if st_s != k.FGSTATUS_OK:
        print(f"KYFG_Scan failed: 0x{st_s:X}")
        pids = []
        n_s = 0
    else:
        print(
            f"KYFG_Scan count: {n_s}; per-device IDs (informational only): {pids}\n"
            f"  -> Use --grabber SLOT with default open: KYFG_Open(SLOT). "
            f"Example: Komodo in slot [0] => --grabber 0 (not {pids[0] if pids else 'N/A'})."
        )

    for i in range(n):
        st_i, info = k.KY_DeviceInfo(i)
        if st_i == k.FGSTATUS_OK:
            pid = pids[i] if i < len(pids) else None
            pid_txt = f"scan id={pid}" if pid is not None else "no scan id"
            print(
                f"  slot [{i}] {info.szDeviceDisplayName!r} "
                f"protocol={info.m_Protocol} virtual={info.isVirtual}\n"
                f"         {pid_txt}; use --grabber {i} => KYFG_Open({i})"
            )
        else:
            print(f"  slot [{i}] KY_DeviceInfo failed: 0x{st_i:X}")
    return 0


def _try_camera_enum(k, cam, param: str, value_name: str) -> bool:
    st = _unwrap_status(k.KYFG_SetCameraValueEnum_ByValueName(cam, param, value_name))
    if st == k.FGSTATUS_OK:
        print(f"  Camera {param} = {value_name!r} OK")
        return True
    print(f"  Camera {param} = {value_name!r} -> 0x{st:X}")
    return False


def _try_camera_int(k, cam, param: str, value: int) -> bool:
    st = _unwrap_status(k.KYFG_SetCameraValueInt(cam, param, int(value)))
    if st == k.FGSTATUS_OK:
        print(f"  Camera {param} = {int(value)} OK")
        return True
    print(f"  Camera {param} = {int(value)} -> 0x{st:X}")
    return False


def _try_apply_offsets(k, cam, args) -> bool:
    ox = getattr(args, "offset_x", None)
    oy = getattr(args, "offset_y", None)
    if ox is None and oy is None:
        return True
    if ox is not None and not _try_camera_int(k, cam, "OffsetX", int(ox)):
        return False
    if oy is not None and not _try_camera_int(k, cam, "OffsetY", int(oy)):
        return False
    return True


def _apply_labview_ref_image_setup(k, cam, args) -> bool:
    """
    KAYA LabVIEW reference design (PDF): before acquisition, set PixelFormat, Width, Height.
    Skipped unless both --width and --height are provided.

    Some sensors (e.g. Bayer / high bit depth) need Width/Height before PixelFormat; use
    --roi-order size-first for that. Default matches the PDF (format-first).
    """
    w = getattr(args, "image_width", None)
    h = getattr(args, "image_height", None)
    if w is None and h is None:
        return True
    if w is None or h is None:
        print("LabVIEW-style setup requires both --width and --height.")
        return False
    if int(w) <= 0 or int(h) <= 0:
        print("--width and --height must be positive.")
        return False
    pf = (getattr(args, "pixel_format", None) or "BayerRG14").strip()
    order = getattr(args, "roi_order", "format-first") or "format-first"
    ox = getattr(args, "offset_x", None)
    oy = getattr(args, "offset_y", None)
    off_txt = ""
    if ox is not None or oy is not None:
        off_txt = f", OffsetX={ox!r}, OffsetY={oy!r}"
    print(f"ROI setup ({order}): PixelFormat={pf!r}, Width={int(w)}, Height={int(h)}{off_txt}")
    if order == "size-first":
        if not _try_apply_offsets(k, cam, args):
            return False
        if not _try_camera_int(k, cam, "Width", int(w)):
            return False
        if not _try_camera_int(k, cam, "Height", int(h)):
            return False
        if not _try_camera_enum(k, cam, "PixelFormat", pf):
            return False
    else:
        if not _try_camera_enum(k, cam, "PixelFormat", pf):
            return False
        if not _try_apply_offsets(k, cam, args):
            return False
        if not _try_camera_int(k, cam, "Width", int(w)):
            return False
        if not _try_camera_int(k, cam, "Height", int(h)):
            return False
    return True


def _print_camera_snapshot(k, cam) -> None:
    """Read back common GenICam nodes (best-effort) before stream alloc."""
    wrong_name = int(getattr(k, "FGSTATUS_WRONG_PARAMETER_NAME", 0x300A))
    print("Camera readback before stream alloc:")
    for name in ("Width", "Height", "OffsetX", "OffsetY"):
        t = k.KYFG_GetCameraValueInt(cam, name)
        if not isinstance(t, tuple) or len(t) < 2:
            print(f"  {name} = ?")
            continue
        st, val = t[0], t[1]
        if st == k.FGSTATUS_OK:
            print(f"  {name} = {val}")
        else:
            print(f"  {name} -> get failed 0x{int(st):X}")
    t_ps = k.KYFG_GetCameraValueInt(cam, "PayloadSize")
    if isinstance(t_ps, tuple) and len(t_ps) >= 2:
        st_ps, v_ps = t_ps[0], t_ps[1]
        if st_ps == k.FGSTATUS_OK:
            print(f"  PayloadSize = {v_ps}")
        elif int(st_ps) == wrong_name:
            print(
                "  PayloadSize = (not in this camera's GenICam — normal; Vision Point often has no such feature)"
            )
        else:
            print(f"  PayloadSize -> get failed 0x{int(st_ps):X}")
    t = k.KYFG_GetCameraValueStringCopy(cam, "PixelFormat")
    if isinstance(t, tuple) and len(t) >= 2:
        st, s = t[0], t[1]
        if st == k.FGSTATUS_OK:
            print(f"  PixelFormat = {s!r}")
        else:
            print(f"  PixelFormat -> get failed 0x{int(st):X}")
    for name in ("AcquisitionMode", "TriggerSelector", "TriggerMode", "TriggerSource"):
        t = k.KYFG_GetCameraValueStringCopy(cam, name)
        if isinstance(t, tuple) and len(t) >= 2 and t[0] == k.FGSTATUS_OK:
            print(f"  {name} = {t[1]!r}")


def _try_grabber_enum(k, fg, param: str, value_name: str) -> bool:
    st = _unwrap_status(k.KYFG_SetGrabberValueEnum_ByValueName(fg, param, value_name))
    if st == k.FGSTATUS_OK:
        print(f"  Grabber {param} = {value_name!r} OK")
        return True
    print(f"  Grabber {param} = {value_name!r} -> 0x{st:X}")
    return False


def _try_grabber_cmd(k, fg, cmd: str) -> bool:
    st = _unwrap_status(k.KYFG_GrabberExecuteCommand(fg, cmd))
    if st == k.FGSTATUS_OK:
        print(f"  GrabberExecute {cmd!r} OK")
        return True
    print(f"  GrabberExecute {cmd!r} -> 0x{st:X}")
    return False


def _try_camera_cmd(k, cam, cmd: str) -> bool:
    st = _unwrap_status(k.KYFG_CameraExecuteCommand(cam, cmd))
    if st == k.FGSTATUS_OK:
        print(f"  CameraExecute {cmd!r} OK")
        return True
    print(f"  CameraExecute {cmd!r} -> 0x{st:X}")
    return False


def _try_camera_cmd_silent_ok_only(k, cam, cmd: str) -> None:
    st = _unwrap_status(k.KYFG_CameraExecuteCommand(cam, cmd))
    if st == k.FGSTATUS_OK:
        print(f"  CameraExecute {cmd!r} OK")


def _open_grabber_handle(k, open_arg: int, use_open_ex: bool):
    if use_open_ex:
        return k.KYFG_OpenEx(open_arg, 0)
    return k.KYFG_Open(open_arg)


def _unwrap_fg(fg_r):
    return fg_r[0] if isinstance(fg_r, tuple) else fg_r


def _grabber_handle_ok(k, fg) -> bool:
    ih = int(fg)
    return ih != int(k.INVALID_FGHANDLE_VAL) and ih != int(k.KYPY_STATUS_INVALID_FGHANDLE)


def cmd_grab(k, args) -> int:
    if not _maybe_kyfblib_initialize(k, getattr(args, "ky_init", False)):
        return 1
    _prime_kyfg_runtime(k)

    st, n = k.KY_DeviceScan()
    if st != k.FGSTATUS_OK:
        print(f"KY_DeviceScan failed: 0x{st:X}")
        return 1
    if n <= 0:
        print("No frame grabbers found.")
        return 1
    if args.grabber < 0 or args.grabber >= n:
        print(f"Invalid --grabber {args.grabber} (0..{n - 1})")
        return 1

    st_s, _n_s, pids = _kyfg_scan_pids(k)
    if args.open_id is not None:
        open_arg = int(args.open_id)
        tag = "--open-id (expert; usually wrong — prefer slot)"
    elif args.open_pid:
        if st_s != k.FGSTATUS_OK or args.grabber >= len(pids):
            print("KYFG_Scan IDs unavailable; cannot use --open-pid.")
            return 1
        open_arg = int(pids[args.grabber])
        tag = "--open-pid (scan array value; normally do not use)"
    else:
        open_arg = int(args.grabber)
        tag = "slot index (default; KYFG_Open(slot))"

    project_str: str | None = None
    if getattr(args, "project", None) is not None:
        pr = args.project.resolve()
        if not pr.is_file():
            print(f"--project is not a file: {pr}")
            print(
                "Use a real file path (not a placeholder). --project is optional: only for "
                "KYFG_OpenEx second argument if KAYA documents a project/config file for your stack."
            )
            return 1
        project_str = str(pr)

    def attempt_open(use_ex: bool, *, use_saved_project: bool = False) -> object | None:
        fn = "KYFG_OpenEx" if use_ex else "KYFG_Open"
        if use_ex and use_saved_project and project_str is not None:
            print(f"{fn}({open_arg}, project={project_str!r})  [{tag}]")
            fg_r = k.KYFG_OpenEx(open_arg, project_str)
        elif use_ex:
            print(f"{fn}({open_arg})  [{tag}]")
            fg_r = _open_grabber_handle(k, open_arg, True)
        else:
            print(f"{fn}({open_arg})  [{tag}]")
            fg_r = _open_grabber_handle(k, open_arg, False)
        fg = _unwrap_fg(fg_r)
        if _grabber_handle_ok(k, fg):
            print(f"{fn} OK (handle {int(fg)})")
            return fg
        print(f"{fn} failed (invalid handle).")
        return None

    fg = None
    if project_str is not None:
        print("Opening grabber with KYFG_OpenEx(..., --project) …")
        fg = attempt_open(True, use_saved_project=True)

    if fg is None:
        if args.open_ex:
            fg = attempt_open(True, use_saved_project=False)
        else:
            fg = attempt_open(False)
            if fg is None and not args.no_open_fallback:
                print("Retrying with KYFG_OpenEx (same slot) …")
                fg = attempt_open(True, use_saved_project=False)

    if fg is None and not args.ky_init:
        print(
            "Trying KYFGLib_Initialize (needs KYService running), then KYFG_Scan + open again …"
        )
        if _maybe_kyfblib_initialize(k, True):
            _prime_kyfg_runtime(k)
            if project_str is not None:
                fg = attempt_open(True, use_saved_project=True)
            if fg is None and not args.open_ex:
                fg = attempt_open(False)
            if fg is None and not args.no_open_fallback:
                fg = attempt_open(True, use_saved_project=False)

    if fg is None and not args.no_open_minus1:
        print(
            "Trying KYFG_Open(-1) — first grabber (KAYA KYFGLib.h: index -1 skips slot/scan mismatch) …"
        )
        fg_r = k.KYFG_Open(-1)
        fg = _unwrap_fg(fg_r)
        if _grabber_handle_ok(k, fg):
            print(f"KYFG_Open(-1) OK (handle {int(fg)})")
        else:
            print("KYFG_Open(-1) failed (invalid handle).")
            fg = None

    if fg is None:
        print(
            "Could not open grabber. Checklist:\n"
            "  • Fully exit Vision Point; Task Manager: end KAYA / Vision Point / Qt processes.\n"
            "  • KYService running:  sc query KYService  (STATE should be RUNNING).\n"
            "  • Komodo III + Vision Point II: try loading the adapter DLL:\n"
            "      python host\\kyfglib_grab_smoke.py --vp2-adapter --ky-init --grabber 0 …\n"
            "    (--kaya-bin must be the folder that contains KYFGLibA_vc141.dll, often Common\\bin.)\n"
            "  • Some stacks need KYFG_OpenEx with a KAYA project/config path (see KAYA docs / support).\n"
            "  • Run:  python host\\kyfglib_grab_smoke.py --list"
        )
        return 1

    stream = None
    cam = None
    cam_opened = False
    acquisition_started = False
    try:
        st, handles = k.KYFG_UpdateCameraList(fg)
        if st != k.FGSTATUS_OK:
            print(f"KYFG_UpdateCameraList failed: 0x{st:X}")
            return 1
        if not handles:
            print("No cameras reported on this grabber.")
            return 1
        if args.camera < 0 or args.camera >= len(handles):
            print(f"Invalid --camera {args.camera} (0..{len(handles) - 1}), have {len(handles)} handle(s)")
            return 1
        cam = handles[args.camera]
        print(f"Using camera handle {int(cam)} (index {args.camera} of {len(handles)})")
        if len(handles) > 1:
            print(
                "Note: multiple camera handles on this grabber. If stream creation fails with "
                "FGSTATUS_CANNOT_START_HW_STREAM, try --camera 1 or --camera 2 (VP may be on another branch)."
            )

        st = _unwrap_status(k.KYFG_CameraOpen2(cam, None))
        if st != k.FGSTATUS_OK:
            print(f"KYFG_CameraOpen2 failed: 0x{st:X}")
            return 1
        cam_opened = True

        if getattr(args, "stop_before_roi", False):
            _try_camera_cmd_silent_ok_only(k, cam, "AcquisitionStop")

        if not _apply_labview_ref_image_setup(k, cam, args):
            return 1

        if getattr(args, "acquisition_mode", None):
            _try_camera_enum(k, cam, "AcquisitionMode", str(args.acquisition_mode))
        elif args.configure_continuous:
            _try_camera_enum(k, cam, "AcquisitionMode", "Continuous")

        ts = (getattr(args, "trigger_selector", None) or "FrameTrigger").strip()
        if getattr(args, "free_run", False):
            print(f"Free-run: TriggerSelector={ts!r}, TriggerMode=Off")
            _try_camera_enum(k, cam, "TriggerSelector", ts)
            _try_camera_enum(k, cam, "TriggerMode", "Off")
        elif args.line_trigger:
            _try_camera_enum(k, cam, "TriggerSelector", ts)
            _try_camera_enum(k, cam, "TriggerMode", "On")
            _try_camera_enum(k, cam, "TriggerSource", "Line1")

        if args.software_pulse:
            print("Software pulse path (KAYA grabber CameraTrigger*):")
            _try_grabber_enum(k, fg, "CameraTriggerMode", "On")
            _try_grabber_enum(k, fg, "CameraTriggerSource", "KY_SOFTWARE")
            _try_grabber_enum(k, fg, "CameraTriggerActivation", "RisingEdge")

        if args.line_trigger and args.configure_continuous and not getattr(
            args, "acquisition_mode", None
        ):
            print(
                "Note: --line-trigger with AcquisitionMode=Continuous can fail stream start on some cameras; "
                "try --acquisition-mode SingleFrame or --no-configure-continuous plus --line-trigger "
                "(match Vision Point)."
            )

        if getattr(args, "dump_camera", False):
            _print_camera_snapshot(k, cam)
            t_tm = k.KYFG_GetCameraValueStringCopy(cam, "TriggerMode")
            if (
                isinstance(t_tm, tuple)
                and len(t_tm) >= 2
                and t_tm[0] == k.FGSTATUS_OK
                and str(t_tm[1]).strip().lower() == "on"
                and not args.line_trigger
                and not getattr(args, "free_run", False)
            ):
                print(
                    "Hint: readback shows TriggerMode=On (camera expects triggers, e.g. Line1). "
                    "Either add --free-run to force TriggerMode=Off for this test, or use --line-trigger "
                    "and supply real triggers / match Vision Point."
                )

        st, stream = k.KYFG_StreamCreateAndAlloc(cam, args.buffers, 0)
        if st != k.FGSTATUS_OK or stream is None:
            name = _fgstatus_name(k, int(st))
            print(f"KYFG_StreamCreateAndAlloc failed: 0x{int(st):X} ({name})")
            print(
                "Things to try next:\n"
                "  • Another --camera index if the grabber lists several handles.\n"
                "  • --roi-order size-first (Width/Height before PixelFormat; helps some Bayer/high-bit cameras).\n"
                "  • --stop-before-roi (AcquisitionStop before ROI; unlocks GenICam on some devices).\n"
                "  • Match VP acquisition: --acquisition-mode SingleFrame with --line-trigger, or "
                "--no-configure-continuous.\n"
                "  • If --dump-camera shows TriggerMode=On / Line1 but you are not pulsing Line1, try --free-run.\n"
                "  • Run again with --dump-camera to print Width/Height/PixelFormat readback before this call.\n"
                "Note: Vision Point's AcquisitionStart/AcquisitionStop are GenICam *commands* used *after* a stream "
                "exists. This failure is *before* that stage, so those buttons are not what blocks StreamCreateAndAlloc."
            )
            return 1
        print(f"Stream handle {int(stream)}, {args.buffers} buffer(s)")

        qst = _unwrap_status(
            k.KYFG_BufferQueueAll(
                stream,
                k.KY_ACQ_QUEUE_TYPE.KY_ACQ_QUEUE_UNQUEUED,
                k.KY_ACQ_QUEUE_TYPE.KY_ACQ_QUEUE_INPUT,
            )
        )
        if qst != k.FGSTATUS_OK:
            print(f"KYFG_BufferQueueAll UNQUEUED->INPUT -> 0x{qst:X} (continuing anyway)")

        lst = _unwrap_status(k.KYFG_StreamLinkFramesContinuously(stream))
        if lst != k.FGSTATUS_OK:
            print(f"KYFG_StreamLinkFramesContinuously -> 0x{lst:X} (continuing anyway)")

        st = _unwrap_status(k.KYFG_CameraStart(cam, stream, 0))
        if st != k.FGSTATUS_OK:
            print(f"KYFG_CameraStart(continuous) failed: 0x{st:X}")
            return 1
        acquisition_started = True
        print("KYFG_CameraStart(0=continuous) OK")

        if args.try_acquisition_start:
            _try_camera_cmd(k, cam, "AcquisitionStart")

        if args.software_pulse:
            _try_grabber_cmd(k, fg, "CameraTriggerSoftware")

        t0 = time.monotonic()
        deadline = t0 + args.wait
        while time.monotonic() < deadline:
            sz_t = k.KYFG_StreamGetSize(stream)
            frame_bytes = int(sz_t[0]) if isinstance(sz_t, tuple) else int(sz_t)
            if frame_bytes > 0:
                _st_f, idx = k.KYFG_StreamGetFrameIndex(stream)
                bi = int(idx) % args.buffers
                ptr = k.KYFG_StreamGetPtr(stream, bi)
                p = ptr[0] if isinstance(ptr, tuple) else ptr
                if p:
                    raw = bytes(k.ctypes.string_at(p, frame_bytes))
                    print(
                        f"Frame {len(raw)} B (buffer {bi}, index {idx}) "
                        f"after {time.monotonic() - t0:.2f}s"
                    )
                    if args.output:
                        args.output.parent.mkdir(parents=True, exist_ok=True)
                        args.output.write_bytes(raw)
                        print(f"Wrote {args.output}")
                    return 0
            time.sleep(0.05)

        print(f"No frame within {args.wait}s (KYFG_StreamGetSize stayed 0).")
        return 2
    finally:
        if acquisition_started:
            k.KYFG_CameraStop(cam)
        if stream is not None:
            k.KYFG_StreamDelete(stream)
        if cam_opened:
            k.KYFG_CameraClose(cam)
        k.KYFG_Close(fg)


def main() -> int:
    p = argparse.ArgumentParser(description="KAYA KYFGLib native grab smoke test")
    p.add_argument(
        "--kaya-bin",
        type=Path,
        default=_default_kaya_bin(),
        help="Directory containing KYFGLib.py and KYFGLib_vc141.dll (or KYFGLibA_vc141.dll with --vp2-adapter)",
    )
    p.add_argument(
        "--vp2-adapter",
        action="store_true",
        help="Load KYFGLibA (Vision Point II adapter): sets WithAdapter=1 and KAYA_VISION_POINT_2_BIN_PATH",
    )
    p.add_argument("--list", action="store_true", help="List grabbers and exit")
    p.add_argument(
        "--ky-init",
        action="store_true",
        help="Call KYFGLib_Initialize before scan (needs KYService running; leave off unless KAYA docs require it)",
    )
    p.add_argument(
        "--grabber",
        type=int,
        default=0,
        help="Grabber slot 0..N-1 from --list (passed to KYFG_Open as that integer)",
    )
    p.add_argument(
        "--open-pid",
        action="store_true",
        help="Pass KYFG_Scan array value for this slot to KYFG_Open (almost always wrong; debug only)",
    )
    p.add_argument(
        "--open-id",
        type=int,
        default=None,
        metavar="N",
        help="Call open with this exact integer (expert / debug only)",
    )
    p.add_argument(
        "--project",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional file path for KYFG_OpenEx(slot, PATH) if KAYA documents one for your setup",
    )
    p.add_argument(
        "--open-ex",
        action="store_true",
        help="Use only KYFG_OpenEx(slot, NULL) (no KYFG_Open first)",
    )
    p.add_argument(
        "--no-open-fallback",
        action="store_true",
        help="Do not auto-try KYFG_OpenEx after KYFG_Open fails",
    )
    p.add_argument(
        "--no-open-minus1",
        action="store_true",
        help="Do not try KYFG_Open(-1) after other open attempts fail",
    )
    p.add_argument("--camera", type=int, default=0, help="Camera index on that grabber")
    p.add_argument(
        "--width",
        type=int,
        default=13400,
        metavar="W",
        dest="image_width",
        help="Camera Width (GenICam); default 13400 for this deployment",
    )
    p.add_argument(
        "--height",
        type=int,
        default=9528,
        metavar="H",
        dest="image_height",
        help="Camera Height (GenICam); default 9528 for this deployment",
    )
    p.add_argument(
        "--pixel-format",
        type=str,
        default="BayerRG14",
        dest="pixel_format",
        metavar="NAME",
        help="PixelFormat enum name (default BayerRG14 for this camera)",
    )
    p.add_argument(
        "--offset-x",
        type=int,
        default=None,
        metavar="X",
        dest="offset_x",
        help="Set GenICam OffsetX before Width/Height (e.g. 0 to clear a stale offset)",
    )
    p.add_argument(
        "--offset-y",
        type=int,
        default=None,
        metavar="Y",
        dest="offset_y",
        help="Set GenICam OffsetY before Width/Height (e.g. 0 to clear a stale offset)",
    )
    p.add_argument(
        "--roi-order",
        choices=("format-first", "size-first"),
        default="format-first",
        help="Order for ROI setup: LabVIEW PDF order vs Width/Height before PixelFormat",
    )
    p.add_argument(
        "--stop-before-roi",
        action="store_true",
        help="CameraExecute AcquisitionStop after open (quiet if already stopped); use before ROI on picky devices",
    )
    p.add_argument(
        "--acquisition-mode",
        type=str,
        default=None,
        metavar="NAME",
        help="Set AcquisitionMode by enum name (e.g. SingleFrame, Continuous). Overrides default Continuous",
    )
    p.add_argument(
        "--dump-camera",
        action="store_true",
        help="Print Width/Height/PixelFormat and related readback before KYFG_StreamCreateAndAlloc",
    )
    p.add_argument("--buffers", type=int, default=8, help="Stream buffer count")
    p.add_argument("--wait", type=float, default=10.0, help="Seconds to wait for a frame")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write first captured frame bytes to this file (raw payload)",
    )
    p.add_argument(
        "--configure-continuous",
        action="store_true",
        default=True,
        help="Set camera AcquisitionMode=Continuous (default: on)",
    )
    p.add_argument(
        "--no-configure-continuous",
        action="store_false",
        dest="configure_continuous",
        help="Skip AcquisitionMode=Continuous",
    )
    p.add_argument(
        "--trigger-selector",
        type=str,
        default="FrameTrigger",
        metavar="NAME",
        help="GenICam TriggerSelector enum (default FrameTrigger for this project; override e.g. FrameStart if needed)",
    )
    p.add_argument(
        "--free-run",
        action="store_true",
        help="Set TriggerMode=Off after ROI (use when camera was left in Line1 trigger; no external pulse needed)",
    )
    p.add_argument(
        "--line-trigger",
        action="store_true",
        help="Set camera TriggerSelector (--trigger-selector), Mode=On, Source=Line1",
    )
    p.add_argument(
        "--software-pulse",
        action="store_true",
        help="After start: set grabber CameraTrigger* and execute CameraTriggerSoftware",
    )
    p.add_argument(
        "--try-acquisition-start",
        action="store_true",
        help="Try CameraExecute AcquisitionStart after KYFG_CameraStart (debug)",
    )
    args = p.parse_args()

    try:
        k = load_kyfblib(args.kaya_bin, vp2_adapter=args.vp2_adapter)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Failed to load KYFGLib: {e}", file=sys.stderr)
        return 1

    if args.list:
        return cmd_list(k, args)
    return cmd_grab(k, args)


if __name__ == "__main__":
    raise SystemExit(main())
