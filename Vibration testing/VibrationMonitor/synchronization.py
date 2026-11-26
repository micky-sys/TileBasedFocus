"""FSYNC time reconstruction. No FIFO-read-time substitution for missing anchors.

DS-000347 sections 6.3, 12.7 and TMST_CONFIG: a marked packet contains
FSYNC-to-ODR ticks instead of its absolute ODR timestamp. Its absolute ticks
are recovered as the midpoint of its two adjacent, ordinary ODR timestamps.
This incurs roughly a tick of quantization uncertainty; it is not a claim of
measured physical-motion timing accuracy (filters and input latency remain).
"""
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np


@dataclass
class TimeMap:
    marks: dict = field(default_factory=dict)  # ID -> (marked ticks, edge ticks)
    events: dict = field(default_factory=dict)  # ID -> (MCU before, bracket)
    errors: set = field(default_factory=set)
    previous: tuple | None = None
    pending: tuple | None = None
    anchor_ticks: np.ndarray = field(default_factory=lambda: np.empty(0))
    anchor_us: np.ndarray = field(default_factory=lambda: np.empty(0))
    valid: bool = False
    max_bracket_us: int = 0
    first_read_us: int | None = None

    def resolve(self, pending, following):
        ident, sequence, previous, delta = pending
        seq, ticks, sync = following
        if sync or seq != sequence+1 or not 80 <= ticks-previous <= 160 or not 0 <= delta <= 80:
            self.errors.add("invalid FSYNC neighbours/delta")
            return
        if ident in self.marks:
            self.errors.add("duplicate FSYNC packet")
        marked = (previous+ticks)*0.5
        self.marks[ident] = (marked, marked-delta)

    def consume(self, rows, cols):
        if self.first_read_us is None:
            self.first_read_us = int(rows[0, cols["mcu_read_us"]])
        seq = rows[:, cols["sample_index"]]
        ticks = rows[:, cols["sensor_ticks"]]
        sync = rows[:, cols["sync_id"]]
        if (np.any(np.diff(seq) != 1) or
                (self.previous is not None and seq[0] != self.previous[0]+1)):
            self.errors.add("sequence gap")
        if np.any(rows[:, cols["timestamp_anomaly"]]):
            self.errors.add("timestamp anomaly")
        if self.pending:
            self.resolve(self.pending, (int(seq[0]), int(ticks[0]), int(sync[0])))
            self.pending = None
        for i in np.flatnonzero(sync):
            prev = (int(seq[i-1]), int(ticks[i-1]), int(sync[i-1])) if i else self.previous
            if prev is None or prev[2] or prev[0] != seq[i]-1 or ticks[i] != prev[1]:
                self.errors.add("missing preceding ODR timestamp")
                continue
            pending = (int(sync[i]), int(seq[i]), prev[1], int(rows[i, cols["timestamp_raw"]]))
            if i+1 < len(rows):
                self.resolve(pending, (int(seq[i+1]), int(ticks[i+1]), int(sync[i+1])))
            else:
                self.pending = pending
        self.previous = (int(seq[-1]), int(ticks[-1]), int(sync[-1]))

    def finish(self):
        # An unfinished last marker is outside the bracketed interval anyway.
        ids = sorted(self.marks)
        if len(ids) < 3:
            self.errors.add("fewer than 3 complete FSYNC anchors")
        if ids and (ids[0] != 1 or np.any(np.diff(ids) != 1)):
            self.errors.add("missing FSYNC packet")
        if any(i not in self.events for i in ids):
            self.errors.add("missing FSYNC edge metadata")
        if self.errors:
            return
        self.anchor_ticks = np.array([self.marks[i][1] for i in ids])
        self.anchor_us = np.array([self.events[i][0]+self.events[i][1]*0.5 for i in ids])
        self.max_bracket_us = max(self.events[i][1] for i in ids)
        dt, du = np.diff(self.anchor_ticks), np.diff(self.anchor_us)
        if (np.any(dt <= 0) or np.any(du < 9000) or np.any(du > 30000) or
                self.max_bracket_us > 10 or np.any(du/dt < 0.9) or np.any(du/dt > 1.3)):
            self.errors.add("implausible FSYNC timing/edge bracket")
            return
        self.valid = True

    def times(self, rows, cols):
        ticks = rows[:, cols["sensor_ticks"]].astype(float)
        sync = rows[:, cols["sync_id"]]
        for i in np.flatnonzero(sync):
            ticks[i] = self.marks.get(int(sync[i]), (np.nan,))[0]
        return np.interp(ticks, self.anchor_ticks, self.anchor_us, left=np.nan, right=np.nan)


def scan_sync(path, cancelled=lambda: False, progress=lambda n: None):
    """First pass keeps sparse anchors only; raw samples are read in bounded chunks."""
    maps, pending, cols = {}, [], None
    size = max(Path(path).stat().st_size, 1)
    done = 0

    def consume():
        if not pending:
            return
        rows = np.loadtxt(pending, delimiter=",", dtype=np.int64, ndmin=2)
        if rows.shape[1] != len(cols):
            raise ValueError("CSV column count does not match header")
        ids = rows[:, cols["sensor_id"]]
        for ident in np.unique(ids):
            maps.setdefault(int(ident), TimeMap()).consume(rows[ids == ident], cols)
        pending.clear()

    with Path(path).open(encoding="utf-8-sig") as file:
        for line in file:
            if cancelled():
                raise InterruptedError("Loading cancelled")
            done += len(line)
            text = line.strip()
            if not text:
                continue
            if text.startswith("# FSYNC sensor="):
                fields = dict(item.split("=", 1) for item in text.split()[2:])
                sensor, ident, before, bracket = (int(fields[k]) for k in ("sensor", "id", "before_us", "bracket_us"))
                model = maps.setdefault(sensor, TimeMap())
                if ident in model.events or ident <= 0 or before < 0 or not 0 <= bracket <= 10:
                    model.errors.add("invalid/duplicate FSYNC edge")
                model.events[ident] = (before, bracket)
                continue
            if text.startswith("#"):
                continue
            if cols is None:
                names = text.split(",")
                if "sync_id" not in names:
                    return {}  # Legacy: do not scan an entire old log twice.
                cols = {key: i for i, key in enumerate(names)}
                needed = {"sample_index", "sensor_id", "sensor_ticks", "timestamp_raw", "timestamp_anomaly", "sync_id"}
                if len(cols) != len(names) or not needed.issubset(cols):
                    raise ValueError("Incomplete FSYNC CSV header")
                continue
            pending.append(text)
            if len(pending) >= 20000:
                consume()
                progress(min(49, int(done*50/size)))
        consume()
    for model in maps.values():
        model.finish()
    return maps
