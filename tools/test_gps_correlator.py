"""Unit tests for the slot-based GPS-frame correlator.

Run with ``python tools/test_gps_correlator.py -v``. Synthetic events
are minted via :func:`capture.sbf_parser.encode_ext_event_pvt_geodetic`
fed through a parser, so the test exercises the same datatypes the live
pipeline sees.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, ROOT)

from capture.gps_correlator import (  # noqa: E402
    FUTURE_MS,
    GpsCorrelator,
    STALE_AHEAD_MS,
    SWEEP_GAP_MS,
    _initial_bearing_deg,
)
from capture.sbf_parser import SbfParser, encode_ext_event_pvt_geodetic  # noqa: E402


CAM_A = "2439006"
CAM_B = "2439007"
CAM_C = "2439008"

NS_MS = 1_000_000
SWEEP_SIZE = 13


def make_event(tow_ms: int, lat: float, lon: float):
    parser = SbfParser()
    blob = encode_ext_event_pvt_geodetic(
        tow_ms=tow_ms, wnc=2345,
        latitude_deg=lat, longitude_deg=lon, height_m=41.0, n_sv=10,
    )
    return parser.feed(blob)[0]


class InOrderTests(unittest.TestCase):
    def test_all_three_cameras_get_same_fix(self) -> None:
        """One trigger pulse -> one GPS event -> all 3 cams see same fix."""
        corr = GpsCorrelator()
        corr.set_connected(True)
        evt = make_event(tow_ms=100_000, lat=10.0, lon=20.0)
        corr.add_event(evt, host_perf_ns=1_000_000_000)
        frame_t = 1_300_000_000  # ~300 ms after the event
        for cam in (CAM_A, CAM_B, CAM_C):
            fix = corr.pop_fix_for(cam, frame_t, sweep=1, frame_in_sweep=1)
            self.assertIsNotNone(fix)
            self.assertAlmostEqual(fix.latitude_deg, 10.0, places=8)
            self.assertAlmostEqual(fix.longitude_deg, 20.0, places=8)

    def test_sequential_events_in_one_sweep(self) -> None:
        """13 events arrive 200 ms apart; each cam frame matches by slot."""
        corr = GpsCorrelator()
        corr.set_connected(True)
        base_t = 1_000_000_000
        for i in range(SWEEP_SIZE):
            corr.add_event(
                make_event(tow_ms=100_000 + 200 * i, lat=10.0 + i, lon=20.0),
                host_perf_ns=base_t + i * 200 * NS_MS,
            )
        for i in range(SWEEP_SIZE):
            ft = base_t + i * 200 * NS_MS + 300 * NS_MS
            fix = corr.pop_fix_for(CAM_A, ft, sweep=1, frame_in_sweep=i + 1)
            self.assertIsNotNone(fix, f"miss at slot {i + 1}")
            self.assertAlmostEqual(fix.latitude_deg, 10.0 + i, places=6)


class FuturePastTests(unittest.TestCase):
    def test_future_event_returns_none_without_consuming(self) -> None:
        """Frame arrives before its event was decoded -> None now, match next call."""
        corr = GpsCorrelator()
        corr.set_connected(True)
        base_t = 1_000_000_000
        # Event 1 s in the future relative to the frame.
        corr.add_event(
            make_event(tow_ms=100_000, lat=10.0, lon=20.0),
            host_perf_ns=base_t + 1_000_000_000,
        )
        fix = corr.pop_fix_for(CAM_A, base_t, sweep=1, frame_in_sweep=1)
        self.assertIsNone(fix)
        # ~1.1 s later: event is now ~100 ms in the past; should match.
        fix = corr.pop_fix_for(
            CAM_A, base_t + 1_100_000_000, sweep=1, frame_in_sweep=1,
        )
        self.assertIsNotNone(fix)

    def test_stale_event_rejected(self) -> None:
        """An event older than STALE_AHEAD_MS is not used."""
        corr = GpsCorrelator()
        corr.set_connected(True)
        corr.add_event(
            make_event(tow_ms=42, lat=10.0, lon=20.0),
            host_perf_ns=0,
        )
        # Frame arrives 2 s later -- well past STALE_AHEAD_MS = 1500 ms.
        frame_t = int(2_000 * NS_MS)
        fix = corr.pop_fix_for(CAM_A, frame_t, sweep=1, frame_in_sweep=1)
        self.assertIsNone(fix)
        st = corr.stats()["cameras"][CAM_A]
        self.assertGreaterEqual(st["stale"], 1)


class SweepBoundaryTests(unittest.TestCase):
    def test_inter_sweep_gap_starts_new_generation(self) -> None:
        """After a SWEEP_GAP_MS gap, slot-1 maps to the new sweep's event."""
        corr = GpsCorrelator()
        corr.set_connected(True)
        base_t = 1_000_000_000
        # Sweep 1: 13 events.
        for i in range(SWEEP_SIZE):
            corr.add_event(
                make_event(tow_ms=100_000 + 200 * i, lat=10.0 + i * 0.1, lon=20.0),
                host_perf_ns=base_t + i * 200 * NS_MS,
            )
        # Cam A consumes 13 frames of sweep 1.
        for i in range(SWEEP_SIZE):
            ft = base_t + i * 200 * NS_MS + 300 * NS_MS
            f = corr.pop_fix_for(CAM_A, ft, sweep=1, frame_in_sweep=i + 1)
            self.assertIsNotNone(f)

        # Sweep 2 begins after a 100 ms inter-sweep pause + 200 ms slot
        # = 300 ms gap, which exceeds SWEEP_GAP_MS (=250 ms).
        gap_ns = int(SWEEP_GAP_MS * NS_MS) + 50 * NS_MS
        sweep2_event_t = base_t + 13 * 200 * NS_MS + gap_ns
        corr.add_event(
            make_event(tow_ms=200_000, lat=99.0, lon=20.0),
            host_perf_ns=sweep2_event_t,
        )
        # Cam A's sweep-2 frame 1 (300 ms after the new event).
        ft = sweep2_event_t + 300 * NS_MS
        f = corr.pop_fix_for(CAM_A, ft, sweep=2, frame_in_sweep=1)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f.latitude_deg, 99.0, places=6)
        # And cam B (which never queried sweep 1) should also get
        # sweep 2's event 1 for its first frame.
        f_b = corr.pop_fix_for(CAM_B, ft, sweep=2, frame_in_sweep=1)
        self.assertAlmostEqual(f_b.latitude_deg, 99.0, places=6)

    def test_cam_falling_behind_still_aligns_at_next_sweep(self) -> None:
        """Even if a camera only queries some sweep-1 frames, sweep-2
        frame 1 still picks the new generation's event.
        """
        corr = GpsCorrelator()
        corr.set_connected(True)
        base_t = 1_000_000_000
        for i in range(SWEEP_SIZE):
            corr.add_event(
                make_event(tow_ms=100_000 + 200 * i, lat=10.0 + i * 0.1, lon=20.0),
                host_perf_ns=base_t + i * 200 * NS_MS,
            )
        # Cam B only queries 11 of the 13 frames.
        for i in range(11):
            ft = base_t + i * 200 * NS_MS + 300 * NS_MS
            corr.pop_fix_for(CAM_B, ft, sweep=1, frame_in_sweep=i + 1)

        gap_ns = int(SWEEP_GAP_MS * NS_MS) + 50 * NS_MS
        sweep2_t = base_t + 13 * 200 * NS_MS + gap_ns
        corr.add_event(
            make_event(tow_ms=200_000, lat=99.0, lon=20.0),
            host_perf_ns=sweep2_t,
        )
        ft = sweep2_t + 300 * NS_MS
        f = corr.pop_fix_for(CAM_B, ft, sweep=2, frame_in_sweep=1)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f.latitude_deg, 99.0, places=6)


class TowSegmentationTests(unittest.TestCase):
    """Sweeps are segmented by GPS-clock TOW, not host-arrival time.

    Regression for the USB-CDC batching bug: the receiver delivers a
    whole ``read()`` worth of events with a single host timestamp, so
    host-arrival gaps are 0 ms within a batch and huge between batches.
    Segmentation must rely on the per-event TOW instead.
    """

    def test_batched_host_timestamps_dont_split_a_sweep(self) -> None:
        corr = GpsCorrelator()
        corr.set_connected(True)
        # One physical sweep of 13 events, TOW a uniform 230 ms apart
        # (all intra-sweep). Host arrival is batched: first 7 events share
        # t=0, the rest share t=5 s -- a 5 s host gap mid-sweep that the
        # old host-gap rule would have mis-read as a new generation.
        host_batches = [0] * 7 + [5_000_000_000] * 6
        for i in range(SWEEP_SIZE):
            corr.add_event(
                make_event(tow_ms=100_000 + 230 * i, lat=10.0 + i, lon=20.0),
                host_perf_ns=host_batches[i],
            )
        # Every slot 1..13 must resolve to its own event in ONE sweep.
        for i in range(SWEEP_SIZE):
            ft = host_batches[i] + 300 * NS_MS
            fix = corr.pop_fix_for(CAM_A, ft, sweep=1, frame_in_sweep=i + 1)
            self.assertIsNotNone(fix, f"miss at slot {i + 1}")
            self.assertAlmostEqual(fix.latitude_deg, 10.0 + i, places=6)
        self.assertEqual(corr.stats()["gps_sweep_gen"], 1)

    def test_tow_gap_starts_new_sweep_even_with_zero_host_gap(self) -> None:
        corr = GpsCorrelator()
        corr.set_connected(True)
        # Two sweeps, every event batched at the SAME host timestamp so
        # host gaps are all 0. Only the TOW gap (350 ms inter-sweep vs
        # 230 ms intra) marks the boundary.
        tows = [100_000 + 230 * i for i in range(SWEEP_SIZE)]
        tows += [tows[-1] + 350 + 230 * i for i in range(SWEEP_SIZE)]
        for tow in tows:
            corr.add_event(
                make_event(tow_ms=tow, lat=1.0, lon=2.0),
                host_perf_ns=1_000_000_000,
            )
        self.assertEqual(corr.stats()["gps_sweep_gen"], 2)


class MissedEventTests(unittest.TestCase):
    def test_missing_slot_falls_back_to_latest_fix(self) -> None:
        """No entry for this slot, but a fresh fix exists -> fallback.

        This is the out-of-phase case that left whole lines with no GPS:
        the camera's frame_in_sweep no longer lines up with any slot key,
        yet a recent fix is sitting in the table. The latest-fix fallback
        supplies it rather than writing the frame with no position.
        """
        corr = GpsCorrelator()
        corr.set_connected(True)
        corr.add_event(
            make_event(tow_ms=100_000, lat=10.0, lon=20.0),
            host_perf_ns=1_000_000_000,
        )
        # Cam asks for slot 1 -> direct slot match.
        f = corr.pop_fix_for(CAM_A, 1_300_000_000, sweep=1, frame_in_sweep=1)
        self.assertIsNotNone(f)
        # Cam asks for slot 2 -> no slot-2 entry, but the slot-1 fix is
        # still fresh (200 ms old), so the fallback returns it.
        f = corr.pop_fix_for(CAM_A, 1_500_000_000, sweep=1, frame_in_sweep=2)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f.latitude_deg, 10.0, places=6)
        st = corr.stats()["cameras"][CAM_A]
        self.assertGreaterEqual(st["fallback_latest"], 1)
        self.assertEqual(st["no_event"], 0)

    def test_missing_slot_returns_none_when_latest_stale(self) -> None:
        """No slot entry and the only fix is older than the fallback
        window -> genuine miss, frame goes out with no GPS.
        """
        corr = GpsCorrelator()
        corr.set_connected(True)
        corr.add_event(
            make_event(tow_ms=100_000, lat=10.0, lon=20.0),
            host_perf_ns=0,
        )
        # Frame 3 s later: no slot-2 entry and the latest fix is far past
        # the fallback bound, so the fallback declines.
        f = corr.pop_fix_for(CAM_A, int(3_000 * NS_MS), sweep=1, frame_in_sweep=2)
        self.assertIsNone(f)
        st = corr.stats()["cameras"][CAM_A]
        self.assertGreaterEqual(st["no_event"], 1)
        self.assertEqual(st["fallback_latest"], 0)

    def test_fallback_can_be_disabled(self) -> None:
        """``latest_fallback_ms=0`` restores the strict slot-only contract."""
        corr = GpsCorrelator(latest_fallback_ms=0)
        corr.set_connected(True)
        corr.add_event(
            make_event(tow_ms=100_000, lat=10.0, lon=20.0),
            host_perf_ns=1_000_000_000,
        )
        f = corr.pop_fix_for(CAM_A, 1_300_000_000, sweep=1, frame_in_sweep=1)
        self.assertIsNotNone(f)
        # Slot 2 missing, fresh latest exists, but fallback is off -> None.
        f = corr.pop_fix_for(CAM_A, 1_500_000_000, sweep=1, frame_in_sweep=2)
        self.assertIsNone(f)
        st = corr.stats()["cameras"][CAM_A]
        self.assertEqual(st["fallback_latest"], 0)
        self.assertGreaterEqual(st["no_event"], 1)


class HeadingTests(unittest.TestCase):
    def test_bearing_north(self) -> None:
        brg = _initial_bearing_deg(32.0, 34.0, 33.0, 34.0)
        self.assertAlmostEqual(brg, 0.0, delta=2.0)

    def test_running_average_heading(self) -> None:
        corr = GpsCorrelator()
        self.assertIsNone(corr.heading_deg())
        # Move ~1 km east from a base point (well above min displacement).
        base_lat, base_lon = 32.0853, 34.7818
        lats = [base_lat] * 4
        lons = [base_lon, base_lon + 0.02, base_lon + 0.04, base_lon + 0.06]
        t0 = 1_000_000_000
        for i, (lat, lon) in enumerate(zip(lats, lons)):
            evt = make_event(tow_ms=100_000 + 200 * i, lat=lat, lon=lon)
            corr.add_event(evt, host_perf_ns=t0 + i * 200 * 1_000_000)
        h = corr.heading_deg()
        self.assertIsNotNone(h)
        # Predominantly eastward track -> heading near 90 deg.
        self.assertGreater(h, 70.0)
        self.assertLess(h, 110.0)

    def test_stationary_no_heading_update(self) -> None:
        corr = GpsCorrelator()
        lat, lon = 32.0, 34.0
        for i in range(4):
            evt = make_event(tow_ms=100_000 + i, lat=lat, lon=lon)
            corr.add_event(evt, host_perf_ns=1_000_000_000 + i * 200_000_000)
        self.assertIsNone(corr.heading_deg())


class StatsTests(unittest.TestCase):
    def test_latest_fix_independent_of_consumption(self) -> None:
        corr = GpsCorrelator()
        corr.set_connected(True)
        evt = make_event(tow_ms=42, lat=10.0, lon=20.0)
        corr.add_event(evt, host_perf_ns=1_000_000_000)
        latest = corr.latest_fix()
        self.assertIsNotNone(latest)
        # Consuming on one camera doesn't change latest_fix for the overlay.
        corr.pop_fix_for(CAM_A, 1_300_000_000, sweep=1, frame_in_sweep=1)
        latest2 = corr.latest_fix()
        # GpsFix is frozen so identity comparison would fail, but both
        # should encode to the same dict.
        self.assertEqual(latest.to_dict(), latest2.to_dict())

    def test_age_of_latest_grows(self) -> None:
        corr = GpsCorrelator()
        self.assertIsNone(corr.age_of_latest_ns())
        evt = make_event(tow_ms=42, lat=10.0, lon=20.0)
        corr.add_event(evt, host_perf_ns=1_000_000_000)
        # Pretend "now" is 500 ms later.
        age = corr.age_of_latest_ns(now_perf_ns=1_500_000_000)
        self.assertEqual(age, 500_000_000)


if __name__ == "__main__":
    unittest.main()
