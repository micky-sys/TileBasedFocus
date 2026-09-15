"""The M4 'Record' arm, tested at the one place that decides: can_write().

Worth a test because both failure directions are expensive and silent. If the
gate stays shut, an entire focus bracket flight lands nothing on disk. If it
stays open, M4 -- where the operator spends all the idle ground time -- fills
the drive. The mode-change disarm is the subtle half: without it, arming once
would leave M4 recording forever.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))

from capture.mode_controller import Mode, ModeController  # noqa: E402


def test_m4_does_not_write_until_armed() -> None:
    m = ModeController()
    m.set_mode(Mode.M4)
    assert not m.can_write(), "M4 must not write by default"
    assert not m.m4_recording()

    m.set_m4_recording(True)
    assert m.can_write(), "armed M4 must write"
    assert m.m4_recording()
    assert m.is_recording_mode(), "the arm must read as recording everywhere"
    # M4 stays the focus mode while recording: the preview, the tap and the
    # focus motor all key off these, and arming must not disturb them.
    assert m.is_focus_mode() and m.is_af_mode()

    m.set_m4_recording(False)
    assert not m.can_write()


def test_any_mode_change_disarms() -> None:
    for leave_to in (Mode.M0, Mode.M5, Mode.M3, Mode.UNKNOWN, Mode.M4):
        m = ModeController()
        m.set_mode(Mode.M4)
        m.set_m4_recording(True)
        assert m.can_write()
        m.set_mode(leave_to)
        assert not m.m4_recording(), f"leaving M4 for {leave_to} left it armed"
        # M0 is a recording mode in its own right, so can_write stays true
        # there -- what must not survive is the M4 arm itself.
        if leave_to not in (Mode.M0,):
            assert not m.can_write(), f"{leave_to} still writing after M4 arm"


def test_arm_ignored_outside_m4() -> None:
    for mode in (Mode.M5, Mode.M3, Mode.UNKNOWN):
        m = ModeController()
        m.set_mode(mode)
        m.set_m4_recording(True)
        assert not m.m4_recording(), f"{mode} accepted an M4 arm"
        assert not m.can_write(), f"{mode} started writing on an M4 arm"


def test_m3_arm_still_independent() -> None:
    """The two manual arms must not have been wired into each other."""
    m = ModeController()
    m.set_mode(Mode.M4)
    m.set_m4_recording(True)
    assert not m.m3_collecting()

    m.set_mode(Mode.M3)
    assert not m.can_write()
    m.set_m3_collecting(True)
    assert m.can_write() and not m.m4_recording()


def main() -> int:
    test_m4_does_not_write_until_armed()
    test_any_mode_change_disarms()
    test_arm_ignored_outside_m4()
    test_m3_arm_still_independent()
    print("m4 record gate OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
