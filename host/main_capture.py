"""Entry point for the unified Iron 661 capture + focus app.

Adds the ``host/`` directory to ``sys.path`` so the inner ``capture`` package
can import the side modules at the repo's ``host/`` level (``serial_comm``,
``gentl_acq_helpers``, ``kaya_harvesters_patch``) regardless of the current
working directory.

Run from the repo root::

    py -3 host/main_capture.py
    py -3 host/main_capture.py --cti "C:\\Program Files\\KAYA Instruments\\Common\\bin\\KYFGLibGenTL_vc141.cti"
    py -3 host/main_capture.py --port COM7
    py -3 host/main_capture.py --gps-port COM4

Both ``--port`` (ClearCore PCB) and ``--gps-port`` (Septentrio SBF
receiver) are optional; each falls back to auto-detect when omitted.

Multiprocessing-spawn note
--------------------------
On Windows, the per-camera writer subprocesses start under
``multiprocessing`` with the "spawn" start method. ``spawn`` re-imports
this script (under the name ``__mp_main__``) in each child to find the
target callable. To keep the child cheap, we deliberately keep all
heavy imports (Qt, KAYA) inside ``if __name__ == "__main__"`` so the
writer subprocess only pays for stdlib + numpy + tifffile + the bits
of ``capture/`` it actually uses.
"""

from __future__ import annotations

import os
import sys


def _bootstrap_paths() -> None:
    # PyInstaller frozen build: ``serial_comm`` / ``tile_geometry`` / ``sharpness``
    # are bundled at the root of ``sys._MEIPASS`` via the spec ``datas`` list.
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
            os.path.abspath(sys.executable)
        )
        if base and base not in sys.path:
            sys.path.insert(0, base)
        return

    here = os.path.dirname(os.path.abspath(__file__))   # .../host
    repo_root = os.path.dirname(here)                   # repo root (one above host/)
    # Repo root holds serial_comm.py, tile_geometry.py, sharpness.py.
    # host/ holds gentl_acq_helpers.py, kaya_harvesters_patch.py.
    for p in (repo_root, here):
        if p and p not in sys.path:
            sys.path.insert(0, p)


# This MUST run at module import time -- the writer subprocess
# re-imports this module under ``__mp_main__`` and depends on the
# sys.path mutation here to find the ``capture`` package.
_bootstrap_paths()


def _apply_windows_realtime_tweaks() -> None:
    """Best-effort Windows tweaks to reduce KAYA frame drops.

    Two changes that match what Vision Point / StreamPix do at startup:

    * ``timeBeginPeriod(1)`` -- raises the system multimedia timer
      from ~15.6 ms granularity to 1 ms. Affects ``Sleep`` /
      ``WaitForSingleObject`` resolution for **every** thread on the
      box, including the KAYA SDK's internal buffer-pump worker that
      hands frames into our callback. Without it, a single Windows
      scheduling slice can leave the SDK buffer ring backed up long
      enough for the camera to silently drop a Line1 trigger.
    * Process priority ``HIGH_PRIORITY_CLASS`` -- shrinks the chance
      Windows preempts the GUI / KAYA worker / writer subprocesses
      to do background work for ~15 ms slices.

    Both are best-effort: failures are swallowed (logged at WARNING
    if a logger is available) so a non-Windows host or a sandboxed
    Python without ``ctypes`` still launches.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # 1 ms multimedia timer.
        winmm = ctypes.WinDLL("winmm")
        winmm.timeBeginPeriod.argtypes = [ctypes.c_uint]
        winmm.timeBeginPeriod.restype = ctypes.c_uint
        rc = winmm.timeBeginPeriod(1)
        if rc != 0:
            print(f"timeBeginPeriod(1) returned {rc} (non-zero)", file=sys.stderr)
    except Exception as exc:
        print(f"timeBeginPeriod(1) failed: {exc!r}", file=sys.stderr)
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        # GetCurrentProcess returns the pseudo-handle (-1, no Close needed).
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        HIGH_PRIORITY_CLASS = 0x00000080
        ok = kernel32.SetPriorityClass(
            kernel32.GetCurrentProcess(), HIGH_PRIORITY_CLASS
        )
        if not ok:
            err = ctypes.get_last_error()
            print(
                f"SetPriorityClass(HIGH) failed (GetLastError={err})",
                file=sys.stderr,
            )
    except Exception as exc:
        print(f"SetPriorityClass(HIGH) failed: {exc!r}", file=sys.stderr)


if __name__ == "__main__":
    # Required for ``multiprocessing.spawn`` on Windows. Must be the
    # first thing that runs in the parent so the child detects it's
    # a re-import and skips main() rather than re-launching the GUI
    # in every writer subprocess.
    import multiprocessing
    multiprocessing.freeze_support()

    # Apply *before* any KAYA / Qt import so the SDK's internal
    # threads inherit the elevated priority and benefit from the
    # 1 ms timer immediately.
    _apply_windows_realtime_tweaks()

    # blosc2 internal SMP for the per-frame encode. Default of 2 in
    # raw_bay_writer.compress_frame_blosc2 was chosen for synthetic
    # bench scenes (~285 ms encode); high-entropy real scenes can
    # hit ~720 ms which underruns the 4.8 fps trigger rate. Bumping
    # to 4 roughly halves encode latency on this CPU class. setdefault
    # lets the operator still override via shell env.
    #
    # Spawn note: on Windows ``multiprocessing.spawn`` re-execs the
    # child with the parent's environment, so packer subprocesses
    # inherit this value at import time when they call
    # ``compress_frame_blosc2``.
    os.environ.setdefault("BAY14_BLOSC2_NTHREADS", "4")

    # Heavy imports (Qt, KAYA SDK) are deferred to the parent only --
    # see the spawn note in the module docstring.
    from capture.app import main

    sys.exit(main())
