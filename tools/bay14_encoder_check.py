"""Report the per-frame encoder (focus) block in captured .bay14line files.

Companion to ``bay14_gps_check.py``, which validates the ``gps`` block but
says nothing about ``focus``. The encoder is what tells a later analysis WHERE
THE LENS WAS for a given frame, so a line with perfect GPS and a missing or
stale encoder is still not usable as a through-focus record.

Three things can go wrong, and each looks different in the output:

* **Missing** -- ``focus`` is absent, which :meth:`MainWindow._focus_metadata`
  returns when the PCB is disconnected or the encoder is not present for that
  camera. Reported as ``no encoder``.
* **Stale** -- ``encoder_stale`` is true: the reading belongs to a camera other
  than the selected one, so it is a leftover, not this frame's position.
* **Frozen** -- the count never changes. Legitimate when the lens is parked,
  which is why this is reported rather than judged; a dive that reads a
  constant count means the AMT203S SPI read died (see the ``drive_cadence``
  note in ``app.py``).

Usage::

    py tools/bay14_encoder_check.py --root "D:/Project"
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
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
from bay14_gps_check import _line_key  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, nargs="+",
                    help="Directories to walk recursively for .bay14line files.")
    ap.add_argument("--dump-frames", action="store_true",
                    help="Print every frame's encoder state.")
    args = ap.parse_args()

    paths: list[Path] = []
    for root in args.root:
        p = Path(root).expanduser().resolve()
        if not p.is_dir():
            print(f"[enc] not a directory: {p}", file=sys.stderr)
            return 2
        paths.extend(sorted(p.rglob("*.bay14line")))
    paths = sorted(set(paths))
    if not paths:
        print(f"[enc] no .bay14line files under {args.root}", file=sys.stderr)
        return 2

    print(f"[enc] scanning {len(paths)} .bay14line file(s)")

    # (cam, line) -> list of (sweep, fis, focus_dict_or_None)
    rows: dict[tuple[str, str], list[tuple[int, int, dict | None]]] = defaultdict(list)
    for path in paths:
        line = _line_key(str(path))
        try:
            for meta, _arr in iter_line_frames(path, recovery_notice=None,
                                               meta_only=True):
                cam = str(meta.get("camera_serial") or meta.get("camera") or "?")
                focus = meta.get("focus")
                rows[(cam, line)].append((
                    int(meta.get("sweep", -1)),
                    int(meta.get("frame_in_sweep", -1)),
                    focus if isinstance(focus, dict) else None,
                ))
        except Exception as exc:
            print(f"[enc] error reading {path}: {exc!r}", file=sys.stderr)

    failed = 0
    for (cam, line) in sorted(rows):
        entries = sorted(rows[(cam, line)], key=lambda r: (r[0], r[1]))
        total = len(entries)
        present = [f for _, _, f in entries if f is not None]
        missing = total - len(present)
        stale = sum(1 for f in present if f.get("encoder_stale"))
        counts = [f.get("encoder_count") for f in present
                  if f.get("encoder_count") is not None]
        ages = [f.get("encoder_age_s") for f in present
                if f.get("encoder_age_s") is not None]
        epochs = Counter(f.get("encoder_epoch") for f in present)
        enc_cams = Counter(f.get("encoder_camera") for f in present)

        print()
        print(f"[enc] {cam} / {line}")
        print(f"  frames             : {total}")
        print(f"  with encoder block : {len(present)}  "
              f"({100.0 * len(present) / total:.0f}%)")
        print(f"  no encoder         : {missing}")
        print(f"  stale readings     : {stale}")
        if missing or stale:
            failed += 1

        if counts:
            lo, hi = min(counts), max(counts)
            distinct = len(set(counts))
            print(f"  encoder_count      : min={lo} max={hi} span={hi - lo} "
                  f"distinct={distinct}")
            if distinct == 1:
                print(f"  NOTE: count frozen at {lo} for the whole line -- "
                      f"expected if the lens was parked, a dead SPI read if not.")
        else:
            print("  encoder_count      : none recorded")
            failed += 1
        if ages:
            print(f"  encoder_age_s      : min={min(ages)} max={max(ages)}")
        print(f"  encoder_epoch      : {dict(epochs)}")
        print(f"  encoder_camera     : {dict(enc_cams)}")

        if args.dump_frames:
            for sweep, fis, f in entries:
                if f is None:
                    print(f"    s={sweep:>2d} fis={fis:>2d}  NO ENCODER")
                else:
                    print(f"    s={sweep:>2d} fis={fis:>2d}  "
                          f"count={f.get('encoder_count')} "
                          f"age={f.get('encoder_age_s')} "
                          f"stale={f.get('encoder_stale')} "
                          f"enc_cam={f.get('encoder_camera')} "
                          f"sel_cam={f.get('selected_camera')}")

    print()
    if failed == 0:
        print("[enc] encoder present and fresh on every frame")
        return 0
    print(f"[enc] {failed} (camera, line) group(s) have missing or stale encoder data")
    return 1


if __name__ == "__main__":
    sys.exit(main())
