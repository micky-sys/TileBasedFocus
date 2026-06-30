"""Per-line, per-camera dropped-frame report for one or more .bay14line trees.

How drops are detected
----------------------
``frame_in_sweep`` is assigned on the host, per camera, by
:class:`capture.sweep_counter.SweepCounter` the moment the host processes
a frame (1..SWEEP_SIZE with rollover). A frame dropped *before* the host
sees it (camera buffer, GenICam callback, raw-SHM queue) therefore does
NOT leave a gap in the numbers -- it just compacts the sequence into a
shorter run. So "missing numbers within max" is useless for that class
of drop, which is the one we actually care about here.

The cameras share the same hardware trigger, so for any given
(project, line, take, sweep) the true expected frame count is identical
across cameras. We compute it as ``SWEEP_SIZE`` for every fully-fired
sweep and ``max(received_per_camera)`` for the final (possibly partial)
sweep of each line. A camera that received fewer than that for a given
sweep dropped the difference -- regardless of whether the lost frame
appears as a missing number or as a shortened tail.

Output:

* per-(line, take) row with per-camera received vs expected and a drop %
* per-camera summary across all lines
* per-drive summary
* with ``--details``: per-sweep breakdown listing every sweep that was
  short on any camera, the count received per camera, and which camera(s)
  lost frames there.

Usage:
  py -3 tools/bay14line_drop_report.py H:\\Nablus I:\\Nablus J:\\Nablus
  py -3 tools/bay14line_drop_report.py --details H:\\Nablus I:\\Nablus J:\\Nablus
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_HOST = _REPO / "host"
for p in (str(_REPO), str(_HOST)):
    if p not in sys.path:
        sys.path.insert(0, p)

from capture.raw_bay_writer import (  # noqa: E402
    LINE_HEADER_FIXED_PREFIX,
    LINE_INDEX_ENTRY_SIZE,
    parse_line_header,
    parse_line_header_prefix,
    parse_line_index,
    recover_line_chunks,
)
from capture.sweep_counter import SWEEP_SIZE  # noqa: E402


_FNAME_RE = re.compile(
    r"^Cam(?P<serial>\d+)_(?P<date>\d{8})_(?P<project>.+?)_L(?P<line>\d+)_"
    r"(?P<hhmmss>\d{6})_p(?P<part>\d+)\.bay14line$",
    re.IGNORECASE,
)


@dataclass
class FileFacts:
    path: Path
    drive: str
    serial: str
    project: str
    line: str
    hhmmss: str
    part: int
    size: int
    n_frames: int
    width: int
    height: int
    codec: str
    pixel_format: str
    sweep_ids: list[int] = field(default_factory=list)
    frame_in_sweep_ids: list[int] = field(default_factory=list)
    recovered: bool = False
    payload_total: int = 0  # sum of payload_len from the index


def _parse_filename(path: Path) -> tuple[str, str, str, str, int] | None:
    m = _FNAME_RE.match(path.name)
    if not m:
        return None
    return (
        m.group("serial"),
        m.group("project"),
        m.group("line"),
        m.group("hhmmss"),
        int(m.group("part")),
    )


def _drive_root(path: Path) -> str:
    drive = path.drive
    return drive.rstrip(":\\/").upper() or str(path.anchor)


def _read_facts(path: Path) -> FileFacts | None:
    parsed = _parse_filename(path)
    if parsed is None:
        return None
    serial, project, line, hhmmss, part = parsed
    size = path.stat().st_size
    with open(path, "rb") as f:
        prefix = f.read(LINE_HEADER_FIXED_PREFIX)
        try:
            header_len, n_frames_prefix, index_offset, _ = parse_line_header_prefix(
                prefix
            )
        except Exception as exc:
            print(f"!! {path}: header prefix parse failed: {exc}", file=sys.stderr)
            return None
        f.seek(0)
        full_hdr = f.read(header_len)
        try:
            meta, _hl, n_frames, _idx = parse_line_header(full_hdr)
        except Exception as exc:
            print(f"!! {path}: header parse failed: {exc}", file=sys.stderr)
            return None

        recovered = False
        chunks: list[tuple[int, int, int, int, int]]  # (sweep, fis, off, plen, unc)
        if index_offset and n_frames_prefix > 0:
            f.seek(index_offset)
            index_blob = f.read(n_frames_prefix * LINE_INDEX_ENTRY_SIZE)
            try:
                chunks = parse_line_index(index_blob, n_frames_prefix)
            except Exception as exc:
                print(f"!! {path}: index parse failed ({exc}); recovering",
                      file=sys.stderr)
                _meta, chunks = recover_line_chunks(str(path))
                recovered = True
        else:
            _meta, chunks = recover_line_chunks(str(path))
            recovered = True

    sweep_ids = [c[0] for c in chunks]
    fis_ids = [c[1] for c in chunks]
    payload_total = sum(c[3] for c in chunks)

    facts = FileFacts(
        path=path,
        drive=_drive_root(path),
        serial=serial,
        project=project,
        line=line,
        hhmmss=hhmmss,
        part=part,
        size=size,
        n_frames=len(chunks),
        width=int(meta.get("width", 0)),
        height=int(meta.get("height", 0)),
        codec=str(meta.get("compression", meta.get("codec", "?"))),
        pixel_format=str(meta.get("format", meta.get("pixel_format", "?"))),
        sweep_ids=sweep_ids,
        frame_in_sweep_ids=fis_ids,
        recovered=recovered,
        payload_total=payload_total,
    )
    return facts


@dataclass
class LineGroup:
    """All files for one camera × one (project, line, hhmmss) — all parts."""
    serial: str
    project: str
    line: str
    hhmmss: str
    drive: str
    parts: dict[int, FileFacts] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.serial, self.project, self.line, self.hhmmss)

    def all_frames_by_sweep(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for ff in self.parts.values():
            for s, fi in zip(ff.sweep_ids, ff.frame_in_sweep_ids):
                out[s].append(fi)
        return out

    def received_per_sweep(self) -> dict[int, int]:
        """Number of frames this camera actually received per sweep id.

        Counts entries, not distinct fis values, so a duplicate (e.g.
        an unlikely re-emit) would show up. In normal operation each fis
        appears at most once per sweep.
        """
        return {s: len(fis) for s, fis in self.all_frames_by_sweep().items()}


def expected_per_sweep_cross(
    line_groups_by_serial: dict[str, "LineGroup"],
    sweep_size: int = SWEEP_SIZE,
) -> dict[int, int]:
    """Compute the true expected frame count per sweep for ONE (line, take),
    given all per-camera LineGroups for that line.

    Rules:
    * A "full" sweep is one where any camera received ``sweep_size`` frames
      (the cameras share the hardware trigger; that's the PCB's planned
      sweep length). Expected = ``sweep_size``.
    * The final sweep of a line may be deliberately partial (operator stopped
      the line mid-sweep). For that we use the max received across cameras.
    * If no camera received ``sweep_size`` for a non-last sweep but received
      fewer, we still expect ``sweep_size`` (all cameras dropped frames in
      that sweep -- pessimistic but correct: the trigger fired N times).
    """
    received_by_sweep: dict[int, dict[str, int]] = defaultdict(dict)
    for serial, g in line_groups_by_serial.items():
        for s, n in g.received_per_sweep().items():
            received_by_sweep[s][serial] = n
    if not received_by_sweep:
        return {}
    sweeps_sorted = sorted(received_by_sweep.keys())
    last_sweep = sweeps_sorted[-1]
    out: dict[int, int] = {}
    for s in sweeps_sorted:
        per_cam = received_by_sweep[s]
        max_rec = max(per_cam.values()) if per_cam else 0
        if s == last_sweep and max_rec < sweep_size:
            out[s] = max_rec
        else:
            out[s] = sweep_size
    return out


def gather(roots: list[Path]) -> list[FileFacts]:
    out: list[FileFacts] = []
    for root in roots:
        if not root.exists():
            print(f"!! root missing: {root}", file=sys.stderr)
            continue
        if root.is_file():
            ff = _read_facts(root)
            if ff is not None:
                out.append(ff)
            continue
        for p in sorted(root.rglob("*.bay14line")):
            ff = _read_facts(p)
            if ff is not None:
                out.append(ff)
    return out


def build_groups(files: list[FileFacts]) -> dict[tuple[str, str, str, str], LineGroup]:
    groups: dict[tuple[str, str, str, str], LineGroup] = {}
    for ff in files:
        k = (ff.serial, ff.project, ff.line, ff.hhmmss)
        g = groups.get(k)
        if g is None:
            g = LineGroup(
                serial=ff.serial,
                project=ff.project,
                line=ff.line,
                hhmmss=ff.hhmmss,
                drive=ff.drive,
            )
            groups[k] = g
        g.parts[ff.part] = ff
    return groups


def fmt_pct(n: int, d: int) -> str:
    if d <= 0:
        return "   -- "
    return f"{(100.0 * n / d):5.1f}%"


def print_report(
    groups: dict[tuple[str, str, str, str], LineGroup],
    show_details: bool,
) -> None:
    # Key: (project, line, hhmmss) -> {serial: LineGroup}. Using hhmmss keeps
    # repeated takes of the same line distinct (the operator can re-record a
    # line; the writer file names include the start time so two takes coexist).
    by_line: dict[tuple[str, str, str], dict[str, LineGroup]] = defaultdict(dict)
    by_serial_summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {"received": 0, "expected": 0, "drops": 0, "lines": 0}
    )
    by_drive_summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {"received": 0, "expected": 0, "drops": 0, "files": 0}
    )

    for g in groups.values():
        by_line[(g.project, g.line, g.hhmmss)][g.serial] = g

    all_serials = sorted({g.serial for g in groups.values()})

    # Compute per-(line, take, camera) drop using cross-camera expectation.
    drop_per_group: dict[tuple[str, str, str, str], tuple[int, int, int]] = {}
    short_sweeps_per_group: dict[
        tuple[str, str, str, str], list[tuple[int, int, int]]
    ] = {}  # value = list of (sweep, received, expected) where received < expected
    for (proj, line, hhmmss), per_serial in by_line.items():
        exp_per_sweep = expected_per_sweep_cross(per_serial)
        total_exp = sum(exp_per_sweep.values())
        for serial in all_serials:
            g = per_serial.get(serial)
            if g is None:
                drop_per_group[(proj, line, hhmmss, serial)] = (0, total_exp, total_exp)
                short_sweeps_per_group[(proj, line, hhmmss, serial)] = [
                    (s, 0, e) for s, e in sorted(exp_per_sweep.items()) if e > 0
                ]
                continue
            rec_per_sweep = g.received_per_sweep()
            received = 0
            shorts: list[tuple[int, int, int]] = []
            for s, exp in exp_per_sweep.items():
                got = rec_per_sweep.get(s, 0)
                received += got
                if got < exp:
                    shorts.append((s, got, exp))
            drops = total_exp - received
            drop_per_group[(proj, line, hhmmss, serial)] = (received, total_exp, drops)
            short_sweeps_per_group[(proj, line, hhmmss, serial)] = shorts

            su = by_serial_summary[serial]
            su["received"] += received
            su["expected"] += total_exp
            su["drops"] += drops
            su["lines"] += 1
            dr = by_drive_summary[g.drive]
            dr["received"] += received
            dr["expected"] += total_exp
            dr["drops"] += drops
            dr["files"] += sum(1 for _ in g.parts.values())

    print(f"\n=== Per-(line,take) drop report ({len(groups)} (cam,line,take) "
          f"groups, {len(all_serials)} cameras) ===\n")
    header = ["project", "line", "hhmmss"]
    for s in all_serials:
        header += [f"cam{s} recv", f"cam{s} exp", f"cam{s} drop%"]
    widths = [12, 5, 7] + [10, 10, 9] * len(all_serials)
    fmt_row = "  ".join(f"{{:>{w}}}" for w in widths)
    print(fmt_row.format(*header))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))

    def _sort_key(k: tuple[str, str, str]) -> tuple[str, int | str, str]:
        proj, line, hhmmss = k
        return (proj, int(line) if line.isdigit() else line, hhmmss)

    for (proj, line, hhmmss) in sorted(by_line.keys(), key=_sort_key):
        row: list[str] = [proj, line, hhmmss]
        for s in all_serials:
            rec, exp, drops = drop_per_group[(proj, line, hhmmss, s)]
            row += [str(rec), str(exp), fmt_pct(drops, exp)]
        print(fmt_row.format(*row))

    print("\n=== Per-camera summary ===")
    print(f"{'serial':>10}  {'lines':>5}  {'received':>10}  "
          f"{'expected':>10}  {'drops':>9}  {'drop%':>7}")
    print("-" * 60)
    for s in sorted(by_serial_summary.keys()):
        st = by_serial_summary[s]
        print(f"{s:>10}  {st['lines']:>5}  {st['received']:>10}  "
              f"{st['expected']:>10}  {st['drops']:>9}  "
              f"{fmt_pct(st['drops'], st['expected'])}")

    print("\n=== Per-drive summary ===")
    print(f"{'drive':>6}  {'files':>5}  {'received':>10}  "
          f"{'expected':>10}  {'drops':>9}  {'drop%':>7}")
    print("-" * 56)
    for d in sorted(by_drive_summary.keys()):
        st = by_drive_summary[d]
        print(f"{d:>6}  {st['files']:>5}  {st['received']:>10}  "
              f"{st['expected']:>10}  {st['drops']:>9}  "
              f"{fmt_pct(st['drops'], st['expected'])}")

    if not show_details:
        return

    print("\n=== Per-(line,take) details (per-sweep cross-camera) ===")
    for (proj, line, hhmmss) in sorted(by_line.keys(), key=_sort_key):
        per_serial = by_line[(proj, line, hhmmss)]
        exp_per_sweep = expected_per_sweep_cross(per_serial)
        print(f"\n  project={proj!r} line=L{line} hhmmss={hhmmss}  "
              f"({len(exp_per_sweep)} sweeps, sum expected="
              f"{sum(exp_per_sweep.values())})")
        for s in all_serials:
            g = per_serial.get(s)
            if g is None:
                print(f"    cam{s}: (no file on this take)")
                continue
            parts_str = ", ".join(
                f"p{p}={g.parts[p].n_frames}f@{g.parts[p].drive}"
                for p in sorted(g.parts.keys())
            )
            rec, exp, drops = drop_per_group[(proj, line, hhmmss, s)]
            print(f"    cam{s} drive={g.drive} recv={rec} exp={exp} "
                  f"drops={drops} ({parts_str})")

        # Show short sweeps: any sweep where at least one camera dropped.
        short_sweeps: set[int] = set()
        for s in all_serials:
            for (sw, _got, _exp) in short_sweeps_per_group[
                (proj, line, hhmmss, s)
            ]:
                short_sweeps.add(sw)
        if not short_sweeps:
            print("    -> all sweeps complete on every camera")
            continue
        print(f"    -> {len(short_sweeps)} sweep(s) with at least one camera short:")
        for sw in sorted(short_sweeps):
            exp = exp_per_sweep.get(sw, 0)
            parts: list[str] = []
            for s in all_serials:
                g = per_serial.get(s)
                got = g.received_per_sweep().get(sw, 0) if g else 0
                short = exp - got
                tag = f"cam{s}:{got}/{exp}"
                if short > 0:
                    tag += f" (-{short})"
                parts.append(tag)
            print(f"       sweep={sw:>5}  " + "  ".join(parts))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="+", type=Path,
                    help="Folders (or individual .bay14line files) to scan.")
    ap.add_argument("--details", action="store_true",
                    help="Per-sweep listing of missing frame numbers.")
    args = ap.parse_args(argv)

    files = gather(args.roots)
    if not files:
        print("no .bay14line files found", file=sys.stderr)
        return 1
    groups = build_groups(files)
    print_report(groups, show_details=args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
