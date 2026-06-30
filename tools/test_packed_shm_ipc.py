"""Smoke test for the packer/io_writer split with the new SHM packed-payload pool.

What this test validates:

* Spawn 1 packer + 1 io_writer subprocess against a tiny FrameBufferPool
  (4 slots, 64x32 uint16 frames) + a tiny PackedFramePool (4 slots,
  raw_frame_bytes + 64).
* Send a LineOpenJob, then 6 real WriteJobs. Each WriteJob targets a
  pre-filled slot whose ndarray view is filled with a deterministic
  pattern (the slot index repeated across the row dimension so the
  decoded frame can be checked).
* Send a LineCloseJob, drain the close, then read the resulting
  ``.bay14line`` and verify:
    - 6 chunks were written (no IPC drops).
    - Each chunk's decoded uint16 ndarray matches the pattern that
      was stored in its source slot.
    - The packed pool's free_q has all 4 slots back (no slot leaks).

Why this test exists: the first live run dropped ~50 frames per camera
because the old PackedResult.payload pickle bottlenecked the
packer<->io_writer hop at ~3 fps. The fix routes payloads through SHM
and only pushes a slim ``(slot_idx, len, metadata)`` struct through
the queue. This is the smallest end-to-end check that the new path
actually works (open + write + close + decode round-trip).

Runtime: ~3 seconds end-to-end (most of it subprocess startup +
blosc2 warmup). No network / GPU dependencies; pure stdlib + numpy +
blosc2.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

# Make ``host`` importable when running from the repo root.
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
    LineWriter,
    decompress_frame_blosc2,
    parse_line_header,
    parse_line_header_prefix,
    parse_line_index,
)
from capture.writer_proc import io_writer_main, packer_main  # noqa: E402


HEIGHT = 64
WIDTH = 32
N_RAW_SLOTS = 4
N_PACKED_SLOTS = 4
PACKED_SLOT_BYTES = HEIGHT * WIDTH * 2 + 64  # raw + headroom
N_FRAMES = 6


def fill_slot(pool: FrameBufferPool, slot_idx: int) -> np.ndarray:
    """Fill slot ``slot_idx`` with a deterministic pattern keyed on the
    slot index, and return a copy for later comparison.
    """
    view = pool.slot_view(slot_idx)
    pat = (np.arange(HEIGHT, dtype=np.uint16) * 17 + slot_idx) & 0x3FFF
    view[:] = pat[:, None]  # broadcast across width
    return view.copy()


def main() -> int:
    pid = os.getpid()
    raw_name = f"tbf_test_raw_p{pid}"
    packed_name = f"tbf_test_packed_p{pid}"

    raw_pool = FrameBufferPool(
        name=raw_name, n_slots=N_RAW_SLOTS, height=HEIGHT, width=WIDTH,
    )
    packed_pool = PackedFramePool(
        name=packed_name, n_slots=N_PACKED_SLOTS,
        slot_bytes=PACKED_SLOT_BYTES,
    )

    tmpdir = Path(tempfile.mkdtemp(prefix="tbf_packed_ipc_"))
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
            log_level="INFO",
        ),
        name="test-packer",
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
            io_q=raw_pool.io_q,
            written_count=raw_pool.written_count,
            failed_count=raw_pool.failed_count,
            log_level="INFO",
        ),
        name="test-io",
        daemon=False,
    )
    packer.start()
    iow.start()
    print(f"packer pid={packer.pid}, io_writer pid={iow.pid}")

    # Give the children a moment to attach + warm blosc2.
    time.sleep(1.0)

    # 1. Open the line.
    raw_pool.submit_io(LineOpenJob(
        target_path=str(target),
        line_meta={
            "camera_serial": "TEST",
            "project": "test",
            "line": 1,
            "capture_kind": "sweep",
            "line_started_at": "2026-05-06T12:00:00Z",
        },
    ))

    # 2. Submit N real frames.
    expected: list[np.ndarray] = []
    for i in range(N_FRAMES):
        idx = raw_pool.acquire_slot_nowait()
        # If acquire fails (queue lag), release-spin a tiny bit.
        deadline = time.perf_counter() + 5.0
        while idx is None and time.perf_counter() < deadline:
            time.sleep(0.01)
            idx = raw_pool.acquire_slot_nowait()
        assert idx is not None, "raw pool starved"
        expected.append(fill_slot(raw_pool, idx))
        job = WriteJob(
            slot_idx=idx,
            target_path=str(target),
            camera_serial="TEST",
            project="test",
            line=1,
            sweep=(i // 3) + 1,
            frame_in_sweep=(i % 3) + 1,
            acquire_timestamp=f"2026-05-06T12:00:{i:02d}Z",
        )
        ok = raw_pool.submit(job)
        assert ok, f"work_q full on submit {i}"

    # 3. Drain the pack barrier (submitted_count == processed_count).
    deadline = time.perf_counter() + 10.0
    while time.perf_counter() < deadline:
        with raw_pool.submitted_count.get_lock():
            sub = int(raw_pool.submitted_count.value)
        with raw_pool.processed_count.get_lock():
            proc = int(raw_pool.processed_count.value)
        if proc >= sub:
            break
        time.sleep(0.02)
    print(f"drain: submitted={sub} processed={proc}")
    assert proc == sub == N_FRAMES, (
        f"drain barrier did not converge: sub={sub} proc={proc}"
    )

    # 4. Send LineCloseJob and wait for io_writer to flush + close.
    raw_pool.submit_io(LineCloseJob())
    deadline = time.perf_counter() + 5.0
    while time.perf_counter() < deadline:
        if target.exists() and target.stat().st_size > 0:
            # File exists but the close is async; spin until the
            # io_writer's appended count == N_FRAMES (final write
            # happens before the index dump).
            with raw_pool.written_count.get_lock():
                wc = int(raw_pool.written_count.value)
            if wc >= N_FRAMES:
                break
        time.sleep(0.05)
    with raw_pool.written_count.get_lock():
        wc = int(raw_pool.written_count.value)
    assert wc == N_FRAMES, f"written_count={wc}, expected {N_FRAMES}"

    # Give the io_writer a beat to finish the index dump.
    time.sleep(0.5)

    # 5. Tell both subprocesses to stop and join.
    raw_pool.work_q.put(None)
    raw_pool.submit_io(None)
    packer.join(timeout=5)
    iow.join(timeout=5)
    assert not packer.is_alive(), "packer did not exit"
    assert not iow.is_alive(), "io_writer did not exit"

    # 6. Verify the file: header + index + decoded chunks.
    data = target.read_bytes()
    header_len, n_frames_in_file, index_offset, _entry_size = (
        parse_line_header_prefix(data[:32])
    )
    print(f"file: {len(data):,} bytes, header_len={header_len}, "
          f"n_frames={n_frames_in_file}, index_offset={index_offset}")
    line_meta, _hdr_len, _n, _ioff = parse_line_header(data[:header_len])
    print(f"header line_meta={line_meta}")
    assert n_frames_in_file == N_FRAMES, (
        f"n_frames_in_file={n_frames_in_file}, expected {N_FRAMES}"
    )
    index = parse_line_index(data[index_offset:], n_frames_in_file)
    assert len(index) == N_FRAMES, f"len(index)={len(index)}"

    # Each chunk: BAY14FRM-magic header (variable length, see
    # _build_frame_chunk_header) + payload. We use parse_frame_chunk_header
    # to discover the header length and decode the payload that follows.
    from capture.raw_bay_writer import parse_frame_chunk_header

    decoded_by_seq: dict[tuple[int, int], np.ndarray] = {}
    for entry in index:
        sweep, f_in_sweep, byte_offset, payload_len, _unc = entry
        # Read enough bytes for the chunk: the header is variable-
        # length but bounded by the next chunk's offset (or by
        # index_offset for the last chunk). Slicing to the next
        # entry's offset gives us a safe upper bound.
        chunk_blob = data[byte_offset:index_offset]
        meta, header_len_chunk, payload_len_chunk = parse_frame_chunk_header(
            chunk_blob
        )
        assert payload_len_chunk == payload_len, (
            f"index payload_len {payload_len} != chunk hdr "
            f"{payload_len_chunk}"
        )
        payload = chunk_blob[header_len_chunk:header_len_chunk + payload_len]
        decoded = decompress_frame_blosc2(payload, height=HEIGHT, width=WIDTH)
        decoded_by_seq[(sweep, f_in_sweep)] = decoded

    # Match each decoded frame back to its expected pattern by re-
    # generating the pattern for slot 0..N (we don't know which slot
    # each frame originally landed in, so we check by content rather
    # than by slot index).
    matched = 0
    for arr in decoded_by_seq.values():
        for exp in expected:
            if arr.shape == exp.shape and np.array_equal(arr, exp):
                matched += 1
                break
    assert matched == N_FRAMES, (
        f"only {matched}/{N_FRAMES} decoded frames matched expected pattern"
    )
    print(f"decode: {matched}/{N_FRAMES} frames matched expected pattern OK")

    # 7. Sanity: every packed slot must be back in free_q
    # (otherwise we're leaking SHM slots and the next run will
    # gradually starve).
    seen: set[int] = set()
    deadline = time.perf_counter() + 2.0
    while len(seen) < N_PACKED_SLOTS and time.perf_counter() < deadline:
        try:
            seen.add(int(packed_pool.free_q.get(timeout=0.5)))
        except Exception:
            break
    assert seen == set(range(N_PACKED_SLOTS)), (
        f"packed_pool free_q only had {sorted(seen)}, expected "
        f"{list(range(N_PACKED_SLOTS))} -- slot leak?"
    )
    print(f"packed_pool free_q: all {N_PACKED_SLOTS} slots back, no leak")

    # 8. Cleanup.
    raw_pool.shutdown()
    packed_pool.shutdown()
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
