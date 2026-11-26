import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from unittest.mock import patch
import zlib
import numpy as np
from transfer import LogReceiver
from data import load_csv, Envelope

HEADER = "sample_index,mcu_read_us,sensor_ticks,timestamp_raw,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,temp_raw,valid,timestamp_anomaly,temperature_valid,sensor_id\n"


def fixture():
    lines = ["# DualCsvLogger 2.0; nominal_odr_hz=16000\n", HEADER]
    for i in range(8):
        for sensor in (1, 3):
            lines.append(f"{i},{1000000+i*62+sensor*10},{i*59},{i*59},2048,-2048,0,16384,-16384,0,{-128 if i%2 else 0},1,0,{0 if i%2 else 1},{sensor}\n")
    lines.append("# END reason=STOP samples=16 csv_rows=16\n")
    return "".join(lines).encode()


def frame(payload, crc=None):
    if crc is None:
        crc = zlib.crc32(payload)
    return (f"=== LOG_CSV_BEGIN bytes={len(payload)} ===\r\n".encode() + payload +
            f"\r\n=== LOG_CSV_END bytes={len(payload)} crc32={crc:08X} ===\r\nWAIT_LOG_ACK\r\n".encode())


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.events, self.acks = [], []
        def ack(data):
            # At ACK time the permanent complete file already exists on disk.
            self.assertTrue(list(self.folder.glob("*.csv")))
            self.assertIsNone(self.receiver.part)
            self.acks.append(data)
        self.receiver = LogReceiver(self.folder, lambda k, v: self.events.append((k, v)), ack)

    def tearDown(self):
        if self.receiver.file:
            self.receiver.file.close()
        self.temp.cleanup()

    def test_every_byte_fragmented_and_unique_filenames(self):
        payload = fixture()
        for _ in range(2):
            for byte in frame(payload):
                self.receiver.feed(bytes([byte]))
        files = list(self.folder.glob("*.csv"))
        self.assertEqual(len(files), 2)
        self.assertTrue(all(p.read_bytes() == payload for p in files))
        self.assertEqual(self.acks, [f"ACK_LOG {len(payload)} {zlib.crc32(payload):08X}\n".encode()]*2)
        self.assertEqual(sum(k == "saved" for k, _ in self.events), 2)

    def test_crc_failure_preserves_part_and_no_ack(self):
        self.receiver.feed(frame(fixture(), crc=0))
        self.assertFalse(self.acks)
        self.assertEqual(len(list(self.folder.glob("*.part"))), 1)
        self.assertFalse(list(self.folder.glob("*.csv")))

    def test_disconnect_and_retry(self):
        payload = fixture()
        self.receiver.feed(frame(payload)[:200])
        self.receiver.abort("disconnect")
        self.assertFalse(self.acks)
        self.receiver.feed(frame(payload))
        self.assertEqual(len(self.acks), 1)
        self.assertEqual(len(list(self.folder.glob("*.part"))), 1)

    def test_sync_failure_never_acknowledged(self):
        with patch("transfer.os.fsync", side_effect=OSError("disk full")):
            self.receiver.feed(frame(fixture()))
        self.assertFalse(self.acks)
        self.assertTrue(list(self.folder.glob("*.part")))

    def test_wrong_footer_size(self):
        wire = frame(fixture()).replace(b"LOG_CSV_END bytes=", b"LOG_CSV_END bytes=9")
        self.receiver.feed(wire)
        self.assertFalse(self.acks)

    def test_zero_length_and_payload_with_marker_text(self):
        self.receiver.feed(frame(b""))
        payload = b"# === LOG_CSV_END bytes=0 crc32=00000000 ===\n\r\n"
        self.receiver.feed(frame(payload))
        self.assertEqual(len(self.acks), 2)
        self.assertIn(payload, [p.read_bytes() for p in self.folder.glob("*.csv")])

    def test_large_payload_streams_without_buffering_entire_log(self):
        payload = fixture()*2500
        wire = frame(payload)
        for n in range(0, len(wire), 32768):
            self.receiver.feed(wire[n:n+32768])
            self.assertLess(len(self.receiver.buffer), 8192)
        self.assertEqual(len(self.acks), 1)
        self.assertEqual(next(self.folder.glob("*.csv")).read_bytes(), payload)

    def test_midstream_connection_does_not_flood_ui_or_ack(self):
        row = b"10,1000,100,100,0,0,2048,0,0,0,-128,1,0,0,1\n"
        self.receiver.feed(row*5000)
        self.assertFalse(self.acks)
        self.assertEqual(len(self.events), 1)
        self.receiver.feed(frame(fixture()))
        self.assertEqual(len(self.acks), 1)


class DataTests(unittest.TestCase):
    def test_two_sensors_scaling_missing_temperature(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"log.csv"
            path.write_bytes(fixture())
            data = load_csv(path)
        self.assertEqual(set(data.sensors), {1, 3})
        self.assertTrue(data.clean_stop)
        for s in data.sensors.values():
            self.assertEqual(s.count, 8)
            self.assertEqual(s.motion_invalid, 0)
            self.assertEqual(s.temp_missing, 4)
            self.assertEqual(s.sequence_errors, 0)
            np.testing.assert_equal(s.plot.low[:, 0], 1)
            np.testing.assert_equal(s.plot.low[:, 3], 1000)
            self.assertTrue(np.isnan(s.plot.low[1, 6]))

    def test_legacy_temperature_flag_does_not_discard_motion(self):
        text = fixture().decode()
        lines = []
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            cols = line.split(",")
            if cols[-1] == "3":
                continue
            cols = cols[:-2]  # original 13-column format, no temperature_valid/sensor_id
            if cols[0].isdigit():
                cols[11] = "0" if cols[10] == "-128" else "1"
            lines.append(",".join(cols))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"old.csv"
            path.write_text("\n".join(lines))
            result = load_csv(path)
        self.assertFalse(result.clean_stop)
        self.assertEqual(result.sensors[1].motion_invalid, 0)
        self.assertEqual(result.sensors[1].temp_missing, 4)

    def test_envelope_retains_spikes_and_is_bounded(self):
        e = Envelope(max_bins=50)
        y = np.zeros((5000, 7));y[104, 0] = 900;y[4855, 0] = -700
        for start in range(0, len(y), 137):
            e.add(np.arange(start, min(start+137, len(y))), y[start:start+137])
        self.assertLessEqual(len(e.keys), 50)
        self.assertEqual(np.max(e.high[:, 0]), 900)
        self.assertEqual(np.min(e.low[:, 0]), -700)
        self.assertEqual(e.total, 5000)

    def test_bad_header_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"excerpt.csv";path.write_text("23,5,7,8\n")
            with self.assertRaisesRegex(ValueError, "header"):
                load_csv(path)

    def test_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"log.csv";path.write_bytes(fixture())
            with self.assertRaises(InterruptedError):
                load_csv(path, cancelled=lambda: True)


if __name__ == "__main__":
    unittest.main()
