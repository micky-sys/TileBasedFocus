"""App camera number <-> PCB slot translation in :class:`SerialClient`.

Regression test for the bug that left every captured frame with no ``focus``
block: serial 2439008 is wired to PCB slot 3, so a rig running that camera
alone has the app calling it cam1 while its ``$ENC`` readings arrive tagged 3.
Crediting the tag straight to app-camera 3 made the real camera look like it
had no encoder, and the GUI reported "enc: <count> (cam3)" for a camera the
rest of the app called cam1.

Run: py tools/test_camera_slot_mapping.py
"""

from __future__ import annotations

import os
import sys


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    for p in (repo_root, os.path.join(repo_root, "host")):
        if p and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_paths()

from capture.mode_controller import ModeController  # noqa: E402
from capture.serial_client import SerialClient  # noqa: E402


class _FakeCtrl:
    """Stands in for FocusController: records what went on the wire."""

    def __init__(self) -> None:
        self.sent: list[int] = []

    def select_camera(self, cam: int) -> None:
        self.sent.append(int(cam))


def _client() -> tuple[SerialClient, _FakeCtrl]:
    c = SerialClient(ModeController())
    fake = _FakeCtrl()
    c._ctrl = fake
    return c, fake


def test_single_camera_in_slot_3() -> None:
    """The rig as it actually ran: one camera, 2439008, which the app calls 1."""
    c, fake = _client()
    c.set_camera_serial(1, "2439008")

    assert c.pcb_slot_for(1) == 3, c.pcb_slot_for(1)
    assert c.camera_for_pcb_slot(3) == 1, c.camera_for_pcb_slot(3)

    # Selecting app cam1 must put slot 3 on the wire -- this part was already
    # right, and driving the wrong lens is the dangerous direction.
    c.select_camera(1)
    assert fake.sent == [3], fake.sent

    # A reading tagged with PCB slot 3 belongs to app camera 1. The tracker
    # accumulates unwrapped TRAVEL, so the first reading is only a baseline.
    c._on_line("$ENC=3,1234*")
    assert c.encoder_present_for(1), "cam1 must own the slot-3 reading"
    assert c.encoder_position_for(1) == 0.0, "first reading is the baseline"
    c._on_line("$ENC=3,1334*")
    assert c.encoder_position_for(1) == 100.0, c.encoder_position_for(1)
    assert c.selected_camera == 1, c.selected_camera
    assert c.encoder_present, "the selected camera's encoder must read as present"
    assert c.encoder_position == 100.0, c.encoder_position

    # And it must NOT be credited to app camera 3, which is what produced the
    # missing focus block.
    assert not c.encoder_present_for(3), "slot 3 is not app camera 3 here"


def test_sentinel_read_is_not_presence() -> None:
    """-1 means the bus answered but the encoder did not; that is not a fix."""
    c, _ = _client()
    c.set_camera_serial(1, "2439008")
    c._on_line("$ENC=3,-1*")
    assert not c.encoder_present_for(1), "a -1 sentinel must not count as present"
    assert c.encoder_age_for(1) is None, c.encoder_age_for(1)


def test_full_rig_in_wiring_order() -> None:
    """Three cameras enumerated in wiring order: the numbers coincide."""
    c, fake = _client()
    for cam, serial in ((1, "2439006"), (2, "2439007"), (3, "2439008")):
        c.set_camera_serial(cam, serial)
    assert [c.pcb_slot_for(n) for n in (1, 2, 3)] == [1, 2, 3]

    c.select_camera(2)
    assert fake.sent == [2], fake.sent
    c._on_line("$ENC=2,77*")
    c._on_line("$ENC=2,177*")
    assert c.encoder_position_for(2) == 100.0, c.encoder_position_for(2)
    assert c.selected_camera == 2


def test_reordered_rig() -> None:
    """Grabber enumeration need not match wiring: cam1 may be slot 3."""
    c, fake = _client()
    c.set_camera_serial(1, "2439008")  # slot 3
    c.set_camera_serial(2, "2439006")  # slot 1

    c.select_camera(2)
    assert fake.sent == [1], fake.sent
    c._on_line("$ENC=1,50*")
    c._on_line("$ENC=1,60*")
    assert c.encoder_position_for(2) == 10.0, "slot 1 belongs to app cam2"
    assert not c.encoder_present_for(1), "app cam1 (slot 3) has not reported"
    assert c.selected_camera == 2


def test_unknown_serial_falls_back_to_identity() -> None:
    """An unrecognised serial must still produce a usable encoder, not none."""
    c, fake = _client()
    c.set_camera_serial(1, "9999999")
    assert c.pcb_slot_for(1) == 1
    c.select_camera(1)
    assert fake.sent == [1], fake.sent
    c._on_line("$ENC=1,9*")
    c._on_line("$ENC=1,19*")
    assert c.encoder_present_for(1)
    assert c.encoder_position_for(1) == 10.0, c.encoder_position_for(1)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("camera slot mapping: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
