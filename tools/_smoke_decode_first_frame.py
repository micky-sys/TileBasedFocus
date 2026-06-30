"""Sanity-check: decode the first frame of one cam2 .bay14line."""
from __future__ import annotations

import mmap
import sys
from pathlib import Path

import numpy as np

_HOST = Path(__file__).resolve().parent.parent / "host"
if str(_HOST) not in sys.path:
    sys.path.insert(0, str(_HOST))

from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_NONE,
    LINE_HEADER_FIXED_PREFIX,
    LINE_INDEX_ENTRY_SIZE,
    LINE_PIXEL_FORMAT_UINT16_2D,
    parse_frame_chunk_header,
    parse_line_header,
    parse_line_header_prefix,
    parse_line_index,
)


SRC = Path(r"E:\Project\Cam2439006_20260507_Project_L1_115223_p0.bay14line")

with open(SRC, "rb") as fh:
    with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        hlen, n_frames, idx_off, _ = parse_line_header_prefix(
            mm[:LINE_HEADER_FIXED_PREFIX]
        )
        meta, _, _, _ = parse_line_header(mm[:hlen])
        h = int(meta["height"])
        w = int(meta["width"])
        codec = str(meta.get("compression", COMPRESSION_NONE))
        fmt = str(meta.get("format", LINE_PIXEL_FORMAT_UINT16_2D))
        print(f"file       : {SRC.name}")
        print(f"resolution : {h} x {w}  codec={codec}  format={fmt}")
        print(f"n_frames   : {n_frames}  idx_off={idx_off}")

        idx_blob = mm[idx_off:idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
        chunks = parse_line_index(idx_blob, n_frames)
        sweep, f_in, offset, payload_len, _unc = chunks[0]
        print(
            f"chunk[0]   : sweep={sweep} f_in={f_in} offset={offset} "
            f"payload_len={payload_len}"
        )

        cmeta, c_hdr_len, c_payload_len = parse_frame_chunk_header(
            mm[offset:offset + 4096]
        )
        assert c_payload_len == payload_len
        payload_start = offset + c_hdr_len

        view = np.ndarray((h, w), dtype=np.uint16, buffer=mm,
                          offset=payload_start)
        arr = np.ascontiguousarray(view)
        print(
            f"frame meta : imageid={cmeta.get('imageid')} "
            f"ts_sec={cmeta.get('timestamp_sec')}"
        )
        print(
            f"array      : shape={arr.shape} dtype={arr.dtype} "
            f"min={arr.min()} max={arr.max()} mean={arr.mean():.1f}"
        )
        expected_bytes = h * w * 2
        print(f"size check : expected={expected_bytes}  got={payload_len}  "
              f"match={expected_bytes == payload_len}")
