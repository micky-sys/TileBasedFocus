"""Bare-bones probe of ``capture.kyfg_backend.KyfgGrabber`` open().

Reproduces the unified app's bring-up sequence in isolation so KAYA
errors print straight to the console instead of getting swallowed into
the worker thread's signal flow. Run with::

    py -3 host/probe_kyfg_backend.py

Cleans up cleanly on success; on failure dumps the traceback verbatim.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    for p in (repo, here):
        if p not in sys.path:
            sys.path.insert(0, p)


def main() -> int:
    _bootstrap_paths()
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("probe")
    bin_path = os.environ.get("KAYA_VISION_POINT_BIN_PATH", "")
    log.info("KAYA_VISION_POINT_BIN_PATH = %r", bin_path)
    if not bin_path:
        log.error("KAYA_VISION_POINT_BIN_PATH is not set; aborting.")
        return 1

    from capture.kyfg_backend import KyfgCamera, KyfgGrabber  # noqa: WPS433

    grabber = KyfgGrabber(grabber_index=0)
    try:
        log.info("Calling grabber.open()...")
        grabber.open()
        log.info("grabber.open() returned. fg_handle=%r", grabber.fg_handle)
        for c in grabber.cameras:
            log.info("  - %s handle=%r", c.label, c.handle)
        if not grabber.cameras:
            log.error("Grabber sees zero cameras.")
            return 2
        first = grabber.cameras[0]
        cam = KyfgCamera(grabber, first)
        log.info("Calling cam.open() for %s...", first.label)
        cam.open()
        log.info("Calling cam.configure()...")
        for line in cam.configure():
            log.info("  cfg: %s", line)
        log.info("Width=%d Height=%d", cam.width, cam.height)

        frame_count = [0]

        def on_frame(small):
            frame_count[0] += 1
            if frame_count[0] in (1, 5, 10, 50, 100):
                log.info("frame %d: shape=%s dtype=%s", frame_count[0],
                         small.shape, small.dtype)

        log.info("Calling cam.arm() -- waiting up to 10s for triggers...")
        cam.arm(on_frame)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            time.sleep(0.5)
            if frame_count[0]:
                log.info("Got %d frames so far", frame_count[0])
        log.info("Final frame count: %d", frame_count[0])
        cam.disarm()
        cam.close()
    except Exception:  # noqa: BLE001
        log.error("PROBE FAILED:")
        traceback.print_exc()
        return 99
    finally:
        try:
            grabber.close()
        except Exception:
            log.warning("Error closing grabber", exc_info=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
