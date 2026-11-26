"""Streaming CSV analysis with bounded, extrema-preserving plot storage."""
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
from synchronization import scan_sync

CHANNELS = ["ax_raw", "ay_raw", "az_raw", "gx_raw", "gy_raw", "gz_raw", "temp_raw"]
REQUIRED = {"sample_index", "mcu_read_us", "sensor_ticks", "timestamp_raw", *CHANNELS}


class Envelope:
    def __init__(self, max_bins=60000):
        self.max_bins, self.stride, self.total = max_bins, 1, 0
        self.keys = np.empty(0, dtype=np.int64)
        self.first = self.last = np.empty(0)
        self.low = self.high = np.empty((0, 7))

    def _merge(self, keys, first, last, low, high):
        starts = np.r_[0, np.flatnonzero(np.diff(keys)) + 1]
        ends = np.r_[starts[1:]-1, len(keys)-1]
        self.keys, self.first, self.last = keys[starts], first[starts], last[ends]
        self.low = np.fmin.reduceat(low, starts, axis=0)
        self.high = np.fmax.reduceat(high, starts, axis=0)

    def add(self, time, values):
        n = len(time)
        if not n:
            return
        keys = np.arange(self.total, self.total+n, dtype=np.int64)//self.stride
        self.total += n
        self._merge(np.r_[self.keys, keys], np.r_[self.first, time], np.r_[self.last, time],
                    np.concatenate([self.low, values]), np.concatenate([self.high, values]))
        while len(self.keys) > self.max_bins:
            self.stride *= 2
            self._merge(self.keys//2, self.first, self.last, self.low, self.high)

    def curve(self, channel, origin):
        if self.stride == 1:
            return (self.first-origin)/1e6, self.low[:, channel]
        # At long durations show min/max within bins, not invented exact peak times.
        x = np.repeat(((self.first+self.last)*0.5-origin)/1e6, 2)
        y = np.column_stack([self.low[:, channel], self.high[:, channel]]).ravel()
        return x, y


@dataclass
class SensorData:
    id: int
    count: int = 0
    motion_invalid: int = 0
    temp_missing: int = 0
    sequence_errors: int = 0
    timestamp_anomalies: int = 0
    first_us: int | None = None
    last_us: int = 0
    previous_sequence: int | None = None
    plot: Envelope = field(default_factory=Envelope)
    time_map: object = None
    unanchored: int = 0
    display_window: tuple | None = None

    def add(self, values, cols):
        idx = values[:, cols["sample_index"]]
        time = values[:, cols["mcu_read_us"]]
        self.sequence_errors += int(np.count_nonzero(np.diff(idx) != 1))
        if self.previous_sequence is not None and idx[0] != self.previous_sequence+1:
            self.sequence_errors += 1
        self.previous_sequence = int(idx[-1])
        if self.first_us is None:
            self.first_us = int(time[0])
        self.last_us = int(time[-1])
        self.count += len(idx)
        raw = values[:, [cols[c] for c in CHANNELS]].astype(np.float64)
        bad_axes = raw[:, :6] == -32768
        self.motion_invalid += int(np.count_nonzero(np.any(bad_axes, axis=1)))
        temp_missing = raw[:, 6] == -128
        self.temp_missing += int(np.count_nonzero(temp_missing))
        if "timestamp_anomaly" in cols:
            self.timestamp_anomalies += int(np.count_nonzero(values[:, cols["timestamp_anomaly"]]))
        # Derive validity from actual channels, including old v1.1 files where
        # temperature missing incorrectly marked motion invalid as well.
        raw[:, :6][bad_axes] = np.nan
        raw[:, :3] /= 2048.0
        raw[:, 3:6] *= 2000.0/32768.0
        raw[:, 6] = raw[:, 6]/2.07+25
        raw[temp_missing, 6] = np.nan
        if self.time_map is not None:
            time = self.time_map.times(values, cols)
            keep = np.isfinite(time)
            self.unanchored += int(np.count_nonzero(~keep))
        else:
            keep = np.ones(len(time), dtype=bool)
        if self.display_window is not None:
            keep &= (time >= self.display_window[0]) & (time <= self.display_window[1])
        self.plot.add(time[keep], raw[keep])

    @property
    def rate(self):
        return (self.count-1)*1e6/(self.last_us-self.first_us) if self.first_us is not None and self.last_us > self.first_us else 0.0


@dataclass
class LogData:
    path: Path
    sensors: dict[int, SensorData]
    comments: list[str]
    clean_stop: bool
    end_reason: str
    synchronized: bool = False
    timing_note: str = "MCU batch-read time only; no FSYNC reference."
    window: tuple | None = None

    @property
    def origin(self):
        return min((s.first_us for s in self.sensors.values() if s.first_us is not None), default=0)


def load_csv(path, cancelled=lambda: False, progress=lambda n: None, window=None):
    path = Path(path)
    time_maps = scan_sync(path, cancelled, progress)
    synchronized = bool(time_maps) and all(m.valid for m in time_maps.values())
    if window is not None and (window[1] <= window[0] or window[1]-window[0] > 3):
        raise ValueError("For individual samples, select a positive time span of at most 3 seconds.")
    display_origin = min((m.first_read_us for m in time_maps.values() if m.first_read_us is not None), default=None)
    sensors, comments, pending = {}, [], []
    header = None
    end_reason = "MISSING_END"
    total_bytes = max(path.stat().st_size, 1)
    processed = 0

    def consume():
        nonlocal display_origin
        if not pending:
            return
        array = np.loadtxt(pending, delimiter=",", dtype=np.int64, ndmin=2)
        if array.shape[1] != len(header):
            raise ValueError("CSV column count does not match header")
        cols = {name: i for i, name in enumerate(header)}
        if display_origin is None:
            display_origin = int(np.min(array[:, cols["mcu_read_us"]]))
        ids = array[:, cols["sensor_id"]] if "sensor_id" in cols else np.ones(len(array), dtype=np.int64)
        for sensor_id in np.unique(ids):
            if int(sensor_id) not in (1, 2, 3, 4):
                raise ValueError(f"Unsupported sensor ID: {sensor_id}")
            ident = int(sensor_id)
            bounds = tuple(display_origin+t*1e6 for t in window) if window is not None else None
            sensor = sensors.setdefault(ident, SensorData(ident, time_map=time_maps.get(ident) if synchronized else None, display_window=bounds))
            sensor.add(array[ids == sensor_id], cols)
        pending.clear()

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for line in file:
            if cancelled():
                raise InterruptedError("Loading cancelled")
            processed += len(line)
            text = line.strip()
            if not text:
                continue
            if text.startswith("#"):
                if len(comments) < 200:
                    comments.append(text)
                if text.startswith("# END "):
                    for token in text.split():
                        if token.startswith("reason="):
                            end_reason = token.split("=", 1)[1]
                continue
            if header is None:
                header = text.split(",")
                if len(header) != len(set(header)) or not REQUIRED.issubset(header):
                    raise ValueError("Missing CSV header. Open a complete saved log, not a Serial Monitor excerpt.")
                continue
            pending.append(text)
            if len(pending) >= 20000:
                consume()
                progress(min(99, (50 if time_maps else 0)+int(processed*(50 if time_maps else 100)/total_bytes)))
        consume()
    if header is None:
        raise ValueError("No CSV header found")
    progress(100)
    note = "MCU batch-read time only; no FSYNC reference."
    if synchronized:
        details = "; ".join(f"SEN{s}: {len(m.anchor_ticks)} anchors, edge bracket <= {m.max_bracket_us} us" for s, m in time_maps.items())
        note = "FSYNC-aligned sample time; independent sample phases. " + details
    elif time_maps:
        details = "; ".join(f"SEN{s}: {', '.join(sorted(m.errors)) or 'anchors valid'}" for s, m in time_maps.items())
        note = "FSYNC NOT VALIDATED — showing batch time only. " + details
    return LogData(path, sensors, comments, end_reason == "STOP", end_reason, synchronized, note, window)
