"""Validate GPS metadata in captured .bay14line files.

Walks a directory of .bay14line files (one or more per camera) and runs
three structural checks against the per-frame ``gps`` JSON block:

1. **Cross-camera identity** -- for each (sweep, frame_in_sweep) pair,
   every camera that recorded that frame must report the same TOW.
   Mismatch = the correlator is mis-correlating across cameras
   (worst-case failure mode F5 from the planning discussion).

2. **TOW monotonicity per camera** -- consecutive frames (ordered by
   sweep, then frame_in_sweep) must have strictly increasing TOW.
   A flat or backwards step means the same fix was returned twice or
   the slot lookup went wrong.

3. **Sweep-gap pattern** -- intra-sweep TOW deltas should cluster
   around 200 ms; inter-sweep (last frame of sweep N -> first frame
   of sweep N+1) should be noticeably larger (~300 ms). If the
   distribution is uniform, the ClearCore wasn't actually pulsing in
   sweep mode, or the receiver dropped events.

The tool also reports the fraction of frames with no GPS (gps=None)
per camera, since plan-A end-to-end validation cares about that too.

Usage::

    py tools/bay14_gps_check.py --root "E:/MyProject/line_3"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    host_dir = os.path.join(repo_root, "host")
    for p in (repo_root, host_dir, here):
        if p and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_paths()

from bay14_export_core import iter_line_frames  # noqa: E402


def _find_bay14line(root: Path) -> list[Path]:
    return sorted(root.rglob("*.bay14line"))


_PART_RE = __import__("re").compile(r"_p\d+$")


def _line_key(path: str) -> str:
    """Collapse a camera's part-files into one logical line/run.

    A single recording is split across writer parts (``..._p0``,
    ``..._p1``) but shares everything else in the stem. The per-line
    SweepCounter resets to S1 each run, so sweep numbers only make sense
    *within* a line -- group by the stem with the ``_pN`` suffix and the
    camera-serial prefix removed.
    """
    stem = Path(path).stem
    stem = _PART_RE.sub("", stem)
    # Drop the leading ``Cam<serial>_`` so the same line on different
    # cameras shares a key (for cross-camera identity).
    parts = stem.split("_", 1)
    return parts[1] if len(parts) == 2 and parts[0].startswith("Cam") else stem


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, nargs="+",
                    help="One or more directories to walk recursively for "
                         ".bay14line files (cameras may live on separate drives).")
    ap.add_argument("--show-mismatches", type=int, default=10,
                    help="Cap on how many mismatch rows to print per check.")
    ap.add_argument("--dump-frames", action="store_true",
                    help="Print every frame's GPS state (sweep, fis, ts, lat, lon).")
    args = ap.parse_args()

    roots = [Path(r).expanduser().resolve() for r in args.root]
    paths: list[Path] = []
    for root in roots:
        if not root.is_dir():
            print(f"[check] not a directory: {root}", file=sys.stderr)
            return 2
        paths.extend(_find_bay14line(root))
    paths = sorted(set(paths))
    if not paths:
        print(f"[check] no .bay14line files under {roots}", file=sys.stderr)
        return 2

    print(f"[check] scanning {len(paths)} .bay14line file(s) under "
          f"{', '.join(str(r) for r in roots)}")

    # Collected rows: (cam, line, sweep, frame_in_sweep, ts_us_or_None,
    #                  lat, lon, source_file)
    # ``line`` groups part-files of one recording (sweep numbers reset per
    # line). ts_us is microseconds-since-epoch from timestamp_utc --
    # ordered AND equality-comparable across cameras.
    rows: list[tuple[str, str, int, int, int | None, float | None, float | None, str]] = []
    files_per_cam: dict[str, set[str]] = defaultdict(set)
    no_gps_per_cam: dict[str, int] = defaultdict(int)
    frames_per_cam: dict[str, int] = defaultdict(int)

    import datetime as _dt

    def _parse_ts_us(ts_str):
        if ts_str is None:
            return None
        try:
            dt = _dt.datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        except ValueError:
            return None
        epoch = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
        return int((dt - epoch).total_seconds() * 1_000_000)

    # Additional parallel data: camera acquire_timestamp per row, in us-since-epoch.
    cam_ts_per_row: list[int | None] = []

    for path in paths:
        line = _line_key(str(path))
        try:
            for meta, _arr in iter_line_frames(path, recovery_notice=None, meta_only=True):
                cam = str(meta.get("camera_serial") or meta.get("camera") or "?")
                sweep = int(meta.get("sweep", -1))
                fis = int(meta.get("frame_in_sweep", -1))
                acq_ts_us = _parse_ts_us(meta.get("acquire_timestamp"))
                gps_dict = meta.get("gps")
                if isinstance(gps_dict, dict):
                    ts_us = _parse_ts_us(gps_dict.get("timestamp_utc"))
                    lat = gps_dict.get("latitude_deg")
                    lon = gps_dict.get("longitude_deg")
                else:
                    ts_us = None
                    lat = lon = None
                    no_gps_per_cam[cam] += 1
                rows.append((cam, line, sweep, fis, ts_us, lat, lon, str(path)))
                cam_ts_per_row.append(acq_ts_us)
                files_per_cam[cam].add(str(path))
                frames_per_cam[cam] += 1
        except Exception as exc:
            print(f"[check] error reading {path}: {exc!r}", file=sys.stderr)

    cams = sorted(files_per_cam.keys())
    print(f"[check] cameras found: {cams}")
    for cam in cams:
        n = frames_per_cam[cam]
        no_gps = no_gps_per_cam[cam]
        pct = (100.0 * no_gps / n) if n else 0
        print(f"  {cam}: {n} frame(s), {no_gps} with no GPS ({pct:.1f}%)")

    lines = sorted({r[1] for r in rows})
    print(f"[check] recordings/lines found: {lines}")

    # Per-(camera, line) no-GPS rate -- the real end-to-end metric.
    print()
    print("[check] per-(camera, line) GPS coverage:")
    per_line = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # cam->line->[has,no]
    for cam, line, _sw, _fis, ts_us, *_ in rows:
        per_line[cam][line][0 if ts_us is not None else 1] += 1
    for cam in cams:
        for line in lines:
            has, no = per_line[cam][line]
            tot = has + no
            if tot == 0:
                continue
            pct = 100.0 * has / tot
            print(f"  {cam} / {line}: {has}/{tot} have GPS ({pct:.0f}%)")

    # Per-(camera, line, sweep) coverage to spotlight failure patterns.
    print()
    print("[check] per-(camera, line, sweep) GPS coverage:")
    per_sweep = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # (cam,line)->sweep->[has,no]
    for cam, line, sweep, _fis, ts_us, *_ in rows:
        per_sweep[(cam, line)][sweep][0 if ts_us is not None else 1] += 1
    for cam in cams:
        for line in lines:
            key = (cam, line)
            if key not in per_sweep:
                continue
            sweeps = sorted(per_sweep[key].keys())
            per = " ".join(
                f"s{sw}:{per_sweep[key][sw][0]}/"
                f"{per_sweep[key][sw][0]+per_sweep[key][sw][1]}"
                for sw in sweeps
            )
            print(f"  {cam} / {line}: {per}")

    # Per-frame_in_sweep coverage: is the miss correlated with slot index?
    print()
    print("[check] per-frame_in_sweep GPS coverage (all cams/lines pooled):")
    per_fis = defaultdict(lambda: [0, 0])  # fis -> [has, no]
    for cam, line, _sw, fis, ts_us, *_ in rows:
        per_fis[fis][0 if ts_us is not None else 1] += 1
    for fis in sorted(per_fis.keys()):
        has, no = per_fis[fis]
        tot = has + no
        pct = 100.0 * has / tot if tot else 0
        print(f"  fis={fis:>2d}: {has:>3d}/{tot:<3d} ({pct:3.0f}%)")

    if args.dump_frames:
        print()
        print("[check] frame-level dump (cam | line | sweep | fis | gps? | dt_cam(ms) | dt_gps(ms) | lag(ms)):")
        # zip rows with cam_ts to sort consistently
        joined = list(zip(rows, cam_ts_per_row))
        joined.sort(key=lambda x: (x[0][0], x[0][1], x[0][2], x[0][3]))  # cam, line, sweep, fis
        prev_cam_ts: int | None = None
        prev_gps_ts: int | None = None
        prev_key: tuple[str, str, int] | None = None
        for (cam, line, sweep, fis, ts_us, _lat, _lon, _src), cam_ts in joined:
            key = (cam, line, sweep)
            if key != prev_key:
                prev_cam_ts = None
                prev_gps_ts = None
                prev_key = key
            if cam_ts is not None and prev_cam_ts is not None:
                dt_cam_ms = (cam_ts - prev_cam_ts) / 1000.0
                dt_cam_str = f"{dt_cam_ms:+8.1f}"
            else:
                dt_cam_str = "       -"
            if ts_us is not None and prev_gps_ts is not None:
                dt_gps_ms = (ts_us - prev_gps_ts) / 1000.0
                dt_gps_str = f"{dt_gps_ms:+8.1f}"
            else:
                dt_gps_str = "       -"
            if ts_us is not None and cam_ts is not None:
                lag_ms = (cam_ts - ts_us) / 1000.0
                lag_str = f"{lag_ms:+8.1f}"
            else:
                lag_str = "       -"
            tag = "YES" if ts_us is not None else " NO"
            print(f"  {cam}  {line}  s={sweep:>2d}  fis={fis:>2d}  {tag}  "
                  f"dt_cam={dt_cam_str} dt_gps={dt_gps_str} lag={lag_str}")
            prev_cam_ts = cam_ts if cam_ts is not None else prev_cam_ts
            prev_gps_ts = ts_us if ts_us is not None else prev_gps_ts

    failed = 0

    # ── Check 1: cross-camera identity ─────────────────────────────────
    print()
    print("[check 1] cross-camera identity (same trigger -> same TOW across cams)")
    by_slot: dict[tuple[str, int, int], dict[str, int | None]] = defaultdict(dict)
    for cam, line, sweep, fis, tow, _lat, _lon, _src in rows:
        by_slot[(line, sweep, fis)][cam] = tow
    mismatches_c1 = []
    incomplete = 0
    for (line, sweep, fis), cam_tows in by_slot.items():
        # Skip frames where not every cam has data (transient drops are
        # not a correlation failure).
        if any(t is None for t in cam_tows.values()):
            continue
        towset = set(cam_tows.values())
        if len(cam_tows) >= 2 and len(towset) > 1:
            mismatches_c1.append((line, sweep, fis, dict(cam_tows)))
        if len(cam_tows) < len(cams):
            incomplete += 1
    print(f"  slots checked      : {len(by_slot)}")
    print(f"  slots missing a cam: {incomplete}")
    print(f"  TOW mismatches     : {len(mismatches_c1)}")
    if mismatches_c1:
        failed += 1
        for line, sweep, fis, cam_tows in mismatches_c1[:args.show_mismatches]:
            print(f"    line={line} sweep={sweep:>3d} fis={fis:>2d}: {cam_tows}")
        if len(mismatches_c1) > args.show_mismatches:
            print(f"    ... and {len(mismatches_c1)-args.show_mismatches} more")
    else:
        print("  PASS")

    # ── Check 2: TOW monotonicity per camera ───────────────────────────
    print()
    print("[check 2] TOW monotonicity per (camera, line) "
          "(sorted by sweep then frame_in_sweep)")
    rows_by_cl: dict[tuple[str, str], list[tuple[int, int, int | None]]] = defaultdict(list)
    for cam, line, sweep, fis, tow, *_ in rows:
        rows_by_cl[(cam, line)].append((sweep, fis, tow))
    for cam in cams:
        for line in lines:
            key = (cam, line)
            if key not in rows_by_cl:
                continue
            ordered = sorted(rows_by_cl[key], key=lambda r: (r[0], r[1]))
            violations = []
            last_tow = None
            for sweep, fis, tow in ordered:
                if tow is None or last_tow is None:
                    last_tow = tow
                    continue
                if tow <= last_tow:
                    violations.append((sweep, fis, last_tow, tow))
                last_tow = tow
            n_tagged = sum(1 for _, _, t in ordered if t is not None)
            print(f"  {cam} / {line}: {len(violations)} violations "
                  f"(over {n_tagged} GPS-tagged frames)")
            if violations:
                failed += 1
                for sweep, fis, prev, cur in violations[:args.show_mismatches]:
                    print(f"    sweep={sweep:>3d} fis={fis:>2d}: "
                          f"prev_tow={prev} cur_tow={cur}")
                if len(violations) > args.show_mismatches:
                    print(f"    ... and {len(violations)-args.show_mismatches} more")

    # ── Check 3: sweep-gap pattern (use first camera as proxy) ─────────
    # We don't have actual TOW (in ms) because GpsFix doesn't carry it;
    # we used hash(timestamp_utc) above as an equality key. For the gap
    # pattern we need a real ms value. Read GpsFix.timestamp_utc directly.
    print()
    print("[check 3] inter-frame timestamp_utc deltas (intra-sweep vs inter-sweep)")
    # Reuse the already-collected rows (line-aware) instead of re-reading.
    rows_with_ts: dict[tuple[str, str], list[tuple[int, int, int | None]]] = defaultdict(list)
    for cam, line, sweep, fis, ts_us, *_ in rows:
        rows_with_ts[(cam, line)].append((sweep, fis, ts_us))

    # Use the first camera, pooling all its lines (gaps reset per line).
    if cams:
        cam = cams[0]
        intra_gaps_ms = []
        inter_gaps_ms = []
        for line in lines:
            key = (cam, line)
            if key not in rows_with_ts:
                continue
            ordered = sorted(rows_with_ts[key], key=lambda r: (r[0], r[1]))
            prev = None
            for sweep, fis, ts_us in ordered:
                if ts_us is None:
                    prev = None
                    continue
                if prev is not None:
                    prev_sweep, prev_fis, prev_ts = prev
                    delta_ms = int((ts_us - prev_ts) / 1000)
                    if sweep == prev_sweep:
                        intra_gaps_ms.append(delta_ms)
                    elif sweep > prev_sweep:
                        inter_gaps_ms.append(delta_ms)
                prev = (sweep, fis, ts_us)

        def _stats(label, gaps):
            if not gaps:
                print(f"  {label:<14s}: no samples")
                return
            gaps_sorted = sorted(gaps)
            mid = gaps_sorted[len(gaps_sorted) // 2]
            mn, mx = gaps_sorted[0], gaps_sorted[-1]
            print(f"  {label:<14s}: n={len(gaps_sorted):>3d}  min={mn} ms  "
                  f"median={mid} ms  max={mx} ms")

        _stats("intra-sweep", intra_gaps_ms)
        _stats("inter-sweep", inter_gaps_ms)

        if intra_gaps_ms and inter_gaps_ms:
            mi = sorted(intra_gaps_ms)[len(intra_gaps_ms) // 2]
            me = sorted(inter_gaps_ms)[len(inter_gaps_ms) // 2]
            if me > mi:
                print(f"  PASS: inter-sweep median ({me} ms) > intra-sweep "
                      f"median ({mi} ms) -- sweep structure visible in GPS")
            else:
                print(f"  WARN: inter-sweep median ({me} ms) not > intra-sweep "
                      f"median ({mi} ms) -- sweep structure NOT visible "
                      "(receiver free-running, or all events dropped)")

    print()
    if failed == 0:
        print("[check] all enabled checks PASSED")
        return 0
    print(f"[check] {failed} check(s) FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
