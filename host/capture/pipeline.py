"""Per-camera capture + write pipeline.

One :class:`CameraPipeline` owns:

* a :class:`~capture.kyfg_backend.KyfgCamera` for one Iron 661 (KYFGLib
  C-API: ``KYFG_CameraOpen2`` + ``KYFG_StreamCreateAndAlloc`` +
  ``KYFG_CameraStart``). The backend's per-buffer SDK callback hands us
  ``(small, full)`` tuples: ``small`` is a 4x-decimated uint16 preview
  copy (~8 MB), ``full`` is either ``None`` (focus-only modes) or an
  integer slot index in the per-camera shared-memory pool when full-res
  emission is on for sweep capture.
* an :class:`AcquisitionThread` that drains the SDK callback's tuple
  queue, fans the small frames out to the preview / focus-tap signals,
  and -- when the PCB is in M0 / M1 + a drive and project are set --
  builds a :class:`~capture.frame_buffer_pool.WriteJob` for the slot
  and submits it to the writer subprocess via
  :class:`FrameBufferPool.work_q`.
* a :class:`FrameBufferPool` (when full-res capture is enabled): a
  single SharedMemory ring of N pre-allocated full-res slots plus the
  two cross-process queues that coordinate the SDK callback (producer)
  with the writer subprocess (consumer). See
  ``capture/frame_buffer_pool.py`` for the slot-lifecycle invariants.
* a :class:`WriterProcessClient`: a thin :class:`QObject` that owns
  the per-camera writer subprocess (one ``multiprocessing.Process``
  running :func:`capture.writer_proc.writer_main`) and a QTimer that
  polls the pool's ``written_count`` / ``failed_count`` shared values
  to drive the GUI's "written:N / failed:M" labels.

Why a writer subprocess instead of a thread? Empirically, in-process
TIFF writes hit ~700 ms/frame under live three-camera load (vs the
~240 ms/frame the same code measured stand-alone in
``tools/bench_drive.py``). The 2.9x slowdown was Python GIL +
page-zeroing contention with the SDK callback and acquisition
threads. Pushing the writer into a child process drops both
contention sources -- the child has its own GIL, and the
SharedMemory pages it reads are pre-allocated and reused (no
per-frame ndarray allocation, no Windows page-zero hit).
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
import multiprocessing as mp
import os
import queue
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QImage

from .bayer_demosaic import opencv_bayer_rgb_demosaic_code
from .file_naming import (
    build_line_name,
    ensure_directory,
)
from .frame_buffer_pool import (
    FrameBufferPool,
    LineCloseJob,
    LineOpenJob,
    PackedFramePool,
    WriteJob,
)
from .attitude import compute_attitude_dict
from .gps_fix import GpsFix
from .kyfg_backend import KyfgCamera
from .mode_controller import ModeController
from .sweep_counter import SweepCounter
from .tiff_writer import FrameMetadata, write_frame
from .writer_proc import io_writer_main, packer_main

log = logging.getLogger(__name__)

PREVIEW_MAX_EDGE = 1280
PREVIEW_MIN_INTERVAL_S = 0.1  # cap preview emission to 10 Hz
FOCUS_TAP_MIN_INTERVAL_S = 0.18  # cap focus tap to ~5.5 Hz. MUST stay BELOW the M4
# trigger period (209 ms): a gate >= the frame period skips every other frame (the
# next frame lands 1 ms early, is rejected, so the tap aliases to ~half rate / ~2.4 Hz).
# 0.18 clears 209 ms with ~29 ms of jitter margin; conflation still caps GUI backlog.
# the M4 focus loop need not be human-usable, and a faster tap means more
# autofocus measurements per run and less scene drift between focus steps).

# Number of pre-allocated full-resolution frame slots in the per-camera
# SharedMemory pool. Each slot holds one 13400x9528 uint16 frame
# (~244 MB), and the pool is split between:
#
#   * the SDK callback's "in flight" frame waiting in work_q,
#   * frames being actively written by the subprocess,
#   * fresh slots in free_q ready to receive the next callback.
#
# At observed rates:
#
#   * Acquisition fires at ~5 Hz inside a sweep (200 ms/frame).
#   * Live writer rate (3 cameras, full system load) measured at
#     ~360 ms/frame on the U.2 drives -- 50% slower than the
#     stand-alone bench. So per 3 s sweep cycle, the pool fills at
#     ~13 - 3*(1/0.36) = ~+5 frames net.
#
# 24 slots gives us ~5 sweep cycles (~15 s) of continuous full-res
# capture before the writer's deficit eats into safety margin. That
# is enough for a typical 13-shot mission, and gives the operator
# a clear "we are losing ground" signal via the dropped:N counter
# without dropping any frames during a single sweep.
#
#   24 slots * ~244 MB ~= 5.9 GB / camera, ~17.7 GB across all three.
#
# Memory: the 24-slot sizing suits the 64 GB *operational* host, where 17.7 GB
# of DMA-pinned pool is comfortable. But the same 17.7 GB on a 32 GB *bench*
# box is ~55% of RAM and pushes the system commit toward its limit -- which
# coincided with two MEMORY_MANAGEMENT (0x1A) BSODs during focus testing. So
# scale the slot count to physical RAM instead of hard-coding it: this keeps
# 24 slots on the 64 GB operational machine (no recording-depth regression)
# while dropping to 8 (~5.9 GB) on the 32 GB bench box. Slots must fit in
# *physical* RAM (DMA buffers can't be paged out), so we scale on total
# physical memory, not the commit limit.


def _physical_ram_gb() -> float:
    """Best-effort total physical RAM in GB (0.0 if it can't be determined)."""
    import sys

    try:
        if sys.platform == "win32":
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return 0.0
            return stat.ullTotalPhys / (1024 ** 3)
        import os as _os

        return (
            _os.sysconf("SC_PAGE_SIZE") * _os.sysconf("SC_PHYS_PAGES")
        ) / (1024 ** 3)
    except Exception:
        return 0.0


def _pool_slots_for_ram(default_slots: int = 24, min_slots: int = 8) -> int:
    """Scale the full-res pool to physical RAM: ~0.5 slots/GB, clamped to
    [min_slots, default_slots]. Gives 24 @ 64 GB (operational, unchanged) and
    8 @ 32 GB (bench). Falls back to default_slots if RAM can't be read."""
    ram_gb = _physical_ram_gb()
    if ram_gb <= 0.0:
        return default_slots
    return max(min_slots, min(default_slots, int(0.5 * ram_gb - 8)))


SHM_POOL_SLOTS = _pool_slots_for_ram()

# Per-camera SHM ring of compressed-payload slots, sitting between the
# packer and the io_writer. See the docstring on
# :class:`~capture.frame_buffer_pool.PackedFramePool` for the full
# diagnosis -- in short: pickling ~78 MB ``payload`` blobs through the
# old in-queue PackedResult capped throughput at ~3 fps end-to-end and
# cost ~50 dropped frames per camera per ~75 s sweep run. Routing
# payloads through a dedicated SHM ring drops queue latency back to
# control-message scale.
#
# Sizing:
#
# * ``PACKED_POOL_SLOT_BYTES`` = raw uint16 frame bytes ``H*W*2`` + 64 B
#   headroom (blosc2 worst case vs incompressible input).
# * ``PACKED_POOL_SLOTS`` = 8. With 2 packers feeding 1 io_writer at
#   matching rates, only a few slots are in flight in steady state.
#   8 over-provisions to absorb a transient io_writer stall (e.g.
#   the U.2 SLC cache flushing) without dropping into backpressure.
#
# Memory: 8 slots * (~244 MB + 64 B) ~= 1.9 GB / camera, ~5.9 GB across
# all three. Combined with the existing ~17.7 GB raw pool we sit at
# ~23.6 GB shm on a 64 GB host -- well inside the headroom that
# survived the BSOD-class failures the raw-pool sizing comment above
# walks through.
PACKED_POOL_SLOTS = 8
# Computed at pool-construction time from EXPECTED_FRAME_HEIGHT/WIDTH;
# see CameraPipeline.__init__.
PACKED_POOL_SLOT_HEADROOM_BYTES = 64

# Stage 4 expected sensor geometry. The actual KyfgCamera reports its
# real shape on first frame; we use these values to pre-allocate the
# pool at bring-up because the pool MUST exist before the SDK callback
# starts producing. If a camera's first frame disagrees with these
# dimensions we'll log + raise during configure.
EXPECTED_FRAME_HEIGHT = 9528
EXPECTED_FRAME_WIDTH = 13400

# Type alias: a callable the AcquisitionThread asks for the GPS fix that
# matches the current trigger pulse for THIS camera. Arguments are:
#
#   * ``camera_serial``  -- the GenICam serial of the camera asking;
#                           lets the correlator maintain per-camera
#                           consumer state in its slot table.
#   * ``frame_perf_ns``  -- ``time.perf_counter_ns()`` captured as
#                           close to the actual frame arrival as
#                           possible (the same monotonic clock the
#                           receiver tags events with).
#   * ``sweep``          -- the per-camera sweep number from
#                           :class:`SweepCounter`. Reserved for the
#                           SBF-TOW cross-check / diagnostics.
#   * ``frame_in_sweep`` -- 1..13, the slot the correlator should
#                           look up in its per-sweep event table.
#
# Returning ``None`` means "no fix available; write the frame without
# GPS metadata" (the preview will say "NO GPS" on the next refresh).
GpsProvider = Callable[[str, int, int, int], Optional[GpsFix]]
HeadingProvider = Callable[[], Optional[float]]
# Returns the ``focus`` block to stamp on this frame, or None when the lens
# position is unknown. Assembled by the caller (app.py) rather than here,
# because the encoder and its epoch live on the serial client, which the
# per-camera pipeline has no business knowing about. Takes no arguments: the
# caller has already bound WHICH camera this pipeline is, so each camera gets
# its own encoder rather than the currently selected one.
FocusProvider = Callable[[], Optional[dict]]


# What the SDK callback hands to AcquisitionThread via ``frame_queue``.
# The first element is always a small (4x-decimated) uint16 ndarray for
# the preview / focus-tap path. The second element is either a fresh
# full-resolution uint16 ndarray (when M0/M1 + writer enabled and the
# backend has been told to emit full-res via
# :meth:`KyfgCamera.set_full_res_emission`) or ``None`` (preview-only
# modes -- saves the ~244 MB per-frame copy). A bare ``None`` on the
# queue (no tuple) is the shutdown sentinel; AcquisitionThread
# distinguishes by an explicit ``is None`` check.
BackendFrame = tuple  # (np.ndarray, Optional[np.ndarray])


# ---------------------------------------------------------------------------


@dataclass
class CameraConfig:
    """What the manager needs to open one camera + identify it on disk."""

    camera_number: int  # 1, 2, or 3 -- friendly index for UI / log prefixes
    camera_serial: str  # GenICam DeviceSerialNumber, used in filenames
    device_index: Optional[int] = None  # branch alignment (preferred when known)


@dataclass
class CaptureContext:
    """Slowly-changing state shared across all pipelines.

    Read from the GUI thread; writer threads read it each time they pop a
    frame, so updates must not tear. Python assignments to a single
    attribute on a CPython object are atomic, which is enough here -- each
    field is read independently and we never need a consistent snapshot.
    """

    project: str = ""
    line: int = 1


# ---------------------------------------------------------------------------


PREVIEW_DISPLAY_GAIN_MIN = 1.0
PREVIEW_DISPLAY_GAIN_MAX = 64.0


def _u16_to_gray8(a_u16: np.ndarray, gain: float = 1.0) -> np.ndarray:
    """14-bit sensor data -> uint8 for display, times a software ``gain``.

    ``gain=1`` is the historical ``>> 6`` mapping (14-bit full scale -> 255).
    Larger values brighten the PREVIEW ONLY -- nothing written to disk is
    touched -- so the operator can run the sensor at minimum analog gain for
    the recording and still see the ground in flight. Saturates at 255.
    """
    if gain == 1.0:
        return np.clip(a_u16 >> 6, 0, 255).astype(np.uint8)
    return cv2.convertScaleAbs(a_u16, alpha=float(gain) / 64.0)


SENSOR_FULL_SCALE = 16383  # BayerRG14


def exposure_stats(raw_u16: np.ndarray, sub: int = 2) -> dict:
    """How much of the 14-bit range a frame uses, from its RAW counts (display gain
    plays no part -- this is what goes to disk).

    Returns ``hist`` (256 bins of DN>>6, i.e. 64 DN per bin), ``p01``/``p50``/``p999``
    (0.1 / 50 / 99.9 percentiles in DN, bin-upper-edge resolution), ``clip`` (fraction
    of pixels in the top bin, DN >= 16320), ``headroom`` (stops between p99.9 and full
    scale) and ``used`` (stops between p0.1 and p99.9). ``sub`` further decimates the
    already-coarse frame: ~2 Mpx is plenty for 0.1 % quantiles and keeps it ~ms."""
    a = raw_u16[::sub, ::sub]
    hist = np.bincount((a >> 6).ravel(), minlength=256)[:256]
    n = int(hist.sum())
    if n == 0:
        return dict(hist=hist, p01=0, p50=0, p999=0, clip=0.0, headroom=0.0, used=0.0, n=0)
    cdf = np.cumsum(hist)
    def pct(q: float) -> int:  # upper DN edge of the bin where the CDF crosses q
        return int(np.searchsorted(cdf, q * n, side="left")) * 64 + 63
    p01, p50, p999 = pct(0.001), pct(0.5), pct(0.999)
    return dict(hist=hist, p01=p01, p50=p50, p999=p999, n=n,
                clip=float(hist[-1]) / n,
                headroom=math.log2(SENSOR_FULL_SCALE / max(p999, 1)),
                used=math.log2(max(p999, 1) / max(p01, 1)))


def exposure_line(st: dict) -> str:
    """One-line form for the log / overlay readout."""
    return (f"p50 {st['p50']} ({100.0 * st['p50'] / SENSOR_FULL_SCALE:.1f}%)  "
            f"p99.9 {st['p999']}  clip {100.0 * st['clip']:.2f}%  "
            f"head {st['headroom']:.1f}st  used {st['used']:.1f}st")


def _downsample_bayer_to_gray8(
    raw_u16: np.ndarray, max_edge: int, gain: float = 1.0
) -> np.ndarray:
    """Decimate a Bayer uint16 frame to a small uint8 grayscale preview.

    No debayer, no color: the operator only needs to see what the sensor
    points at. Step by 2 first (aligns to Bayer quads, keeps speed O(N/4)),
    then further decimate if still above ``max_edge``.
    """
    h, w = raw_u16.shape
    step_h = max(1, h // max_edge)
    step_w = max(1, w // max_edge)
    step = max(step_h, step_w, 2) & ~1  # even, Bayer-aligned, at least 2
    small = raw_u16[::step, ::step]
    return _u16_to_gray8(small, gain)


def _full_res_center_crop_gray8(
    full_u16: np.ndarray, crop_w: int, crop_h: int, gain: float = 1.0
) -> np.ndarray:
    """Centre crop of a full-res Bayer uint16 frame as uint8 gray (100% zoom).

    No decimation: 1 sensor pixel -> 1 output pixel. The crop is clamped
    to the frame and aligned to even (Bayer-quad) boundaries. Uses the
    same intensity scaling as :func:`_downsample_bayer_to_gray8`
    so brightness is unchanged when the operator toggles zoom on/off.
    """
    h, w = full_u16.shape
    cw = min(w, max(2, int(crop_w))) & ~1
    ch = min(h, max(2, int(crop_h))) & ~1
    x0 = ((w - cw) // 2) & ~1
    y0 = ((h - ch) // 2) & ~1
    crop = full_u16[y0:y0 + ch, x0:x0 + cw]
    return _u16_to_gray8(crop, gain)


def _iso_now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------


class AcquisitionThread(QThread):
    """Drains the backend frame queue and fans frames out to consumers.

    With the KYFGLib backend, raw buffers are delivered to us by the SDK
    on its own callback thread (see :class:`KyfgCamera._buffer_callback`).
    The callback drops a decimated uint16 ``ndarray`` into
    ``frame_queue`` and returns immediately. This thread pops frames from
    the queue, builds preview QImages, emits the focus tap, and (if the
    writer is enabled in stage 2) enqueues per-frame write jobs.
    """

    log_line = Signal(str)
    failed = Signal(str)
    frame_acquired = Signal(int, float)  # total count, instantaneous Hz
    preview_ready = Signal(QImage)
    raw_focus_frame = Signal(object, str)  # (np.ndarray uint16, ISO timestamp)
    # Full-res uint16 crops of the pinned AF ROIs (list, ``set_preview_rois`` order),
    # cut from the held full-res slot so the w10 engine scores sensor-native pixels
    # instead of the 4x-decimated coarse tap. (list[np.ndarray], ISO timestamp).
    raw_focus_preview_crops = Signal(object, str)
    # Full-res uint16 crop of the Fine view's tracked ROI (``set_fine_roi``), cut from
    # the same held slot. Display only -- nothing scores it. (np.ndarray, ISO timestamp).
    raw_focus_fine_crop = Signal(object, str)
    fetch_timeout_count = Signal(int)
    # Cumulative count of sweep frames AcquisitionThread had to drop
    # rather than submit to the writer subprocess. Reasons include
    # writer disabled, no drive selected, no project name, work_q
    # full, ndarray legacy path. The GUI's per-row "dropped:N" label
    # binds to this. Replaces the old WriterThread.frame_dropped_not_recording
    # signal one-for-one (source-compatible signature).
    frame_dropped = Signal(int)

    def __init__(
        self,
        camera_number: int,
        camera_serial: str,
        frame_queue: "queue.Queue[tuple | None]",
        sweep: SweepCounter,
        modes: ModeController,
        context: CaptureContext,
        get_drive_root: Callable[[], Optional[str]],
        pool: Optional[FrameBufferPool] = None,
        gps_provider: Optional["GpsProvider"] = None,
        heading_provider: Optional["HeadingProvider"] = None,
        focus_provider: Optional["FocusProvider"] = None,
        full_res_enabled: Optional[Callable[[], bool]] = None,
        line_open_check: Optional[Callable[[], bool]] = None,
        no_frame_warn_s: float = 5.0,
        parent: Optional[QThread] = None,
    ) -> None:
        super().__init__(parent)
        self._camera_number = camera_number
        self._camera_serial = camera_serial
        # Input: SDK-callback-fed queue of (small_u16, full_payload)
        # tuples. ``full_payload`` is None (focus / preview-only mode),
        # an int slot index (M0/M1 + pool wired), or a fresh ndarray
        # (legacy / pool not wired). We accept all three but the only
        # path actually exercised in stage 4 is the int-slot one.
        self._frame_queue = frame_queue
        self._sweep = sweep
        self._modes = modes
        self._context = context
        self._get_drive_root = get_drive_root
        # Pool is None when the writer is disabled. With a pool, the
        # AcquisitionThread builds a WriteJob per slot and submits it
        # to the writer subprocess via pool.work_q. Without a pool we
        # never enter the writer branch -- the SDK callback will also
        # not allocate full-res copies because the GUI keeps
        # set_full_res_emission off.
        self._pool: Optional[FrameBufferPool] = pool
        # Optional live w10 scorer (w10_live.W10LiveScorer): offered one frame per sweep from
        # _submit_slot while the slot is still ours. None = feature off.
        self._w10_live = None
        self._gps_provider = gps_provider
        self._heading_provider = heading_provider
        self._focus_provider = focus_provider
        self._full_res_enabled = full_res_enabled or (lambda: False)
        # Callback returning True iff a .bay14line container is open on
        # the io_writer side. Used to gate ``_submit_to_writer`` so we
        # don't burn pack CPU on frames that would just be dropped at
        # the io_writer (and bump failed_count). Defaults to "always
        # open" for back-compat with tests that bypass the line
        # lifecycle.
        self._line_open_check = line_open_check or (lambda: True)
        self._no_frame_warn_s = float(no_frame_warn_s)
        self._running = True
        self._preview_enabled = False
        self._preview_max_edge = PREVIEW_MAX_EDGE
        # 100% zoom preview (M0/M1): when True, the preview is built from
        # a centre crop of the FULL-resolution frame (1 sensor px = 1
        # display px) sized to ``_preview_zoom_w`` x ``_preview_zoom_h``
        # instead of the cheap whole-frame decimation. Falls back to the
        # decimated path automatically when no full-res payload is
        # available for the frame (e.g. emission was off at callback).
        self._preview_zoom = False
        self._preview_zoom_w = 1280
        self._preview_zoom_h = 720
        # Software brightness multiplier for the preview image only (see
        # ``_u16_to_gray8``). Recorded data is never scaled.
        self._preview_gain = 1.0
        self._focus_tap_enabled = False
        # ``_armed`` reflects whether the backend camera is currently
        # producing frames into ``_frame_queue``. When False we just
        # idle in the run loop. CameraPipeline.arm()/disarm() drive this
        # in lockstep with KyfgCamera.arm()/disarm() so the queue can't
        # accumulate frames between disarms.
        self._armed = False
        self._last_preview_t = 0.0
        self._last_focus_tap_t = 0.0
        self._dropped_due_to_queue_full = 0
        self._dropped_writer_not_ready = 0
        # Diagnostic counter for the M4 focus-tap path. Bumped each
        # time we emit a ``raw_focus_frame`` signal; we log it at low
        # cadence so the smoke log shows whether frames are actually
        # leaving AcquisitionThread for the focus panel.
        self._focus_tap_emit_count = 0
        # Backpressure for the acq->GUI focus-frame hop. ``raw_focus_frame``
        # ships a FRESH ~8 MB ``small_u16`` copy as a QUEUED cross-thread event,
        # which Qt's event loop never bounds: when the GUI thread falls behind
        # (heavy per-frame AF work, or a blocking serial write stalling it for
        # seconds) those 8 MB payloads pile up unconsumed -> ~40 MB/s leak that
        # crawled the frame rate and then crashed the box. This flag conflates
        # the stream to AT MOST ONE in-flight frame: we skip the emit while the
        # GUI hasn't picked up the previous one (the dropped copy is just a local
        # -> GC'd), so a stalled GUI can never balloon the queue. The GUI slot
        # clears it (``note_focus_frame_consumed``) as it starts handling a frame.
        # Bool assignment is atomic under the GIL, so no lock is needed; the worst
        # case is dropping/keeping one extra frame, which is invisible at preview.
        self._focus_frame_inflight = False
        self._focus_frames_conflated = 0  # diag: frames dropped by conflation
        # w10 AF: fixed full-res ROIs (sensor-pixel coords) to crop from the held slot
        # each frame, and a conflation latch so the (larger) crops can't balloon the
        # GUI queue any more than the coarse tap can. Set from the GUI thread; list
        # assignment is atomic under the GIL.
        self._preview_rois: list = []
        self._preview_crops_inflight = False
        # M4/M5 Fine view: ONE full-res ROI (sensor-pixel coords) that the GUI's
        # leading-edge tracker moves with the scene, cropped from the same held slot
        # as the w10 ROIs. Separate from ``_preview_rois`` because the w10 engine
        # rejects any batch that isn't exactly its five boxes.
        self._fine_roi: Optional[tuple] = None
        self._fine_crop_inflight = False
        # State-transition logging for "why we couldn't write": a
        # missing project name + a busy trigger would otherwise emit
        # ~40 lines/sec into the log, freezing the GUI's QPlainTextEdit.
        # We only emit a log_line when the reason CHANGES.
        self._last_block_reason: Optional[str] = None
        # When set by an external thread (e.g. the GUI after the
        # camera-recovery worker finishes), the next iteration of the
        # ``run`` loop zeroes ``frame_count`` / ``last_emitted_dropped``
        # and the two ``_dropped_*`` instance fields, then emits 0-valued
        # ``frame_acquired`` / ``frame_dropped`` signals so the per-camera
        # GUI labels read ``acq:0  hz:0.0`` and ``dropped:0`` immediately.
        # Bool assignment is atomic in CPython so we don't bother with a
        # lock here; the worst case is a one-iteration delay before the
        # reset takes effect, which the user can't see.
        self._pending_counter_reset = False

    @property
    def label(self) -> str:
        """Short identifier for log lines, e.g. ``cam1[2439007]``."""
        return f"cam{self._camera_number}[{self._camera_serial}]"

    def set_w10_live(self, scorer) -> None:
        """Attach (or detach with None) the shared live w10 scorer; read on the acquisition thread."""
        self._w10_live = scorer

    def w10_note_drop(self) -> None:
        """A recording frame was lost to backpressure: tell the live scorer to stand down (sec.54.12)."""
        scorer = self._w10_live
        if scorer is not None:
            scorer.note_drop(self.label, int(self._context.line))

    @property
    def camera_number(self) -> int:
        return self._camera_number

    def stop_gracefully(self) -> None:
        self._running = False

    def set_preview_enabled(self, enabled: bool) -> None:
        self._preview_enabled = bool(enabled)

    def set_preview_max_edge(self, max_edge: int) -> None:
        self._preview_max_edge = max(64, int(max_edge))

    def set_preview_zoom(self, enabled: bool) -> None:
        """Toggle the 100% full-resolution centre-crop preview."""
        self._preview_zoom = bool(enabled)

    def set_preview_zoom_size(self, width: int, height: int) -> None:
        """Set the crop size (in sensor pixels) used by the zoom preview.

        Normally driven by the on-screen preview-window size so the crop
        maps 1:1 to display pixels. Clamped to even values to stay
        Bayer-quad aligned.
        """
        w = max(2, int(width)) & ~1
        h = max(2, int(height)) & ~1
        self._preview_zoom_w = w
        self._preview_zoom_h = h

    def set_preview_gain(self, gain: float) -> None:
        """Set the display-only brightness multiplier for preview frames."""
        self._preview_gain = float(
            min(PREVIEW_DISPLAY_GAIN_MAX, max(PREVIEW_DISPLAY_GAIN_MIN, float(gain)))
        )

    def set_focus_tap_enabled(self, enabled: bool) -> None:
        """Opt in to ``raw_focus_frame`` emission while in M4."""
        self._focus_tap_enabled = bool(enabled)

    def note_focus_frame_consumed(self) -> None:
        """Called from the GUI thread when it picks up a ``raw_focus_frame``.
        Releases the conflation latch so the acq thread may emit the next one.
        Clearing at the START of the GUI handler (not the end) lets the acq
        thread stage the next frame while this one is still being processed --
        at most ~1 extra frame in flight -- which keeps the preview live without
        letting the 8 MB payloads accumulate."""
        self._focus_frame_inflight = False

    def set_preview_rois(self, rois: list) -> None:
        """w10 AF: pin the full-res ROIs (sensor-pixel coords, ``roi_boxes`` order) to crop
        from the held slot each frame; ``[]`` releases them. Fixed in SENSOR coordinates --
        the metric scores natural step edges wherever they are, so nothing tracks the scene.

        The whole Bayer rectangle is cut, even though scoring reduces it to one phase: the
        cut is a quarter of a patch's cost and the column stride reads the same cache lines
        (tried and reverted 2026-09-10). List assignment is atomic under the GIL."""
        self._preview_rois = list(rois) if rois else []

    def note_preview_crops_consumed(self) -> None:
        """GUI-thread ack for a ``raw_focus_preview_crops`` batch (see
        ``note_focus_frame_consumed``); releases the crop conflation latch."""
        self._preview_crops_inflight = False

    def set_fine_roi(self, roi: Optional[tuple]) -> None:
        """M4/M5 Fine view: pin one full-res ROI to crop from the held slot each frame,
        or ``None`` to stop emitting. Unlike ``set_preview_rois`` this one MOVES -- the
        GUI tracker re-pins it every coarse frame. Tuple assignment is atomic under
        the GIL, so the run loop always reads a whole ROI."""
        self._fine_roi = tuple(int(v) for v in roi) if roi else None

    def note_fine_crop_consumed(self) -> None:
        """GUI-thread ack for a ``raw_focus_fine_crop`` (see ``note_focus_frame_consumed``)."""
        self._fine_crop_inflight = False

    def set_armed(self, armed: bool) -> None:
        """Tell the run loop whether the IA is currently started/streaming.

        Set to True *after* the backend's ``KyfgCamera.arm`` succeeds and
        to False *before* ``KyfgCamera.disarm`` runs. The acquisition
        thread keeps running across the transition; only the inner emit
        work is gated.
        """
        self._armed = bool(armed)

    @property
    def dropped_queue_full(self) -> int:
        return self._dropped_due_to_queue_full

    @property
    def dropped_writer_not_ready(self) -> int:
        return self._dropped_writer_not_ready

    def request_counter_reset(self) -> None:
        """Ask the run loop to zero its frame / drop counters.

        Set from the GUI thread after camera recovery completes so the
        per-camera ``acq:N`` / ``dropped:N`` labels start fresh instead
        of carrying the pre-recovery counts forward (which makes the
        ``written:M`` label, populated by a separate writer-subprocess
        counter, look out of sync after we reset that one too). Safe
        to call while the thread is running -- the actual reset happens
        on the run-loop thread, which avoids racing with reads /
        increments inside ``run``.
        """
        self._pending_counter_reset = True

    def _writer_ready_for_submit(self) -> tuple[bool, Optional[str]]:
        """Return ``(ready, block_reason)`` for the writer-submit path.

        ``block_reason`` is ``None`` when ready. When not ready, the
        caller is expected to drop the frame (and release any slot it
        already pulled) and pass the reason through :meth:`_note_block`
        for state-transition logging.
        """
        if self._pool is None:
            return (False, "writer_disabled")
        try:
            drive_root = self._get_drive_root()
        except Exception:
            return (False, "no_drive")
        if not drive_root:
            return (False, "no_drive")
        project = (self._context.project or "").strip()
        if not project:
            return (False, "no_project")
        return (True, None)

    def _note_block(self, reason: str, message: str) -> None:
        if reason == self._last_block_reason:
            return
        self._last_block_reason = reason
        self.log_line.emit(f"{self.label}: {message}")

    def _note_unblocked(self) -> None:
        if self._last_block_reason is not None:
            self.log_line.emit(f"{self.label}: writer is now writing.")
            self._last_block_reason = None

    def run(self) -> None:
        from .mode_controller import Mode

        no_frame_warns = 0
        frame_count = 0
        last_t = time.perf_counter()

        first_frame_logged = False
        last_no_frame_warn_t = 0.0
        last_frame_seen_t = time.perf_counter()
        last_logged_mode = Mode.UNKNOWN
        last_emitted_dropped = 0

        while self._running:
            # GUI-requested counter reset (e.g. after camera recovery).
            # Done first thing in the loop so the very next emit -- and
            # any subsequent drop accounting -- starts from zero.
            if self._pending_counter_reset:
                self._pending_counter_reset = False
                frame_count = 0
                last_emitted_dropped = 0
                self._dropped_writer_not_ready = 0
                self._dropped_due_to_queue_full = 0
                last_t = time.perf_counter()
                last_frame_seen_t = last_t
                first_frame_logged = False
                # Push a zeroed pair into the GUI immediately so the
                # labels flip to ``acq:0  hz:0.0`` / ``dropped:0``
                # without waiting for the next frame to arrive.
                self.frame_acquired.emit(0, 0.0)
                self.frame_dropped.emit(0)

            mode_now = self._modes.mode

            if mode_now != last_logged_mode:
                last_logged_mode = mode_now
                if mode_now != Mode.UNKNOWN:
                    self.log_line.emit(
                        f"{self.label}: acquisition active in {mode_now.name}"
                    )

            # Idle-gate: only consume frames when the camera is armed and
            # at least one consumer wants frames.
            #
            # M3 (CollectionON) keeps the selected camera previewing live
            # so the SDK/trigger path stays warm (avoids a cold-start
            # stall on the first sweep after "Start line"). Disk WRITES,
            # however, stay gated by the recording gate, which only turns
            # on between ?/! via the m3_collecting flag -- so nothing is
            # recorded until the operator opens a line.
            needs_buffers = (
                self._armed
                and mode_now != Mode.UNKNOWN
                and (
                    self._modes.is_recording_mode()
                    or self._preview_enabled
                    or self._focus_tap_enabled
                )
            )
            if not needs_buffers:
                # Drain any frames the SDK callback may have queued before
                # the GUI noticed the disarm, so we don't show a stale
                # frame after re-arming.
                self._drain_queue()
                self.msleep(100)
                # Reset the no-frame timer while idle so the next "armed"
                # transition gets a fresh 5 s budget.
                last_frame_seen_t = time.perf_counter()
                continue

            try:
                # Block briefly so we yield to the Qt event loop on idle.
                item = self._frame_queue.get(timeout=0.2)
            except queue.Empty:
                now = time.perf_counter()
                # Suppress the "no frame in 5s" warning while the PCB is
                # in M0 (nav-point-based collection): in that mode the
                # cameras really are armed and waiting between nav-point
                # triggers, sometimes for tens of seconds at a stretch
                # while the rig taxis between waypoints. Treating that
                # as a stuck-camera fault produced an endless log spam.
                # We still warn in M1 (continuous trigger) and M4
                # (continuous focus assist), where 5 s of silence really
                # does mean something is wrong.
                warn_in_this_mode = (mode_now != Mode.M0)
                if warn_in_this_mode and (now - last_frame_seen_t) >= self._no_frame_warn_s and (
                    now - last_no_frame_warn_t
                ) > self._no_frame_warn_s:
                    no_frame_warns += 1
                    last_no_frame_warn_t = now
                    self.fetch_timeout_count.emit(no_frame_warns)
                    self.log_line.emit(
                        f"{self.label}: no frame in "
                        f"{self._no_frame_warn_s:.0f}s "
                        f"(total: {no_frame_warns}). Camera armed but not "
                        "streaming -- check PCB triggers and CXP link."
                    )
                continue

            if item is None:
                # Sentinel: shutdown OR a wakeup poke from disarm().
                # When it's a disarm poke, the next ``while`` iteration
                # will see ``_armed=False`` and idle. When it's the real
                # shutdown sentinel, we exit by also setting ``_running
                # = False`` from outside.
                if not self._running:
                    break
                continue
            small_u16, full_u16 = item

            try:
                last_frame_seen_t = time.perf_counter()
                # Capture the frame-arrival monotonic timestamp once,
                # right when we dequeue, so the GPS correlator can
                # match it against the SBF event's host-arrival time
                # on the SAME clock. We pass this through
                # :meth:`_submit_slot` -> :attr:`_gps_provider`.
                frame_perf_ns = time.perf_counter_ns()
                acquire_ts_iso = _iso_now()
                in_recording_mode = self._modes.is_recording_mode()
                # M4 (focus) AND M5 (ground test) both feed the focus preview /
                # AF engine via the raw-focus tap, so gate emission on is_af_mode.
                # (The focus-crop *disk* writer stays is_focus_mode/M4-only below
                # -- M5 is preview-only.)
                in_focus_mode = self._modes.is_af_mode()

                writer_ready, block_reason = self._writer_ready_for_submit()
                will_write = in_recording_mode and writer_ready
                # NOTE: do not bump _dropped_writer_not_ready here based
                # only on (in_recording_mode and not writer_ready) -- a
                # tuple where ``full_u16 is None`` means the SDK never
                # captured a full-res payload (stale frame from a prior
                # mode), so there's nothing to drop. Bump only inside
                # the structured branches below where we know what
                # ``full_u16`` actually is.

                now_t = time.perf_counter()
                want_focus_tap = (
                    in_focus_mode
                    and self._focus_tap_enabled
                    and (now_t - self._last_focus_tap_t)
                    >= FOCUS_TAP_MIN_INTERVAL_S
                )
                want_preview = (
                    self._preview_enabled
                    and (now_t - self._last_preview_t)
                    >= PREVIEW_MIN_INTERVAL_S
                )

                if not first_frame_logged:
                    first_frame_logged = True
                    fh, fw = small_u16.shape
                    self.log_line.emit(
                        f"{self.label}: first frame from backend "
                        f"({fw}x{fh} uint16, subsampled)."
                    )

                # ``full_u16`` semantics from the SDK callback:
                #   * ``None``     -- emission was OFF at callback time
                #                    (focus / preview-only, or a stale
                #                    frame still in the queue from
                #                    BEFORE the GUI flipped emission to
                #                    True at a mode boundary). Never a
                #                    drop -- there's no full-res
                #                    payload to lose.
                #   * ``-1``       -- emission was ON, pool had no free
                #                    slot. Real writer-behind drop.
                #   * ``int >= 0`` -- valid slot index; SDK already
                #                    copied the frame into the slot.
                #   * ndarray      -- legacy in-process path (no pool).
                is_real_pool_drop = (
                    isinstance(full_u16, (int, np.integer))
                    and int(full_u16) == -1
                )
                is_valid_slot = (
                    isinstance(full_u16, (int, np.integer))
                    and int(full_u16) >= 0
                )
                is_legacy_ndarray = (
                    full_u16 is not None
                    and not isinstance(full_u16, (int, np.integer))
                )

                # Build the 100% zoom preview crop from the FULL-res frame
                # BEFORE the slot is handed to the writer / released below
                # (reading a slot view after submit races the writer
                # subprocess). ``_full_res_center_crop_gray8`` returns a
                # fresh uint8 copy, so it stays valid after slot release.
                zoom_preview_gray: Optional[np.ndarray] = None
                if want_preview and self._preview_zoom:
                    try:
                        if is_valid_slot and self._pool is not None:
                            zoom_preview_gray = _full_res_center_crop_gray8(
                                self._pool.slot_view(int(full_u16)),
                                self._preview_zoom_w,
                                self._preview_zoom_h,
                                self._preview_gain,
                            )
                        elif is_legacy_ndarray:
                            zoom_preview_gray = _full_res_center_crop_gray8(
                                full_u16,
                                self._preview_zoom_w,
                                self._preview_zoom_h,
                                self._preview_gain,
                            )
                    except Exception as exc:
                        log.warning(
                            "%s zoom preview build failed: %s", self.label, exc
                        )

                # Does the focus tap below need to read the full-res slot? Only when
                # the w10 AF has pinned ROIs it wants scored at full resolution, or
                # the Fine view has a tracked ROI it wants shown at 1:1.
                slot_for_af: Optional[int] = None
                if (
                    is_valid_slot
                    and in_focus_mode
                    and self._focus_tap_enabled
                    and want_focus_tap
                    and (bool(self._preview_rois) or self._fine_roi is not None)
                ):
                    slot_for_af = int(full_u16)

                # Slot waiting to be handed to the writer, resolved AFTER the
                # focus tap; see the deferred submit below.
                pending_submit_slot: Optional[int] = None

                if is_valid_slot:
                    if will_write:
                        # Deferred, NOT submitted here: reading a slot view
                        # after submit races the writer subprocess, and in M4
                        # with Record armed the focus tap still has to cut its
                        # crop out of this same frame. Recording and focusing
                        # used to be mutually exclusive, which is why this was
                        # a plain submit.
                        pending_submit_slot = int(full_u16)
                    else:
                        if slot_for_af is None and self._pool is not None:
                            self._pool.release_slot(int(full_u16))
                        if in_recording_mode and block_reason is not None:
                            self._dropped_writer_not_ready += 1
                            self._note_block(
                                block_reason,
                                self._block_message_for(block_reason),
                            )
                elif is_real_pool_drop:
                    # SDK had emission on but pool had no free slot --
                    # writer is genuinely behind.
                    if in_recording_mode:
                        self._dropped_writer_not_ready += 1
                        self.w10_note_drop()
                        # Log the first ~8 drops individually so the
                        # operator sees the onset, then drop to one
                        # per 8 thereafter so a long-running stall
                        # doesn't flood the GUI log. The earlier
                        # 1-per-16 throttle hid ~94% of drops in the
                        # first live run -- we logged 3 messages for
                        # 54 actual drops on cam2 -- and that masked
                        # the IPC-bottleneck diagnosis.
                        n = self._dropped_writer_not_ready
                        if n <= 8 or n % 8 == 0:
                            self.log_line.emit(
                                f"{self.label}: dropped sweep frame "
                                f"#{n} (pool has no free slots right "
                                "now -- writer backpressure; frame "
                                "dropped.)"
                            )
                elif is_legacy_ndarray:
                    # Legacy in-process path -- the writer subprocess
                    # can't consume ndarrays directly. Treat as a
                    # drop only when we WOULD have written.
                    if will_write:
                        self._dropped_writer_not_ready += 1
                # else: full_u16 is None -- emission was off at
                # callback time. No payload, no drop. Silently fall
                # through; the small/preview/focus paths below still
                # run for this frame.

                if want_focus_tap:
                    self._last_focus_tap_t = now_t
                    pinned_rois = self._preview_rois
                    try:
                        # w10 AF: crop each pinned ROI from the held slot (sensor-native
                        # pixels) and ship the batch, with its own conflation latch so
                        # the (bigger) crops can't back up the GUI queue. Slot release
                        # is in the finally below.
                        if (
                            pinned_rois
                            and slot_for_af is not None
                            and self._pool is not None
                            and not self._preview_crops_inflight
                        ):
                            fvp = self._pool.slot_view(slot_for_af)
                            fhp, fwp = fvp.shape[:2]
                            crops: list = []
                            for proi in pinned_rois:
                                px0, py0, px1, py1 = proi
                                px0 = max(0, min(int(px0), fwp - 2))
                                py0 = max(0, min(int(py0), fhp - 2))
                                px1 = max(px0 + 2, min(int(px1), fwp))
                                py1 = max(py0 + 2, min(int(py1), fhp))
                                # ONE copy that ALWAYS owns its data: ascontiguousarray
                                # returns the input untouched when a slice happens to be
                                # contiguous (a full-width ROI), which would hand the GUI
                                # a live view of a slot the finally-block recycles.
                                crops.append(np.array(fvp[py0:py1, px0:px1], order="C"))
                            self._preview_crops_inflight = True
                            self.raw_focus_preview_crops.emit(crops, acquire_ts_iso)
                        # Fine view: the tracked patch at 1:1, cut from the same slot.
                        fine_roi = self._fine_roi      # ONE read: the GUI re-pins it live
                        if (
                            fine_roi is not None
                            and slot_for_af is not None
                            and self._pool is not None
                            and not self._fine_crop_inflight
                        ):
                            fvf = self._pool.slot_view(slot_for_af)
                            fhf, fwf = fvf.shape[:2]
                            fx0, fy0, fx1, fy1 = fine_roi
                            fx0 = max(0, min(int(fx0), fwf - 2)) & ~1   # even: same Bayer quad
                            fy0 = max(0, min(int(fy0), fhf - 2)) & ~1
                            fx1 = max(fx0 + 2, min(int(fx1), fwf))
                            fy1 = max(fy0 + 2, min(int(fy1), fhf))
                            self._fine_crop_inflight = True
                            # np.array, not a slice: the finally-block below recycles
                            # this slot, and a contiguous slice would alias it.
                            self.raw_focus_fine_crop.emit(
                                np.array(fvf[fy0:fy1, fx0:fx1], order="C"), acquire_ts_iso)
                        # Conflate: only emit if the GUI has consumed the previous 8 MB
                        # frame. Otherwise drop this one so the queued-event backlog
                        # can't grow unbounded (the crash).
                        if self._focus_frame_inflight:
                            self._focus_frames_conflated += 1
                        else:
                            self._focus_frame_inflight = True
                            self.raw_focus_frame.emit(small_u16, acquire_ts_iso)
                        self._focus_tap_emit_count += 1
                        if self._focus_tap_emit_count == 1 or (
                            self._focus_tap_emit_count % 16 == 0
                        ):
                            self.log_line.emit(
                                f"{self.label}: focus-tap emit "
                                f"#{self._focus_tap_emit_count} ("
                                f"conflated={self._focus_frames_conflated}, "
                                f"emit={'slot' if is_valid_slot else ('pooldrop' if is_real_pool_drop else 'noemit')}, "
                                f"rois={len(pinned_rois)})"
                            )
                    except Exception as exc:
                        log.warning(
                            "%s focus-tap emit failed: %s", self.label, exc
                        )
                    finally:
                        # Not ours to release when the writer is about to take
                        # it; the writer subprocess returns it to free_q.
                        if (
                            slot_for_af is not None
                            and pending_submit_slot is None
                            and self._pool is not None
                        ):
                            self._pool.release_slot(slot_for_af)

                # Every full-res read of the slot is done by here, so the
                # writer can have it.
                if pending_submit_slot is not None:
                    try:
                        self._submit_slot(
                            pending_submit_slot, acquire_ts_iso, frame_perf_ns)
                    except Exception as exc:
                        # Final safety net -- _submit_slot is supposed to
                        # handle its own slot release on every error path. If
                        # it raises, count the drop and release the slot here
                        # so it doesn't leak.
                        self._dropped_writer_not_ready += 1
                        assert self._pool is not None
                        self._pool.release_slot(pending_submit_slot)
                        log.exception(
                            "%s submit slot %d failed: %s",
                            self.label, pending_submit_slot, exc,
                        )

                if want_preview:
                    self._last_preview_t = now_t
                    try:
                        if zoom_preview_gray is not None:
                            # 100% zoom: full-res centre crop, 1:1 pixels.
                            gray8 = np.ascontiguousarray(zoom_preview_gray)
                        else:
                            # Whole-frame decimated preview (default, and
                            # the fallback when zoom is on but no full-res
                            # payload was available for this frame).
                            gray8 = np.ascontiguousarray(
                                _downsample_bayer_to_gray8(
                                    small_u16, self._preview_max_edge,
                                    self._preview_gain,
                                )
                            )
                        gh, gw = gray8.shape
                        qimg = QImage(
                            gray8.data, gw, gh, gw,
                            QImage.Format.Format_Grayscale8,
                        ).copy()
                        self.preview_ready.emit(qimg)
                    except Exception as exc:
                        log.warning(
                            "%s preview build failed: %s", self.label, exc
                        )

            except Exception as exc:
                self.failed.emit(f"{self.label} acquisition error: {exc}")
                break

            frame_count += 1
            now = time.perf_counter()
            period = now - last_t
            last_t = now
            hz = 1.0 / period if period > 1e-6 else 0.0
            self.frame_acquired.emit(frame_count, hz)

            # Emit drop-count updates lazily: only if the cumulative
            # drop counter advanced this iteration. Saves a round-trip
            # to the GUI thread on every frame in the steady-state
            # case where nothing's being dropped.
            cur_dropped = (
                self._dropped_writer_not_ready
                + self._dropped_due_to_queue_full
            )
            if cur_dropped != last_emitted_dropped:
                last_emitted_dropped = cur_dropped
                self.frame_dropped.emit(cur_dropped)

    @staticmethod
    def _block_message_for(reason: str) -> str:
        if reason == "no_drive":
            return ("no drive selected; sweep frames are being dropped "
                    "until you pick a target drive.")
        if reason == "no_project":
            return ("project name is empty; sweep frames are being "
                    "dropped until you set a project name.")
        if reason == "writer_disabled":
            return ("writer is disabled (preview-only). enable in code "
                    "to capture sweep frames.")
        if reason == "bad_name":
            return ("filename build failed (sanitize error); fix project "
                    "name / line and try again.")
        if reason == "work_q_full":
            return ("writer subprocess is behind (work queue full). "
                    "frames are being dropped until it catches up.")
        return f"writer block: {reason}"

    def _submit_slot(
        self, slot_idx: int, acquire_ts_iso: str, frame_perf_ns: int,
    ) -> Optional[tuple[int, int]]:
        """Build a WriteJob for ``slot_idx`` and hand it to the writer
        subprocess via the pool's work_q.

        Returns the ``(sweep, frame_in_sweep)`` the job was stamped with, or
        ``None`` if the frame was dropped. The M4 focus tap reuses it so the
        small crop and the full frame from the SAME trigger carry the same
        coordinates -- that pairing is the only way to line a focus crop up
        against the full-res image it was cut from.

        On any failure (bad path, work_q full, GPS provider raised) the
        slot is returned to free_q before this method returns. Counters
        are bumped and a state-transition log line is emitted via
        :meth:`_note_block`.

        Ordering note: the sweep counter is advanced BEFORE we call
        ``_gps_provider`` so the provider sees the same
        ``(sweep, frame_in_sweep)`` the WriteJob will carry, which is
        what the slot-based correlator looks up.
        """
        pool = self._pool
        assert pool is not None  # caller guarantees this

        drive_root = self._get_drive_root()
        project_raw = self._context.project
        # Caller has already gated on writer_ready, so drive_root and
        # project should both be valid here. Belt-and-braces re-check
        # in case state flipped between the gate and now.
        if not drive_root or not (project_raw or "").strip():
            pool.release_slot(slot_idx)
            self._dropped_writer_not_ready += 1
            return None

        # Gate on "is a line currently open on the io_writer side?"
        # If not, drop the frame here rather than burn pack CPU
        # encoding a frame the io_writer would just discard.
        if not self._line_open_check():
            pool.release_slot(slot_idx)
            self._dropped_writer_not_ready += 1
            self._note_block(
                "no_line_open",
                "no .bay14line container is open; frames being dropped. "
                "Did the line spinbox get bumped without re-entering M0/M1?",
            )
            return None

        try:
            sweep_num, frame_index = self._sweep.next_position()
        except Exception as exc:
            self._dropped_writer_not_ready += 1
            log.exception("%s sweep counter failed: %s", self.label, exc)
            pool.release_slot(slot_idx)
            return None

        gps_dict: Optional[dict] = None
        if self._gps_provider is not None:
            try:
                fix = self._gps_provider(
                    self._camera_serial,
                    int(frame_perf_ns),
                    int(sweep_num),
                    int(frame_index),
                )
            except Exception as exc:
                log.debug("%s GPS provider raised: %s", self.label, exc)
                fix = None
            if fix is not None:
                try:
                    # to_dict() ISO-encodes the timestamp so the
                    # writer subprocess can JSON-serialise the chunk
                    # header without datetime support.
                    gps_dict = fix.to_dict()
                except Exception:
                    log.debug("%s GpsFix.to_dict raised", self.label, exc_info=True)
                    gps_dict = None

        heading_deg: Optional[float] = None
        if self._heading_provider is not None:
            try:
                heading_deg = self._heading_provider()
            except Exception:
                log.debug("%s heading provider raised", self.label, exc_info=True)

        focus_dict: Optional[dict] = None
        if self._focus_provider is not None:
            try:
                focus_dict = self._focus_provider()
            except Exception:
                log.debug("%s focus provider raised", self.label, exc_info=True)

        attitude_dict: Optional[dict] = None
        try:
            attitude_dict = compute_attitude_dict(
                self._camera_serial,
                int(frame_index),
                heading_deg=heading_deg,
            )
        except Exception as exc:
            log.debug(
                "%s attitude compute failed (frame=%d): %s",
                self.label, frame_index, exc,
            )

        # No per-frame path build: the io_writer subprocess owns the
        # currently-open .bay14line container path. We just hand it
        # the metadata it needs to label the chunk inside the file.
        job = WriteJob(
            slot_idx=slot_idx,
            target_path="",  # io_writer owns the path; see WriterProcessClient.open_line
            camera_serial=self._camera_serial,
            project=project_raw,
            line=int(self._context.line),
            sweep=int(sweep_num),
            frame_in_sweep=int(frame_index),
            acquire_timestamp=acquire_ts_iso,
            capture_kind="sweep",
            gps=gps_dict,
            attitude=attitude_dict,
            focus=focus_dict,
        )
        # Live w10 (sec.54.12): one frame per sweep, offered while the slot is still ours. The
        # scorer copies ~10 MB of ROIs and returns; it drops when busy or when the pool is short.
        scorer = self._w10_live
        if scorer is not None and int(frame_index) == scorer.frame_in_sweep:
            try:
                scorer.offer(pool, slot_idx, dict(
                    cam=self.label, camera=int(self._camera_number), serial=self._camera_serial,
                    line=int(self._context.line), sweep=int(sweep_num), frame=int(frame_index),
                    ts=acquire_ts_iso, focus=focus_dict))
            except Exception:  # noqa: BLE001 -- the recording path must not notice
                log.debug("%s w10 live offer raised", self.label, exc_info=True)
        if not pool.submit(job):
            self._dropped_due_to_queue_full += 1
            pool.release_slot(slot_idx)
            self.w10_note_drop()
            self._note_block(
                "work_q_full",
                "writer subprocess is behind (work queue full); "
                "frames being dropped.",
            )
            return None

        self._note_unblocked()
        return int(sweep_num), int(frame_index)

    def _drain_queue(self) -> None:
        """Drop any frames currently sitting in the input queue.

        Called whenever we transition into the "idle" branch of the run
        loop so we don't process stale frames after the operator disarms
        and re-arms a camera. With the SharedMemory pool a queued
        ``full_payload`` is just an ``int`` slot index, so draining is
        cheap -- but we MUST return the slot to free_q so the SDK
        callback can reuse it. Without this, a disarm with a full
        frame_queue would leak up to ``maxsize`` slots per camera.
        """
        while True:
            try:
                item = self._frame_queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, tuple) and len(item) == 2:
                _small, full_payload = item
                if isinstance(full_payload, (int, np.integer)) and self._pool is not None:
                    self._pool.release_slot(int(full_payload))
                del _small, full_payload
            del item


# ---------------------------------------------------------------------------


class WriterProcessClient(QObject):
    """Owns N packer subprocesses + 1 io_writer subprocess per camera.

    Architecture (per camera):

        SDK callback ──► free_q / shm slot
                                │
                                ▼ work_q
                       ┌────────┴────────┐
                       │     │      │   │
                    packer1 packer2 ...  (N=2 by default)
                  blosc2 SHUFFLE+ZSTD-1 encode
                       │
                       ▼ io_q  (PackedResult / Line(Open|Close)Job)
                       │
                  io_writer (1 subprocess)
              opens .bay14line on LineOpenJob
              appends chunk on PackedResult
              writes index + closes on LineCloseJob

    Lifecycle:

    1. Constructed in CameraPipeline.__init__ along with its pool.
    2. :meth:`start` spawns the N packers + 1 io_writer.
    3. :meth:`start_telemetry` starts the QTimer that polls the
       cross-process written / failed counters.
    4. :meth:`open_line` (called by the MainWindow when entering
       M0/M1 or after a line spinbox bump) tells the io_writer to
       create the next .bay14line container.
    5. AcquisitionThread feeds the pool by calling
       :meth:`FrameBufferPool.submit`. Packers drain it concurrently.
    6. :meth:`close_line` waits for in-flight pack work to drain
       (``submitted_count == processed_count``), then sends a
       :class:`LineCloseJob`. The io_writer flushes the index,
       patches the file header and closes the file.
    7. :meth:`stop_gracefully` sentinels every subprocess and stops
       the timer; :meth:`wait` joins them.

    Why N packers + 1 io_writer (vs N independent writers)?

    * blosc2 SHUFFLE+ZSTD-1 takes ~285 ms / frame at 2 threads on
      this CPU. The 4.76 fps producer needs ~210 ms/frame served --
      one packer can't keep up; two comfortably can. Three is the
      ceiling before CPU contention hurts more than it helps.
    * I/O is purely sequential into a freshly-opened container --
      no offset coordination, no per-write file create. One io_writer
      saturates the per-camera U.2 drive with no contention.

    Telemetry signal contract: ``frame_written(count, basename)``
    matches the old WriterThread signature for source compatibility
    with ``CameraRow.update_written``. The basename is empty because
    the io_writer doesn't push per-frame paths back to the GUI --
    the GUI only ever displayed the count, so this is fine.
    """

    log_line = Signal(str)
    frame_written = Signal(int, str)
    write_error = Signal(str)
    # Emitted whenever the failed-counter advances.
    write_failed = Signal(int)

    POLL_INTERVAL_MS = 250

    # Per-camera packer count.
    #
    # History:
    # * Pre-blosc2 (per-frame .bay14, no compression): drives saturated
    #   at ~4.71 fps with 4 in-process writers and still dropped
    #   ~400 frames into a long M1 run because the per-camera disk
    #   demand (~1063 MB/s of packed14) was inside ~1 % of the U.2
    #   sustained-write ceiling (~1.07 GB/s).
    # * blosc2 v1 (2 packers, 2 nthreads each): comfortable on
    #   low-entropy bench scenes (~285 ms encode), but on real
    #   high-entropy scenes (textured ground, glare) one camera saw
    #   720 ms encode -> 2.6 fps drain vs ~4.8 fps trigger -> 49 of
    #   157 frames dropped due to writer backpressure (cam2,
    #   2026-05-07 10:51 run).
    # * blosc2 v2 (4 packers, 4 nthreads, single io_writer): packers
    #   could now sustain ~8 fps in encode but the per-camera
    #   io_writer plateaued at ~640 MB/s due to a 165 MB
    #   bytes()-from-SHM heap memcpy on the io_writer hot path,
    #   capping cam2 at ~2.7 fps overall. Drops persisted (49 of
    #   157, then again with the 4-packer build).
    # * Now (4 packers, 4 nthreads, 2 io_writers, zero-copy SHM
    #   read in the io_writer): each camera runs two parallel
    #   io_writer subprocesses round-robined by the packers, each
    #   owning its own ``Cam..._L<N>_<HHMMSS>_pK.bay14line`` file on
    #   the camera's drive. Doubles per-camera disk write bandwidth
    #   and decouples the packer drain from a single io serial
    #   channel; combined with the zero-copy SHM read in the
    #   io_writer, the packer ``packed_wait`` window collapses to
    #   ~0 ms even on the high-entropy cam2 scene.
    DEFAULT_N_PACKERS = 4

    # Per-camera io_writer subprocess count. See N_PACKERS history
    # for the bandwidth analysis. Each io_writer owns one
    # ``.bay14line`` part-file per line; reader tools reassemble
    # parts by sorting on the ``_pN`` filename suffix.
    DEFAULT_N_IO_WRITERS = 2

    def __init__(
        self,
        *,
        camera_number: int,
        camera_serial: str,
        pool: FrameBufferPool,
        packed_pool: PackedFramePool,
        n_workers: Optional[int] = None,
        codec: Optional[str] = None,
        log_level: str = "INFO",
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._camera_number = camera_number
        self._camera_serial = camera_serial
        self._pool = pool
        self._packed_pool = packed_pool
        self._log_level = log_level
        # ``n_workers`` is kept for back-compat with the old
        # constructor signature; it now means "number of packers".
        self._n_packers = max(1, int(n_workers or self.DEFAULT_N_PACKERS))
        # Codec the packer + io_writer subprocesses agree on. None
        # falls back to whatever ``CameraPipeline`` resolved from
        # the env var (``BAY14_CAPTURE_CODEC``); see
        # :func:`CameraPipeline._resolve_capture_codec`. We keep the
        # writer client codec-agnostic on purpose so unit tests +
        # the IPC smoke test can drive it directly without going
        # through the env var path.
        from .raw_bay_writer import COMPRESSION_NONE
        self._codec = codec or COMPRESSION_NONE
        # Number of parallel io_writer subprocesses per camera; see
        # DEFAULT_N_IO_WRITERS. Validated against the pool's own
        # ``n_io_writers`` so we don't end up with mismatched queues.
        self._n_io_writers = max(1, int(pool.n_io_writers))
        self._packer_procs: list[mp.Process] = []
        self._io_procs: list[mp.Process] = []
        self._timer: Optional[QTimer] = None
        self._last_written = 0
        self._last_failed = 0
        self._stopped = False
        # Track whether a line is currently open from this client's
        # perspective so :meth:`open_line` / :meth:`close_line` can
        # detect double-open / double-close mistakes locally without
        # waiting on the io_writer to log a complaint.
        self._line_open: bool = False
        # Each io_writer owns its own ``_pN.bay14line`` part-file;
        # we keep all paths so the GUI / log can show every part
        # that was touched. The "primary" path (index 0) stays
        # available via :attr:`current_line_path` for code that
        # only wants one identifier.
        self._current_line_paths: list[str] = []

    @property
    def label(self) -> str:
        return f"cam{self._camera_number}[{self._camera_serial}]"

    @property
    def written(self) -> int:
        with self._pool.written_count.get_lock():
            return int(self._pool.written_count.value)

    @property
    def failed(self) -> int:
        with self._pool.failed_count.get_lock():
            return int(self._pool.failed_count.value)

    def reset_counters(self) -> None:
        """Zero the cross-process written / failed counters.

        Called after a camera-recovery sequence so the per-camera
        ``written:N`` GUI label restarts at zero rather than carrying
        the pre-recovery cumulative count forward (which would make
        ``written`` look out of step with ``acq`` -- which the
        AcquisitionThread also resets to 0 -- after a recovery).

        We do NOT reset ``pool.warmup_count`` -- that one's a
        once-per-process startup counter that the bring-up worker
        polls to detect when the pool finished its warmup, and the
        warmup never re-runs for the lifetime of the pool.

        Safe to call from any thread; the ``mp.Value`` lock makes
        the writes race-free even if a writer subprocess is
        mid-increment when we fire.
        """
        with self._pool.written_count.get_lock():
            self._pool.written_count.value = 0
        with self._pool.failed_count.get_lock():
            self._pool.failed_count.value = 0
        self._last_written = 0
        self._last_failed = 0
        # Push zero into the GUI immediately so the labels flip on
        # the same screen frame as the AcquisitionThread reset, even
        # if no writer subprocess increments before the next poll
        # tick.
        self.frame_written.emit(0, "")
        self.write_failed.emit(0)

    def start(self) -> None:
        """Spawn the per-camera packers + io_writer subprocesses.

        Safe to call from any thread. Spawning child processes via
        ``multiprocessing`` is independent of Qt thread affinity so
        bring-up code can call this from a worker thread without
        worrying about which Qt thread the QObject was created on.
        The telemetry QTimer is NOT started here -- call
        :meth:`start_telemetry` once on the GUI thread (after the
        QObject has been moved there if needed) to begin polling.

        Idempotent: a second start() while subprocesses are alive
        is a no-op.
        """
        if (
            self._packer_procs
            and any(p.is_alive() for p in self._packer_procs)
            and self._io_procs
            and all(p.is_alive() for p in self._io_procs)
        ):
            return
        ctx = mp.get_context("spawn")
        # ── packers ──────────────────────────────────────────────
        self._packer_procs = []
        for i in range(self._n_packers):
            packer_label = f"{self.label}.pack#{i}"
            p = ctx.Process(
                target=packer_main,
                kwargs=dict(
                    label=packer_label,
                    shm_name=self._pool.name,
                    n_slots=self._pool.n_slots,
                    height=self._pool.height,
                    width=self._pool.width,
                    packed_shm_name=self._packed_pool.name,
                    packed_n_slots=self._packed_pool.n_slots,
                    packed_slot_bytes=self._packed_pool.slot_bytes,
                    packed_free_q=self._packed_pool.free_q,
                    work_q=self._pool.work_q,
                    free_q=self._pool.free_q,
                    io_qs=tuple(self._pool.io_qs),
                    warmup_count=self._pool.warmup_count,
                    processed_count=self._pool.processed_count,
                    failed_count=self._pool.failed_count,
                    codec=self._codec,
                    log_level=self._log_level,
                ),
                name=f"packer-{packer_label}",
                daemon=False,
            )
            p.start()
            log.info("%s packer subprocess[%d] pid=%d started",
                     self.label, i, p.pid)
            self._packer_procs.append(p)
        # ── io_writers ───────────────────────────────────────────
        # One subprocess per :attr:`_pool.io_qs` entry. Each owns
        # its own ``.bay14line`` part-file (named ``..._pK.bay14line``
        # by :meth:`open_line`). The :attr:`written_count` /
        # :attr:`failed_count` mp.Value counters are shared across
        # writers so the GUI sees aggregate totals across all parts
        # without per-writer book-keeping.
        self._io_procs = []
        for k in range(self._n_io_writers):
            io_label = f"{self.label}.io#{k}"
            io_proc = ctx.Process(
                target=io_writer_main,
                kwargs=dict(
                    label=io_label,
                    height=self._pool.height,
                    width=self._pool.width,
                    packed_shm_name=self._packed_pool.name,
                    packed_n_slots=self._packed_pool.n_slots,
                    packed_slot_bytes=self._packed_pool.slot_bytes,
                    packed_free_q=self._packed_pool.free_q,
                    io_q=self._pool.io_qs[k],
                    written_count=self._pool.written_count,
                    failed_count=self._pool.failed_count,
                    codec=self._codec,
                    log_level=self._log_level,
                ),
                name=f"io-{io_label}",
                daemon=False,
            )
            io_proc.start()
            log.info("%s io_writer subprocess[%d] pid=%d started",
                     self.label, k, io_proc.pid)
            self._io_procs.append(io_proc)

    # ── line lifecycle ──────────────────────────────────────────────────

    # How long to wait for in-flight pack work to drain before
    # sending LineCloseJob. Each packer's encode is ~285 ms; with
    # the SHM pool fully loaded (24 slots) and 2 packers that's a
    # worst-case ~3.5 s of pack queue. 8 s gives us comfortable
    # headroom, and we log + force-close past it so a stuck packer
    # doesn't strand a line container without an index.
    CLOSE_LINE_DRAIN_TIMEOUT_S = 8.0
    CLOSE_LINE_POLL_S = 0.025

    @staticmethod
    def _build_part_path(target_path: str, part_idx: int) -> str:
        """Insert a ``_pN`` suffix before the ``.bay14line`` extension.

        ``target_path = "...\\Cam..._L1_HHMMSS.bay14line"`` ->
        ``"...\\Cam..._L1_HHMMSS_p0.bay14line"`` for part 0, etc.
        Pure string surgery: the caller (``CameraPipeline.open_line``)
        could compute these via :class:`LineName.full_path(part_idx)`,
        but routing through the same suffix logic here keeps the
        WriterProcessClient agnostic to LineName plumbing.
        """
        suffix = ".bay14line"
        if target_path.endswith(suffix):
            stem = target_path[: -len(suffix)]
            return f"{stem}_p{part_idx}{suffix}"
        return f"{target_path}_p{part_idx}"

    def open_line(self, target_path: str, line_meta: dict) -> bool:
        """Tell every io_writer to start a fresh ``.bay14line`` part-file.

        *target_path* is the BASE path; each io_writer gets a path
        with a ``_pK`` suffix inserted before the extension. So a
        single ``open_line(...)`` call lays N part-files on disk,
        one per writer, all under the same line.

        *line_meta* is embedded in each part's header (camera_serial,
        project, line, capture_kind, line_started_at, etc.).

        Idempotent on a "same path" reopen: if a line is already
        open, we close it first (with the standard drain protocol)
        before opening the new one. Returns True on success, False
        if any io_writer subprocess isn't alive.
        """
        if not self._io_procs or not all(p.is_alive() for p in self._io_procs):
            log.warning("%s open_line(%s) but io_writer(s) not alive",
                        self.label, target_path)
            return False
        if self._line_open:
            # Defensive: parent should normally close before opening
            # a new one. Do it for them so we never silently lose
            # frames mid-line.
            log.info("%s open_line: closing prior line %s before opening %s",
                     self.label, self._current_line_path, target_path)
            self.close_line()
        part_paths = [
            self._build_part_path(target_path, k)
            for k in range(self._n_io_writers)
        ]
        try:
            self._pool.submit_io_per_writer([
                LineOpenJob(
                    target_path=path, line_meta=dict(line_meta),
                )
                for path in part_paths
            ])
        except Exception as exc:
            log.exception("%s open_line submit failed: %s", self.label, exc)
            return False
        self._line_open = True
        self._current_line_paths = part_paths
        return True

    def close_line(self) -> bool:
        """Drain in-flight pack work and tell the io_writer to close
        the current ``.bay14line`` (write index, patch header, fsync,
        close).

        Drain protocol:

        1. The caller is expected to have stopped feeding new
           :class:`WriteJob`\\s before invoking close_line (i.e. the
           AcquisitionThread is in M0/idle, or has paused at a line
           boundary). This method does NOT touch the producer.
        2. Spin until ``submitted_count == processed_count``. That
           equality is the barrier: every WriteJob the parent
           submitted has been popped by a packer, the result has
           been pushed onto io_q, and the slot has been released.
           So when we now push :class:`LineCloseJob` onto io_q, the
           io_writer will consume it AFTER every PackedResult for
           the closing line.
        3. Push LineCloseJob and return. The io_writer flushes the
           index + closes asynchronously; the GUI's written / failed
           counters still tick via the existing telemetry QTimer.

        Returns True even if the drain timed out (we still send the
        close so the io_writer makes a best-effort attempt at the
        index). Returns False if the io_writer subprocess is dead.
        """
        if not self._io_procs or not any(p.is_alive() for p in self._io_procs):
            log.warning("%s close_line: io_writer(s) not alive", self.label)
            self._line_open = False
            self._current_line_paths = []
            return False
        if not self._line_open:
            log.debug("%s close_line: no line is open; ignoring", self.label)
            return True

        deadline = time.monotonic() + self.CLOSE_LINE_DRAIN_TIMEOUT_S
        while time.monotonic() < deadline:
            with self._pool.submitted_count.get_lock():
                submitted = int(self._pool.submitted_count.value)
            with self._pool.processed_count.get_lock():
                processed = int(self._pool.processed_count.value)
            if processed >= submitted:
                break
            time.sleep(self.CLOSE_LINE_POLL_S)
        else:
            log.warning(
                "%s close_line: drain timed out (submitted=%d processed=%d); "
                "sending LineClose anyway so the io_writer(s) make a "
                "best-effort flush",
                self.label, submitted, processed,
            )

        try:
            # LineCloseJob is identical for every writer; submit_io
            # broadcasts to all io_qs so each part-file flushes its
            # index + closes independently.
            self._pool.submit_io(LineCloseJob())
        except Exception as exc:
            log.exception("%s close_line submit failed: %s", self.label, exc)
            self._line_open = False
            self._current_line_paths = []
            return False
        self._line_open = False
        closing_paths = list(self._current_line_paths)
        self._current_line_paths = []
        log.info("%s close_line: LineClose dispatched for %s",
                 self.label, closing_paths)
        return True

    @property
    def line_open(self) -> bool:
        """True if the io_writer currently has a .bay14line open."""
        return self._line_open

    @property
    def current_line_path(self) -> Optional[str]:
        """Path of the FIRST open .bay14line part, or None.

        Kept single-valued for back-compat with the GUI log (it
        only ever shows one identifier per line). The complete
        list is :attr:`current_line_paths`.
        """
        return self._current_line_paths[0] if self._current_line_paths else None

    @property
    def current_line_paths(self) -> list[str]:
        """All open .bay14line part-file paths (one per io_writer)."""
        return list(self._current_line_paths)

    def start_telemetry(self) -> None:
        """Begin polling the cross-process counters and emitting Qt
        signals. Must be called from the thread the QObject lives on
        (typically the GUI thread). Idempotent.

        Separated from :meth:`start` so bring-up code that runs on a
        worker thread can spawn the subprocess at bring-up time, then
        let the GUI thread (which owns the wired-up Qt slots) start
        the timer once it has the pipeline in hand.
        """
        if self._timer is not None:
            return
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll_counters)
        self._timer.start()

    def _poll_counters(self) -> None:
        if self._stopped:
            return
        written = self.written
        failed = self.failed
        if written != self._last_written:
            self._last_written = written
            # basename arg kept for source compat with the old signal.
            self.frame_written.emit(written, "")
        if failed != self._last_failed:
            self._last_failed = failed
            self.write_failed.emit(failed)
            self.write_error.emit(
                f"{self.label}: writer subprocess reported "
                f"{failed} failed write(s) (cumulative)."
            )

    def stop_gracefully(self) -> None:
        """Send each subprocess its shutdown sentinel and stop the timer.

        Order matters:

        1. If a line is open, dispatch :meth:`close_line` so the
           io_writer flushes the index before we ask it to exit.
           Otherwise the file would close mid-stream and recovery
           would be needed on next read.
        2. Send N ``None`` sentinels on ``work_q`` -- each packer
           pops exactly one and exits.
        3. Send one ``None`` on ``io_q`` -- the io_writer drains
           any remaining PackedResults a packer pushed before it
           saw its sentinel, then exits.

        Doesn't block on subprocess joins; call :meth:`wait` if you
        need to be sure they're gone before tearing down the pool.
        """
        if self._stopped:
            return
        self._stopped = True
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        try:
            self._poll_counters_final()
        except Exception:
            log.debug("%s final poll raised", self.label, exc_info=True)
        # 1. flush any open line so its index lands.
        if self._line_open:
            try:
                self.close_line()
            except Exception:
                log.exception("%s shutdown: close_line raised", self.label)
        # 2. one sentinel per packer.
        for i in range(self._n_packers):
            try:
                self._pool.work_q.put_nowait(None)
            except Exception:
                try:
                    self._pool.work_q.put(None, timeout=2.0)
                except Exception:
                    log.warning(
                        "%s could not enqueue packer stop sentinel %d/%d; "
                        "subprocess may not exit cleanly",
                        self.label, i + 1, self._n_packers,
                    )
        # 3. one sentinel per io_writer.
        for k, q in enumerate(self._pool.io_qs):
            try:
                q.put_nowait(None)
            except Exception:
                try:
                    q.put(None, timeout=2.0)
                except Exception:
                    log.warning(
                        "%s could not enqueue io_writer stop sentinel "
                        "%d/%d; subprocess may not exit cleanly",
                        self.label, k + 1, self._n_io_writers,
                    )

    def _poll_counters_final(self) -> None:
        """One last counter sync at shutdown so the GUI shows the true
        final ``written:N`` count instead of the second-to-last one
        the polling timer caught.
        """
        written = self.written
        failed = self.failed
        if written != self._last_written:
            self._last_written = written
            self.frame_written.emit(written, "")
        if failed != self._last_failed:
            self._last_failed = failed
            self.write_failed.emit(failed)

    def wait(self, timeout_ms: int = 15000) -> bool:
        """Wait for all writer subprocesses (packers + io_writer) to
        exit. Returns True on clean exit of every subprocess.
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        clean = True
        all_procs: list[tuple[str, mp.Process]] = [
            (f"packer[{i}]", p) for i, p in enumerate(self._packer_procs)
        ]
        for k, p in enumerate(self._io_procs):
            all_procs.append((f"io_writer[{k}]", p))
        for label, p in all_procs:
            remaining = max(0.0, deadline - time.monotonic())
            p.join(timeout=remaining)
            if p.is_alive():
                log.warning("%s %s did not exit within deadline; terminating",
                            self.label, label)
                p.terminate()
                p.join(timeout=2.0)
                if p.is_alive():
                    log.error("%s %s still alive after terminate; killing",
                              self.label, label)
                    p.kill()
                    p.join(timeout=2.0)
                clean = False
        return clean


# ---------------------------------------------------------------------------


class CameraPipeline:
    """Coordinator for one camera: backend + acquisition + writer subprocess.

    Stage 4 (current): full-resolution sweep capture is enabled. The
    SDK callback delivers ``(small, slot_idx_or_None)`` tuples to
    :class:`AcquisitionThread`. When the GUI has flipped
    :meth:`set_full_res_emission` on (true while the PCB is in M0/M1),
    the SDK callback grabs a slot from :attr:`pool` and copies the raw
    bytes into the SharedMemory mapping; the AcquisitionThread builds a
    :class:`WriteJob` and submits it via :class:`WriterProcessClient`,
    which forwards it to a child process for the actual TIFF write.
    M4 normally keeps full-res emission off for the cheap decimated focus
    tap; **Fine** sub-mode turns emission on for the active camera only so
    the acquisition thread can pick and demosaic a full-res tile.

    Stage 5 (future): GPS correlation. AcquisitionThread already pulls
    a fix from the ``gps_provider`` callback at submit time; once the
    antenna is wired in we just plumb a real provider through here.
    """

    WRITER_ENABLED = True

    @staticmethod
    def _resolve_capture_codec() -> str:
        """Pick the packer/io_writer codec from the environment.

        Recognised values for ``BAY14_CAPTURE_CODEC``:

        * ``none`` (default) -- no compression. Each frame is a raw uint16
          plane (~243 MB); the packer does a single SHM->SHM memcpy.
        * ``blosc2-zstd1-shuffle`` -- Blosc2 SHUFFLE + ZSTD-1,
          ~2.5x ratio at significant CPU cost (see env
          ``BAY14_BLOSC2_NTHREADS``).

        Unrecognised values fall back to ``none`` with a logged warning.
        We resolve once per pipeline (per camera) so a late-bound env var
        change can't desync packer / io_writer.
        """
        from .raw_bay_writer import (
            COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
            COMPRESSION_NONE,
        )
        raw = os.environ.get("BAY14_CAPTURE_CODEC", "").strip().lower()
        if not raw:
            return COMPRESSION_NONE
        if raw in (COMPRESSION_NONE,):
            return COMPRESSION_NONE
        if raw in (COMPRESSION_BLOSC2_ZSTD1_SHUFFLE, "blosc2", "zstd"):
            return COMPRESSION_BLOSC2_ZSTD1_SHUFFLE
        log.warning(
            "BAY14_CAPTURE_CODEC=%r not recognised; "
            "falling back to %s",
            raw,
            COMPRESSION_NONE,
        )
        return COMPRESSION_NONE

    def __init__(
        self,
        backend: KyfgCamera,
        config: CameraConfig,
        mode_controller: ModeController,
        context: CaptureContext,
        get_drive_root: Callable[[], Optional[str]],
        gps_provider: Optional[GpsProvider] = None,
        heading_provider: Optional[HeadingProvider] = None,
        focus_provider: Optional[FocusProvider] = None,
    ) -> None:
        self._backend = backend
        self._config = config
        self._modes = mode_controller
        self._context = context
        self._get_drive_root = get_drive_root
        self._armed = False

        self.sweep = SweepCounter()
        # SDK callback drops ``(small, slot_idx_or_None)`` tuples here;
        # AcquisitionThread drains. ``maxsize=4`` is enough for a brief
        # GUI hiccup without backing the SDK callback up. With the
        # SharedMemory pool the second tuple element is just an int
        # (or None), so this queue's memory footprint is negligible --
        # the heavy frame data lives in the pool itself.
        self.frame_queue: queue.Queue[tuple | None] = queue.Queue(maxsize=4)

        # Per-camera SharedMemory pool + writer subprocess. We allocate
        # the pool BEFORE constructing AcquisitionThread (so it has a
        # valid pool reference) but we don't start the writer
        # subprocess until ``start()`` is called, so a CameraPipeline
        # that's constructed-but-never-armed (e.g. bring-up failed for
        # this serial) doesn't leave a zombie writer process around.
        self.pool: Optional[FrameBufferPool] = None
        # Dedicated SHM ring for the compressed-payload bytes that the
        # packer hands to the io_writer. See PACKED_POOL_SLOTS comment
        # at module top + the PackedFramePool docstring for why.
        self.packed_pool: Optional[PackedFramePool] = None
        self.writer: Optional[WriterProcessClient] = None
        if self.WRITER_ENABLED:
            # SharedMemory names must be globally unique on Windows.
            # Embed the pid + camera serial so two app instances don't
            # collide.
            shm_name = f"tbf_pool_p{os.getpid()}_{config.camera_serial}"
            self.pool = FrameBufferPool(
                name=shm_name,
                n_slots=SHM_POOL_SLOTS,
                height=EXPECTED_FRAME_HEIGHT,
                width=EXPECTED_FRAME_WIDTH,
                n_io_writers=WriterProcessClient.DEFAULT_N_IO_WRITERS,
            )
            # Wire the pool into the backend so its SDK callback can
            # grab slots directly. Without this the backend falls back
            # to allocating fresh ndarrays per frame, which we no
            # longer have a writer path for.
            self._backend.set_full_res_pool(self.pool)
            packed_shm_name = (
                f"tbf_packed_p{os.getpid()}_{config.camera_serial}"
            )
            packed_slot_bytes = (
                EXPECTED_FRAME_HEIGHT * EXPECTED_FRAME_WIDTH * 2
                + PACKED_POOL_SLOT_HEADROOM_BYTES
            )
            self.packed_pool = PackedFramePool(
                name=packed_shm_name,
                n_slots=PACKED_POOL_SLOTS,
                slot_bytes=packed_slot_bytes,
            )
            self.writer = WriterProcessClient(
                camera_number=config.camera_number,
                camera_serial=config.camera_serial,
                pool=self.pool,
                packed_pool=self.packed_pool,
                codec=self._resolve_capture_codec(),
            )

        self.acq = AcquisitionThread(
            camera_number=config.camera_number,
            camera_serial=config.camera_serial,
            frame_queue=self.frame_queue,
            sweep=self.sweep,
            modes=mode_controller,
            context=context,
            get_drive_root=get_drive_root,
            pool=self.pool,
            gps_provider=gps_provider,
            heading_provider=heading_provider,
            focus_provider=focus_provider,
            full_res_enabled=lambda b=self._backend: b.full_res_emission,
            line_open_check=lambda: self.line_open,
        )
        # Counter for SDK-callback frames we had to drop because the
        # AcquisitionThread was draining slower than the SDK delivered.
        # Useful diagnostic but should be 0 in steady state at 4 Hz.
        self._dropped_full_queue = 0

    @property
    def camera_number(self) -> int:
        return self._config.camera_number

    @property
    def camera_serial(self) -> str:
        return self._config.camera_serial

    @property
    def backend(self) -> KyfgCamera:
        return self._backend

    def read_exposure_time_us(self) -> Optional[float]:
        return self._backend.read_exposure_time_us()

    def read_gain(self) -> Optional[float]:
        return self._backend.read_gain()

    def set_exposure_time_us(self, us: float) -> tuple[bool, str]:
        return self._backend.set_exposure_time_us(us)

    def set_gain(self, gain: float) -> tuple[bool, str]:
        return self._backend.set_gain(gain)

    # -- backend SDK callback ----------------------------------------------

    def _on_backend_frame(
        self,
        small_u16: np.ndarray,
        full_payload: Any,
    ) -> None:
        """Called from the KAYA SDK callback thread, once per buffer.

        ``small_u16`` is always a fresh decimated copy (~8 MB) used for
        preview + focus tap. ``full_payload`` is either:

        * ``None``: full-res emission off (M4 / no writer wanted).
        * ``int``: a SharedMemory slot index that the SDK callback
          already filled with the full-res bytes. AcquisitionThread
          will hand it to the writer subprocess (or, if the GUI has
          flipped out of M0/M1 between the callback and us, return
          the slot to free_q).
        * ``ndarray``: legacy path, only fires if
          ``set_full_res_pool`` was never called. We accept it for
          backward compat but the writer subprocess can't consume
          ndarrays directly, so AcquisitionThread will drop these
          and account.

        Must be fast: drop the tuple into the queue and return. If the
        queue is full (AcquisitionThread is behind), discard the tuple
        and immediately return any held slot to the pool so the SDK
        callback's next iteration has a slot waiting.
        """
        try:
            self.frame_queue.put_nowait((small_u16, full_payload))
        except queue.Full:
            self._dropped_full_queue += 1
            # If we held a slot, return it to free_q immediately --
            # otherwise it would leak (the next callback would find
            # free_q one shorter) and the pool would shrink each time
            # the GUI fell behind.
            if isinstance(full_payload, (int, np.integer)) and self.pool is not None:
                self.pool.release_slot(int(full_payload))
                # A full-res slot was in hand, so this was a recording frame. The acquisition
                # thread being behind is the one failure the live w10 scorer could cause (GIL).
                self.acq.w10_note_drop()

    # -- lifecycle ---------------------------------------------------------

    def configure_camera(self, log_sink: Any = None) -> None:
        """Apply trigger / exposure / pixel-format setup via the backend.

        Already done by :meth:`KyfgCamera.configure` during ``open_all``;
        this method is kept as a no-op for compatibility with existing GUI
        code and gives us a hook for re-applying config in the future.
        """
        if log_sink:
            for line in self._backend.info and []:
                pass  # placeholder

    def start(self, log_sink: Any = None) -> None:
        """Boot the long-lived workers. Does NOT arm the backend.

        Spawns the writer subprocess (if enabled), starts the focus
        writer thread, and starts AcquisitionThread. AcquisitionThread
        runs continuously but its ``_armed`` gate keeps it idling
        until :meth:`arm` is called.
        """
        if self.WRITER_ENABLED:
            assert self.writer is not None
            self.writer.start()
            if log_sink:
                log_sink(
                    f"cam{self.camera_number}[{self.camera_serial}]: "
                    "writer subprocess started; pool="
                    f"{SHM_POOL_SLOTS} slots ({EXPECTED_FRAME_HEIGHT}x"
                    f"{EXPECTED_FRAME_WIDTH} uint16)."
                )
        elif log_sink:
            log_sink(
                f"cam{self.camera_number}[{self.camera_serial}]: "
                "writer disabled (preview-only mode)"
            )
        self.acq.start()

    @property
    def is_armed(self) -> bool:
        """True iff the SDK is currently streaming this camera.

        Read by :class:`MainWindow._on_mode_clicked` so it can re-arm
        any cameras that aren't ready before sending ``$Mn*`` to the
        PCB. Avoids the start-boundary frame loss where the PCB
        starts triggering against a sensor that hasn't fully
        completed its arm sequence.
        """
        return self._armed

    def arm(self, log_sink: Any = None) -> None:
        """Tell the KYFGLib backend to start the data stream and arm the
        sensor for external Line1 triggers from the PCB.

        Safe to call when already armed (no-op).
        """
        # Diagnostic: show one line of the call site so we can see in the
        # log whether arms/disarms are coming from the GUI thread (the
        # expected source) or from somewhere unexpected.
        import traceback
        caller = traceback.extract_stack(limit=3)[-2]
        log.info(
            "cam%d[%s].arm() called from %s:%d (%s)",
            self.camera_number, self.camera_serial,
            os.path.basename(caller.filename), caller.lineno, caller.name,
        )
        if self._armed:
            return
        # Drop any stale frames left over from a previous arm session
        # before the SDK callback starts pushing new ones.
        while True:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._backend.arm(self._on_backend_frame)
        except Exception as exc:
            if log_sink:
                log_sink(
                    f"cam{self.camera_number}[{self.camera_serial}] arm: "
                    f"{exc}"
                )
            return
        self.acq.set_armed(True)
        self._armed = True
        if log_sink:
            log_sink(
                f"cam{self.camera_number}[{self.camera_serial}]: armed"
            )

    def reset_counters(self) -> None:
        """Zero acq / dropped / written counters and the sweep position.

        Called from the GUI after camera-recovery completes. The two
        sources of truth are :class:`AcquisitionThread` (acq + dropped,
        in-process) and :class:`WriterProcessClient` (written + failed,
        cross-process via ``mp.Value``); we ask both to reset and they
        re-emit zero-valued signals so the per-camera labels flip
        immediately. We also reset the per-camera ``SweepCounter`` so
        the ``S{n} F{m}`` label restarts at ``S1 F1`` on the next
        position read -- otherwise the position would resume mid-sweep
        from wherever the producer left off, which is misleading
        after a hardware-level reset.

        Counter for SDK-callback drops (``_dropped_full_queue``) is
        zeroed too for consistency, even though it isn't surfaced in
        a label.
        """
        self.acq.request_counter_reset()
        if self.writer is not None:
            self.writer.reset_counters()
        self.sweep.reset()
        self._dropped_full_queue = 0

    def disarm(self, log_sink: Any = None) -> None:
        """Tell the backend to stop streaming. AcquisitionThread keeps
        running but idles.
        """
        import traceback
        caller = traceback.extract_stack(limit=3)[-2]
        log.info(
            "cam%d[%s].disarm() called from %s:%d (%s)",
            self.camera_number, self.camera_serial,
            os.path.basename(caller.filename), caller.lineno, caller.name,
        )
        if not self._armed:
            return
        # Stop the consumer first so it doesn't trip on a stale frame
        # arriving from the SDK after we've torn down. AcquisitionThread's
        # idle slice is 100 ms, so 200 ms is plenty of margin.
        self.acq.set_armed(False)
        self._armed = False
        time.sleep(0.2)
        try:
            self._backend.disarm()
        except Exception as exc:
            if log_sink:
                log_sink(
                    f"cam{self.camera_number}[{self.camera_serial}] disarm: "
                    f"{exc}"
                )
        # Wake up AcquisitionThread.run() if it was blocked on get(),
        # so it can notice the disarm immediately.
        try:
            self.frame_queue.put_nowait(None)
        except queue.Full:
            pass
        if log_sink:
            log_sink(
                f"cam{self.camera_number}[{self.camera_serial}]: disarmed"
            )

    def stop(self) -> None:
        """Final teardown: disarm, halt threads, kill subprocess, close backend.

        Order matters:

        1. Disarm the camera so no more SDK callbacks fire.
        2. Stop AcquisitionThread (it's the producer for both pool's
           work_q and frame_queue). Wait for it to exit before we tear
           down the writer subprocess so we don't pull a slot from
           under it.
        3. Tell the writer subprocess to drain (sentinel) and wait
           for join.
        4. Tear down the focus writer thread.
        5. Close the backend camera handle.
        6. Tear down the SharedMemory pool (must happen AFTER the
           subprocess has exited -- otherwise unlinking a still-mapped
           region leaks the kernel mapping until reboot on Windows).
        """
        self.disarm()
        self.acq.stop_gracefully()
        try:
            self.frame_queue.put_nowait(None)
        except queue.Full:
            pass
        self.acq.wait(15_000)
        if self.WRITER_ENABLED:
            assert self.writer is not None
            self.writer.stop_gracefully()
            self.writer.wait(15_000)
        try:
            self._backend.close()
        except Exception:
            log.warning("backend close failed for cam%d[%s]",
                        self.camera_number, self.camera_serial,
                        exc_info=True)
        # Pool teardown comes last so the subprocess's SharedMemory
        # attach has already been released by the join above. Both
        # the raw + packed pools follow the same lifecycle: parent
        # owns the SharedMemory block, children attach by name; the
        # subprocess join above ensures every attach is released
        # before we ``unlink`` here.
        if self.pool is not None:
            try:
                self.pool.shutdown()
            except Exception:
                log.warning("pool shutdown failed for cam%d[%s]",
                            self.camera_number, self.camera_serial,
                            exc_info=True)
            self.pool = None
        if self.packed_pool is not None:
            try:
                self.packed_pool.shutdown()
            except Exception:
                log.warning("packed_pool shutdown failed for cam%d[%s]",
                            self.camera_number, self.camera_serial,
                            exc_info=True)
            self.packed_pool = None

    # -- line lifecycle ------------------------------------------------------

    def open_line(self) -> bool:
        """Open a fresh ``.bay14line`` for this camera.

        Reads the current drive root, project, and line number from
        the pipeline's context. Builds the line name with a
        wall-clock ``HHMMSS`` suffix (so re-using a line number
        creates a new file, never overwrites). Forwards the path +
        line metadata to the io_writer subprocess.

        Returns False if the writer isn't enabled, the drive isn't
        ready, the project name is empty, or the io_writer is dead.
        """
        if not self.WRITER_ENABLED or self.writer is None:
            return False
        drive_root = self._get_drive_root()
        project_raw = self._context.project
        if not drive_root or not (project_raw or "").strip():
            return False
        when = _dt.datetime.now()
        # An M4 bracket and a mapping line are the same container format on the
        # same drive; only the intent differs, and only one of them is training
        # data. Mark it in BOTH the name and the header -- the name is what the
        # operator sorts by, the header is what survives a rename.
        is_focus = self._modes.m4_recording()
        try:
            lname = build_line_name(
                drive_root=drive_root,
                project_raw=project_raw,
                camera_serial=self.camera_serial,
                line=int(self._context.line),
                when=when,
                focus=is_focus,
                camera_number=self.camera_number,
            )
            ensure_directory(lname)
            target_path = lname.full_path()
        except Exception as exc:
            log.exception("cam%d open_line: build failed: %s",
                          self.camera_number, exc)
            return False
        line_meta = {
            "camera_serial": self.camera_serial,
            "camera_number": self.camera_number,
            "project": lname.project,
            "line": lname.line,
            "capture_kind": "focus" if is_focus else "sweep",
            "line_started_at": when.isoformat(timespec="milliseconds"),
            "genicam_pixel_format": self._backend.pixel_format or "",
        }
        # Reset the per-line sweep counter so the new container
        # starts at sweep 1 frame 1.
        self.sweep.reset()
        return self.writer.open_line(target_path, line_meta)

    def close_line(self) -> bool:
        """Close this camera's currently-open ``.bay14line``, if any.

        Drains in-flight pack work first (see
        :meth:`WriterProcessClient.close_line`).
        """
        if not self.WRITER_ENABLED or self.writer is None:
            return False
        return self.writer.close_line()

    @property
    def line_open(self) -> bool:
        """True if this camera currently has a .bay14line open."""
        return bool(self.writer and self.writer.line_open)

    # -- taps ----------------------------------------------------------------

    def set_preview(self, enabled: bool) -> None:
        self.acq.set_preview_enabled(enabled)

    def set_preview_max_edge(self, max_edge: int) -> None:
        self.acq.set_preview_max_edge(max_edge)

    def set_preview_zoom(self, enabled: bool) -> None:
        """Toggle the 100% full-resolution centre-crop preview (M0/M1)."""
        self.acq.set_preview_zoom(enabled)

    def set_preview_zoom_size(self, width: int, height: int) -> None:
        """Set the zoom-preview crop size in sensor pixels (≈ preview window)."""
        self.acq.set_preview_zoom_size(width, height)

    def set_preview_gain(self, gain: float) -> None:
        """Display-only brightness multiplier for the preview (never recorded)."""
        self.acq.set_preview_gain(gain)

    def set_focus_tap(self, enabled: bool) -> None:
        """Enable/disable raw-frame emission for in-app tile sharpness analysis."""
        self.acq.set_focus_tap_enabled(enabled)

    def set_full_res_emission(self, enabled: bool) -> None:
        """Tell the backend whether to copy a full-resolution frame on
        every SDK callback.

        Flipped on by the GUI when entering M0/M1 (frames will be
        written), and back off in M4 / UNKNOWN **unless** the w10 AF (or
        M4 Record) needs it for the active camera. Safe to call from
        the GUI thread; the worker thread reads the flag atomically.
        """
        self._backend.set_full_res_emission(bool(enabled))

    def set_preview_rois(self, rois: list) -> None:
        """w10 AF: pin the full-res ROIs (sensor pixels, ``roi_boxes`` order) cropped
        from the held slot each frame; ``[]`` releases them."""
        self.acq.set_preview_rois(rois)

    def note_preview_crops_consumed(self) -> None:
        """GUI-thread ack releasing the AF crop conflation latch."""
        self.acq.note_preview_crops_consumed()

    def set_fine_roi(self, roi: Optional[tuple]) -> None:
        """Fine view: pin the tracked full-res ROI (sensor pixels), or ``None`` to stop."""
        self.acq.set_fine_roi(roi)

    def note_fine_crop_consumed(self) -> None:
        """GUI-thread ack releasing the Fine crop conflation latch."""
        self.acq.note_fine_crop_consumed()
