"""Self-checks for the AMT203S encoder trackers (no hardware).

* :class:`capture.focus_axis.EncoderTracker` -- parses/unwraps the ``$ENC=n*``
  stream (a 12-bit angle that rolls over every motor revolution) into a monotonic
  count, and reports absence on the firmware ``-1`` sentinel.
* :class:`capture.focus_axis.MultiEncoderTracker` -- routes the shared stream by
  its ``$ENC=<cam>,<pos>*`` camera tag, so the three encoders on one SPI bus each
  get their own accumulator and a camera switch cannot leak counts between them.

Run: ``py -3 tools/test_encoder_axis.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

from capture.focus_axis import EncoderTracker, MultiEncoderTracker  # noqa: E402

# ── EncoderTracker ───────────────────────────────────────────────────────────


def test_tracker_unwraps_both_directions() -> None:
    t = EncoderTracker(counts_per_rev=4096)
    t.update_raw(4090)          # seed
    assert t.position == 0 and t.present
    t.update_raw(10)            # +16 across the top (4090 -> 4106 == 10)
    assert t.position == 16, t.position
    t.update_raw(4090)          # -16 back down across the top
    assert t.position == 0, t.position
    t.update_raw(4080)          # plain -10, no wrap
    assert t.position == -10, t.position


def test_tracker_parses_line_and_handles_sentinel() -> None:
    t = EncoderTracker()
    assert t.update_line("garbage without enc") is False
    assert t.present is False
    assert t.update_line("$ENC=1919*") is True
    assert t.present and t.position == 0  # first real reading seeds at 0
    assert t.update_line("$ENC=1925*") is True
    assert t.position == 6, t.position
    # firmware sentinel: encoder stopped responding
    assert t.update_line("$ENC=-1*") is True
    assert t.present is False


# ── MultiEncoderTracker ──────────────────────────────────────────────────────


def test_multi_routes_by_camera_tag() -> None:
    t = MultiEncoderTracker()
    assert t.reported_camera is None
    assert t.update_line("$ENC=1,1000*") == 1
    assert t.update_line("$ENC=1,1010*") == 1
    assert t.position(1) == 10, t.position(1)
    assert t.reported_camera == 1
    # A different camera starts from its own zero and leaves camera 1 alone.
    assert t.update_line("$ENC=2,3000*") == 2
    assert t.update_line("$ENC=2,2990*") == 2
    assert t.position(2) == -10, t.position(2)
    assert t.position(1) == 10, "camera 1 moved when camera 2 reported"
    assert t.reported_camera == 2
    assert t.present(1) and t.present(2)
    assert not t.present(3), "camera 3 never reported yet claims a reading"
    assert t.position(3) == 0


def test_multi_switch_cannot_leak_counts() -> None:
    """The bug this class exists to prevent.

    One accumulator would fold camera 2's absolute angle in by shortest path from
    camera 1's last reading and invent up to half a revolution of travel. Here the
    two shafts sit at opposite ends of the range, which is the worst case."""
    t = MultiEncoderTracker()
    for _ in range(3):  # bounce between cameras at maximum angular distance
        t.update_line("$ENC=1,10*")
        t.update_line("$ENC=2,2058*")
    assert t.position(1) == 0, t.position(1)
    assert t.position(2) == 0, t.position(2)

    # Same stream through a single tracker: ~2048 counts of pure fiction.
    single = EncoderTracker()
    for _ in range(3):
        single.update_line("$ENC=1,10*")
        single.update_line("$ENC=2,2058*")
    assert abs(single.position) > 1000, (
        "single tracker no longer conflates shafts -- is this test still needed?"
    )


def test_multi_untagged_line_credited_to_default_camera() -> None:
    """Old single-encoder firmware emits ``$ENC=<pos>*`` with no camera."""
    t = MultiEncoderTracker()
    assert t.update_line("$ENC=500*", default_cam=2) == 2
    assert t.update_line("$ENC=520*", default_cam=2) == 2
    assert t.position(2) == 20, t.position(2)
    assert t.position(1) == 0
    # No camera at all, tagged or defaulted: nothing to credit it to.
    assert t.update_line("$ENC=520*") is None
    # A camera we do not track is ignored rather than crashing.
    assert t.update_line("$ENC=7,100*") is None
    assert t.update_line("not an encoder line") is None


def test_multi_sentinel_does_not_refresh_age() -> None:
    """A ``-1`` proves the bus answered but the encoder did not, so the count is
    no fresher than before -- otherwise a dead encoder would look live forever."""
    t = MultiEncoderTracker()
    t.update_line("$ENC=1,1000*")
    assert t.present(1)
    age_before = t.age_s(1)
    assert age_before is not None
    t.update_line("$ENC=1,-1*")
    assert not t.present(1), "sentinel left the camera marked present"
    assert (t.age_s(1) or 0.0) >= age_before, "failed read counted as fresh"
    assert t.age_s(3) is None, "camera that never reported has an age"


def test_multi_reset_drops_counts_and_freshness() -> None:
    t = MultiEncoderTracker()
    t.update_line("$ENC=1,1000*")
    t.update_line("$ENC=1,1010*")
    assert t.position(1) == 10
    t.reset()
    assert t.position(1) == 0 and not t.present(1)
    assert t.age_s(1) is None and t.reported_camera is None
    # And it still tracks afterwards.
    t.update_line("$ENC=1,2000*")
    t.update_line("$ENC=1,2005*")
    assert t.position(1) == 5, t.position(1)


def main() -> int:
    for t in (
        test_tracker_unwraps_both_directions,
        test_tracker_parses_line_and_handles_sentinel,
        test_multi_routes_by_camera_tag,
        test_multi_switch_cannot_leak_counts,
        test_multi_untagged_line_credited_to_default_camera,
        test_multi_sentinel_does_not_refresh_age,
        test_multi_reset_drops_counts_and_freshness,
    ):
        t()
        print(f"  ok: {t.__name__}")
    print("encoder-tracker tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
