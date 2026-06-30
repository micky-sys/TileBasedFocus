"""Compare host-arrival gaps vs SBF TOW gaps for a recorded ExtEvent stream.

The live correlator segments sweeps using host-arrival time deltas
(``SWEEP_GAP_MS`` on ``host_perf_ns``). This script shows, for the same
recording, what the GPS-clock TOW deltas look like -- to decide whether
sweep segmentation should switch to the (jitter-free) TOW gap.

Usage:
    py tools/sbf_gap_analysis.py --in tools/taps/sbftap_COM17_20260604_132101
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

from capture.sbf_parser import SbfParser  # noqa: E402
from sbf_replay import _scan_recording  # noqa: E402

SWEEP_GAP_MS = 250.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="basename", required=True,
                    help="Recording basename (without extension).")
    ap.add_argument("--limit", type=int, default=80,
                    help="Max events to print in the per-event table.")
    args = ap.parse_args()

    sbf_path = args.basename + ".sbf"
    timing_path = args.basename + ".timing.jsonl"
    data, blocks = _scan_recording(sbf_path, timing_path)

    # Decode every ExtEvent to get tow_ms, paired with t_arrival_ns.
    parser = SbfParser()
    events = parser.feed(data)
    ee_blocks = [b for b in blocks if b.is_ext_event]

    if len(events) != len(ee_blocks):
        print(f"[warn] decoded {len(events)} events but scanned "
              f"{len(ee_blocks)} ext-event blocks; pairing by index")
    n = min(len(events), len(ee_blocks))
    print(f"[gap] {n} ExtEventPVTGeodetic events in {sbf_path}")
    print()

    # Build per-event (tow_ms, t_arrival_ms) and gaps.
    host_gaps: list[float] = []
    tow_gaps: list[float] = []
    rows = []
    for k in range(n):
        ev = events[k]
        blk = ee_blocks[k]
        tow = ev.tow_ms
        host_ms = blk.t_arrival_ns / 1e6
        if k == 0:
            hg = tg = None
        else:
            hg = host_ms - (ee_blocks[k - 1].t_arrival_ns / 1e6)
            tg = tow - events[k - 1].tow_ms
            host_gaps.append(hg)
            tow_gaps.append(tg)
        rows.append((k, tow, host_ms, hg, tg))

    print(f"{'idx':>4} {'tow_ms':>12} {'host_ms':>12} "
          f"{'host_gap':>9} {'tow_gap':>9}  seg(host)/seg(tow)")
    for (k, tow, host_ms, hg, tg) in rows[: args.limit]:
        hgs = f"{hg:9.1f}" if hg is not None else f"{'-':>9}"
        tgs = f"{tg:9.1f}" if tg is not None else f"{'-':>9}"
        seg_h = "NEW" if (hg is not None and hg > SWEEP_GAP_MS) else "   "
        seg_t = "NEW" if (tg is not None and tg > SWEEP_GAP_MS) else "   "
        print(f"{k:>4} {tow:>12} {host_ms:>12.1f} {hgs} {tgs}  {seg_h} / {seg_t}")

    def _hist(label: str, gaps: list[float]) -> None:
        if not gaps:
            print(f"  {label}: no samples")
            return
        gaps_sorted = sorted(gaps)
        n = len(gaps_sorted)
        p = lambda q: gaps_sorted[min(n - 1, int(q * n))]  # noqa: E731
        big = sum(1 for g in gaps if g > SWEEP_GAP_MS)
        print(f"  {label}: n={n} min={gaps_sorted[0]:.1f} "
              f"p10={p(0.10):.1f} median={p(0.5):.1f} p90={p(0.9):.1f} "
              f"max={gaps_sorted[-1]:.1f}  >{SWEEP_GAP_MS:.0f}ms: {big}")

    print()
    print("[gap] distribution of consecutive-event gaps (ms):")
    _hist("host-arrival", host_gaps)
    _hist("TOW (GPS clk)", tow_gaps)

    # Count segmentation boundaries each method would declare.
    host_segs = sum(1 for g in host_gaps if g > SWEEP_GAP_MS)
    tow_segs = sum(1 for g in tow_gaps if g > SWEEP_GAP_MS)
    print()
    print(f"[gap] sweep boundaries at >{SWEEP_GAP_MS:.0f} ms: "
          f"host-arrival -> {host_segs} segments, "
          f"TOW -> {tow_segs} segments")
    print(f"[gap] expected ~{n // 13} sweeps if 13 triggers each "
          f"({n} events / 13)")

    # Simulate the correlator's online segmentation using TOW gaps at a
    # few candidate thresholds and report the resulting per-sweep sizes.
    # A good threshold yields mostly size-13 segments in steady state.
    tows = [events[k].tow_ms for k in range(n)]

    def _segment_sizes(threshold_ms: float, sweep_size: int = 13) -> list[int]:
        sizes: list[int] = []
        slot = 0
        for k in range(n):
            if k == 0:
                slot = 1
            else:
                gap = tows[k] - tows[k - 1]
                if gap > threshold_ms or slot + 1 > sweep_size:
                    sizes.append(slot)
                    slot = 1
                else:
                    slot += 1
        sizes.append(slot)
        return sizes

    # Cumulative drift between host-arrival and GPS-clock (TOW). Consecutive
    # gaps can look normal even when a whole batch lands seconds late, so we
    # track the running difference: drift[k] = (host_ms[k]-host_ms[0]) -
    # (tow[k]-tow[0]). A positive, growing drift means events are arriving at
    # the host progressively LATER than their triggers fired -- exactly the
    # lag that makes the correlator's staleness/future window reject fixes.
    if n >= 2:
        host0 = ee_blocks[0].t_arrival_ns / 1e6
        tow0 = events[0].tow_ms
        drift = []
        for k in range(n):
            d = ((ee_blocks[k].t_arrival_ns / 1e6) - host0) - (events[k].tow_ms - tow0)
            drift.append(d)
        dmin = min(drift)
        dmax = max(drift)
        # Largest single jump in drift (the worst sudden lag step).
        jumps = [drift[k] - drift[k - 1] for k in range(1, n)]
        max_jump = max(jumps)
        max_jump_idx = jumps.index(max_jump) + 1
        print()
        print("[gap] host-vs-TOW cumulative drift (host arrival minus GPS clock):")
        print(f"  start={drift[0]:.0f} ms  end={drift[-1]:.0f} ms  "
              f"min={dmin:.0f} ms  max={dmax:.0f} ms")
        print(f"  biggest single lag jump: +{max_jump:.0f} ms at event idx "
              f"{max_jump_idx} (tow={events[max_jump_idx].tow_ms})")
        # Count events whose absolute lag (relative to the run's start) exceeds
        # the correlator staleness window -- those frames can't be matched.
        STALE_MS = 1500.0
        n_late = sum(1 for d in drift if (d - drift[0]) > STALE_MS)
        print(f"  events arriving >{STALE_MS:.0f} ms later than trigger pace: "
              f"{n_late}/{n}")

    print()
    print("[gap] TOW-segmentation sweep-size distribution by threshold:")
    for thr in (250.0, 270.0, 280.0, 290.0, 300.0):
        sizes = _segment_sizes(thr)
        hist: dict[int, int] = {}
        for s in sizes:
            hist[s] = hist.get(s, 0) + 1
        n13 = hist.get(13, 0)
        dist = " ".join(f"{sz}x{cnt}" for sz, cnt in sorted(hist.items()))
        print(f"  thr={thr:5.0f} ms: {len(sizes)} sweeps, "
              f"size13={n13}  sizes[{dist}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
