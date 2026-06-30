"""Read the header of one or more .bay14line files and print codec / geometry.

Usage:
    py -3 tools/inspect_bay14line_codec.py <file1.bay14line> [<file2.bay14line> ...]

For each file: prints codec, codec_extras, pixel_format, width x height,
n_frames (from prefix), file size, and an estimate of the uncompressed pixel
payload size vs the actual file size so you can spot compression at a glance.

Safe for very large files: only reads the first ~64 KB to parse the line
header, never loads frames.
"""

from __future__ import annotations

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
    parse_line_header,
    parse_line_header_prefix,
)


def inspect(path: Path) -> None:
    size = path.stat().st_size
    print(f"=== {path} ===")
    print(f"  size on disk: {size:,} bytes ({size/1_073_741_824:.2f} GiB)")

    with open(path, "rb") as f:
        prefix = f.read(LINE_HEADER_FIXED_PREFIX)
        header_len, n_frames_prefix, index_offset, _ = parse_line_header_prefix(
            prefix
        )
        f.seek(0)
        full_hdr = f.read(header_len)

    meta, _hl, n_frames, _idx = parse_line_header(full_hdr)
    print(f"  header_len   : {header_len}")
    print(f"  n_frames (prefix) : {n_frames_prefix}")
    print(f"  n_frames (parsed) : {n_frames}")
    print(f"  index_offset : {index_offset} {'(0 -> writer killed mid-line)' if index_offset == 0 else ''}")

    keys = (
        "codec",
        "compression",
        "codec_extras",
        "pixel_format",
        "width",
        "height",
        "bit_depth",
        "bayer_pattern",
        "camera_serial",
        "project",
        "line",
        "format_version",
        "container",
    )
    for k in keys:
        if k in meta:
            print(f"  {k:13}: {meta[k]!r}")

    w = int(meta.get("width", 0))
    h = int(meta.get("height", 0))
    if w and h and n_frames_prefix:
        packed_per_frame = w * h * 7 // 4
        uncompressed_total = packed_per_frame * n_frames_prefix
        print(
            f"  packed (per frame) : {packed_per_frame:,} bytes "
            f"({packed_per_frame/1_048_576:.1f} MiB)"
        )
        print(
            f"  packed (all {n_frames_prefix} frames): "
            f"{uncompressed_total:,} bytes ({uncompressed_total/1_073_741_824:.2f} GiB)"
        )
        # Header + index + payload approx
        ratio = size / uncompressed_total if uncompressed_total else 0
        print(
            f"  file / packed-no-compress ratio: {ratio:.3f} "
            f"({'~no compression' if 0.95 <= ratio <= 1.05 else 'looks COMPRESSED' if ratio < 0.95 else 'larger than expected'})"
        )
    print()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_file():
            print(f"!! not a file: {p}")
            continue
        try:
            inspect(p)
        except Exception as exc:
            print(f"!! {p}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
