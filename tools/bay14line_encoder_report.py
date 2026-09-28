"""Report the focus encoder position recorded per frame in .bay14line files.

The save-and-analyse focus plan rests on one assumption: every frame's chunk
JSON carries the lens position it was shot at. A clean frame count is worthless
if all frames claim the same encoder_count. This checks that assumption.

Usage:
    py -3 tools/bay14line_encoder_report.py <file.bay14line> [...]
    py -3 tools/bay14line_encoder_report.py --per-frame <file.bay14line>

Reads only the line header, the appended index and each frame's chunk header
(~64 KB per frame), never the pixel payload, so it is safe on 35 GB files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_HOST = _REPO / "host"
for p in (str(_REPO), str(_HOST)):
    if p not in sys.path:
        sys.path.insert(0, p)

from capture.raw_bay_writer import (  # noqa: E402
    LINE_HEADER_FIXED_PREFIX,
    LINE_INDEX_ENTRY_SIZE,
    parse_frame_chunk_header,
    parse_line_header_prefix,
    parse_line_index,
)

_MAX_CHUNK_HEADER = 65536


def chunk_metas(path: Path) -> list[tuple[int, int, dict]]:
    """Return ``(sweep, frame_in_sweep, chunk_meta)`` per frame, in file order."""
    with open(path, "rb") as f:
        _hl, n_frames, index_offset, _ = parse_line_header_prefix(
            f.read(LINE_HEADER_FIXED_PREFIX)
        )
        if not n_frames:
            return []
        if not index_offset:
            # ponytail: no index -> writer was killed mid-line. Ceiling: we give
            # up rather than rescan. Upgrade path: raw_bay_writer.recover_line_chunks.
            raise ValueError(
                "index_offset=0 (writer killed mid-line); use recover_line_chunks"
            )
        f.seek(index_offset)
        entries = parse_line_index(f.read(n_frames * LINE_INDEX_ENTRY_SIZE), n_frames)

        out = []
        for sweep, f_in, offset, _plen, _unc in entries:
            f.seek(offset)
            meta, _hlen, _plen2 = parse_frame_chunk_header(f.read(_MAX_CHUNK_HEADER))
            out.append((sweep, f_in, meta))
        return out


def _focus(meta: dict) -> dict:
    """Focus fields live under a ``focus`` sub-dict; tolerate a flat layout too."""
    sub = meta.get("focus")
    return sub if isinstance(sub, dict) else meta


def _encoder(meta: dict) -> float | None:
    return _focus(meta).get("encoder_count")


def report(path: Path, per_frame: bool) -> None:
    print(f"=== {path.name} ===")
    frames = chunk_metas(path)
    if not frames:
        print("  no frames\n")
        return

    print(f"  frames: {len(frames)}")
    print(f"  first chunk meta: {json.dumps(frames[0][2], indent=4, sort_keys=True)}")

    counts = [_encoder(m) for _s, _f, m in frames]
    present = [c for c in counts if c is not None]
    if not present:
        print("  !! NO encoder_count in any chunk -- frames are unusable for focus\n")
        return
    if len(present) != len(counts):
        print(f"  !! encoder_count missing on {len(counts) - len(present)} frame(s)")

    epochs = {_focus(m).get("encoder_epoch") for _s, _f, m in frames}
    changes = sum(1 for a, b in zip(present, present[1:]) if a != b)
    print(f"  encoder_epoch : {epochs if len(epochs) > 1 else epochs.pop()}")
    print(f"  encoder_count : min={min(present)} max={max(present)} "
          f"span={max(present) - min(present)} distinct={len(set(present))} "
          f"changes={changes}")
    if len(set(present)) == 1:
        print("  !! CONSTANT encoder_count -- the lens never moved (or was not logged)")
    else:
        print(f"  OK: lens position varies across the line")

    # Multi-camera rigs share one SPI bus, so only the selected camera's encoder is
    # live; the others report a real but frozen count. Reading those as current is
    # how you "measure" a through-focus series that was never flown.
    stale = [m for _s, _f, m in frames if _focus(m).get("encoder_stale")]
    if stale:
        sel = sorted({_focus(m).get("selected_camera") for m in stale})
        print(f"  !! {len(stale)}/{len(frames)} frames STALE -- this camera's encoder "
              f"was not selected (bus was on cam {sel}); counts are frozen, not current")
    elif any("encoder_stale" in _focus(m) for _s, _f, m in frames):
        print("  OK: every frame's encoder was live (selected) when it was shot")

    if per_frame:
        print("   idx  sweep  f_in  encoder_count")
        for i, (sweep, f_in, m) in enumerate(frames):
            print(f"  {i:4d}  {sweep:5d}  {f_in:4d}  {_encoder(m)}")
    print()


def self_check() -> int:
    """Guard the metadata layout: focus fields are nested, not top level."""
    nested = {"focus": {"encoder_count": 12.0, "encoder_epoch": "e"}}
    assert _encoder(nested) == 12.0, "missed the nested focus dict"
    assert _focus(nested)["encoder_epoch"] == "e"
    assert _encoder({"encoder_count": 7.0}) == 7.0, "flat layout must still work"
    assert _encoder({}) is None, "absent encoder must be None, not 0"
    assert _encoder({"focus": None}) is None, "non-dict focus must not crash"
    # Staleness must be read from the same nested block, not the top level.
    assert _focus({"focus": {"encoder_stale": True}})["encoder_stale"] is True
    print("self-check OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--per-frame", action="store_true",
                    help="list every frame, not just the summary")
    ap.add_argument("--self-check", action="store_true",
                    help="run assertions on the metadata layout and exit")
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    if not args.paths:
        ap.error("give at least one .bay14line path (or --self-check)")
    for p in args.paths:
        if not p.is_file():
            print(f"!! not a file: {p}")
            continue
        try:
            report(p, args.per_frame)
        except Exception as exc:
            print(f"!! {p}: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
