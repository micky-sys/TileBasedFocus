"""One-shot helper: peek at recent cam2 .bay14line headers."""
from __future__ import annotations

import sys
from pathlib import Path

_HOST = Path(__file__).resolve().parent.parent / "host"
if str(_HOST) not in sys.path:
    sys.path.insert(0, str(_HOST))

from capture.raw_bay_writer import (  # noqa: E402
    LINE_HEADER_FIXED_PREFIX,
    parse_line_header,
    parse_line_header_prefix,
)


CANDIDATES = [
    r"E:\Project\Cam2439006_20260507_Project_L1_111718_p0.bay14line",
    r"E:\Project\Cam2439006_20260507_Project_L1_111718_p1.bay14line",
    r"E:\Project\Cam2439006_20260507_Project_L1_115223_p0.bay14line",
    r"E:\Project\Cam2439006_20260507_Project_L1_115223_p1.bay14line",
    r"E:\Project\Cam2439006_20260507_Project_L1_115433_p0.bay14line",
    r"E:\Project\Cam2439006_20260507_Project_L1_115433_p1.bay14line",
]

for p in CANDIDATES:
    pth = Path(p)
    if not pth.exists():
        print(f"{pth.name}: MISSING")
        continue
    with open(p, "rb") as f:
        prefix = f.read(LINE_HEADER_FIXED_PREFIX)
        hlen, n_frames, idx_off, _ = parse_line_header_prefix(prefix)
        f.seek(0)
        hdr = f.read(hlen)
    meta, _, _, _ = parse_line_header(hdr)
    print(pth.name)
    print(
        f"  size_GB={pth.stat().st_size / 1e9:.2f}  "
        f"n_frames(idx)={n_frames}  idx_off={idx_off}"
    )
    print(
        f"  codec={meta.get('compression')}  "
        f"format={meta.get('format')}  "
        f"opened_local={meta.get('opened_local')}  "
        f"closed_local={meta.get('closed_local')}"
    )
