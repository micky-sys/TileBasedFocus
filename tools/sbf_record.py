"""Record a live Septentrio SBF byte stream from a COM port to disk.

Three sibling files are produced from a single ``--out`` basename::

    BASENAME.sbf            raw bytes off the wire (append-only)
    BASENAME.timing.jsonl   one JSON-line per read() chunk:
                            {"t_perf_ns": int, "offset": int, "n": int}
    BASENAME.meta.json      port / baud / wall start / total bytes / parser stats

The timing manifest is granular enough to reproduce inter-chunk gaps
within ~1 ms; the .sbf alone is enough for any test that doesn't care
about wire-level timing (most parser-only work).

Usage (from repo root)::

    py tools/sbf_record.py --port COM3 --seconds 30 --out tools/rec1
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

import serial  # noqa: E402

from capture.sbf_parser import SbfParser  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", required=True, help="COM port to tap (e.g. COM3).")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=30.0,
                    help="Recording duration (default 30).")
    ap.add_argument("--out", required=True,
                    help="Output basename. Files .sbf, .timing.jsonl, "
                         ".meta.json are created next to it.")
    args = ap.parse_args()

    sbf_path = args.out + ".sbf"
    timing_path = args.out + ".timing.jsonl"
    meta_path = args.out + ".meta.json"

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    print(f"[record] opening {args.port} @ {args.baud} for {args.seconds:.1f} s ...")
    print(f"[record] writing -> {sbf_path}")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05)
    except serial.SerialException as exc:
        print(f"[record] open failed: {exc}", file=sys.stderr)
        return 2

    parser = SbfParser()
    total = 0
    t0_perf = time.perf_counter_ns()
    t0_wall = time.time()
    deadline_perf = t0_perf + int(args.seconds * 1e9)
    last_tick = time.monotonic()

    with open(sbf_path, "wb") as sbf_f, open(timing_path, "w") as t_f:
        try:
            while time.perf_counter_ns() < deadline_perf:
                chunk = ser.read(4096)
                if chunk:
                    t = time.perf_counter_ns() - t0_perf
                    sbf_f.write(chunk)
                    t_f.write(json.dumps(
                        {"t_perf_ns": t, "offset": total, "n": len(chunk)}
                    ) + "\n")
                    total += len(chunk)
                    parser.feed(chunk)
                now = time.monotonic()
                if now - last_tick >= 1.0:
                    last_tick = now
                    s = parser.stats()
                    print(f"[record] bytes={total:>7d}  good={s['good_blocks']:>4d}  "
                          f"events={s['events_decoded']:>4d}  bad_crc={s['bad_crc']}  "
                          f"bad_len={s['bad_length']}")
        except KeyboardInterrupt:
            print("[record] interrupted by user", file=sys.stderr)
        finally:
            ser.close()

    meta = {
        "port": args.port,
        "baud": args.baud,
        "duration_requested_s": args.seconds,
        "started_iso_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0_wall)),
        "total_bytes": total,
        "parser_stats": parser.stats(),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print()
    print(f"[record] done. {total} bytes recorded, "
          f"{parser.stats()['events_decoded']} ExtEventPVTGeodetic blocks parsed, "
          f"{parser.stats()['bad_crc']} bad CRC, "
          f"{parser.stats()['bad_length']} bad length.")
    print(f"[record] -> {sbf_path}")
    print(f"[record] -> {timing_path}")
    print(f"[record] -> {meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
