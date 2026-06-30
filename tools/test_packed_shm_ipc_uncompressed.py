"""Smoke test for the COMPRESSION_NONE packer + io_writer round-trip.

Mirrors :file:`test_packed_shm_ipc.py` but drives the packer with
``codec="none"`` so we exercise the raw memcpy path end-to-end. On
exit, decodes each chunk by reading raw uint16 bytes (no blosc2).

Usage:
  py -3 tools/test_packed_shm_ipc_uncompressed.py
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "host"))

from capture.frame_buffer_pool import (  # noqa: E402
    FrameBufferPool,
    LineCloseJob,
    LineOpenJob,
    PackedFramePool,
    WriteJob,
)
from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_NONE,
    parse_frame_chunk_header,
    parse_line_header,
    parse_line_index,
)
from capture.writer_proc import io_writer_main, packer_main  # noqa: E402

HEIGHT = 64
WIDTH = 32
N_RAW_SLOTS = 4
N_PACKED_SLOTS = 4
PACKED_SLOT_BYTES = HEIGHT * WIDTH * 2 + 64  # raw + headroom
N_FRAMES = 6
FRAME_BYTES = HEIGHT * WIDTH * 2


def fill_slot(pool: FrameBufferPool, slot_idx: int) -> np.ndarray:
    view = pool.slot_view(slot_idx)
    pat = (np.arange(HEIGHT, dtype=np.uint16) * 17 + slot_idx) & 0x3FFF
    view[:] = pat[:, None]
    return view.copy()


def main() -> int:
    pid = os.getpid()
    raw_name = f"tbf_test_raw_uncomp_p{pid}"
    packed_name = f"tbf_test_packed_uncomp_p{pid}"

    raw_pool = FrameBufferPool(
        name=raw_name, n_slots=N_RAW_SLOTS, height=HEIGHT, width=WIDTH,
    )
    packed_pool = PackedFramePool(
        name=packed_name, n_slots=N_PACKED_SLOTS,
        slot_bytes=PACKED_SLOT_BYTES,
    )

    tmpdir = Path(tempfile.mkdtemp(prefix="tbf_packed_ipc_uncomp_"))
    target = tmpdir / "test_line.bay14line"
    print(f"target file: {target}")

    ctx = mp.get_context("spawn")
    packer = ctx.Process(
        target=packer_main,
        kwargs=dict(
            label="testcam.pack#0",
            shm_name=raw_pool.name,
            n_slots=raw_pool.n_slots,
            height=raw_pool.height,
            width=raw_pool.width,
            packed_shm_name=packed_pool.name,
            packed_n_slots=packed_pool.n_slots,
            packed_slot_bytes=packed_pool.slot_bytes,
            packed_free_q=packed_pool.free_q,
            work_q=raw_pool.work_q,
            free_q=raw_pool.free_q,
            io_qs=tuple(raw_pool.io_qs),
            warmup_count=raw_pool.warmup_count,
            processed_count=raw_pool.processed_count,
            failed_count=raw_pool.failed_count,
            codec=COMPRESSION_NONE,
            log_level="INFO",
        ),
        name="test-packer-uncomp",
        daemon=False,
    )
    iow = ctx.Process(
        target=io_writer_main,
        kwargs=dict(
            label="testcam.io",
            height=raw_pool.height,
            width=raw_pool.width,
            packed_shm_name=packed_pool.name,
            packed_n_slots=packed_pool.n_slots,
            packed_slot_bytes=packed_pool.slot_bytes,
            packed_free_q=packed_pool.free_q,
            io_q=raw_pool.io_qs[0],
            written_count=raw_pool.written_count,
            failed_count=raw_pool.failed_count,
            codec=COMPRESSION_NONE,
            log_level="INFO",
        ),
        name="test-io-uncomp",
        daemon=False,
    )
    packer.start()
    iow.start()
    print(f"packer pid={packer.pid}, io_writer pid={iow.pid}")

    time.sleep(1.0)
    raw_pool.submit_io(LineOpenJob(
        target_path=str(target),
        line_meta={
            "camera_serial": "TEST",
            "project": "test_uncomp",
            "line": 1,
            "capture_kind": "sweep",
            "line_started_at": "2026-05-07T11:00:00Z",
        },
    ))

    expected: list[np.ndarray] = []
    for f in range(N_FRAMES):
        slot_idx = raw_pool.acquire_slot_nowait()
        if slot_idx is None:
            time.sleep(0.05)
            slot_idx = raw_pool.acquire_slot_nowait()
        assert slot_idx is not None
        expected.append(fill_slot(raw_pool, slot_idx))
        ok = raw_pool.submit(WriteJob(
            slot_idx=slot_idx,
            target_path="",
            camera_serial="TEST",
            project="test_uncomp",
            line=1,
            sweep=1,
            frame_in_sweep=f + 1,
            acquire_timestamp=str(time.time()),
            capture_kind="sweep",
        ))
        assert ok

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        with raw_pool.submitted_count.get_lock():
            sub = int(raw_pool.submitted_count.value)
        with raw_pool.processed_count.get_lock():
            proc = int(raw_pool.processed_count.value)
        if proc >= sub and proc >= N_FRAMES:
            break
        time.sleep(0.02)
    print(f"drain: submitted={sub} processed={proc}")
    assert proc == N_FRAMES, f"only {proc} processed (expected {N_FRAMES})"

    raw_pool.submit_io(LineCloseJob())
    raw_pool.work_q.put(None)
    raw_pool.io_qs[0].put(None)
    packer.join(timeout=5)
    iow.join(timeout=5)

    raw_blob = target.read_bytes()
    print(f"file: {len(raw_blob):,} bytes")
    header_meta, header_len, n_frames, index_off = parse_line_header(raw_blob)
    print(f"header_len={header_len}, n_frames={n_frames}, "
          f"index_offset={index_off}")
    print(f"header compression={header_meta['compression']}, "
          f"format={header_meta['format']}")
    assert header_meta["compression"] == COMPRESSION_NONE
    assert n_frames == N_FRAMES

    index = parse_line_index(raw_blob[index_off:], n_frames=n_frames)
    matched = 0
    for sweep, f_in, offset, plen, _unc in index:
        _meta, chunk_hdr_len, payload_len = parse_frame_chunk_header(
            raw_blob[offset:offset + 256]
        )
        payload_off = offset + chunk_hdr_len
        payload = raw_blob[payload_off:payload_off + plen]
        assert plen == FRAME_BYTES, (
            f"payload {plen} != raw frame {FRAME_BYTES} for "
            f"sweep={sweep} frame={f_in}"
        )
        assert payload_len == plen, (
            f"chunk header payload_len {payload_len} != index plen {plen}"
        )
        decoded = np.frombuffer(payload, dtype=np.uint16).reshape(HEIGHT, WIDTH)
        expected_frame = expected[f_in - 1]
        if np.array_equal(decoded, expected_frame):
            matched += 1
        else:
            print(f"  MISMATCH frame {f_in}")
    print(f"decode: {matched}/{n_frames} frames matched expected pattern OK")
    assert matched == n_frames

    free_left = 0
    while True:
        try:
            packed_pool.free_q.get_nowait()
            free_left += 1
        except Exception:
            break
    print(f"packed_pool free_q: {free_left}/{N_PACKED_SLOTS} slots back")
    assert free_left == N_PACKED_SLOTS

    raw_pool.shutdown()
    packed_pool.shutdown()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
