"""Per-camera writer subprocesses.

Two subprocess roles per camera:

* **packer** (``packer_main``): one or more per camera. Each drains
  ``FrameBufferPool.work_q``, blosc2-encodes the raw frame slot bytes
  (SHUFFLE + ZSTD level 1 by default), copies the encoded bytes into
  a slot of the per-camera :class:`PackedFramePool` (a SECOND SHM
  ring -- see below), pushes a slim :class:`PackedResult` onto
  ``io_q`` (just the packed slot index + length + frame metadata),
  then returns the raw frame slot to ``free_q``. Packers never
  touch the disk.

* **io_writer** (``io_writer_main``): exactly one per camera. Drains
  ``io_q`` in arrival order. Handles three message types:

    - :class:`LineOpenJob`  -> open a fresh :class:`LineWriter`
    - :class:`PackedResult` -> read packed-pool slot, append chunk,
      release packed-pool slot
    - :class:`LineCloseJob` -> append index, patch header, close

  All disk I/O happens here, sequentially, so the drive sees one
  long write stream per line instead of N writers contending on
  the same file.

Why a subprocess split (packer vs io_writer)?

* Pack+encode is CPU-bound (blosc2 SHUFFLE+ZSTD takes ~285 ms per
  13400x9528 uint16 frame at 2 threads). At a 4.76 fps producer
  one packer can't keep up; two packers comfortably can.
* I/O is sequential and benefits from owning the file handle alone
  (no contention, no offset coordination, no shared file create).
* Splitting the role keeps each subprocess simple and lets the
  pack workers and the writer scale independently of each other.

Why a SECOND SHM pool for the packed payload (PackedFramePool)?

* mp.Queue on Windows pickles each item into a named pipe via a
  background feeder thread. With ~78 MB ``payload`` bytes per
  result the pipe transfer + pickle overhead measured ~150 ms per
  item -- enough to cap BOTH packers and the io_writer at the
  same ~3 fps in our first live run, well below the per-stage
  ceilings (packers ~7 fps, io_writer ~7 fps). Cam2/Cam3 (78 MB
  compressed) lost ~50 frames per camera; cam1 (64 MB compressed,
  smaller IPC) lost zero -- the IPC bottleneck hypothesis fits
  the data.
* Routing the payload through SHM and pushing only the
  ``(slot_idx, len)`` index struct through the queue removes the
  bottleneck. The queue now carries control-message-sized objects
  (~hundreds of bytes), so its latency is independent of payload
  size; the packer + io_writer can each run at their stage budget.

Why a subprocess and not a thread?

* The Python GIL caps real CPU parallelism; even with ``blosc2``
  releasing the GIL during compression, packer setup + result
  marshalling stays in Python. Subprocesses give us full per-core
  isolation from the SDK callback + acquisition threads in the
  parent.
* Subprocesses also have their own working set: the SHM pool
  pages are mapped into both the parent and each child, but the
  blosc2 internal buffers live in the child's heap and don't
  fight with the parent for memory bandwidth.

The packer/io_writer never imports Qt, KAYA SDK, or pyserial; they
run on stdlib + numpy + blosc2 + (the pure-python parts of)
``capture.raw_bay_writer`` only.
"""

from __future__ import annotations

import logging
import os
import queue as _queue  # for the Empty exception
import sys
import time
from multiprocessing.shared_memory import SharedMemory
from typing import Any

import numpy as np

# These imports run in the child process, so they must avoid pulling
# in Qt / KAYA SDK / pyserial. Everything below is pure Python +
# numpy + blosc2 (compiled C, ships its own thread pool).
from .frame_buffer_pool import (
    LineCloseJob,
    LineOpenJob,
    PackedResult,
    WriteJob,
)
from .raw_bay_writer import (
    COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    COMPRESSION_NONE,
    LineWriter,
    compress_frame_blosc2,
    pack_u14_into,
)


log = logging.getLogger(__name__)


def _configure_logging(level_name: str) -> None:
    """Configure the child's root logger so messages reach a console.

    The parent reads stderr/stdout from the child via the spawn-
    managed pipe; on Windows that pipe is a real OS pipe attached
    to the parent's terminal. So plain stderr logging is enough --
    no need for a QueueHandler back to the parent.
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(h)
    root.setLevel(level)


def _shape_view(shm: SharedMemory, slot_idx: int, frame_bytes: int,
                shape: tuple[int, int]) -> np.ndarray:
    offset = slot_idx * frame_bytes
    return np.ndarray(shape, dtype=np.uint16, buffer=shm.buf, offset=offset)


# ---------------------------------------------------------------------------
# Packer subprocess
# ---------------------------------------------------------------------------


def packer_main(
    *,
    label: str,
    shm_name: str,
    n_slots: int,
    height: int,
    width: int,
    packed_shm_name: str,
    packed_n_slots: int,
    packed_slot_bytes: int,
    packed_free_q,
    work_q,
    free_q,
    io_qs,           # tuple/list of mp.Queue, len == n_io_writers
    warmup_count,   # mp.Value('i')
    processed_count,  # mp.Value('L')
    failed_count,   # mp.Value('i')
    codec: str = COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    log_level: str = "INFO",
) -> int:
    """Drain ``work_q``, encode each slot into the packed SHM ring.

    Two codec paths:

    * ``COMPRESSION_BLOSC2_ZSTD1_SHUFFLE``: blosc2 SHUFFLE + ZSTD
      level 1, ~2.5x ratio at ~285 ms / frame on a 4-thread build,
      payload ~95 MB for the easier scenes / ~165 MB for the busier
      ones. CPU-intensive; can saturate all packer cores at 3-camera
      M1 rates.
    * ``COMPRESSION_NONE``: numba-packed 14-bit LSB-first, lossless,
      ~213 MiB / frame (vs ~244 MiB raw uint16). Pack itself takes
      ~15 ms / frame (numba, 2 threads). We pack instead of plain
      memcpy because live profiling showed the io_writer was the
      bottleneck at the raw-uint16 rate (~470 MiB/s/stream effective
      vs ~780 MiB/s/stream in the disk bench), and the 14 % byte
      reduction is enough to close the gap between the trigger
      rate and the io_writer drain rate. Packer CPU has plenty of
      headroom -- the packers were already idle ~1 s/frame on
      ``packed_wait`` in the raw path -- so the pack cost is
      effectively free.

    Round-robins each completed PackedResult across ``io_qs`` so the
    N io_writers each get roughly half the workload (when N > 1).
    """
    _configure_logging(log_level)
    pid = os.getpid()
    n_io_writers = len(io_qs)
    log.info(
        "[%s pid=%d] packer subprocess starting (shm='%s', %d slots, %dx%d, "
        "packed_shm='%s', %d packed slots x %.1f MB, codec=%s, io_writers=%d)",
        label, pid, shm_name, n_slots, height, width,
        packed_shm_name, packed_n_slots,
        packed_slot_bytes / (1024 * 1024),
        codec,
        n_io_writers,
    )
    routing_counter = 0

    try:
        shm = SharedMemory(name=shm_name, create=False)
    except FileNotFoundError as exc:
        log.error("[%s] shm '%s' not found: %s", label, shm_name, exc)
        return 1
    try:
        packed_shm = SharedMemory(name=packed_shm_name, create=False)
    except FileNotFoundError as exc:
        log.error("[%s] packed shm '%s' not found: %s",
                  label, packed_shm_name, exc)
        try:
            shm.close()
        except Exception:
            pass
        return 1

    frame_bytes = height * width * 2
    shape = (height, width)
    # Packed-14 payload size: 4 px pack to 7 bytes. ``packed_slot_bytes`` is
    # sized for raw uint16 plus headroom; the packed-14 output trivially fits.
    packed14_bytes = height * width * 7 // 4
    local_packed = 0
    local_failed = 0
    packed_wait_total_s = 0.0
    packed_wait_count = 0
    packed_drops = 0

    try:
        t0 = time.perf_counter()
        compress_frame_blosc2(np.zeros((1, 4), dtype=np.uint16))
        log.info("[%s] blosc2 warmup encode: %.0f ms",
                 label, (time.perf_counter() - t0) * 1000.0)
    except Exception:
        log.exception("[%s] blosc2 warmup raised; encode will init lazily",
                      label)

    if codec == COMPRESSION_NONE:
        # JIT-compile the numba packer once on a tiny input so the first
        # live frame doesn't pay the ~200-500 ms compile cost. The pack
        # output buffer for the hot path is the packed SHM slot itself
        # (zero-copy), so we don't allocate a private scratch here.
        try:
            t0 = time.perf_counter()
            _warm_in = np.zeros(8, dtype=np.uint16)
            _warm_out = np.zeros(8 * 7 // 4, dtype=np.uint8)
            pack_u14_into(_warm_in, _warm_out)
            log.info("[%s] pack_u14 warmup: %.0f ms",
                     label, (time.perf_counter() - t0) * 1000.0)
        except Exception:
            log.exception("[%s] pack_u14 warmup raised; pack will init lazily",
                          label)

    RATE_WINDOW_S = 5.0
    last_rate_t = time.perf_counter()
    last_rate_packed = 0
    sum_encode_s = 0.0
    sum_payload = 0

    try:
        while True:
            try:
                job = work_q.get(timeout=1.0)
            except _queue.Empty:
                now = time.perf_counter()
                if (now - last_rate_t) >= RATE_WINDOW_S * 2:
                    log.info("[%s] idle: %d packed total, %d failed",
                             label, local_packed, local_failed)
                    last_rate_t = now
                continue
            if job is None:
                log.info("[%s pid=%d] sentinel received; "
                         "packed=%d failed=%d", label, pid,
                         local_packed, local_failed)
                break

            assert isinstance(job, WriteJob)
            slot_idx = job.slot_idx

            if job.is_warmup:
                try:
                    arr_view = _shape_view(shm, slot_idx, frame_bytes, shape)
                    t_enc = time.perf_counter()
                    compress_frame_blosc2(arr_view)
                    enc_ms = (time.perf_counter() - t_enc) * 1000.0
                    with warmup_count.get_lock():
                        warmup_count.value += 1
                    if warmup_count.value <= 3:
                        log.info("[%s] warmup encode: %.0f ms (cumulative=%d)",
                                 label, enc_ms, int(warmup_count.value))
                except Exception:
                    log.exception("[%s] warmup encode failed for slot %d",
                                  label, slot_idx)
                finally:
                    try:
                        free_q.put(int(slot_idx))
                    except Exception:
                        log.exception(
                            "[%s] warmup: failed to return slot %d to free_q",
                            label, slot_idx,
                        )
                continue

            try:
                if codec == COMPRESSION_NONE:
                    # Lossless 14-bit pack (4 px -> 7 bytes). Acquire
                    # the packed slot FIRST, then pack the raw SHM
                    # view straight into the packed SHM slot
                    # (zero-copy: ``np.frombuffer`` on ``shm.buf``
                    # is a view, not a copy). pack_u14_into accepts
                    # either a numba-JIT loop, an imagecodecs path,
                    # or a numpy fallback; the on-disk bytes are
                    # the GenICam LSB-first packed-14 stream.
                    payload_len = packed14_bytes
                    if payload_len > packed_slot_bytes:
                        packed_drops += 1
                        local_failed += 1
                        with failed_count.get_lock():
                            failed_count.value += 1
                        log.error(
                            "[%s] packed-14 payload %d B exceeds packed "
                            "slot %d B (sweep=%d frame=%d); dropping frame",
                            label, payload_len, packed_slot_bytes,
                            job.sweep, job.frame_in_sweep,
                        )
                        with processed_count.get_lock():
                            processed_count.value += 1
                        # NB: the surrounding ``finally`` returns the
                        # raw slot to free_q; we just need to keep
                        # going to the next job.
                        continue

                    t_wait = time.perf_counter()
                    packed_idx = packed_free_q.get(timeout=10.0)
                    wait_s = time.perf_counter() - t_wait
                    if wait_s > 0.001:
                        packed_wait_total_s += wait_s
                        packed_wait_count += 1

                    t_enc = time.perf_counter()
                    raw_offset = slot_idx * frame_bytes
                    packed_offset = packed_idx * packed_slot_bytes
                    arr_view = np.ndarray(
                        shape, dtype=np.uint16, buffer=shm.buf,
                        offset=raw_offset,
                    )
                    out_view = np.ndarray(
                        (payload_len,), dtype=np.uint8,
                        buffer=packed_shm.buf, offset=packed_offset,
                    )
                    pack_u14_into(arr_view, out_view)
                    enc_s = time.perf_counter() - t_enc
                else:
                    arr_view = _shape_view(shm, slot_idx, frame_bytes, shape)
                    t_enc = time.perf_counter()
                    payload = compress_frame_blosc2(arr_view)
                    enc_s = time.perf_counter() - t_enc
                    payload_len = len(payload)

                    if payload_len > packed_slot_bytes:
                        packed_drops += 1
                        local_failed += 1
                        with failed_count.get_lock():
                            failed_count.value += 1
                        log.error(
                            "[%s] encoded payload %d B exceeds packed slot "
                            "%d B (sweep=%d frame=%d); dropping frame",
                            label, payload_len, packed_slot_bytes,
                            job.sweep, job.frame_in_sweep,
                        )
                        with processed_count.get_lock():
                            processed_count.value += 1
                        continue

                    t_wait = time.perf_counter()
                    packed_idx = packed_free_q.get(timeout=10.0)
                    wait_s = time.perf_counter() - t_wait
                    if wait_s > 0.001:
                        packed_wait_total_s += wait_s
                        packed_wait_count += 1
                    offset = packed_idx * packed_slot_bytes
                    packed_shm.buf[offset:offset + payload_len] = payload

                if True:

                    result = PackedResult(
                        packed_slot_idx=int(packed_idx),
                        payload_size=payload_len,
                        camera_serial=job.camera_serial,
                        line=job.line,
                        sweep=job.sweep,
                        frame_in_sweep=job.frame_in_sweep,
                        acquire_timestamp=job.acquire_timestamp,
                        capture_kind=job.capture_kind,
                        gps=job.gps,
                        attitude=job.attitude,
                        focus=job.focus,
                        encode_s=enc_s,
                    )
                    # Round-robin per-packer: this packer's
                    # successive results alternate across
                    # ``io_qs``. Combined across N packers the
                    # io_writers see a balanced (50/50 for N=2)
                    # workload.
                    io_qs[routing_counter % n_io_writers].put(result)
                    routing_counter += 1

                    local_packed += 1
                    sum_encode_s += enc_s
                    sum_payload += payload_len
                    with processed_count.get_lock():
                        processed_count.value += 1
            except _queue.Empty:
                packed_drops += 1
                local_failed += 1
                with failed_count.get_lock():
                    failed_count.value += 1
                log.error(
                    "[%s] timed out (>10 s) waiting for packed slot "
                    "(sweep=%d frame=%d); io_writer wedged? Dropping frame",
                    label, job.sweep, job.frame_in_sweep,
                )
                with processed_count.get_lock():
                    processed_count.value += 1
            except Exception as exc:
                local_failed += 1
                with failed_count.get_lock():
                    failed_count.value += 1
                log.exception("[%s] encode failed (sweep=%d frame=%d): %s",
                              label, job.sweep, job.frame_in_sweep, exc)
                with processed_count.get_lock():
                    processed_count.value += 1
                time.sleep(0.05)
            finally:
                try:
                    free_q.put(int(slot_idx))
                except Exception:
                    log.exception("[%s] failed to return slot %d to free_q",
                                  label, slot_idx)

            now = time.perf_counter()
            elapsed = now - last_rate_t
            if elapsed >= RATE_WINDOW_S:
                packed = local_packed - last_rate_packed
                if packed > 0:
                    fps = packed / elapsed
                    avg_enc_ms = (sum_encode_s / packed) * 1000.0
                    avg_payload_mb = sum_payload / packed / (1024 * 1024)
                    raw_mb = frame_bytes / (1024 * 1024)
                    ratio = raw_mb / avg_payload_mb if avg_payload_mb else 0.0
                    avg_pwait_ms = (
                        (packed_wait_total_s / packed_wait_count) * 1000.0
                        if packed_wait_count else 0.0
                    )
                    log.info(
                        "[%s] pack rate: %.2f fps (encode %.0f ms/frame, "
                        "%.1f MB/frame payload, %.2fx ratio; %d in %.1fs; "
                        "packed_wait %d frames avg %.1f ms; "
                        "total packed=%d failed=%d packed_drops=%d)",
                        label, fps, avg_enc_ms, avg_payload_mb, ratio,
                        packed, elapsed, packed_wait_count, avg_pwait_ms,
                        local_packed, local_failed, packed_drops,
                    )
                last_rate_t = now
                last_rate_packed = local_packed
                sum_encode_s = 0.0
                sum_payload = 0
                packed_wait_total_s = 0.0
                packed_wait_count = 0
    finally:
        try:
            shm.close()
        except Exception:
            pass
        try:
            packed_shm.close()
        except Exception:
            pass
    log.info("[%s pid=%d] packer subprocess exiting "
             "(packed=%d, failed=%d, packed_drops=%d)",
             label, pid, local_packed, local_failed, packed_drops)
    return 0


# ---------------------------------------------------------------------------
# IO writer subprocess
# ---------------------------------------------------------------------------


def io_writer_main(
    *,
    label: str,
    height: int,
    width: int,
    packed_shm_name: str,
    packed_n_slots: int,
    packed_slot_bytes: int,
    packed_free_q,
    io_q,
    written_count,  # mp.Value('i')
    failed_count,   # mp.Value('i')
    codec: str = COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    log_level: str = "INFO",
) -> int:
    """Drain ``io_q``: open / append / close ``.bay14line`` containers.

    Single-process, single-thread by design. Sequential file I/O
    on a freshly-opened container saturates the per-camera U.2 SSD
    at full bandwidth without offset coordination or per-write
    file-create overhead.

    Each PackedResult carries an index into the per-camera
    :class:`PackedFramePool` SHM region. The io_writer maps the
    same SHM block (``packed_shm_name``) at startup and reads
    ``payload_size`` bytes out of slot ``packed_slot_idx`` to
    write to disk, then returns the slot to ``packed_free_q``.
    This lets the queue carry ~hundreds of bytes per item instead
    of ~80 MB, which the first live run showed was the actual
    end-to-end throughput cap.

    State machine:

    * ``current_writer`` is None when no line is open. PackedResults
      that arrive in this state are LOGGED + DROPPED (and the
      packed slot still released so it doesn't leak; ``failed_count``
      bumped) -- this should never happen if the parent's line
      lifecycle is correct, but we avoid silently losing slots.
    * ``current_writer`` is a :class:`LineWriter` after a LineOpenJob
      arrives. PackedResults are appended in arrival order. The
      file's index reorders by (sweep, frame_in_sweep) on read,
      so out-of-order packer completions are fine.
    * LineCloseJob flushes the index, patches the file header,
      closes the file, and resets ``current_writer`` to None.
    """
    _configure_logging(log_level)
    pid = os.getpid()
    # Per-line file pre-allocation. Set to 0 to disable. Default 16 GiB
    # covers ~75 packed-14 frames per part file (which is more than 2
    # typical sweeps' worth), so the hot streaming phase never pays an
    # NTFS file-extension cost. Over-allocation is truncated back to
    # actual size by LineWriter.close_with_index() so unused space is
    # released the moment the line closes.
    try:
        prealloc_gib = float(os.environ.get("BAY14_PREALLOC_GIB", "16"))
    except ValueError:
        prealloc_gib = 16.0
    prealloc_bytes = max(0, int(prealloc_gib * (1024 ** 3)))
    # Direct I/O (Windows FILE_FLAG_NO_BUFFERING) bypasses the page
    # cache. In the live profile, buffered io_writers only sustained
    # ~410 MiB/s/stream vs ~775 MiB/s/stream on the same drive in the
    # write bench -- about half the available bandwidth was being eaten
    # by page-cache contention across the 6 concurrent writer streams.
    # Direct I/O eliminates that contention at the cost of sector-aligned
    # writes; LineWriter pads chunks transparently and records the
    # alignment in the file header so offline readers still work.
    # Default ON on Windows; set BAY14_DIRECT_IO=0 to disable.
    direct_io_env = os.environ.get("BAY14_DIRECT_IO", "1").strip().lower()
    direct_io = direct_io_env in ("1", "true", "yes", "on")
    log.info(
        "[%s pid=%d] io_writer subprocess starting (%dx%d uint16, "
        "packed_shm='%s', %d packed slots x %.1f MB, codec=%s, "
        "prealloc=%.1f GiB, direct_io=%s)",
        label, pid, height, width,
        packed_shm_name, packed_n_slots,
        packed_slot_bytes / (1024 * 1024),
        codec, prealloc_gib, "on" if direct_io else "off",
    )

    try:
        packed_shm = SharedMemory(name=packed_shm_name, create=False)
    except FileNotFoundError as exc:
        log.error("[%s] packed shm '%s' not found: %s",
                  label, packed_shm_name, exc)
        return 1

    current_writer: LineWriter | None = None
    current_path: str | None = None
    local_appended = 0
    local_dropped = 0  # PackedResult arrived with no line open
    local_lines = 0

    RATE_WINDOW_S = 5.0
    last_rate_t = time.perf_counter()
    last_rate_appended = 0
    sum_write_s_window = 0.0
    sum_payload_window = 0

    try:
        while True:
            try:
                item = io_q.get(timeout=1.0)
            except _queue.Empty:
                now = time.perf_counter()
                if (now - last_rate_t) >= RATE_WINDOW_S * 2:
                    log.info(
                        "[%s] idle: appended=%d dropped(no_line)=%d "
                        "lines_closed=%d (current_line=%s)",
                        label, local_appended, local_dropped, local_lines,
                        current_path or "<none>",
                    )
                    last_rate_t = now
                continue
            if item is None:
                log.info("[%s pid=%d] sentinel received; "
                         "appended=%d dropped=%d lines=%d",
                         label, pid, local_appended, local_dropped, local_lines)
                if current_writer is not None:
                    log.warning(
                        "[%s] sentinel arrived with line still open (%s); "
                        "aborting without final index. Recovery via "
                        "BAY14FRM walk will work.",
                        label, current_path,
                    )
                    try:
                        current_writer.abort()
                    except Exception:
                        log.exception("[%s] abort failed for %s",
                                      label, current_path)
                break

            if isinstance(item, LineOpenJob):
                if current_writer is not None:
                    log.warning(
                        "[%s] LineOpenJob arrived with %s still open; "
                        "aborting prior writer (parent should have "
                        "sent a LineCloseJob first)",
                        label, current_path,
                    )
                    try:
                        current_writer.abort()
                    except Exception:
                        log.exception("[%s] prior-line abort failed", label)
                line_meta = item.line_meta or {}
                try:
                    current_writer = LineWriter(
                        item.target_path,
                        height=height,
                        width=width,
                        line_meta=line_meta,
                        codec=codec,
                        prealloc_bytes=prealloc_bytes,
                        direct_io=direct_io,
                    )
                    current_writer.open()
                    current_path = item.target_path
                    log.info("[%s] line OPEN: %s (line=%s sweep=%s)",
                             label, current_path,
                             line_meta.get("line"),
                             line_meta.get("capture_kind"))
                except Exception as exc:
                    current_writer = None
                    current_path = None
                    log.exception("[%s] LineOpen failed for %s: %s",
                                  label, item.target_path, exc)
                continue

            if isinstance(item, LineCloseJob):
                if current_writer is None:
                    log.info("[%s] LineCloseJob received but no line is "
                             "open; ignoring", label)
                    continue
                try:
                    stats = current_writer.close_with_index()
                    local_lines += 1
                    log.info(
                        "[%s] line CLOSE: %s (frames=%d, payload=%.1f MB, "
                        "wall=%.2f s)",
                        label, stats["path"], stats["n_frames"],
                        stats["payload_total"] / (1024 * 1024),
                        stats["wall_s"],
                    )
                except Exception as exc:
                    log.exception("[%s] LineClose failed for %s: %s",
                                  label, current_path, exc)
                    try:
                        current_writer.abort()
                    except Exception:
                        log.exception("[%s] abort after failed close failed",
                                      label)
                finally:
                    current_writer = None
                    current_path = None
                continue

            # Default: a PackedResult.
            assert isinstance(item, PackedResult)
            packed_idx = int(item.packed_slot_idx)
            payload_len = int(item.payload_size)
            if current_writer is None:
                local_dropped += 1
                with failed_count.get_lock():
                    failed_count.value += 1
                # ALWAYS release the packed slot here, even on the
                # drop path; otherwise a sustained no-line race
                # would leak slots and stall the packer at
                # acquire-time.
                try:
                    packed_free_q.put_nowait(packed_idx)
                except Exception:
                    log.warning(
                        "[%s] failed to release packed slot %d on no-line drop",
                        label, packed_idx,
                    )
                # Cap log rate -- one log per 16 drops keeps a long
                # bug from spamming the GUI log.
                if local_dropped == 1 or local_dropped % 16 == 0:
                    log.warning(
                        "[%s] PackedResult arrived with no line open "
                        "(sweep=%d frame=%d, %d total dropped). Parent "
                        "lifecycle bug?",
                        label, item.sweep, item.frame_in_sweep, local_dropped,
                    )
                continue

            payload_view: "memoryview | None" = None
            try:
                # Zero-copy SHM read. Previously we did
                # ``payload = bytes(packed_shm.buf[offset:offset+len])``
                # which forced a 165 MB heap memcpy per frame on
                # the io_writer's hot path -- profiling on a
                # high-entropy cam2 scene showed this dropped the
                # io_writer's effective bandwidth to ~640 MB/s,
                # well below the U.2 NVMe ceiling.
                # ``LineWriter.append_chunk`` only needs a
                # bytes-like with ``len(...)`` and feeds it
                # straight into ``self._f.write(payload)``; passing
                # a memoryview into the SHM buffer skips the heap
                # copy entirely and lets the OS write straight from
                # SHM to the page cache.
                # Safety: we hold the memoryview for the synchronous
                # duration of append_chunk and release the packed
                # slot only afterwards (in the ``finally`` block),
                # so the packer can't reuse the slot mid-write.
                offset = packed_idx * packed_slot_bytes
                # Direct-I/O LineWriter wants a sector-aligned view so it
                # can issue one ``WriteFile`` per chunk; the slot is
                # 1.14x larger than the packed-14 payload, so rounding up
                # to a 4 KiB sector never overruns. Buffered LineWriter
                # ignores the extra tail and writes exactly payload_len
                # bytes.
                view_bytes = min(
                    packed_slot_bytes,
                    ((payload_len + 4095) // 4096) * 4096,
                )
                payload_view = packed_shm.buf[offset:offset + view_bytes]
                t_w = time.perf_counter()
                current_writer.append_chunk(
                    payload_view,
                    sweep=item.sweep,
                    frame_in_sweep=item.frame_in_sweep,
                    frame_meta={
                        "acquire_timestamp": item.acquire_timestamp,
                        "capture_kind": item.capture_kind,
                        "encode_s": float(item.encode_s),
                        # frame_meta is merged verbatim into the chunk JSON and
                        # from there into the TIFF ImageDescription on export, so
                        # a new key needs no writer or exporter change.
                        **({"focus": item.focus} if item.focus else {}),
                    },
                    gps=item.gps,
                    attitude=item.attitude,
                    payload_view_size=payload_len,
                )
                sum_write_s_window += time.perf_counter() - t_w
                sum_payload_window += payload_len
                local_appended += 1
                with written_count.get_lock():
                    written_count.value += 1
            except Exception as exc:
                with failed_count.get_lock():
                    failed_count.value += 1
                log.exception("[%s] append_chunk failed (sweep=%d frame=%d): %s",
                              label, item.sweep, item.frame_in_sweep, exc)
                # Don't tear the file down on a single chunk error;
                # the rest of the line is still writable.
            finally:
                # Drop our reference to the memoryview BEFORE
                # releasing the slot so the packer never finds the
                # SHM page still mapped in another buffer when it
                # starts overwriting. ``bytes()`` view has no other
                # holder at this point; explicit ``release()``
                # makes the lifecycle obvious.
                if payload_view is not None:
                    try:
                        payload_view.release()
                    except Exception:
                        pass
                # Always release the packed slot. Holding it across
                # an exception would slowly drain the packed pool.
                try:
                    packed_free_q.put_nowait(packed_idx)
                except Exception:
                    log.warning(
                        "[%s] failed to release packed slot %d after write",
                        label, packed_idx,
                    )

            now = time.perf_counter()
            elapsed = now - last_rate_t
            if elapsed >= RATE_WINDOW_S:
                appended = local_appended - last_rate_appended
                if appended > 0:
                    fps = appended / elapsed
                    avg_w_ms = (sum_write_s_window / appended) * 1000.0
                    avg_payload_mb = (
                        sum_payload_window / appended / (1024 * 1024)
                    )
                    log.info(
                        "[%s] write rate: %.2f fps (write %.0f ms/frame, "
                        "%.1f MB/frame; %d in %.1fs; total appended=%d "
                        "dropped=%d lines=%d)",
                        label, fps, avg_w_ms, avg_payload_mb,
                        appended, elapsed, local_appended,
                        local_dropped, local_lines,
                    )
                last_rate_t = now
                last_rate_appended = local_appended
                sum_write_s_window = 0.0
                sum_payload_window = 0
    finally:
        if current_writer is not None:
            try:
                current_writer.abort()
            except Exception:
                pass
        try:
            packed_shm.close()
        except Exception:
            pass
    log.info("[%s pid=%d] io_writer subprocess exiting "
             "(appended=%d, dropped=%d, lines=%d)",
             label, pid, local_appended, local_dropped, local_lines)
    return 0


# ---------------------------------------------------------------------------
# Back-compat shim
# ---------------------------------------------------------------------------
#
# Older callers / tests imported ``writer_main`` directly. The
# packer/io_writer split replaces it but we expose a thin shim so a
# stale import doesn't crash. The shim asserts loudly so the
# offending caller can be updated.
def writer_main(*_args: Any, **_kwargs: Any) -> int:  # pragma: no cover
    raise RuntimeError(
        "writer_main() was replaced by packer_main + io_writer_main; "
        "see capture.writer_proc for the new architecture."
    )
