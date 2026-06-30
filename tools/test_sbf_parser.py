"""Unit tests for the SBF stream parser.

Run with ``py -3 tools/test_sbf_parser.py``. No external SBF capture is
required; tests synthesise their own blocks with the helper encoder
co-located in :mod:`capture.sbf_parser`.
"""

from __future__ import annotations

import os
import sys
import struct
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, ROOT)

from capture.sbf_parser import (  # noqa: E402
    BLOCK_ID_EXT_EVENT_PVT_GEODETIC,
    DEFAULT_LEAP_SECONDS,
    HEADER_LEN,
    SYNC,
    SbfParser,
    crc16_ccitt,
    encode_block,
    encode_ext_event_pvt_geodetic,
)


class CrcTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(crc16_ccitt(b""), 0)

    def test_known_xmodem_vector(self) -> None:
        # "123456789" is the canonical CRC-16/XMODEM check vector and
        # CRC-CCITT (with seed 0, no reflection) is bit-identical to it.
        # Expected value: 0x31C3.
        self.assertEqual(crc16_ccitt(b"123456789"), 0x31C3)


class FramingTests(unittest.TestCase):
    def test_decode_single_block(self) -> None:
        blob = encode_ext_event_pvt_geodetic(
            tow_ms=123_456_789, wnc=2345,
            latitude_deg=32.0853, longitude_deg=34.7818, height_m=41.2,
            n_sv=11,
        )
        parser = SbfParser()
        events = parser.feed(blob)
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt.tow_ms, 123_456_789)
        self.assertEqual(evt.wnc, 2345)
        self.assertAlmostEqual(evt.latitude_deg, 32.0853, places=8)
        self.assertAlmostEqual(evt.longitude_deg, 34.7818, places=8)
        self.assertAlmostEqual(evt.height_m, 41.2, places=6)
        self.assertEqual(evt.n_sv, 11)
        self.assertTrue(evt.is_valid_fix)
        self.assertEqual(parser.good_blocks, 1)

    def test_byte_at_a_time_streaming(self) -> None:
        blob = encode_ext_event_pvt_geodetic(tow_ms=1000, wnc=2300)
        parser = SbfParser()
        all_events = []
        for b in blob:
            all_events.extend(parser.feed(bytes([b])))
        self.assertEqual(len(all_events), 1)
        self.assertEqual(all_events[0].tow_ms, 1000)

    def test_multiple_blocks_in_one_feed(self) -> None:
        blobs = b"".join(
            encode_ext_event_pvt_geodetic(tow_ms=1000 + i, wnc=2300)
            for i in range(5)
        )
        parser = SbfParser()
        events = parser.feed(blobs)
        self.assertEqual(len(events), 5)
        self.assertEqual([e.tow_ms for e in events], list(range(1000, 1005)))

    def test_garbage_before_sync_is_skipped(self) -> None:
        blob = encode_ext_event_pvt_geodetic(tow_ms=42, wnc=2300)
        junk = b"\xde\xad\xbe\xef" + b"$" + b"\xff" * 7
        parser = SbfParser()
        events = parser.feed(junk + blob)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].tow_ms, 42)
        self.assertGreater(parser.skipped_bytes, 0)

    def test_bad_crc_is_rejected(self) -> None:
        blob = bytearray(encode_ext_event_pvt_geodetic(tow_ms=42, wnc=2300))
        # Flip a byte in the payload; CRC must reject the block.
        blob[20] ^= 0x5A
        parser = SbfParser()
        events = parser.feed(bytes(blob))
        self.assertEqual(len(events), 0)
        self.assertGreaterEqual(parser.bad_crc, 1)

    def test_bad_length_is_rejected(self) -> None:
        # Manually craft a header with a non-multiple-of-4 length.
        bogus = SYNC + b"\x00\x00" + struct.pack("<HH", 4038, 9) + b"\x00"
        parser = SbfParser()
        events = parser.feed(bogus)
        self.assertEqual(len(events), 0)
        self.assertGreaterEqual(parser.bad_length, 1)

    def test_unknown_block_is_consumed(self) -> None:
        # Encode some other block (PVTGeodetic, id 4007) and confirm
        # the parser consumes it without raising and without yielding
        # an event.
        body = b"\x00" * 100
        blob = encode_block(4007, body)
        parser = SbfParser()
        events = parser.feed(blob)
        self.assertEqual(len(events), 0)
        self.assertEqual(parser.good_blocks, 1)
        self.assertEqual(parser.events_decoded, 0)

    def test_resync_after_false_sync(self) -> None:
        # A "$@" pair that's actually part of payload (false sync)
        # followed by a real block. The parser must skip past the
        # false sync (bad length / CRC) and decode the real one.
        false_sync = SYNC + b"\x00" * 6  # length=0 in the bogus header → rejected
        real = encode_ext_event_pvt_geodetic(tow_ms=99, wnc=2300)
        parser = SbfParser()
        events = parser.feed(false_sync + real)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].tow_ms, 99)


class DecoderTests(unittest.TestCase):
    def test_utc_datetime_uses_leap_seconds(self) -> None:
        # 1 second past GPS epoch (no week, 1000 ms TOW): expect
        # 1980-01-06 00:00:01 UTC minus leap_seconds.
        blob = encode_ext_event_pvt_geodetic(tow_ms=1000, wnc=0)
        parser = SbfParser()
        evt = parser.feed(blob)[0]
        ts = evt.utc_datetime(leap_seconds=0)
        self.assertIsNotNone(ts)
        self.assertEqual(ts.year, 1980)
        self.assertEqual(ts.month, 1)
        self.assertEqual(ts.day, 6)
        self.assertEqual(ts.second, 1)

    def test_block_revision_in_high_bits(self) -> None:
        # Encode with revision = 2 and confirm the parser strips it.
        blob = encode_ext_event_pvt_geodetic(tow_ms=7, wnc=2300, block_rev=2)
        parser = SbfParser()
        events = parser.feed(blob)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].block_rev, 2)


if __name__ == "__main__":
    unittest.main()
