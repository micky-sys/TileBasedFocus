"""Synthesize a Septentrio SBF recording with controlled sweep structure.

Produces the same .sbf / .timing.jsonl / .meta.json triple as
``sbf_record.py`` but with timing chosen to match the ClearCore PCB's
real trigger pattern: ``frames_per_sweep`` events at
``intra_sweep_ms`` apart, then an ``inter_sweep_extra_ms`` pause, then
the next sweep, etc.

Use to drive the correlator harness (or sbf_replay) when you don't
have the live rig handy and want a reproducible baseline that's
friendly to the correlator's sweep-gap detector.

Lat / lon are constant per recording (matches a stationary rig); TOW
advances at the same cadence as host arrival, with a configurable
GPS->host latency.

Usage::

    py tools/sbf_synthesize.py --out tools/synth1 \\
        --sweeps 10 --frames-per-sweep 13 \\
        --intra-sweep-ms 200 --inter-sweep-extra-ms 100
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    host_dir = os.path.join(repo_root, "host")
    for p in (repo_root, host_dir):
        if p and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_paths()

from capture.sbf_parser import encode_ext_event_pvt_geodetic  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True,
                    help="Output basename (.sbf/.timing.jsonl/.meta.json).")
    ap.add_argument("--sweeps", type=int, default=10)
    ap.add_argument("--frames-per-sweep", type=int, default=13)
    ap.add_argument("--intra-sweep-ms", type=float, default=200.0,
                    help="Inter-event gap inside a sweep.")
    ap.add_argument("--inter-sweep-extra-ms", type=float, default=100.0,
                    help="Extra gap on top of intra-sweep between sweeps.")
    ap.add_argument("--sbf-latency-ms", type=float, default=5.0,
                    help="Wire latency from trigger to host-read.")
    ap.add_argument("--latitude-deg", type=float, default=32.170250)
    ap.add_argument("--longitude-deg", type=float, default=34.831361)
    ap.add_argument("--height-m", type=float, default=72.12)
    ap.add_argument("--n-sv", type=int, default=22)
    ap.add_argument("--wnc", type=int, default=2420)
    ap.add_argument("--tow-start-ms", type=int, default=210_000_000)
    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    sbf_path = args.out + ".sbf"
    timing_path = args.out + ".timing.jsonl"
    meta_path = args.out + ".meta.json"

    total_events = args.sweeps * args.frames_per_sweep

    # Build the timing schedule. t_arrival_ns is when the host would
    # have finished reading the block off the wire (= what
    # sbf_replay._scan_recording.t_for_offset would now report).
    inter_ns = int(args.intra_sweep_ms * 1e6)
    extra_ns = int(args.inter_sweep_extra_ms * 1e6)
    latency_ns = int(args.sbf_latency_ms * 1e6)

    sbf_bytes = bytearray()
    timing_rows = []
    tow_ms = int(args.tow_start_ms)
    t_perf_ns = 0  # first event arrives at "now"
    for sweep in range(args.sweeps):
        for frame_in_sweep in range(args.frames_per_sweep):
            block = encode_ext_event_pvt_geodetic(
                tow_ms=tow_ms,
                wnc=args.wnc,
                latitude_deg=args.latitude_deg,
                longitude_deg=args.longitude_deg,
                height_m=args.height_m,
                n_sv=args.n_sv,
            )
            offset = len(sbf_bytes)
            sbf_bytes.extend(block)
            # Host receives the whole block one chunk; t_perf_ns is the
            # finalisation time, which is what _scan_recording now uses.
            timing_rows.append({
                "t_perf_ns": int(t_perf_ns),
                "offset": offset,
                "n": len(block),
            })
            # Advance for next event
            tow_ms += int(args.intra_sweep_ms)
            t_perf_ns += inter_ns
        # End-of-sweep extra pause + latency offset
        t_perf_ns += extra_ns
        tow_ms += int(args.inter_sweep_extra_ms)

    with open(sbf_path, "wb") as f:
        f.write(bytes(sbf_bytes))
    with open(timing_path, "w") as f:
        for row in timing_rows:
            f.write(json.dumps(row) + "\n")
    meta = {
        "port": "(synthesized)",
        "baud": 115200,
        "duration_requested_s": (t_perf_ns / 1e9),
        "started_iso_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_bytes": len(sbf_bytes),
        "parser_stats": {
            "good_blocks": total_events,
            "events_decoded": total_events,
            "bad_crc": 0,
            "bad_length": 0,
            "skipped_bytes": 0,
            "events_skipped_unknown": 0,
            "buffered": 0,
        },
        "synth_args": {
            "sweeps": args.sweeps,
            "frames_per_sweep": args.frames_per_sweep,
            "intra_sweep_ms": args.intra_sweep_ms,
            "inter_sweep_extra_ms": args.inter_sweep_extra_ms,
            "sbf_latency_ms": args.sbf_latency_ms,
            "latitude_deg": args.latitude_deg,
            "longitude_deg": args.longitude_deg,
            "height_m": args.height_m,
        },
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"[synth] {total_events} events ({args.sweeps} sweeps x "
          f"{args.frames_per_sweep} frames) written.")
    print(f"[synth] timing: {args.intra_sweep_ms} ms intra, "
          f"+{args.inter_sweep_extra_ms} ms inter, total duration "
          f"{t_perf_ns / 1e9:.2f} s.")
    print(f"[synth] -> {sbf_path}")
    print(f"[synth] -> {timing_path}")
    print(f"[synth] -> {meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
