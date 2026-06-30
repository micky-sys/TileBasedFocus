"""Replay a recorded Septentrio SBF stream onto a COM port (or in-process),
with optional adversarial mutations targeting the correlator's failure modes.

Pairs with :mod:`tools.sbf_record`. Reads a 3-file recording (.sbf +
.timing.jsonl + .meta.json), parse-scans it once to identify every
block boundary, then re-emits the blocks one-by-one at their original
wire arrival times. Mutations operate on the *block index* (0-based,
among ExtEventPVTGeodetic blocks only), which is stable across
recordings and makes scenarios reproducible.

Emission is **block-granular**, not chunk-granular: each block is
written in a single ``ser.write()`` call at its recorded inter-block
time. Sub-block fragmentation of the original recording is collapsed
on purpose -- the parser is unit-tested against byte-at-a-time delivery
already, and block-granular replay keeps adversarial test outcomes
deterministic.

Usage (from repo root)::

    # Sanity check (no serial port required):
    py tools/sbf_replay.py --in tools/rec1 --verify --max-events 5

    # Faithful replay through a com0com pair (write COM19, app reads COM18):
    py tools/sbf_replay.py --in tools/rec1 --port COM19

    # Drop the 50th ExtEventPVTGeodetic block (Section-5 F3 reproducer):
    py tools/sbf_replay.py --in tools/rec1 --port COM19 --drop-event 50

    # Insert 400 ms extra silence before event #30 (cross SWEEP_GAP_MS mid-sweep):
    py tools/sbf_replay.py --in tools/rec1 --port COM19 \\
        --delay-before-block 30 400
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from dataclasses import dataclass


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    host_dir = os.path.join(repo_root, "host")
    for p in (repo_root, host_dir):
        if p and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_paths()

import serial  # noqa: E402

from capture.sbf_parser import (  # noqa: E402
    BLOCK_ID_EXT_EVENT_PVT_GEODETIC,
    BLOCK_ID_MASK,
    HEADER_LEN,
    SYNC,
    SbfParser,
    crc16_ccitt,
)


@dataclass
class Block:
    """A successfully-CRC'd block as found in the recorded byte stream."""
    offset: int            # byte offset of $@ in the .sbf file
    length: int            # full block length (multiple of 4)
    block_id: int          # masked low 13 bits
    is_ext_event: bool
    ext_event_index: int   # 0-based among ExtEventPVTGeodetic; -1 otherwise
    t_arrival_ns: int      # t_perf_ns of the chunk that contained this $@


def _scan_recording(sbf_path: str, timing_path: str) -> tuple[bytes, list[Block]]:
    """Single-pass scan to enumerate every valid block in the recording.

    Uses the same framing logic as :class:`SbfParser` but tracks byte
    offsets and looks them up against the chunk-level timing manifest
    to give each block an arrival time.
    """
    with open(sbf_path, "rb") as f:
        data = f.read()

    chunks: list[tuple[int, int]] = []  # (offset_end, t_perf_ns)
    with open(timing_path) as f:
        for line in f:
            row = json.loads(line)
            chunks.append((row["offset"] + row["n"], int(row["t_perf_ns"])))

    def t_for_offset(off: int) -> int:
        # First chunk whose range covers 'off'. Linear scan is fine here
        # because chunks are small (~hundreds per session).
        for end, t in chunks:
            if off < end:
                return t
        return chunks[-1][1] if chunks else 0

    blocks: list[Block] = []
    ext_idx = 0
    i = 0
    n = len(data)
    while i <= n - HEADER_LEN:
        if data[i:i + 2] != SYNC:
            i += 1
            continue
        crc_field = struct.unpack_from("<H", data, i + 2)[0]
        id_field = struct.unpack_from("<H", data, i + 4)[0]
        length = struct.unpack_from("<H", data, i + 6)[0]
        if length < HEADER_LEN or length % 4 != 0 or i + length > n:
            i += 1
            continue
        crc_input = data[i + 4: i + length]
        if crc16_ccitt(crc_input) != crc_field:
            i += 1
            continue
        block_id = id_field & BLOCK_ID_MASK
        is_ee = (block_id == BLOCK_ID_EXT_EVENT_PVT_GEODETIC)
        # Arrival time is the chunk that contained the LAST byte of the
        # block -- that's when the real GpsReceiver's parser would have
        # finished decoding and called add_event. Using the first-byte
        # chunk instead would mis-time split blocks and fake huge
        # inter-event gaps to the correlator.
        last_byte_off = i + length - 1
        blocks.append(Block(
            offset=i,
            length=length,
            block_id=block_id,
            is_ext_event=is_ee,
            ext_event_index=ext_idx if is_ee else -1,
            t_arrival_ns=t_for_offset(last_byte_off),
        ))
        if is_ee:
            ext_idx += 1
        i += length
    return data, blocks


def _build_schedule(
    data: bytes,
    blocks: list[Block],
    drops: set[int],
    delays_ms_by_idx: dict[int, float],
    speed: float,
) -> list[tuple[int, bytes, Block]]:
    """Return [(t_send_ns, bytes_to_send, block), ...] in send order.

    ``speed > 1.0`` compresses wall time; ``speed < 1.0`` stretches it.
    Mutation semantics:
      * dropped ext-event blocks are removed from the schedule entirely
      * a delay applied at ext-event index N pushes block N (and every
        subsequent block) by that many milliseconds. Cumulative.
    """
    cumulative_offset_ns = 0
    schedule: list[tuple[int, bytes, Block]] = []
    for b in blocks:
        if b.is_ext_event and b.ext_event_index in drops:
            continue
        if b.is_ext_event and b.ext_event_index in delays_ms_by_idx:
            cumulative_offset_ns += int(delays_ms_by_idx[b.ext_event_index] * 1e6)
        t = int(b.t_arrival_ns / max(speed, 1e-6)) + cumulative_offset_ns
        schedule.append((t, data[b.offset: b.offset + b.length], b))
    return schedule


def _summarise_blocks(blocks: list[Block]) -> str:
    n_ee = sum(1 for b in blocks if b.is_ext_event)
    other = {}
    for b in blocks:
        if not b.is_ext_event:
            other[b.block_id] = other.get(b.block_id, 0) + 1
    parts = [f"{len(blocks)} blocks", f"{n_ee} ExtEventPVTGeodetic"]
    if other:
        parts.append(f"other: {dict(sorted(other.items()))}")
    return ", ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="basename", required=True,
                    help="Recording basename (without .sbf/.timing.jsonl).")
    ap.add_argument("--port", default=None,
                    help="Serial port to write to (e.g. COM19). "
                         "Omit and use --verify for in-process check.")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--speed", type=float, default=1.0,
                    help="Time scale; 2.0 = 2x faster than realtime "
                         "(default 1.0).")
    ap.add_argument("--drop-event", type=int, action="append", default=[],
                    metavar="N",
                    help="Skip the Nth (0-based) ExtEventPVTGeodetic block. "
                         "Repeatable.")
    ap.add_argument("--delay-before-block", nargs=2, action="append", default=[],
                    metavar=("BLOCK_IDX", "MS"),
                    help="Insert MS ms of extra silence before the Nth event "
                         "block. Repeatable; cumulative.")
    ap.add_argument("--verify", action="store_true",
                    help="Don't touch a serial port; feed the mutated stream "
                         "through SbfParser in-process and print the decoded events.")
    ap.add_argument("--max-events", type=int, default=0,
                    help="If >0, stop after sending this many events (debug).")
    args = ap.parse_args()

    sbf_path = args.basename + ".sbf"
    timing_path = args.basename + ".timing.jsonl"
    if not os.path.exists(sbf_path):
        print(f"[replay] missing {sbf_path}", file=sys.stderr)
        return 2
    if not os.path.exists(timing_path):
        print(f"[replay] missing {timing_path}", file=sys.stderr)
        return 2

    print(f"[replay] scanning {sbf_path} ...")
    data, blocks = _scan_recording(sbf_path, timing_path)
    print(f"[replay] {_summarise_blocks(blocks)}")

    n_ee_total = sum(1 for b in blocks if b.is_ext_event)
    drops = set(int(x) for x in args.drop_event)
    delays_ms_by_idx: dict[int, float] = {}
    for raw_idx, ms in args.delay_before_block:
        idx = int(raw_idx)
        delays_ms_by_idx[idx] = delays_ms_by_idx.get(idx, 0.0) + float(ms)

    bad_idx = [d for d in drops if d < 0 or d >= n_ee_total]
    bad_idx += [d for d in delays_ms_by_idx if d < 0 or d >= n_ee_total]
    if bad_idx:
        print(f"[replay] mutation index out of range (have {n_ee_total} events): "
              f"{bad_idx}", file=sys.stderr)
        return 2

    if drops:
        print(f"[replay] mutation: drop events {sorted(drops)}")
    if delays_ms_by_idx:
        print(f"[replay] mutation: extra delays before event "
              f"{ {k: f'{v:.0f} ms' for k, v in sorted(delays_ms_by_idx.items())} }")

    schedule = _build_schedule(data, blocks, drops, delays_ms_by_idx, args.speed)
    print(f"[replay] {len(schedule)} blocks scheduled "
          f"({sum(1 for _,_,b in schedule if b.is_ext_event)} events after mutation)")

    if args.verify:
        parser = SbfParser()
        decoded = []
        for _, payload, _ in schedule:
            decoded.extend(parser.feed(payload))
            if args.max_events and len(decoded) >= args.max_events:
                break
        for ev in decoded:
            utc = ev.utc_datetime()
            utc_s = (utc.strftime("%H:%M:%S.") + f"{utc.microsecond // 1000:03d}"
                     if utc else "?")
            lat = ev.latitude_deg
            lon = ev.longitude_deg
            coord = f"lat={lat:+.7f} lon={lon:+.7f}" if (lat is not None and lon is not None) else "no-fix"
            print(f"  TOW={ev.tow_ms:>10d}  fix={ev.fix_solution}  "
                  f"nsv={ev.n_sv}  {coord}  utc={utc_s}")
        print(f"[replay] verify: decoded {len(decoded)} events.")
        print(f"[replay] parser stats: {parser.stats()}")
        return 0

    if not args.port:
        print("[replay] need --port (or --verify) to actually emit anything",
              file=sys.stderr)
        return 2

    print(f"[replay] opening {args.port} @ {args.baud} 8N1 ...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.5)
    except serial.SerialException as exc:
        print(f"[replay] open failed: {exc}", file=sys.stderr)
        return 2

    # Force unbuffered stdout so progress shows up even when the parent
    # process redirects/captures (e.g. PowerShell with 2>&1).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    t_start = time.perf_counter_ns()
    sent_blocks = 0
    sent_events = 0
    last_tick = time.monotonic()
    write_errors = 0
    try:
        for t_target, payload, b in schedule:
            now = time.perf_counter_ns() - t_start
            wait_ns = t_target - now
            if wait_ns > 0:
                time.sleep(wait_ns / 1e9)
            try:
                ser.write(payload)
            except (serial.SerialException, OSError) as exc:
                # com0com pair can raise if the reader closes mid-stream.
                write_errors += 1
                print(f"[replay] write failed at block {sent_blocks}: {exc!r}",
                      file=sys.stderr, flush=True)
                break
            sent_blocks += 1
            if b.is_ext_event:
                sent_events += 1
            wclock = time.monotonic()
            if wclock - last_tick >= 1.0:
                last_tick = wclock
                elapsed = (time.perf_counter_ns() - t_start) / 1e9
                print(f"[replay] t+{elapsed:5.1f}s  blocks_sent={sent_blocks:>4d}  "
                      f"events_sent={sent_events:>4d}", flush=True)
            if args.max_events and sent_events >= args.max_events:
                break
        try:
            ser.flush()
        except Exception:
            pass
    except KeyboardInterrupt:
        print("[replay] interrupted by user", file=sys.stderr, flush=True)
    finally:
        ser.close()

    elapsed = (time.perf_counter_ns() - t_start) / 1e9
    print(f"[replay] done. {sent_blocks} blocks "
          f"({sent_events} events) emitted in {elapsed:.2f}s, "
          f"write_errors={write_errors}.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
