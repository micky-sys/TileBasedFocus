"""
Phase 0: GenTL smoke test — list devices, open one Iron 661 / Komodo stream,
read key GenICam nodes, optionally grab one buffer.

Prerequisites (Windows x64):
  - Python 3.10+ 64-bit
  - KAYA Vision Point II + driver installed; Komodo visible in Device Manager
  - pip install -r requirements.txt

Find the producer (.cti):
  - Often under KAYA install, e.g. ...\\Common\\bin\\KYVPGenTL_vc141.cti
  - Or set user env GENICAM_GENTL64_PATH to the folder containing .cti files

Usage:
  python phase0_gentl_smoke.py [path\\to\\KYVPGenTL_vc141.cti]
  python phase0_gentl_smoke.py   # uses GENICAM_GENTL64_PATH + first *.cti found

Trigger note: If acquisition is external Line1, fetch may block until triggers
arrive; use Ctrl+C or wire triggers before expecting a frame.
"""

from __future__ import annotations

import os
import sys
import glob
import time


def find_cti_from_env() -> str | None:
    base = os.environ.get("GENICAM_GENTL64_PATH", "").strip()
    if not base or not os.path.isdir(base):
        return None
    matches = sorted(glob.glob(os.path.join(base, "*.cti")))
    return matches[0] if matches else None


def main() -> int:
    try:
        from harvesters.core import Harvester
    except ImportError:
        print("Install Harvesters:  pip install -r requirements.txt", file=sys.stderr)
        return 1

    from kaya_harvesters_patch import apply_kaya_gentl_patch

    apply_kaya_gentl_patch()

    cti = sys.argv[1] if len(sys.argv) > 1 else None
    if not cti:
        cti = find_cti_from_env()
    if not cti or not os.path.isfile(cti):
        print(
            "Pass the KAYA .cti path as the first argument, or set GENICAM_GENTL64_PATH.\n"
            "Example:\n"
            r'  python phase0_gentl_smoke.py "C:\Program Files\KAYA Instruments\Common\bin\KYVPGenTL_vc141.cti"',
            file=sys.stderr,
        )
        return 1

    print(f"CTI: {cti}")

    h = Harvester()
    h.add_file(cti)
    h.update()

    if not h.device_info_list:
        print("No devices — check cables, power, CXP links, and Vision Point / driver.")
        h.reset()
        return 2

    for i, info in enumerate(h.device_info_list):
        print(f"  [{i}] {info}")

    index = 0
    if len(h.device_info_list) > 1:
        try:
            choice = input(f"Open device index [0-{len(h.device_info_list) - 1}] (Enter=0): ").strip()
            if choice:
                index = int(choice)
        except (EOFError, ValueError):
            index = 0

    ia = h.create(index)
    nm = ia.remote_device.node_map

    def show(name: str) -> None:
        try:
            n = getattr(nm, name)
            print(f"  {name}: {n.value}")
        except Exception as e:
            print(f"  {name}: <unavailable: {e}>")

    print("GenICam (common acquisition nodes):")
    for key in (
        "DeviceModelName",
        "Width",
        "Height",
        "PixelFormat",
        "AcquisitionMode",
        "TriggerMode",
        "TriggerSource",
        "TriggerActivation",
        "ExposureTime",
    ):
        show(key)

    print("\nStarting acquisition (waits for frames; external Line1 = need triggers)...")
    ia.start()
    try:
        from genicam.gentl import TimeoutException

        t0 = time.time()
        try:
            with ia.fetch(timeout=10.0) as buffer:
                comp = buffer.payload.components[0]
                dt = time.time() - t0
                print(f"Got buffer in {dt:.2f}s")
                print(f"  shape: {comp.width} x {comp.height}, format: {comp.data_format}")
        except TimeoutException:
            print("No buffer within 10 s (TimeoutException).")
            print("  If TriggerMode=On and source is Line1, fire triggers or use free-run for bench.")
    except Exception as e:
        print(f"Fetch error: {e}")
    finally:
        ia.stop()
        ia.destroy()
        h.reset()

    print("\nPhase 0 GenTL path OK. Record PixelFormat + trigger settings for docs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
