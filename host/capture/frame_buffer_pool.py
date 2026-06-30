"""Pre-allocated shared-memory ring of full-resolution frame buffers.

One :class:`FrameBufferPool` per camera. Each pool owns a single
:class:`multiprocessing.shared_memory.SharedMemory` block holding
``n_slots`` consecutive uint16 frames, plus two cross-process queues:

* ``free_q``: slot indices the producer (KAYA SDK callback in the main
  process) may grab to deposit a fresh frame.
* ``work_q``: slot indices the producer hands to the consumer (the
  per-camera writer subprocess) along with the target path + metadata.

Why a pool of pre-allocated buffers instead of fresh ndarrays per
frame?

1. **Zero per-frame allocation**. The SDK callback fires every ~210 ms
   per camera. Allocating a fresh 255 MB ndarray every time means
   Windows must zero ~63,000 4 KB pages per allocation -- about 17-25
   ms of pure page-zero work per frame. Recycling buffers eliminates
   that entirely.
2. **Sharable across processes for free**. The writer runs in a
   subprocess (no GIL contention with the SDK callback or
   AcquisitionThread). The SharedMemory block is mapped into both the
   parent's and the child's address space; the child sees the bytes
   the parent wrote with no IPC copy.
3. **Natural back-pressure**. If the writer falls behind, ``free_q``
   empties; the SDK callback then drops the frame instead of
   indefinitely growing memory. With a deep enough pool (16 slots ~=
   one full sweep + slack) this only fires when the writer is
   genuinely overwhelmed, not on transient bursts.

Slot layout: slot ``i`` occupies bytes ``i * frame_bytes`` through
``(i + 1) * frame_bytes - 1`` of the SharedMemory block.
``frame_bytes = height * width * 2`` (uint16).
"""

from __future__ import annotations

import atexit
import logging
import multiprocessing as mp
import time
import weakref
from dataclasses import dataclass, field
from multiprocessing.shared_memory import SharedMemory
from typing import Any, Optional

import numpy as np


log = logging.getLogger(__name__)


# OS page size on Windows (and the only platform we ship on right now).
# Used by :meth:`FrameBufferPool.warmup_writers` to stride one byte per
# page when forcing the parent-side commit of slot pages, so the touch
# itself stays at memory-bandwidth speed and the actual cost is the
# page-fault traffic into the kernel.
_OS_PAGE_BYTES = 4096
# Stride in uint16 elements (slot dtype) that lands one write per OS page.
_PAGE_PIXELS_U16 = _OS_PAGE_BYTES // 2  # = 2048


# Module-level registry of every live FrameBufferPool the parent
# process has created. Used by ``_atexit_unlink_all`` to release any
# pools that survived a hard exit (e.g. the user closed the window
# while a sweep was still in flight, or a Ctrl-C interrupted the
# graceful shutdown path). On Windows, a SharedMemory block whose
# unlink() was never called outlives the process until reboot, which
# is what we suspect contributed to a MEMORY_MANAGEMENT BSOD on
# relaunch after a previous unclean exit. Weakrefs so the registry
# doesn't itself keep dead pools alive.
_LIVE_POOLS: "weakref.WeakSet[FrameBufferPool]" = weakref.WeakSet()


def _atexit_unlink_all() -> None:
    """Best-effort cleanup of every pool that's still alive at exit.

    Runs from the Python ``atexit`` chain, which the spawn-method
    multiprocessing layer also relies on for child cleanup. We iterate
    over a snapshot of the WeakSet because shutdown() removes entries.
    Any individual pool's shutdown is wrapped in try/except so one
    bad pool can't strand the rest.
    """
    for pool in list(_LIVE_POOLS):
        try:
            pool.shutdown()
        except Exception:
            log.warning("atexit pool shutdown failed for '%s'", pool.name,
                        exc_info=True)


atexit.register(_atexit_unlink_all)


@dataclass
class WriteJob:
    """One unit of pack-and-encode work for a packer subprocess.

    Built in the main process, pushed onto ``FrameBufferPool.work_q``,
    consumed by one of the per-camera packer subprocesses. The frame
    pixels are NOT in this object -- they live in shm slot
    ``slot_idx``. The packer constructs an ndarray view at that
    offset, blosc2-encodes the frame, then pushes a
    :class:`PackedResult` to the io_writer via ``io_q`` (the packer
    NEVER touches the disk -- the io_writer owns the open
    ``.bay14line`` container).

    ``target_path`` is left as an empty string in the per-line model
    (the io_writer holds the path); it's retained so the dataclass
    stays back-compat with warmup jobs and any tooling that builds
    one-off WriteJobs outside the live pipeline.
    """

    slot_idx: int
    target_path: str
    camera_serial: str
    project: str
    line: int
    sweep: int  # 1-indexed sweep within this line
    frame_in_sweep: int  # 1..13 within this sweep
    acquire_timestamp: str
    capture_kind: str = "sweep"  # "sweep" (M0/M1) or "focus" (M4)
    # GPS fix dict (rather than the GpsFix dataclass directly) so
    # downstream pickle doesn't have to know about the dataclass; the
    # subprocess reconstitutes it via ``GpsFix(**gps_dict)``.
    gps: Optional[dict] = None
    attitude: Optional[dict] = None
    extras: dict = field(default_factory=dict)
    # Warmup jobs exercise the packer's full encode pipeline
    # (slot read -> blosc2 compress) WITHOUT pushing a result to the
    # io_writer and without bumping the on-screen ``written:N`` counter.
    # Used at app startup by :meth:`FrameBufferPool.warmup_writers` to
    # pre-fault SHM slot pages and force any one-shot codec init
    # (blosc2 thread-pool spin-up) before the first M1 capture.
    is_warmup: bool = False


@dataclass
class PackedResult:
    """Output of a packer subprocess; consumed by the io_writer.

    Carries an INDEX into a separate :class:`PackedFramePool` SHM
    region (``packed_slot_idx``) plus the metadata needed to write
    one chunk inside an open ``.bay14line`` container. The actual
    compressed bytes live in shared memory at slot ``packed_slot_idx``,
    length ``payload_size``; the io_writer reads them directly from
    that mapping and writes them to disk, then returns the slot to
    the packed pool's ``free_q``.

    History: this dataclass used to carry ``payload: bytes`` directly,
    pickled across an ``mp.Queue``. At ~78 MB per frame the pickle +
    Windows-pipe IPC added ~150 ms per item -- enough to bottleneck
    both the packer (blocked on ``put``) and the io_writer (blocked
    on ``get``) at the same ~3 fps, well below the 4.76 fps producer
    rate. Routing the payload through SHM and only ever sending the
    tiny index struct through the queue eliminates that bottleneck:
    the queue now only carries control-message-sized objects.

    The raw frame slot is released to ``free_q`` BEFORE this result
    is pushed to ``io_q`` so the parent's
    ``submitted_count == processed_count`` barrier still works as a
    "no pack in flight" gate for the close-line drain.
    """

    packed_slot_idx: int
    payload_size: int
    camera_serial: str
    line: int
    sweep: int
    frame_in_sweep: int
    acquire_timestamp: str
    capture_kind: str = "sweep"
    gps: Optional[dict] = None
    attitude: Optional[dict] = None
    # Per-frame stats the packer measured; the io_writer folds them
    # into its periodic rate report.
    encode_s: float = 0.0


@dataclass
class LineOpenJob:
    """Control message: io_writer should open a fresh ``.bay14line``.

    Pushed onto ``FrameBufferPool.io_q`` by the parent the moment the
    operator starts a new line (mode change into M0/M1, line spinbox
    bumped, drive change while in M0/M1, etc.). The io_writer reuses
    its single :class:`LineWriter` slot: any in-progress writer is
    aborted (safety -- the parent should already have sent a
    LineCloseJob) and a new one is opened at ``target_path``.

    *line_meta* is the dict the writer embeds in the file header
    (camera_serial, project, line, capture_kind, line_started_at,
    plus any extras the parent wants to record).
    """

    target_path: str
    line_meta: dict


@dataclass
class LineCloseJob:
    """Control message: io_writer should append the index, patch the
    file header and close the current line container.

    Parent must guarantee no pack work for the closing line is in
    flight before submitting -- see the close protocol on
    :class:`WriterProcessClient.close_line`.
    """

    pass


class FrameBufferPool:
    """Owns a SharedMemory ring + the two coordination queues.

    Constructed in the main process before the writer subprocess is
    spawned. After spawning, the parent only writes through
    :meth:`slot_view`; the subprocess reads. Pickling the pool itself
    crosses the process boundary lazily because :class:`SharedMemory`
    and :class:`multiprocessing.Queue` know how to re-attach in the
    child via the spawn-context handoff.
    """

    def __init__(
        self,
        *,
        name: str,
        n_slots: int,
        height: int,
        width: int,
        n_io_writers: int = 1,
    ) -> None:
        if n_slots < 2:
            raise ValueError(f"n_slots must be >= 2, got {n_slots}")
        if height <= 0 or width <= 0:
            raise ValueError(f"bad shape: {height}x{width}")
        if n_io_writers < 1:
            raise ValueError(f"n_io_writers must be >= 1, got {n_io_writers}")

        self.name = name
        self.n_slots = n_slots
        self.height = height
        self.width = width
        self.frame_bytes = height * width * 2  # uint16
        self.n_io_writers = n_io_writers
        total_bytes = self.frame_bytes * n_slots

        self.shm = SharedMemory(create=True, size=total_bytes, name=name)
        # Pre-fill ``free_q`` with every slot index. ``free_q`` only
        # ever carries ``int``s -- the writer subprocess returns the
        # slot index it just finished, the SDK callback consumes it.
        # Telemetry (frames written / failed) lives in ``mp.Value``s
        # below so we can update counters without touching this hot
        # path.
        ctx = mp.get_context("spawn")
        self.free_q: "mp.Queue[int]" = ctx.Queue(maxsize=n_slots * 2)
        # work_q: parent -> packers. WriteJob items (or None sentinels
        # to terminate the packer subprocess on shutdown).
        self.work_q: "mp.Queue[WriteJob | None]" = ctx.Queue(maxsize=n_slots)
        # io_qs: packers + parent -> io_writer(s). Each io_q feeds
        # ONE io_writer subprocess. Carries either a PackedResult
        # (a frame ready to land in that writer's open .bay14line
        # part-file) or a control message (LineOpenJob /
        # LineCloseJob / None sentinel).
        #
        # When ``n_io_writers > 1`` packers round-robin across the
        # queues so each io_writer drains roughly half the workload
        # in parallel; the per-camera disk gets two concurrent
        # write streams, doubling effective per-camera throughput.
        # The io_writers each own their own ``.bay14line`` file
        # (named with a ``_pN`` suffix) so they never coordinate
        # file offsets.
        #
        # The parent uses ``submitted_count == processed_count`` as
        # a "no pack in flight" barrier when sending LineCloseJob,
        # so all PackedResults for the closing line have already
        # been routed to some io_q by the time we broadcast the
        # close.
        #
        # Sized 2x slots so a brief stall in any one io_writer
        # can't block packers from finishing (they'd otherwise sit
        # on ``io_q.put`` holding nothing useful, just blocking the
        # next slot release).
        self.io_qs: list[
            "mp.Queue[PackedResult | LineOpenJob | LineCloseJob | None]"
        ] = [
            ctx.Queue(maxsize=n_slots * 2) for _ in range(n_io_writers)
        ]
        for i in range(n_slots):
            self.free_q.put(i)
        # Counters bumped by the writer subprocesses; polled by the
        # GUI via a QTimer to update the per-camera "written: N" label.
        # ``mp.Value`` is process-shared, lock-protected.
        self.written_count = ctx.Value("i", 0)
        self.failed_count = ctx.Value("i", 0)
        # Bumped once per completed warmup job (see
        # :meth:`warmup_writers` and the ``is_warmup`` branch in
        # :func:`writer_proc.packer_main`). The parent polls this to
        # detect when the packer subprocess(es) have drained every
        # warmup job we submitted -- that's the earliest the per-pool
        # warmup can be considered "done".
        self.warmup_count = ctx.Value("i", 0)
        # Pack-in-flight book-keeping. The parent bumps
        # ``submitted_count`` on each :meth:`submit`; each packer
        # bumps ``processed_count`` after pushing the PackedResult to
        # ``io_q`` (and BEFORE releasing the slot). The
        # ``WriterProcessClient.close_line`` drain spins on
        # ``submitted_count == processed_count`` before pushing
        # :class:`LineCloseJob` so no PackedResult races past it.
        # ``mp.Queue.qsize()`` is unreliable on Windows, hence the
        # explicit counter.
        self.submitted_count = ctx.Value("L", 0)
        self.processed_count = ctx.Value("L", 0)
        # Track for atexit-time cleanup. Done last so a half-built pool
        # that raised in __init__ doesn't end up registered.
        _LIVE_POOLS.add(self)
        # Idempotency guard: shutdown() may be called explicitly via
        # CameraPipeline.stop AND again from atexit; we want the second
        # call to be a no-op rather than a "shm already closed" trace.
        self._shut_down = False
        log.info(
            "FrameBufferPool '%s' allocated: %d slots x %dx%d uint16 (%.2f GB total)",
            name,
            n_slots,
            height,
            width,
            total_bytes / (1024**3),
        )

    def slot_view(self, slot_idx: int) -> np.ndarray:
        """Return an ``ndarray`` view over the bytes of one slot.

        The view shares storage with the shm block -- writes to it
        appear in the subprocess immediately. Caller is responsible
        for not retaining the view across slot reuse (i.e. drop the
        reference once the slot has been handed to ``work_q``).
        """
        if not 0 <= slot_idx < self.n_slots:
            raise IndexError(slot_idx)
        offset = slot_idx * self.frame_bytes
        return np.ndarray(
            (self.height, self.width),
            dtype=np.uint16,
            buffer=self.shm.buf,
            offset=offset,
        )

    def acquire_slot_nowait(self) -> Optional[int]:
        """Pop a free slot index, returning None if the pool is empty.

        Called from the SDK callback thread; must NOT block, or we'd
        stall the kernel-driver buffer return path. ``free_q`` only
        carries ``int`` slot indices (both the initial fill and the
        writer-subprocess returns).
        """
        try:
            return int(self.free_q.get_nowait())
        except Exception:  # multiprocessing.queues.Empty
            return None

    def release_slot(self, slot_idx: int) -> None:
        """Return a slot to the free pool without sending it through
        the writer subprocess.

        Used when the producer (SDK callback) acquired a slot but
        downstream decided not to write (e.g. mode flipped to M4
        between callback enqueue and AcquisitionThread dequeue, or
        ``submit`` failed because ``work_q`` is full). Without this
        the slot would leak and the pool would shrink by 1 every
        such drop.
        """
        try:
            self.free_q.put_nowait(int(slot_idx))
        except Exception:
            log.warning("FrameBufferPool '%s' release_slot dropped slot %d (free_q full)",
                        self.name, slot_idx)

    def submit(self, job: WriteJob) -> bool:
        """Hand a populated slot off to a packer subprocess.

        Non-blocking ``put_nowait``. Returns False if ``work_q`` is
        full; caller must :meth:`release_slot` for that frame's slot.

        Bumps :attr:`submitted_count` BEFORE the queue put, then
        rolls back on a put failure. This ordering is essential for
        the close-line drain barrier in
        :meth:`WriterProcessClient.close_line`: if we bumped AFTER
        the put, there'd be a window in which the job is on
        ``work_q`` but the counter doesn't yet reflect it -- the
        drain could read ``submitted == processed``, declare the
        line drained, send :class:`LineCloseJob`, and only then
        would the packer pull the queued job and push a
        :class:`PackedResult` AFTER the LineCloseJob in ``io_q``.
        That stale PackedResult would land at the io_writer with no
        line open and become a real frame drop. Bumping first makes
        ``submitted_count`` pessimistic (it always over-counts in
        the worst case), which makes the drain conservative -- it
        may briefly over-wait but can never miss in-flight work.

        Warmup jobs are excluded from the counter so the close-line
        barrier doesn't have to know about non-line submissions.
        """
        is_real = not getattr(job, "is_warmup", False)
        if is_real:
            with self.submitted_count.get_lock():
                self.submitted_count.value += 1
        try:
            self.work_q.put_nowait(job)
        except Exception:  # queue.Full
            if is_real:
                with self.submitted_count.get_lock():
                    self.submitted_count.value -= 1
            return False
        return True

    @property
    def io_q(self) -> "mp.Queue[PackedResult | LineOpenJob | LineCloseJob | None]":
        """Back-compat alias for the first io_q.

        Pre-multi-writer code referenced :attr:`io_q` directly.
        Anything in the new code path that needs to fan out to N
        writers should use :attr:`io_qs` (a list) explicitly so
        the routing is visible at the call site.
        """
        return self.io_qs[0]

    def submit_io(
        self,
        item: "LineOpenJob | LineCloseJob | None",
    ) -> None:
        """Push the same control message to every io_writer.

        Used by the parent for line lifecycle (LineOpen / LineClose)
        and the shutdown sentinel (None). Each writer needs its own
        copy because each owns a separate ``.bay14line`` part-file.
        Blocking put per-queue: control messages are tiny and the
        io_qs are sized to never refuse them in practice; we'd
        rather wait than silently lose a line-close.

        For a per-writer-different message (e.g. LineOpenJob with a
        distinct target_path per part), use :meth:`submit_io_per_writer`
        instead.
        """
        for q in self.io_qs:
            q.put(item)

    def submit_io_per_writer(
        self,
        items: "list[LineOpenJob | LineCloseJob | None]",
    ) -> None:
        """Push one item per writer in order. ``len(items)`` must
        match ``n_io_writers``.

        Used for LineOpenJob fan-out where each writer's target_path
        embeds its own ``_pN`` suffix.
        """
        if len(items) != self.n_io_writers:
            raise ValueError(
                f"submit_io_per_writer expected {self.n_io_writers} items, "
                f"got {len(items)}"
            )
        for q, item in zip(self.io_qs, items):
            q.put(item)

    def warmup_writers(
        self,
        n_frames: Optional[int] = None,
        *,
        batch_size: int = 6,
        batch_sleep_s: float = 0.10,
        timeout_s: float = 30.0,
    ) -> dict:
        """Push synthetic 'warmup' jobs through the full writer pipeline.

        Goal: eliminate the cold-start frame drops in the first ~5 s of
        M1 capture. Two distinct first-touch costs caused those drops:

        1. **Parent-side SHM slot pages**: the SDK callback writes into
           a slot for the first time, faulting ~64K pages per slot into
           the kernel before the producer can return. Spread across
           24 slots and 3 cameras that's ~5 MB of soft+hard fault
           traffic in the kernel during the first second of M1.
        2. **Writer-side SHM slot pages + numba JIT for full size**:
           the writer's first ``pack_u14_into`` call against a 13400x9528
           uint16 view does both a sequential commit of the writer's
           per-process page-table for the slot AND (depending on the
           numba cache state) a one-shot recompile pass.

        This method addresses both. For each slot we:

        * Acquire it from ``free_q``.
        * Touch one byte per OS page in the parent's address space
          (``flat[::_PAGE_PIXELS_U16] = 0``) -- forces the parent-side
          page commits up front, before the SDK callback ever sees the
          slot for the first time.
        * Submit a :class:`WriteJob` with ``is_warmup=True``. The writer
          does its full pack pass (which commits writer-side pages and
          warms the numba JIT for the production frame size) and skips
          the disk write, then returns the slot to ``free_q``.

        Touches are issued in batches of ``batch_size`` slots with a
        small ``batch_sleep_s`` between batches. That spreads the
        memory-commit pressure over ~``(n / batch) * batch_sleep_s``
        seconds rather than firing in a single burst -- the previous
        ``pre_touch`` attempt eagerly committed the entire pool in one
        go and destabilised the KAYA driver.

        Returns a dict with timing/diagnostic info. Safe to call from
        any thread (only uses thread-safe ``mp.Queue`` ops + a sleep).
        """
        n = self.n_slots if n_frames is None else min(int(n_frames), self.n_slots)
        if n <= 0:
            return {"submitted": 0, "completed": 0, "touch_s": 0.0,
                    "drain_s": 0.0, "total_s": 0.0}

        t0 = time.perf_counter()
        with self.warmup_count.get_lock():
            warmup_baseline = int(self.warmup_count.value)

        submitted = 0
        touch_s = 0.0
        for batch_start in range(0, n, max(1, int(batch_size))):
            batch_end = min(n, batch_start + int(batch_size))
            batch_slots: list[int] = []
            t_touch = time.perf_counter()
            for _ in range(batch_start, batch_end):
                idx = self.acquire_slot_nowait()
                if idx is None:
                    # Pool exhausted -- writer hasn't returned slots yet.
                    # Bail out gracefully; we'll still warm whatever
                    # slots we managed to grab.
                    break
                # Stride one byte per OS page across the slot. uint16
                # view, so writing element at index k * 2048 lands one
                # store per 4 KB page. NumPy collapses this to a tight
                # vector loop; the actual cost is the kernel page-fault
                # traffic (this is the whole point).
                view = self.slot_view(idx)
                view.reshape(-1)[::_PAGE_PIXELS_U16] = 0
                batch_slots.append(idx)
            touch_s += time.perf_counter() - t_touch

            for idx in batch_slots:
                job = WriteJob(
                    slot_idx=idx,
                    target_path="",
                    camera_serial="warmup",
                    project="warmup",
                    line=0,
                    sweep=0,
                    frame_in_sweep=0,
                    acquire_timestamp="",
                    is_warmup=True,
                )
                if not self.submit(job):
                    # work_q full (writer way behind) -- give the slot
                    # back ourselves so it doesn't leak.
                    self.release_slot(idx)
                    continue
                submitted += 1

            if not batch_slots:
                # We bailed because the pool was empty; further iteration
                # would just spin.
                break
            if batch_sleep_s > 0:
                time.sleep(float(batch_sleep_s))

        # Wait for the writers to drain every warmup job we managed to
        # submit. We track this through ``warmup_count`` rather than by
        # eyeballing ``free_q.qsize()`` (which is unreliable on Windows).
        t_drain_start = time.perf_counter()
        deadline = t_drain_start + float(timeout_s)
        completed = 0
        while time.perf_counter() < deadline:
            with self.warmup_count.get_lock():
                completed = int(self.warmup_count.value) - warmup_baseline
            if completed >= submitted:
                break
            time.sleep(0.05)
        drain_s = time.perf_counter() - t_drain_start
        total_s = time.perf_counter() - t0

        result = {
            "submitted": submitted,
            "completed": completed,
            "touch_s": touch_s,
            "drain_s": drain_s,
            "total_s": total_s,
        }
        log.info(
            "FrameBufferPool '%s' warmup: submitted=%d completed=%d "
            "touch=%.0f ms drain=%.0f ms total=%.0f ms",
            self.name, submitted, completed,
            touch_s * 1000.0, drain_s * 1000.0, total_s * 1000.0,
        )
        return result

    def shutdown(self) -> None:
        """Tear down the pool. Caller must already have joined the
        writer subprocess (sent the sentinel and ``Process.join()``).

        Drains both queues, closes them, then closes + unlinks the
        SharedMemory. On Windows, unlinking a still-mapped shm block
        leaks the kernel mapping until reboot, so we go to some
        trouble to release every reference here.

        Idempotent: a second call (e.g. from the atexit hook after
        CameraPipeline.stop already ran) is a no-op.
        """
        if self._shut_down:
            return
        self._shut_down = True
        for q in (self.free_q, self.work_q, *self.io_qs):
            try:
                while True:
                    q.get_nowait()
            except Exception:
                pass
            try:
                q.close()
                q.join_thread()
            except Exception:
                pass
        try:
            self.shm.close()
        except Exception:
            pass
        try:
            self.shm.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("FrameBufferPool '%s' unlink failed: %s", self.name, exc)
        # Drop from the live-pool registry so atexit won't try again.
        try:
            _LIVE_POOLS.discard(self)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Packed-payload SHM pool
# ---------------------------------------------------------------------------


# Module-level registry analogous to ``_LIVE_POOLS`` for the packed
# payload SHM pools, so the atexit cleanup can reach both kinds.
_LIVE_PACKED_POOLS: "weakref.WeakSet[PackedFramePool]" = weakref.WeakSet()


def _atexit_unlink_packed_pools() -> None:
    """Best-effort cleanup of every packed payload pool at exit."""
    for pool in list(_LIVE_PACKED_POOLS):
        try:
            pool.shutdown()
        except Exception:
            log.warning(
                "atexit packed pool shutdown failed for '%s'", pool.name,
                exc_info=True,
            )


atexit.register(_atexit_unlink_packed_pools)


class PackedFramePool:
    """SHM ring of variable-length compressed-payload slots.

    One pool per camera, sitting BETWEEN the packer and the io_writer:

    * Each packer subprocess :meth:`acquire` s a slot, memcpy-s the
      blosc2 chunk into the slot's bytes, then sends a slim
      :class:`PackedResult` (carrying ``packed_slot_idx`` and the
      payload length, tens of bytes total) onto ``io_q``.
    * The io_writer subprocess pops the result, copies ``length``
      bytes out of the slot, writes them to the open ``.bay14line``
      container, and :meth:`release` s the slot back to ``free_q``.

    Why this exists at all -- the diagnosis from the first live run:

        Both the packer and the io_writer settled at exactly the
        same ~3 fps -- well below the per-stage ceilings (packers
        ~7 fps, io_writer ~7 fps). The matching rate was the
        signature of a single shared bottleneck. mp.Queue on
        Windows pickles each item and writes to a named pipe via a
        background feeder thread. With ~78 MB ``payload`` bytes per
        :class:`PackedResult` the pipe transfer + pickle overhead
        cost ~150 ms/item, which throttled both ends to that rate
        regardless of the local stage budget. Cam2/Cam3 (78 MB
        compressed) lost ~50 frames per camera; cam1 (64 MB
        compressed, smaller IPC) lost zero -- the IPC hypothesis
        holds.

    Routing the payload through SHM and pushing only the
    ``(slot_idx, len)`` index struct through the queue removes that
    bottleneck entirely. The queue now carries control-message-
    sized objects (~hundreds of bytes), so its latency is
    independent of the payload size.

    Slot sizing: each slot is ``slot_bytes`` large -- big enough to
    hold the worst-case blosc2 output for a raw frame. blosc2's
    output is bounded by ``len(input) + ~16 bytes`` (the codec
    falls back to a no-compression block when the data is
    incompressible), so ``slot_bytes >= raw_frame_bytes + 64``
    guarantees no overflow. The packer asserts this on every encode
    and drops the frame (counts as a write failure) if violated --
    a defensive check that should never fire on real Bayer data.

    Slot count: with 2 packers feeding 1 io_writer, only a few
    slots are ever in flight in steady state. We over-provision
    (default 12) so a transient io_writer stall (e.g. the U.2 SLC
    cache flushing) doesn't immediately stall the packers.

    The pool's SharedMemory block is named uniquely per camera so
    multiple app instances can coexist; the name embeds pid +
    camera serial.
    """

    def __init__(
        self,
        *,
        name: str,
        n_slots: int,
        slot_bytes: int,
    ) -> None:
        if n_slots < 2:
            raise ValueError(f"n_slots must be >= 2, got {n_slots}")
        if slot_bytes <= 0:
            raise ValueError(f"slot_bytes must be > 0, got {slot_bytes}")

        self.name = name
        self.n_slots = n_slots
        self.slot_bytes = int(slot_bytes)
        total_bytes = self.slot_bytes * n_slots

        self.shm = SharedMemory(create=True, size=total_bytes, name=name)
        ctx = mp.get_context("spawn")
        # ``free_q`` carries int slot indices only. Sized 2x so a
        # release that races with another release (parent shutdown
        # path) never blocks.
        self.free_q: "mp.Queue[int]" = ctx.Queue(maxsize=n_slots * 2)
        for i in range(n_slots):
            self.free_q.put(i)

        _LIVE_PACKED_POOLS.add(self)
        self._shut_down = False
        log.info(
            "PackedFramePool '%s' allocated: %d slots x %.1f MB (%.2f GB total)",
            name,
            n_slots,
            self.slot_bytes / (1024 * 1024),
            total_bytes / (1024**3),
        )

    def slot_view(self, slot_idx: int, length: Optional[int] = None) -> memoryview:
        """Return a memoryview over slot ``slot_idx`` (full slot or
        the first ``length`` bytes).

        Both packer (write side) and io_writer (read side) call this.
        Caller must drop the view BEFORE returning the slot to
        ``free_q`` -- holding a memoryview across reuse would let
        the next consumer race on stale bytes.
        """
        if not 0 <= slot_idx < self.n_slots:
            raise IndexError(slot_idx)
        offset = slot_idx * self.slot_bytes
        end = offset + (self.slot_bytes if length is None else int(length))
        if end > offset + self.slot_bytes:
            raise ValueError(
                f"length {length} exceeds slot_bytes {self.slot_bytes}"
            )
        return memoryview(self.shm.buf)[offset:end]

    def acquire(self, timeout_s: Optional[float] = None) -> Optional[int]:
        """Block until a free slot is available, or return None on timeout.

        Called from the packer subprocess after a successful encode.
        Blocking is intentional: if the packed pool is empty the io_writer
        is behind, and we want the packer to wait (which lets backpressure
        cascade naturally to the raw FrameBufferPool, where the SDK
        callback can drop the frame). A None timeout waits forever; a
        positive timeout returns None on expiry so the packer can log
        + drop the frame instead of stalling indefinitely.
        """
        try:
            return int(self.free_q.get(timeout=timeout_s))
        except Exception:  # multiprocessing.queues.Empty
            return None

    def release(self, slot_idx: int) -> None:
        """Return a slot to the free pool.

        Called from the io_writer after the chunk has been written to
        disk, and from the packer if the encode failed (so the slot
        doesn't leak).
        """
        try:
            self.free_q.put_nowait(int(slot_idx))
        except Exception:
            log.warning(
                "PackedFramePool '%s' release dropped slot %d (free_q full)",
                self.name, slot_idx,
            )

    def shutdown(self) -> None:
        """Tear down the pool. Caller must already have joined the
        packer + io_writer subprocesses.

        Idempotent: safe to call from both
        :meth:`WriterProcessClient.stop_gracefully` and the atexit hook.
        """
        if self._shut_down:
            return
        self._shut_down = True
        try:
            while True:
                self.free_q.get_nowait()
        except Exception:
            pass
        try:
            self.free_q.close()
            self.free_q.join_thread()
        except Exception:
            pass
        try:
            self.shm.close()
        except Exception:
            pass
        try:
            self.shm.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning(
                "PackedFramePool '%s' unlink failed: %s", self.name, exc
            )
        try:
            _LIVE_PACKED_POOLS.discard(self)
        except Exception:
            pass
