"""End-to-end validation of TOW-based sweep segmentation against a tap.

Feeds the *real* ExtEvent stream from a recorded tap into a real
:class:`GpsCorrelator`, using realistic de-batched host timing (each
event ~5 ms after its trigger, frames ~300 ms after, derived from TOW so
the USB-CDC batching artefact in the raw capture is removed -- which is
what live steady-state timing looks like). Actions (event arrivals and
per-camera frame queries) are executed in strict time order, mirroring
the live pipeline.

Camera (sweep, frame_in_sweep) is taken from the TOW-detected sweep
grouping, restricted to clean size-13 sweeps. That grouping is the
physical PCB sweep structure (independently confirmed 13-per-sweep by
tools/sbf_gap_analysis.py), so slot-within-sweep == the camera's
frame_in_sweep. The test then checks the full add_event -> pop_fix_for
path returns the correct event for every frame, and that all three
cameras agree.

Usage:
    py tools/validate_tow_fix.py --in tools/taps/sbftap_COM17_20260604_132101
"""

from __future__ import annotations

import argparse
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
repo = os.path.dirname(here)
for p in (repo, os.path.join(repo, "host"), here):
    if p not in sys.path:
        sys.path.insert(0, p)

from capture.gps_correlator import SWEEP_TOW_GAP_MS, GpsCorrelator  # noqa: E402
from capture.sbf_parser import SbfParser  # noqa: E402
from sbf_replay import _scan_recording  # noqa: E402

SWEEP_SIZE = 13
CAMS = ("2439006", "2439007", "2439008")
FRAME_LATENCY_NS = 300_000_000   # camera frame ~300 ms after trigger
EVENT_LATENCY_NS = 5_000_000     # SBF event ~5 ms after trigger


def _detect_sweeps(events) -> list[list[int]]:
    """Group event indices into sweeps by the TOW-gap rule (size-capped)."""
    sweeps: list[list[int]] = []
    cur: list[int] = []
    last_tow = None
    for i, ev in enumerate(events):
        if last_tow is None:
            cur = [i]
        else:
            gap = ev.tow_ms - last_tow
            if gap > SWEEP_TOW_GAP_MS or gap <= 0 or len(cur) >= SWEEP_SIZE:
                sweeps.append(cur)
                cur = [i]
            else:
                cur.append(i)
        last_tow = ev.tow_ms
    if cur:
        sweeps.append(cur)
    return sweeps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="basename", required=True)
    args = ap.parse_args()

    data, _blocks = _scan_recording(
        args.basename + ".sbf", args.basename + ".timing.jsonl"
    )
    events = SbfParser().feed(data)
    all_sweeps = _detect_sweeps(events)
    clean = [s for s in all_sweeps if len(s) == SWEEP_SIZE]
    print(f"[validate] {len(events)} events -> {len(all_sweeps)} sweeps "
          f"({len(clean)} clean size-{SWEEP_SIZE}, "
          f"{len(all_sweeps) - len(clean)} fragments)")
    if not clean:
        print("[validate] no clean sweeps; abort")
        return 1

    tow0 = events[clean[0][0]].tow_ms

    def trig_ns(ev) -> int:
        return int((ev.tow_ms - tow0) * 1_000_000)

    # Build a time-ordered action list: (t, kind, payload).
    #  kind 0 = add_event(event); kind 1 = frame query (sweep, slot).
    actions: list[tuple[int, int, object]] = []
    for sweep_no, s in enumerate(clean, start=1):
        for slot, idx in enumerate(s, start=1):
            ev = events[idx]
            actions.append((trig_ns(ev) + EVENT_LATENCY_NS, 0, ev))
            actions.append(
                (trig_ns(ev) + FRAME_LATENCY_NS, 1, (sweep_no, slot, idx))
            )
    actions.sort(key=lambda a: (a[0], a[1]))  # event before frame at same t

    corr = GpsCorrelator()
    corr.set_connected(True)

    matched = wrong = missed = 0
    cross_ok = cross_bad = total = 0

    for t, kind, payload in actions:
        if kind == 0:
            corr.add_event(payload, t)
            continue
        sweep_no, slot, idx = payload
        ev = events[idx]
        fixes = [corr.pop_fix_for(cam, t, sweep_no, slot) for cam in CAMS]
        total += 1
        f0 = fixes[0]
        if f0 is None:
            missed += 1
        elif (abs(f0.latitude_deg - ev.latitude_deg) < 1e-9
              and abs(f0.longitude_deg - ev.longitude_deg) < 1e-9):
            matched += 1
        else:
            wrong += 1
        present = [f for f in fixes if f is not None]
        if len(present) == len(CAMS):
            same = all(
                abs(f.latitude_deg - present[0].latitude_deg) < 1e-12
                and abs(f.longitude_deg - present[0].longitude_deg) < 1e-12
                for f in present
            )
            cross_ok += 1 if same else 0
            cross_bad += 0 if same else 1
        else:
            cross_bad += 1

    pct = (100.0 * matched / total) if total else 0.0
    print(f"[validate] frames: {total}")
    print(f"[validate]   matched (correct event): {matched} ({pct:.1f}%)")
    print(f"[validate]   wrong fix             : {wrong}")
    print(f"[validate]   missed (no fix)       : {missed}")
    print(f"[validate] cross-camera identity: ok={cross_ok} bad={cross_bad}")
    ok = (matched == total and wrong == 0 and missed == 0 and cross_bad == 0)
    print(f"[validate] RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
