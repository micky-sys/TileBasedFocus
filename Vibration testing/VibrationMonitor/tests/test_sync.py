"""Synthetic truth with different oscillator rates, sample phases and FIFO latency.
No devices are opened. Time matching is tested independently of waveform shape.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
import numpy as np
from data import load_csv
from synchronization import TimeMap

HEADER = "sample_index,mcu_read_us,sensor_ticks,timestamp_raw,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,temp_raw,valid,timestamp_anomaly,temperature_valid,sensor_id,sync_id\n"


def fixture(seconds=0.12, missing=False, bad=False):
    lines = ["# DualCsvLogger 2.2\n", HEADER]
    truth = {}
    # Include >16-bit rollover and a >32-bit MCU clock; sensors drift apart.
    for sensor, period, phase in [(1, 61.6, 11.0), (3, 62.0, 37.0)]:
        times = 5_000_000_000 + phase + np.arange(int(seconds*1e6/period))*period
        ticks = np.floor(np.arange(len(times))*58.59375).astype(np.int64)
        truth[sensor] = times
        tagged = {}
        for ident, edge in enumerate(np.arange(5_000_010_000, times[-1]-1000, 10000), 1):
            idx = int(np.searchsorted(times, edge))
            delta = int(round((times[idx]-edge)/(period/58.59375)))
            tagged[idx] = (ident, delta)
            if not (missing and sensor == 3 and ident == 3):
                lines.append(f"# FSYNC sensor={sensor} id={ident} before_us={int(edge)} bracket_us=0\n")
        for i, (t, tick) in enumerate(zip(times, ticks)):
            ident, stamp = tagged.get(i, (0, (int(tick)+65000)%65536))
            stored_ticks = ticks[i-1] if ident else tick
            # Batch arrival deliberately differs by sensor and is nowhere near
            # microsecond accuracy; reconstructed sample time must ignore it.
            read = int(5_000_000_000+np.ceil((t-5_000_000_000)/500)*500+sensor*70)
            value = int(12000*np.sin(2*np.pi*700*(t-5_000_000_000)/1e6))
            flag = int(bad and sensor == 1 and i == 10)
            lines.append(f"{i},{read},{stored_ticks},{stamp},0,0,2048,{value},0,0,0,1,{flag},1,{sensor},{ident}\n")
    lines.append("# END reason=STOP\n")
    return "".join(lines), truth


class SyncTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"sync.csv"
            path.write_text(text)
            return load_csv(path)

    def test_drift_phase_rollover_and_fifo_latency(self):
        text, truth = fixture()
        data = self.load(text)
        self.assertTrue(data.synchronized, data.timing_note)
        for sensor, view in data.sensors.items():
            self.assertGreater(view.unanchored, 0)
            self.assertTrue(np.all(np.diff(view.plot.first)>0))
            # Nearest actual sample time should be within timestamp quantization,
            # not the 500us read batching or accumulating oscillator drift.
            real = truth[sensor]
            indices = np.rint((view.plot.first-real[0])/(real[1]-real[0])).astype(int)
            self.assertLess(np.max(np.abs(view.plot.first-real[indices])), 2.0)

    def test_bad_metadata_or_discontinuous_data_never_claim_sync(self):
        for options in ({"missing": True}, {"bad": True}, {"seconds": 0.02}):
            text, _ = fixture(**options)
            data = self.load(text)
            self.assertFalse(data.synchronized)
            self.assertIn("NOT VALIDATED", data.timing_note)
            self.assertTrue(all(s.time_map is None for s in data.sensors.values()))

    def test_marker_crosses_chunk_boundary(self):
        cols = {k:i for i,k in enumerate(HEADER.strip().split(','))}
        rows = np.zeros((5,16), dtype=np.int64)
        rows[:,0]=np.arange(5)
        rows[:,2]=[0,59,59,176,235]
        rows[2,3]=20;rows[2,15]=1
        model=TimeMap()
        model.consume(rows[:3],cols)
        self.assertIsNotNone(model.pending)
        model.consume(rows[3:],cols)
        self.assertFalse(model.errors)
        self.assertEqual(model.marks[1],(117.5,97.5))

    def test_cancellation_in_first_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"log.csv";path.write_text(fixture()[0])
            with self.assertRaises(InterruptedError):
                load_csv(path,cancelled=lambda:True)

    def test_longer_file_and_individual_sample_window(self):
        text, _ = fixture(seconds=1.5)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"log.csv";path.write_text(text)
            data=load_csv(path, window=(0.6,0.602))
            self.assertTrue(data.synchronized, data.timing_note)
            for sensor in data.sensors.values():
                self.assertGreater(sensor.count,20000)
                self.assertEqual(sensor.plot.stride,1)
                x,_=sensor.plot.curve(0,data.origin)
                self.assertTrue(np.all((x>=0.6)&(x<=0.602)))
                self.assertTrue(30<=len(x)<=34)
            with self.assertRaises(ValueError):
                load_csv(path,window=(0,4))

if __name__ == "__main__":
    unittest.main()
