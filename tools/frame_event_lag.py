"""Measure how the frame-vs-event arrival lag evolves across a capture.

The correlator matches a frame to a GPS event only if the event's
host-arrival time is within the staleness window of the frame's
``frame_perf_ns`` (captured at dequeue). If the acquisition dequeue
progressively falls behind real acquisition over a long capture, that
lag grows until it exceeds the window and every frame goes stale.

Frames and ExtEvents are produced 1:1 by the same triggers, in the same
order, so we pair frame *i* with event *i* and compute:

    lag_ms[i] = frame_acquire_wall[i] - event_arrival_wall[i]

both on the host wall clock (frame: bay14 ``acquire_timestamp``; event:
tap ``started_iso_utc`` + per-block arrival from the timing sidecar).
A lag that climbs past STALE_AHEAD_MS (1500 ms) explains a stale storm.

Usage:
    py tools/frame_event_lag.py \
        --tap tools/taps/sbftap_COM17_20260604_134025 \
        --frames "D:/GPS TEST" --cam 2439006
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

here = os.path.dirname(os.path.abspath(__file__))
repo = os.path.dirname(here)
for p in (repo, os.path.join(repo, "host"), here):
    if p not in sys.path:
        sys.path.insert(0, p)

from capture.sbf_parser import SbfParser  # noqa: E402
from sbf_replay import _scan_recording  # noqa: E402
from bay14_export_core import iter_line_frames  # noqa: E402

STALE_AHEAD_MS = 1500.0


def _parse_iso_us(s: str) -> int | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return int((d - epoch).total_seconds() * 1_000_000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tap", required=True, help="Tap basename (no extension).")
    ap.add_argument("--frames", required=True, nargs="+",
                    help="Directory(ies) holding the camera's .bay14line files.")
    ap.add_argument("--cam", required=True, help="Camera serial to analyze.")
    args = ap.parse_args()

    # Events: arrival wall time = tap_t0 + per-block arrival.
    with open(args.tap + ".meta.json") as f:
        meta = json.load(f)
    t0_us = _parse_iso_us(meta["started_iso_utc"])
    data, blocks = _scan_recording(args.tap + ".sbf", args.tap + ".timing.jsonl")
    events = SbfParser().feed(data)
    ee = [b for b in blocks if b.is_ext_event]
    n_ev = min(len(events), len(ee))
    ev_arrival_us = [t0_us + int(ee[k].t_arrival_ns / 1000) for k in range(n_ev)]
    print(f"[lag] {n_ev} events from {args.tap}")

    # Frames: collect this camera's frames with acquire wall time, sort by it.
    frames: list[int] = []
    paths: list[Path] = []
    for root in args.frames:
        paths.extend(sorted(Path(root).rglob("*.bay14line")))
    fr_acq: list[int] = []
    for path in paths:
        for m, _arr in iter_line_frames(path, recovery_notice=None, meta_only=True):
            cam = str(m.get("camera_serial") or m.get("camera") or "?")
            if cam != args.cam:
                continue
            ts = _parse_iso_us(m.get("acquire_timestamp"))
            if ts is not None:
                fr_acq.append(ts)
    fr_acq.sort()
    n_fr = len(fr_acq)
    print(f"[lag] {n_fr} frames for cam {args.cam}")

    n = min(n_ev, n_fr)
    if n == 0:
        print("[lag] nothing to pair")
        return 1

    print(f"[lag] pairing {n} frame<->event by trigger order")
    print(f"{'idx':>4} {'lag_ms':>10}   note")
    first_stale = None
    for i in range(n):
        lag_ms = (fr_acq[i] - ev_arrival_us[i]) / 1000.0
        if lag_ms > STALE_AHEAD_MS and first_stale is None:
            first_stale = i
        if i % 20 == 0 or i == n - 1:
            flag = "  <-- exceeds stale window" if lag_ms > STALE_AHEAD_MS else ""
            print(f"{i:>4} {lag_ms:>10.0f}{flag}")
    if first_stale is not None:
        print(f"\n[lag] lag first exceeds {STALE_AHEAD_MS:.0f} ms at frame "
              f"#{first_stale} (of {n})")
    else:
        print(f"\n[lag] lag never exceeds {STALE_AHEAD_MS:.0f} ms")
    lags = [(fr_acq[i] - ev_arrival_us[i]) / 1000.0 for i in range(n)]
    print(f"[lag] lag range: min={min(lags):.0f} ms  max={max(lags):.0f} ms  "
          f"start~{sum(lags[:10])/min(10,n):.0f} ms  end~{sum(lags[-10:])/min(10,n):.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
