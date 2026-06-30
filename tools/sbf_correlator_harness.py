"""In-process correlator harness: drive a real :class:`GpsCorrelator`
from a recorded (and optionally mutated) SBF stream, plus a synthetic
camera-frame schedule, and report per-frame slot assignments.

The point: see exactly what the correlator does to camera frames under
each mutation, without needing a COM port, cameras, disk, or the GUI.
The simulation is fully deterministic given a (recording, mutation,
sim_args) tuple, which makes this a CI-friendly regression target.

Wire model (matches the live system):

* In real life the ClearCore PCB pulses the trigger line and the
  Septentrio EXT_EVENT pin **simultaneously**. The trigger then fires
  3 cameras (frames arrive ~300 ms later) and the receiver emits one
  ``ExtEventPVTGeodetic`` block (arrives ~5 ms later as raw bytes).
* Each event block in the recording therefore stands in for a physical
  trigger; we use its host-arrival time ``t_arrival_ns`` as a proxy
  for the trigger time.
* Camera frames are synthesised: for every ORIGINAL trigger, every
  ``--cameras`` entry produces one frame at
  ``t_trigger + frame_latency_ms``. Mutations (drop / delay) do **not**
  affect the camera side -- those are GPS-stream-only failures.
* GPS events are scheduled per :func:`sbf_replay._build_schedule`,
  i.e. dropped events never call ``add_event``, delayed events call
  it later than the trigger.

Usage::

    # Baseline (no mutation) -- writes per-frame results to baseline.jsonl:
    py tools/sbf_correlator_harness.py --in tools/rec1 \\
        --output tools/baseline.jsonl

    # Mutation: drop GPS event 65, write to drop65.jsonl:
    py tools/sbf_correlator_harness.py --in tools/rec1 \\
        --drop-event 65 --output tools/drop65.jsonl

    # Diff the two -- prints any frame whose matched fix changed:
    py tools/sbf_correlator_harness.py --diff tools/baseline.jsonl tools/drop65.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Optional


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    host_dir = os.path.join(repo_root, "host")
    for p in (repo_root, host_dir, here):
        if p and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_paths()

from capture.gps_correlator import GpsCorrelator  # noqa: E402
from capture.sbf_parser import SbfParser  # noqa: E402

from sbf_replay import Block, _build_schedule, _scan_recording  # noqa: E402


DEFAULT_FRAME_LATENCY_MS = 300.0
DEFAULT_SWEEP_SIZE = 13
DEFAULT_CAMERAS = "cam1,cam2,cam3"


@dataclass
class FrameResult:
    """One simulated camera frame and the correlator's response."""
    trigger_idx: int            # 0-based index into the ORIGINAL recording
    camera: str
    sweep: int                  # synthetic (trigger_idx // sweep_size)
    frame_in_sweep: int         # synthetic (trigger_idx % sweep_size + 1)
    frame_perf_ns: int
    expected_tow_ms: int        # original event's TOW (== this trigger's GPS time)
    matched_tow_ms: Optional[int]
    matched_event_idx: Optional[int]   # reverse-lookup via tow_to_idx
    delta_triggers: Optional[int]      # matched_event_idx - trigger_idx (None on miss)
    fix_lat: Optional[float]
    fix_lon: Optional[float]


def _build_event_table(blocks: list[Block], data: bytes) -> tuple[list[dict], dict[int, int]]:
    """Decode every ExtEventPVTGeodetic block once, up-front.

    Returns:
        events -- one row per ext-event block, with parsed SbfExtEvent
            and original ext_event_index / arrival ns.
        tow_to_idx -- {tow_ms -> ext_event_index} for reverse lookup
            (matched fix -> which event it came from).
    """
    sbf_parser = SbfParser()
    events: list[dict] = []
    tow_to_idx: dict[int, int] = {}
    for b in blocks:
        if not b.is_ext_event:
            continue
        decoded = sbf_parser.feed(data[b.offset: b.offset + b.length])
        if not decoded:
            raise RuntimeError(
                f"block at offset {b.offset} parsed as good earlier but "
                "SbfParser.feed returned nothing now"
            )
        evt = decoded[0]
        events.append({
            "ext_event_index": b.ext_event_index,
            "block": b,
            "evt": evt,
            "tow_ms": int(evt.tow_ms),
        })
        if evt.tow_ms in tow_to_idx:
            # Duplicate TOW -- shouldn't normally happen; first wins
            continue
        tow_to_idx[int(evt.tow_ms)] = b.ext_event_index
    return events, tow_to_idx


def _build_synthetic_camera_schedule(
    events: list[dict],
    cameras: list[str],
    sweep_size: int,
    frame_latency_ns: int,
) -> list[tuple[int, str, int, int, int, int]]:
    """Return (t_frame_perf_ns, camera, trigger_idx, sweep, frame_in_sweep, expected_tow_ms)
    rows, one per (trigger, camera).

    The trigger schedule is the *original* recording's events --
    triggers exist regardless of what the GPS stream does, because the
    ClearCore pulses are upstream of the receiver.
    """
    frames: list[tuple[int, str, int, int, int, int]] = []
    for ev in events:
        trigger_idx = ev["ext_event_index"]
        sweep = trigger_idx // sweep_size
        frame_in_sweep = (trigger_idx % sweep_size) + 1
        t_trigger_ns = ev["block"].t_arrival_ns
        for cam in cameras:
            frames.append((
                t_trigger_ns + frame_latency_ns,
                cam,
                trigger_idx,
                sweep,
                frame_in_sweep,
                int(ev["evt"].tow_ms),
            ))
    return frames


def simulate(
    basename: str,
    drops: set[int],
    delays_ms_by_idx: dict[int, float],
    cameras: list[str],
    sweep_size: int,
    frame_latency_ms: float,
    speed: float = 1.0,
) -> tuple[list[FrameResult], dict]:
    """Run the full simulation and return (frame_results, correlator_stats)."""
    sbf_path = basename + ".sbf"
    timing_path = basename + ".timing.jsonl"
    data, blocks = _scan_recording(sbf_path, timing_path)
    events, tow_to_idx = _build_event_table(blocks, data)
    schedule = _build_schedule(data, blocks, drops, delays_ms_by_idx, speed)

    # GPS event arrivals: keep only ext-event blocks (others would be
    # add_event no-ops anyway) and pair with their decoded SbfExtEvent.
    gps_arrivals: list[tuple[int, int]] = []  # (t_perf_ns, ext_event_index)
    ext_index_to_evt = {ev["ext_event_index"]: ev["evt"] for ev in events}
    for t_send_ns, _payload, b in schedule:
        if b.is_ext_event:
            gps_arrivals.append((t_send_ns, b.ext_event_index))

    frame_latency_ns = int(frame_latency_ms * 1e6)
    cam_schedule = _build_synthetic_camera_schedule(
        events, cameras, sweep_size, frame_latency_ns,
    )

    # Interleave by time. Tie-break: GPS events before camera frames
    # if simultaneous (matches the wire reality: SBF arrives ~5 ms
    # after the pulse, frame ~300 ms after).
    events_sorted = [(t, "gps", idx) for t, idx in gps_arrivals]
    frames_sorted = [
        (t, "cam", (cam, trigger_idx, sweep, fis, exp_tow))
        for (t, cam, trigger_idx, sweep, fis, exp_tow) in cam_schedule
    ]
    # 0 = gps tag sorts before 1 = cam tag at same t
    merged = sorted(
        events_sorted + frames_sorted,
        key=lambda r: (r[0], 0 if r[1] == "gps" else 1),
    )

    corr = GpsCorrelator(sweep_size=sweep_size)
    corr.set_connected(True)

    results: list[FrameResult] = []
    for t_ns, kind, payload in merged:
        if kind == "gps":
            ext_idx = payload
            evt = ext_index_to_evt[ext_idx]
            corr.add_event(evt, t_ns)
        else:
            cam, trigger_idx, sweep, fis, exp_tow = payload
            fix = corr.pop_fix_for(cam, t_ns, sweep, fis)
            if fix is None:
                matched_idx = None
                matched_tow = None
                delta = None
                lat = lon = None
            else:
                # Reverse-lookup: fix.timestamp_utc encodes the TOW.
                # Easier path: iterate ext_index_to_evt and find the
                # event whose lat+lon match (lat/lon are unique per
                # trigger in practice because TOW is). For robustness
                # we compare via TOW recomputed from the original event.
                # We don't have access to TOW on the fix directly, so
                # we keep a side table.
                # NOTE: GpsFix carries timestamp_utc; we can derive a
                # TOW-ish value by mapping fix.timestamp_utc back, but
                # the simpler path is to remember which evt produced
                # each fix. The correlator returns the GpsFix object;
                # we can compare by identity if we keep a reverse map.
                # Instead, use the timestamp_utc -> closest-event TOW
                # lookup below.
                matched_tow = _recover_tow_from_fix(fix, ext_index_to_evt)
                matched_idx = tow_to_idx.get(matched_tow) if matched_tow is not None else None
                delta = (matched_idx - trigger_idx) if matched_idx is not None else None
                lat = fix.latitude_deg
                lon = fix.longitude_deg
            results.append(FrameResult(
                trigger_idx=int(trigger_idx),
                camera=cam,
                sweep=int(sweep),
                frame_in_sweep=int(fis),
                frame_perf_ns=int(t_ns),
                expected_tow_ms=int(exp_tow),
                matched_tow_ms=matched_tow,
                matched_event_idx=matched_idx,
                delta_triggers=delta,
                fix_lat=lat,
                fix_lon=lon,
            ))

    return results, corr.stats()


def _recover_tow_from_fix(fix, ext_index_to_evt: dict) -> Optional[int]:
    """Map a returned GpsFix back to its source event's TOW.

    GpsFix exposes ``timestamp_utc`` (datetime) but not TOW directly.
    We find the event whose ``utc_datetime()`` is closest -- the
    correlator hands back the same GpsFix object every time so this
    is exact, not fuzzy, in practice.
    """
    if fix.timestamp_utc is None:
        return None
    best_tow = None
    best_dt = None
    for evt in ext_index_to_evt.values():
        evt_utc = evt.utc_datetime()
        if evt_utc is None:
            continue
        # exact match is overwhelmingly likely
        if evt_utc == fix.timestamp_utc:
            return int(evt.tow_ms)
        delta = abs((evt_utc - fix.timestamp_utc).total_seconds())
        if best_dt is None or delta < best_dt:
            best_dt = delta
            best_tow = int(evt.tow_ms)
    if best_dt is not None and best_dt < 0.001:  # within 1 ms = same event
        return best_tow
    return None


def _summarise(results: list[FrameResult]) -> dict:
    n = len(results)
    n_matched = sum(1 for r in results if r.matched_event_idx is not None)
    n_none = n - n_matched
    deltas = [r.delta_triggers for r in results if r.delta_triggers is not None]
    delta_hist = dict(sorted(Counter(deltas).items()))
    correct = delta_hist.get(0, 0)
    return {
        "frames_total": n,
        "frames_matched": n_matched,
        "frames_no_fix": n_none,
        "frames_correct_match": correct,  # delta_triggers == 0
        "frames_wrong_match": n_matched - correct,
        "delta_histogram": delta_hist,    # offset (in triggers) -> count
    }


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _diff(ref_path: str, cur_path: str) -> int:
    with open(ref_path) as f:
        ref = [json.loads(line) for line in f if line.strip()]
    with open(cur_path) as f:
        cur = [json.loads(line) for line in f if line.strip()]
    ref_idx = {(r["trigger_idx"], r["camera"]): r for r in ref}
    cur_idx = {(r["trigger_idx"], r["camera"]): r for r in cur}

    keys = sorted(ref_idx.keys() | cur_idx.keys())
    n_diff = 0
    diff_rows: list[tuple] = []
    for k in keys:
        r = ref_idx.get(k)
        c = cur_idx.get(k)
        if r is None or c is None or r["matched_event_idx"] != c["matched_event_idx"]:
            n_diff += 1
            diff_rows.append((k, r, c))

    print(f"[diff] ref={ref_path}  cur={cur_path}")
    print(f"[diff] {len(keys)} rows, {n_diff} differ")
    if not n_diff:
        print("[diff] runs are identical")
        return 0

    print(f"[diff] showing differing rows (trigger_idx, camera): "
          f"ref_match -> cur_match  (sweep, slot)  positional delta in m")
    for (trig, cam), r, c in diff_rows[:100]:
        ref_m = r["matched_event_idx"] if r else None
        cur_m = c["matched_event_idx"] if c else None
        sweep = (r or c)["sweep"]
        slot = (r or c)["frame_in_sweep"]
        pos_delta = ""
        if r and c and r["fix_lat"] is not None and c["fix_lat"] is not None:
            d = _haversine_m(r["fix_lat"], r["fix_lon"],
                             c["fix_lat"], c["fix_lon"])
            pos_delta = f"  delta={d:6.2f} m"
        elif r and c and (r["fix_lat"] is None) != (c["fix_lat"] is None):
            pos_delta = "  (lost fix)" if c["fix_lat"] is None else "  (gained fix)"
        print(f"  trig={trig:>4d} {cam:<6}: {str(ref_m):>5} -> {str(cur_m):>5}  "
              f"(sweep={sweep}, slot={slot}){pos_delta}")
    if len(diff_rows) > 100:
        print(f"[diff] ... and {len(diff_rows)-100} more")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")

    # Default action: simulate.
    ap.add_argument("--in", dest="basename", default=None,
                    help="Recording basename. Required unless using --diff.")
    ap.add_argument("--drop-event", type=int, action="append", default=[],
                    metavar="N", help="Skip the Nth (0-based) ExtEventPVTGeodetic.")
    ap.add_argument("--delay-before-block", nargs=2, action="append", default=[],
                    metavar=("BLOCK_IDX", "MS"),
                    help="Insert MS ms of extra silence before event N.")
    ap.add_argument("--frame-latency-ms", type=float, default=DEFAULT_FRAME_LATENCY_MS)
    ap.add_argument("--sweep-size", type=int, default=DEFAULT_SWEEP_SIZE)
    ap.add_argument("--cameras", default=DEFAULT_CAMERAS,
                    help=f"Comma-separated camera serials (default {DEFAULT_CAMERAS}).")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--output", default=None,
                    help="Write per-frame results as JSON Lines to this path.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every frame's result line.")
    ap.add_argument("--diff", nargs=2, metavar=("REF_JSONL", "CUR_JSONL"),
                    help="Compare two previous --output files and exit.")
    args = ap.parse_args()

    if args.diff:
        return _diff(args.diff[0], args.diff[1])

    if not args.basename:
        print("--in BASENAME is required", file=sys.stderr)
        return 2

    drops = set(int(x) for x in args.drop_event)
    delays = {}
    for raw_idx, ms in args.delay_before_block:
        idx = int(raw_idx)
        delays[idx] = delays.get(idx, 0.0) + float(ms)
    cameras = [c.strip() for c in args.cameras.split(",") if c.strip()]

    print(f"[sim] recording={args.basename}  cameras={cameras}  "
          f"sweep_size={args.sweep_size}  frame_latency={args.frame_latency_ms} ms")
    if drops:
        print(f"[sim] mutation: drop events {sorted(drops)}")
    if delays:
        print(f"[sim] mutation: extra delays {dict(sorted(delays.items()))}")

    results, corr_stats = simulate(
        basename=args.basename,
        drops=drops,
        delays_ms_by_idx=delays,
        cameras=cameras,
        sweep_size=args.sweep_size,
        frame_latency_ms=args.frame_latency_ms,
        speed=args.speed,
    )

    if args.output:
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w") as f:
            for r in results:
                f.write(json.dumps(asdict(r)) + "\n")
        print(f"[sim] wrote {len(results)} rows -> {args.output}")

    if args.verbose:
        print("[sim] per-frame results:")
        for r in results:
            if r.matched_event_idx is None:
                tag = "  NO FIX"
            elif r.delta_triggers == 0:
                tag = "  ok"
            else:
                tag = f"  off by {r.delta_triggers:+d}"
            print(f"  trig={r.trigger_idx:>4d} {r.camera:<6} "
                  f"sweep={r.sweep:>2d} slot={r.frame_in_sweep:>2d}  "
                  f"expected_tow={r.expected_tow_ms}  "
                  f"matched_idx={r.matched_event_idx}{tag}")

    summary = _summarise(results)
    print()
    print("[sim] summary:")
    print(f"  frames simulated        : {summary['frames_total']}")
    print(f"  matched to a fix        : {summary['frames_matched']}")
    print(f"  no fix returned         : {summary['frames_no_fix']}")
    print(f"  matched the RIGHT event : {summary['frames_correct_match']}")
    print(f"  matched the WRONG event : {summary['frames_wrong_match']}")
    print(f"  delta histogram         : {summary['delta_histogram']}")
    print()
    print("[sim] correlator stats:")
    for k, v in corr_stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
