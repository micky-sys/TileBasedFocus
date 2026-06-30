"""Raw 14-bit-packed Bayer writer for sweep frames.

Why this module exists
----------------------
Per-camera U.2 SSD sustained-write bandwidth was measured at
~1.07 GB/s while the live producer demands ~1.108 GB/s when sweep
frames are stored as uncompressed uint16 TIFFs. We were 4 % under
the ceiling and dropping ~30 % of frames once the pool filled. The
camera is 14-bit, but we were widening to 16 bits per pixel for TIFF
compatibility -- 12.5 % of every byte we wrote was a wasted high-2-
bits zero.

This module persists each frame to disk as **raw 14-bit packed bytes
plus a JSON sidecar**, dropping per-frame size from 256 MB to
~213 MiB (12.5 % less). At observed drive throughput, that pulls
sustained write demand to ~970 MB/s -- below the drive ceiling, with
~10 % headroom. No drops.

Pack backend (priority order)
-----------------------------
1. **numba (preferred, fastest)** -- a JIT-compiled tight loop that
   packs at ~70 ms per 256 MB frame single-threaded, ~30 ms with 2
   threads, ~15 ms with all-cores parallel. JIT compile happens
   once per process on first call; numba caches the compiled
   artefact to disk, so subsequent processes pay only the cache-load
   cost (~30 ms).
2. **imagecodecs (fast Cython baseline)** -- ~210-300 ms per frame.
   Used when numba isn't installed; the pack format is byte-for-byte
   identical to numba's output (verified by ``tools/bench_numba_pack.py``).
3. **numpy (correctness fallback)** -- ~520 ms per frame. Used only
   when neither numba nor imagecodecs is installed; the module
   imports cleanly in dev / CI environments without optional deps.

All three paths produce the same on-disk bytes: the GenICam
"LSB-first continuous bitstream" packing scheme used by industrial
cameras for ``MONO12p``/``MONO14p`` formats.

CPU thread budget
-----------------
With N writer subprocesses per camera × 3 cameras, a numba @njit
function defaults to using all available cores per call -- so 6
subprocesses each spawning 28 threads = 168 threads competing.
That's wasteful. We pin numba's per-process thread count to a
small number (configurable via ``NUMBA_NUM_THREADS_PER_WRITER``,
default 2) which is enough to drop pack time well below the
producer budget without saturating the CPU.

File layout
-----------
For each sweep frame we write two files under
``<drive>:\\<project>\\``::

    Cam{SN}_{project}_{YYYYMMDD}_L{line}_{frame}.bay14
    Cam{SN}_{project}_{YYYYMMDD}_L{line}_{frame}.bay14.json

* ``.bay14`` -- packed pixel bytes only. No header, no padding.
  Total size: ``H * W // 4 * 7`` bytes for an HxW 14-bit Bayer frame.
* ``.bay14.json`` -- ASCII JSON sidecar with everything a downstream
  reader needs (geometry, bit depth, Bayer pattern, capture
  metadata, GPS fix). The sidecar makes ``.bay14`` self-describing
  without complicating the binary path.

Pack format
-----------
Little-endian, LSB-first. 4 consecutive pixels (each storing a
14-bit value in the low 14 bits of a uint16) become 7 bytes::

    byte 0 = p0[ 7: 0]
    byte 1 = p0[13: 8] | (p1[ 1: 0] << 6)
    byte 2 = p1[ 9: 2]
    byte 3 = p1[13:10] | (p2[ 3: 0] << 4)
    byte 4 = p2[11: 4]
    byte 5 = p2[13:12] | (p3[ 5: 0] << 2)
    byte 6 = p3[13: 6]

This is a standard "packed-14" representation. ``imagecodecs``-style
``packints`` routines decode it directly; our ``unpack_u14`` below
is a numpy implementation for the offline reconstruction tool.

The pixel count must be divisible by 4. Our 13400 x 9528 sensor has
127,568,000 pixels = 31,892,000 groups of 4 = 223,244,000 packed
bytes (~213 MiB / 223 MB). 13400 itself is divisible by 4 so each
*row* is packable independently if a future caller wants per-row
streaming.

Performance
-----------
Pack itself is 7 vectorised numpy bit operations on ~32 M-element
uint16 arrays. On the rig's CPU that runs in ~50-100 ms per frame.
Write time at 1 GB/s for 213 MiB is ~213 ms. Well under the per-
producer-frame budget of 230 ms (1/4.33 fps).

The writer subprocess in ``writer_proc.py`` copies the SharedMemory
slot to a private ndarray BEFORE packing so it can return the slot
to the pool's free queue immediately, freeing it for the next SDK
callback after only ~80 ms instead of after the full ~300 ms write.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Optional

import numpy as np


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File pre-allocation
# ---------------------------------------------------------------------------
#
# Each ``LineWriter`` writes one ~244 MiB chunk per frame (and at 4.33 fps,
# 0.5-3 minutes per line). Without pre-allocation, NTFS has to extend the
# file's MFT entry on every ``f.write()`` call -- live profiling showed the
# io_writer running at ~470 MiB/s/stream vs ~780 MiB/s/stream in the disk
# bench, a ~40 % shortfall that's enough to cause back-pressure into the
# SHM pool over ~50 s of sustained capture.
#
# Pre-allocating once on file open eliminates the per-write extension. On
# Windows we use ``SetEndOfFile`` directly so the extend does NOT zero-fill
# the new region (sequential writes from the previous EOF naturally extend
# the file's "valid data length" without ever reading the unwritten gap, so
# we don't need the privileged ``SetFileValidData``). On POSIX we fall back
# to ``os.posix_fallocate`` when present, otherwise to ``os.ftruncate``.
#
# On close, the writer truncates back to actual size so partially-filled
# lines don't waste disk.

def _prealloc_file_handle(fd: int, size_bytes: int) -> None:
    """Reserve ``size_bytes`` of disk for the file behind ``fd``.

    No zero-fill on Windows: ``SetEndOfFile`` only updates the NTFS
    allocation + EOF marker. Subsequent sequential writes naturally extend
    the file's valid-data-length without reading the unwritten gap, so
    reads past valid-data return zeros even though the allocated clusters
    may hold stale data.

    On POSIX, prefers ``posix_fallocate`` (reserves clusters without
    zero-fill on filesystems that support it), falling back to
    ``ftruncate``.
    """
    if size_bytes <= 0:
        return
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        handle = msvcrt.get_osfhandle(fd)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        kernel32.SetFilePointerEx.restype = wintypes.BOOL
        kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
        kernel32.SetEndOfFile.restype = wintypes.BOOL
        FILE_BEGIN = 0
        new_pos = ctypes.c_longlong(0)
        ok = kernel32.SetFilePointerEx(
            wintypes.HANDLE(handle),
            ctypes.c_longlong(int(size_bytes)),
            ctypes.byref(new_pos),
            wintypes.DWORD(FILE_BEGIN),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, f"SetFilePointerEx({size_bytes}) failed")
        ok = kernel32.SetEndOfFile(wintypes.HANDLE(handle))
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, "SetEndOfFile failed")
        return
    # POSIX
    fallocate = getattr(os, "posix_fallocate", None)
    if fallocate is not None:
        fallocate(fd, 0, int(size_bytes))
        return
    os.ftruncate(fd, int(size_bytes))


# ---------------------------------------------------------------------------
# Direct I/O writer (Windows FILE_FLAG_NO_BUFFERING)
# ---------------------------------------------------------------------------
#
# Buffered I/O (the default for Python's ``open``) routes every write
# through the Windows system page cache. On this rig, with 6 io_writers
# pumping ~410 MiB/s each into the cache, the modified-page-writer can't
# drain it fast enough; live ``f.write`` ends up running at ~half the
# raw drive speed even though the same drive benchmarks at ~775 MiB/s
# per stream under similar load.
#
# ``FILE_FLAG_NO_BUFFERING`` bypasses the page cache entirely and lets
# the io_writer issue direct DMA writes. The trade-off is strict
# alignment requirements:
#
#   * The OS file pointer at every ``WriteFile`` call must be a multiple
#     of the disk's sector size (typically 4096 B on modern NVMe).
#   * The byte count of every ``WriteFile`` call must be a multiple of
#     the sector size.
#   * The source buffer pointer must be a multiple of the sector size
#     (page-aligned in practice; ``VirtualAlloc`` returns 64 KiB aligned
#     pages on Windows, ``SharedMemory.buf`` is similarly page-aligned).
#
# To satisfy these without breaking the ``.bay14line`` chunked format,
# we pad each chunk header AND payload up to the sector boundary. The
# index records the *real* payload length, so the offline reader still
# unpacks only the valid bytes; the padding lives in the gap between
# (payload_end) and (next_chunk_start). The file header gains an
# ``alignment`` field so :func:`recover_line_chunks` knows how far to
# round up its sequential walk.
#
# Header patching and index appending happen via a regular buffered
# handle that's opened AFTER the direct-I/O handle closes (the patch
# offset 12 is not sector-aligned, so it can't go through the
# no-buffering handle).

_DIRECT_IO_SECTOR_SIZE = int(
    os.environ.get("BAY14_DIRECT_IO_SECTOR", "4096")
)


def _align_up(n: int, alignment: int) -> int:
    return ((int(n) + alignment - 1) // alignment) * alignment


def _aligned_zero_buffer(size_bytes: int, alignment: int = 4096) -> bytearray:
    """Allocate a zero-filled bytearray whose underlying buffer is at least
    ``alignment``-aligned. On Windows, large CPython allocations come from
    ``VirtualAlloc`` and are 64 KiB-aligned; that satisfies the typical
    4 KiB sector requirement. We force size to be a multiple of
    ``alignment`` so the buffer can be used directly for sector-aligned
    WriteFile calls.
    """
    sz = _align_up(size_bytes, alignment)
    return bytearray(sz)


class _DirectIOFile:
    """Minimal Win32 wrapper around a FILE_FLAG_NO_BUFFERING file handle.

    All ``write(buf)`` calls go straight to ``WriteFile``; the caller is
    responsible for ensuring ``len(buf)`` is a multiple of
    :attr:`sector_size` and that the file pointer is sector-aligned
    after each call.

    The handle exposes :meth:`set_end_of_file` for pre-allocation and
    :meth:`logical_position` so the LineWriter can track where it is
    inside the file without doing a kernel ``tell``.
    """

    def __init__(self, path: str, sector_size: int = _DIRECT_IO_SECTOR_SIZE):
        if sys.platform != "win32":
            raise OSError(
                "_DirectIOFile is only implemented on Windows"
            )
        import ctypes
        from ctypes import wintypes

        self._path = path
        self.sector_size = int(sector_size)
        self._pos = 0
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.WriteFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        self._kernel32.WriteFile.restype = wintypes.BOOL
        self._kernel32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE, ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD,
        ]
        self._kernel32.SetFilePointerEx.restype = wintypes.BOOL
        self._kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
        self._kernel32.SetEndOfFile.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        CREATE_ALWAYS = 2
        FILE_FLAG_NO_BUFFERING = 0x20000000
        FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
        FILE_FLAG_WRITE_THROUGH = 0x80000000
        FILE_ATTRIBUTE_NORMAL = 0x00000080
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        # WRITE_THROUGH alongside NO_BUFFERING tells the storage stack to
        # complete the I/O only after the drive acknowledges; on NVMe this
        # is essentially free (the controller has battery-backed cache)
        # and it guarantees crash-consistency for the bytes we report as
        # written.
        flags = (
            FILE_FLAG_NO_BUFFERING
            | FILE_FLAG_SEQUENTIAL_SCAN
            | FILE_FLAG_WRITE_THROUGH
            | FILE_ATTRIBUTE_NORMAL
        )
        h = self._kernel32.CreateFileW(
            path, GENERIC_WRITE, FILE_SHARE_READ, None,
            CREATE_ALWAYS, flags, None,
        )
        if h is None or h == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            raise OSError(
                err,
                f"CreateFileW({path!r}, FILE_FLAG_NO_BUFFERING) failed",
            )
        self._handle = h

    @property
    def logical_position(self) -> int:
        return self._pos

    def write(self, buf) -> None:
        """Issue one ``WriteFile`` for ``buf``. ``len(buf)`` must be a
        multiple of :attr:`sector_size`; the buffer's underlying address
        must also be sector-aligned (page-aligned heap allocations,
        SharedMemory views, and numpy arrays backed by ``VirtualAlloc``
        all satisfy this on Windows).

        Accepts any object that implements the buffer protocol
        (``bytes``, ``bytearray``, ``memoryview``, ``numpy.ndarray``,
        ``ctypes`` array). Uses ``ctypes.c_ubyte.from_buffer`` to extract
        a stable pointer with zero copies for writable buffers; falls
        back to ``from_buffer_copy`` for read-only buffers like ``bytes``.
        """
        import ctypes
        from ctypes import wintypes

        n = len(buf)
        if n == 0:
            return
        if n % self.sector_size != 0:
            raise ValueError(
                f"_DirectIOFile.write size {n} is not a multiple of "
                f"sector_size {self.sector_size}"
            )

        try:
            ptr = ctypes.addressof((ctypes.c_ubyte * n).from_buffer(buf))
            keepalive = None
        except TypeError:
            # ``bytes`` and read-only memoryviews don't satisfy
            # ``from_buffer``; copy into a stable ctypes array. This
            # only happens for header / index writes, never for the hot
            # SHM payload (which is a writable bytearray).
            tmp = (ctypes.c_ubyte * n).from_buffer_copy(bytes(buf))
            ptr = ctypes.addressof(tmp)
            keepalive = tmp  # keep the temp buffer alive across the call

        bytes_written = wintypes.DWORD(0)
        ok = self._kernel32.WriteFile(
            self._handle, ctypes.c_void_p(ptr), wintypes.DWORD(n),
            ctypes.byref(bytes_written), None,
        )
        # ``keepalive`` deliberately survives until after WriteFile returns.
        del keepalive
        if not ok or bytes_written.value != n:
            err = ctypes.get_last_error()
            raise OSError(
                err,
                f"WriteFile failed: wrote {bytes_written.value}/{n} "
                f"bytes (path={self._path!r}, pos={self._pos})",
            )
        self._pos += n

    def set_end_of_file(self, size_bytes: int) -> None:
        """Pre-allocate ``size_bytes``. Returns immediately (no zero-fill)
        but reserves NTFS clusters."""
        import ctypes
        from ctypes import wintypes

        # Move the file pointer to the requested size, then commit.
        new_pos = ctypes.c_longlong(0)
        ok = self._kernel32.SetFilePointerEx(
            self._handle, ctypes.c_longlong(int(size_bytes)),
            ctypes.byref(new_pos), wintypes.DWORD(0),  # FILE_BEGIN
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, "SetFilePointerEx failed")
        ok = self._kernel32.SetEndOfFile(self._handle)
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, "SetEndOfFile failed")
        # Restore the file pointer to where the writer thinks it is.
        ok = self._kernel32.SetFilePointerEx(
            self._handle, ctypes.c_longlong(int(self._pos)),
            ctypes.byref(new_pos), wintypes.DWORD(0),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, "SetFilePointerEx (restore) failed")

    def close(self) -> None:
        if self._handle is None:
            return
        ok = self._kernel32.CloseHandle(self._handle)
        self._handle = None
        if not ok:
            import ctypes
            err = ctypes.get_last_error()
            raise OSError(err, "CloseHandle failed")


# Default number of CPU threads numba's @njit(parallel=True) loops
# may use *per writer subprocess*. With six writer subprocesses
# across three cameras, leaving numba on its default (=hardware
# thread count, ~28) would create ~168 contending threads. 2 is
# the empirical sweet spot: pack drops to ~30-40 ms (3x the
# single-thread number) while system thread pressure stays modest.
# Override via the ``NUMBA_NUM_THREADS_PER_WRITER`` env var.
NUMBA_NUM_THREADS_PER_WRITER = int(
    os.environ.get("NUMBA_NUM_THREADS_PER_WRITER", "2")
)


# numba (preferred) -- compiled on first call per process.  We keep
# ``cache=False`` on the @njit below: with 9 writer subprocesses
# starting at once, ``cache=True`` makes every child race to finalize
# the same ``__pycache__/*.nbc`` path.  On Windows + Google Drive (or
# any sync / odd FS) that surfaces as ``FileExistsError`` on
# ``os.replace`` and spams ERROR logs; it does not affect live
# preview, but it confuses operators.  Disk cache was only a minor
# startup win -- writers run long enough that one-time JIT cost is fine.
try:
    from numba import njit, prange, set_num_threads
    _HAVE_NUMBA = True
except ImportError:  # pragma: no cover -- exercised only on bare envs
    _HAVE_NUMBA = False


# imagecodecs (fallback) ships a fast Cython packints encoder/decoder
# that is ~2.5x faster than our numpy fallback and matches the wire
# format industrial cameras use for ``MONO14p`` / ``MONO12p``
# (GenICam LSB-first continuous bitstream, ``bitorder='<'``).
try:
    from imagecodecs import packints_encode as _ic_packints_encode
    from imagecodecs import packints_decode as _ic_packints_decode
    _HAVE_IMAGECODECS = True
except ImportError:  # pragma: no cover -- exercised only on bare envs
    _HAVE_IMAGECODECS = False


# Bit ordering used by ``imagecodecs.packints_*``. We pin this in one
# place so the encoder and decoder can never disagree.
_IMAGECODECS_BITORDER = "<"  # GenICam LSB-first; matches MONO14p


# --------------------------------------------------------------------------- compression
#
# LZ4 (and ZSTD) ride on top of the 14-bit bit-pack as a second-stage
# size reduction. Reasons we ALSO compress, rather than just packing:
#
# * The U.2 SSD ceiling is ~1.07 GB/s sustained and the producer asks
#   for ~1063 MB/s after pack. We have effectively zero headroom for
#   jitter, so a steady-state run accumulates a slow deficit and starts
#   dropping after ~400 frames.
# * LZ4 on bit-packed Bayer data gives a modest (~1.05-1.3x) ratio --
#   modest because the bit-pack already removed the trivial 12.5 % of
#   high-zero bits and shuffled byte boundaries away from pixel
#   boundaries (LZ4's byte-level dictionary search loses some grip).
#   Even 1.1x ratio buys us 10 % drop in disk demand -> 957 MB/s vs
#   1070 MB/s ceiling = comfortable margin.
# * LZ4 encode at level 0 runs at ~2-3 GB/s/core on this CPU, so the
#   in-writer cost on a 213 MB pack_buf is ~70-100 ms per frame
#   (single-threaded). Producer-side budget is ~800 ms per writer
#   per frame (we have 4 writers per camera at ~4.71 fps producer);
#   the compress fits inside that budget without crowding the disk
#   write phase.
#
# Backends, priority order:
#   1. imagecodecs.lz4_encode / lz4_decode (preferred; same dep we
#      already require for packints fallback).
#   2. None: feature is silently disabled and the writer falls back
#      to writing the raw packed bytes (compression="none" in header).
try:
    from imagecodecs import lz4_encode as _ic_lz4_encode
    from imagecodecs import lz4_decode as _ic_lz4_decode
    _HAVE_LZ4 = True
except ImportError:  # pragma: no cover -- exercised only on bare envs
    _HAVE_LZ4 = False


# blosc2 powers the .bay14line container's payload codec
# (BITSHUFFLE/SHUFFLE + ZSTD). On this rig with synthetic Bayer-ish
# data (low-freq base + sensor noise, clamped to 14 bits), zstd-1 +
# SHUFFLE at nthreads=4 hits ~1.43x on a 13400x9528 uint16 frame --
# real scenes should compress better. Encode wall time at nth=2 is
# ~285 ms, which fits the per-packer budget at our 4.76 fps producer
# rate when 2 packer subprocesses run per camera.
try:
    import blosc2 as _blosc2  # noqa: F401 -- import-only; constants used below
    _HAVE_BLOSC2 = True
except ImportError:  # pragma: no cover -- exercised only on bare envs
    _HAVE_BLOSC2 = False


# Header field tag values. Stable across the file format -- old
# readers that only know "none" will still find the data because the
# embedded header carries enough info (data_len + compression name)
# to skip / decode correctly.
COMPRESSION_NONE = "none"
COMPRESSION_LZ4 = "lz4"
# Blosc2 + SHUFFLE filter + ZSTD level 1; encoded payload includes
# the blosc2 self-describing chunk header so decode doesn't need to
# know the original shape, only the dtype + element count which we
# recover from the file/chunk header.
COMPRESSION_BLOSC2_ZSTD1_SHUFFLE = "blosc2-zstd1-shuffle"

# .bay14line container: pixel layout recorded in the JSON ``format`` field.
LINE_PIXEL_FORMAT_UINT16_2D = "uint16_2d"
LINE_PIXEL_FORMAT_PACKED_U14 = "packed_u14_lsb_first_4_pixels_per_7_bytes"

# Live sweep lines use blosc2-compressed uint16 payloads only (see ``writer_proc``).

# Default compression chosen at module import time. The ``BAY14_COMPRESSION``
# env var lets ops flip this without touching code: e.g. set
# ``BAY14_COMPRESSION=lz4`` for an A/B run that re-enables compression.
# We resolve the env var here so every spawned writer subprocess sees
# the same value (mp.spawn carries env into children on Windows).
#
# Default is ``"none"``. Rationale: a synthetic + analytic A/B
# (``tools/bench_compression.py``) on this rig showed that for our
# bit-packed-14 byte stream, every compression option *increases*
# per-writer wall-clock when the drive is saturated:
#
#   * LZ4 on packed bytes: 1.00x ratio (bit-pack already removed the
#     trivial 12.5 % zero-padding and shuffled byte boundaries away
#     from pixel boundaries, defeating LZ4's byte-level dictionary),
#     but it still costs ~60 ms / frame to encode.
#   * Shuffle+LZ4 on raw uint16: 1.3-1.4x ratio, but the shuffle +
#     encode cost ~320 ms together.
#   * ZSTD: similar story -- modest ratio gains, larger CPU cost.
#
# Threshold formula at our 4-writer drive-saturated regime: each
# 1 MB saved on disk only frees ~3.75 ms of writer wall time
# (drive supplies ~267 MB/s per writer). Every codec we tested adds
# more CPU than it saves in disk wait, so compression-by-default
# would make drops worse, not better.
#
# Infrastructure is left in place because (a) different sensor
# settings or scene types might compress better, and (b) the
# correctness of the round-trip is unit-tested. Flip the env var
# to opt in.
_DEFAULT_COMPRESSION_RAW = os.environ.get(
    "BAY14_COMPRESSION", COMPRESSION_NONE,
).strip().lower()
if _DEFAULT_COMPRESSION_RAW not in (COMPRESSION_NONE, COMPRESSION_LZ4):
    # Unknown value: treat as "none" rather than failing import. Logged
    # via the writer's startup line so ops can spot the typo.
    _DEFAULT_COMPRESSION_RAW = COMPRESSION_NONE
DEFAULT_COMPRESSION = _DEFAULT_COMPRESSION_RAW
if DEFAULT_COMPRESSION == COMPRESSION_LZ4 and not _HAVE_LZ4:
    # imagecodecs missing or broken; degrade gracefully so the writer
    # still produces valid output.
    DEFAULT_COMPRESSION = COMPRESSION_NONE


# LZ4 level 0 is the standard fast LZ4 (block format). Higher
# ``level`` values switch imagecodecs to LZ4HC, which roughly halves
# throughput for ~10-20 % better ratio -- not worth it for our
# producer-rate-bound workload. We pin this in one place so the
# encoder and decoder can't drift.
_LZ4_LEVEL = int(os.environ.get("BAY14_LZ4_LEVEL", "0"))


def compress_packed(packed: np.ndarray, *, codec: str) -> bytes:
    """Compress a packed-14 ``uint8`` buffer for on-disk storage.

    *packed* must be a contiguous 1-D ``uint8`` ndarray (the output
    of :func:`pack_u14_into`). *codec* is one of :data:`COMPRESSION_NONE`
    or :data:`COMPRESSION_LZ4`.

    Returns the bytes to write to disk after the file header.
    Returning ``bytes`` (rather than packing into a caller-supplied
    buffer) is a deliberate trade-off: imagecodecs' encoders all
    return ``bytes`` and the alloc + copy is ~30 ms / 213 MB at
    memory bandwidth -- small relative to the disk write itself,
    and small relative to the LZ4 compress cost.

    Raises ``RuntimeError`` if LZ4 is requested but imagecodecs is
    not available -- the caller in :func:`write_raw_bay14_into`
    catches this and falls back to ``COMPRESSION_NONE``.
    """
    if codec == COMPRESSION_NONE:
        # ``tobytes()`` here is the explicit "we hand off a copy of
        # the buffer". On the hot path the caller bypasses this
        # branch entirely and writes ``packed`` direct to disk via
        # ``ndarray.tofile``; ``compress_packed`` exists mostly as
        # a uniform API for the offline tooling.
        return packed.tobytes()
    if codec == COMPRESSION_LZ4:
        if not _HAVE_LZ4:
            raise RuntimeError(
                "BAY14_COMPRESSION=lz4 requested but imagecodecs.lz4_* "
                "is not available; install imagecodecs or unset the env var."
            )
        return _ic_lz4_encode(packed, level=_LZ4_LEVEL)
    raise ValueError(f"unsupported compression codec: {codec!r}")


def compress_frame_blosc2(arr_u16: np.ndarray) -> bytes:
    """Compress a 2-D uint16 frame with blosc2 SHUFFLE + ZSTD level 1.

    Returns the blosc2 chunk bytes (with self-describing header). The
    chunk is suitable for direct write to disk; the matching decoder
    is :func:`decompress_frame_blosc2`.

    SHUFFLE on uint16 plus ZSTD-1 was the bench winner on this rig
    for 13400x9528 14-bit Bayer (~1.43x on synthetic, expected
    1.6-2.0x on real scenes), at ~160 ms encode with nthreads=4.

    Raises ``RuntimeError`` if blosc2 isn't installed -- callers are
    expected to fall back to :data:`COMPRESSION_NONE` in that case.
    """
    if not _HAVE_BLOSC2:
        raise RuntimeError(
            "blosc2 is not installed; install with `pip install blosc2`"
        )
    if arr_u16.dtype != np.uint16:
        raise TypeError(f"expected uint16 frame, got {arr_u16.dtype}")
    return _blosc2.compress2(
        arr_u16,
        codec=_blosc2.Codec.ZSTD,
        clevel=1,
        filters=[_blosc2.Filter.SHUFFLE],
        typesize=2,
        nthreads=int(os.environ.get("BAY14_BLOSC2_NTHREADS", "2")),
    )


def decompress_frame_blosc2(
    blob: bytes,
    *,
    height: int,
    width: int,
) -> np.ndarray:
    """Inverse of :func:`compress_frame_blosc2`.

    Returns a freshly-allocated ``height x width`` uint16 ndarray.
    Allocates on every call -- offline tooling, not the hot path.
    """
    if not _HAVE_BLOSC2:
        raise RuntimeError(
            "compression='blosc2-zstd1-shuffle' on this file but blosc2 "
            "is not installed; install with `pip install blosc2`"
        )
    out = np.empty((height, width), dtype=np.uint16)
    _blosc2.decompress2(
        blob, dst=out,
        nthreads=int(os.environ.get("BAY14_BLOSC2_NTHREADS", "2")),
    )
    return out


def decompress_packed(blob: bytes, *, codec: str, expected_size: int) -> np.ndarray:
    """Inverse of :func:`compress_packed` for offline tooling.

    *blob* is the on-disk pixel-data bytes (after the embedded header).
    *expected_size* is the uncompressed packed length in bytes
    (``width * height * 7 // 4``); used both as a hint to imagecodecs
    and as a post-hoc sanity check. Returns a 1-D ``uint8`` ndarray.
    """
    if codec == COMPRESSION_NONE:
        out = np.frombuffer(blob, dtype=np.uint8)
        if out.size != expected_size:
            raise ValueError(
                f"uncompressed payload size mismatch: got {out.size}, "
                f"expected {expected_size}"
            )
        return out
    if codec == COMPRESSION_LZ4:
        if not _HAVE_LZ4:
            raise RuntimeError(
                "compression='lz4' on this file but imagecodecs.lz4_decode "
                "is not available; install imagecodecs to read these files."
            )
        # imagecodecs.lz4_decode returns a bytes-like; wrap in a
        # uint8 ndarray so the caller can slice efficiently.
        decoded = _ic_lz4_decode(blob, out=expected_size)
        out = np.frombuffer(decoded, dtype=np.uint8)
        if out.size != expected_size:
            raise ValueError(
                f"LZ4-decoded payload size mismatch: got {out.size}, "
                f"expected {expected_size}"
            )
        return out
    raise ValueError(f"unsupported compression codec: {codec!r}")


# --------------------------------------------------------------------------- numba pack

if _HAVE_NUMBA:
    # Tell numba how many threads to use in @prange loops in this
    # process. Must be set BEFORE the first @njit(parallel=True)
    # call; setting it later has no effect on already-compiled
    # functions.
    try:
        set_num_threads(max(1, NUMBA_NUM_THREADS_PER_WRITER))
    except Exception:  # pragma: no cover -- defensive only
        pass

    @njit(cache=False, fastmath=True, boundscheck=False, parallel=True)
    def _pack_u14_numba(src, dst):  # pragma: no cover -- compiled JIT
        """JIT-compiled tight loop for 14-bit GenICam LSB-first pack.

        Each iteration reads 4 uint16 pixels and writes 7 uint8
        bytes via a uint64 staging value -- exactly the same wire
        layout ``imagecodecs.packints_encode(..., bitorder='<')``
        produces. Numba's LLVM backend vectorises the inner shift
        + store sequence into AVX2 (or wider) on modern CPUs.

        The buffer ``dst`` must be pre-allocated with
        ``size = src.size * 7 // 4``. We don't allocate inside the
        nopython region because allocation is the slowest part of
        repeat-pack workloads on Windows (page-zeroing).

        ``parallel=True`` + ``prange`` lets numba split the group
        loop across threads. The number of threads is bounded by
        the process-wide ``set_num_threads`` setting above.
        """
        n = src.size
        ngroups = n // 4
        for i in prange(ngroups):
            p0 = np.uint64(src[4 * i + 0])
            p1 = np.uint64(src[4 * i + 1])
            p2 = np.uint64(src[4 * i + 2])
            p3 = np.uint64(src[4 * i + 3])
            q = (
                p0
                | (p1 << np.uint64(14))
                | (p2 << np.uint64(28))
                | (p3 << np.uint64(42))
            )
            b = 7 * i
            dst[b] = np.uint8(q & np.uint64(0xFF))
            dst[b + 1] = np.uint8((q >> np.uint64(8)) & np.uint64(0xFF))
            dst[b + 2] = np.uint8((q >> np.uint64(16)) & np.uint64(0xFF))
            dst[b + 3] = np.uint8((q >> np.uint64(24)) & np.uint64(0xFF))
            dst[b + 4] = np.uint8((q >> np.uint64(32)) & np.uint64(0xFF))
            dst[b + 5] = np.uint8((q >> np.uint64(40)) & np.uint64(0xFF))
            dst[b + 6] = np.uint8((q >> np.uint64(48)) & np.uint64(0xFF))


# --------------------------------------------------------------------------- pack

def _pack_u14_numpy(flat: np.ndarray) -> np.ndarray:
    """Slow numpy fallback for :func:`pack_u14`.

    Implements the same GenICam LSB-first 14-bit packing as
    ``imagecodecs.packints_encode(..., bitorder='<')``: 4 pixels ->
    7 bytes, with byte 1 carrying p0[13:8] | (p1[1:0] << 6), etc.

    Used only when imagecodecs isn't installed; this branch costs
    ~520 ms/frame on the rig vs ~210 ms for the C path. Kept as a
    correctness reference and a dev-environment fallback.
    """
    n = flat.size
    g = flat.reshape(-1, 4)
    p0 = g[:, 0]
    p1 = g[:, 1]
    p2 = g[:, 2]
    p3 = g[:, 3]

    out = np.empty((n // 4, 7), dtype=np.uint8)
    out[:, 0] = p0 & 0xFF
    out[:, 1] = (p0 >> 8) | ((p1 & 0x03) << 6)
    out[:, 2] = (p1 >> 2) & 0xFF
    out[:, 3] = (p1 >> 10) | ((p2 & 0x0F) << 4)
    out[:, 4] = (p2 >> 4) & 0xFF
    out[:, 5] = (p2 >> 12) | ((p3 & 0x3F) << 2)
    out[:, 6] = (p3 >> 6) & 0xFF
    return out.ravel()


def pack_u14_into(arr_u16: np.ndarray, out: np.ndarray) -> np.ndarray:
    """Pack a 2-D uint16 array into a caller-supplied output buffer.

    The output buffer ``out`` must be a contiguous 1-D ``uint8``
    ``ndarray`` of size ``arr_u16.size * 7 // 4``. Reusing the same
    buffer across calls is the key throughput win on Windows: the
    OS only zero-faults the pages once (on first touch), so steady-
    state pack writes are pure memory bandwidth with no allocator
    or zero-fault overhead.

    Returns ``out`` so call sites read naturally.
    """
    if arr_u16.dtype != np.uint16:
        raise TypeError(f"expected uint16 input, got {arr_u16.dtype}")
    if out.dtype != np.uint8:
        raise TypeError(f"expected uint8 output buffer, got {out.dtype}")
    flat = arr_u16.ravel()
    n = flat.size
    if n % 4 != 0:
        raise ValueError(
            f"pixel count must be divisible by 4 for packed-14, got {n}"
        )
    expected = n * 7 // 4
    if out.size != expected:
        raise ValueError(
            f"output buffer size {out.size} != expected {expected}"
        )

    if _HAVE_NUMBA:
        _pack_u14_numba(flat, out)
        return out

    if _HAVE_IMAGECODECS:
        encoded = _ic_packints_encode(
            flat, 14, bitorder=_IMAGECODECS_BITORDER,
        )
        # imagecodecs returns a bytes-like; we have to copy into out.
        np.copyto(out, np.frombuffer(encoded, dtype=np.uint8))
        return out

    np.copyto(out, _pack_u14_numpy(flat))
    return out


def pack_u14(arr_u16: np.ndarray) -> np.ndarray:
    """Pack a 2-D uint16 array; allocates a fresh output buffer.

    Convenience wrapper around :func:`pack_u14_into` for one-shot
    callers (offline tooling, benchmarks). Hot-path code in the
    writer subprocesses uses :func:`pack_u14_into` with a reusable
    buffer to avoid per-frame allocation + zero-fault overhead.

    Backend priority: numba (~30-70 ms/frame) > imagecodecs (~210 ms)
    > numpy (~520 ms). All three produce identical on-disk bytes
    (GenICam LSB-first). See module docstring for details.
    """
    if arr_u16.dtype != np.uint16:
        raise TypeError(f"expected uint16, got {arr_u16.dtype}")
    n = arr_u16.size
    if n % 4 != 0:
        raise ValueError(
            f"pixel count must be divisible by 4 for packed-14, got {n}"
        )
    out = np.empty(n * 7 // 4, dtype=np.uint8)
    return pack_u14_into(arr_u16, out)


def _unpack_u14_numpy(packed: np.ndarray, n_pixels: int) -> np.ndarray:
    """Slow numpy fallback for :func:`unpack_u14`. Correctness reference."""
    g = packed.reshape(-1, 7).astype(np.uint16)
    b0, b1, b2, b3, b4, b5, b6 = (g[:, i] for i in range(7))

    p0 = b0 | ((b1 & 0x3F) << 8)
    p1 = (b1 >> 6) | (b2 << 2) | ((b3 & 0x0F) << 10)
    p2 = (b3 >> 4) | (b4 << 4) | ((b5 & 0x03) << 12)
    p3 = (b5 >> 2) | (b6 << 6)

    out = np.empty(n_pixels, dtype=np.uint16)
    out[0::4] = p0
    out[1::4] = p1
    out[2::4] = p2
    out[3::4] = p3
    return out


def unpack_u14(packed: np.ndarray, n_pixels: int) -> np.ndarray:
    """Inverse of :func:`pack_u14`. Used by the offline conversion tool.

    Uses ``imagecodecs.packints_decode`` when available; otherwise a
    numpy fallback. Both paths produce identical output.
    """
    if packed.dtype != np.uint8:
        raise TypeError(f"expected uint8 packed buffer, got {packed.dtype}")
    expected = n_pixels * 7 // 4
    if packed.size != expected:
        raise ValueError(
            f"packed size mismatch: got {packed.size} bytes, expected {expected}"
        )

    if _HAVE_IMAGECODECS:
        decoded = _ic_packints_decode(
            packed, "uint16", 14, bitorder=_IMAGECODECS_BITORDER,
        )
        # Sanity check: imagecodecs returns full unpacked count.
        return np.asarray(decoded, dtype=np.uint16).ravel()[:n_pixels]

    return _unpack_u14_numpy(packed, n_pixels)


def packed_size_bytes(width: int, height: int) -> int:
    """Bytes a ``width x height`` 14-bit-packed frame occupies on disk."""
    n = int(width) * int(height)
    if n % 4 != 0:
        raise ValueError(
            f"pixel count {n} not divisible by 4; pack format unsupported"
        )
    return n * 7 // 4


def warmup_pack(n_pixels: int = 4096) -> None:
    """Trigger numba JIT compile of :func:`pack_u14` once at startup.

    Calling this from a writer subprocess's ``writer_main`` before
    the producer starts feeding frames moves the ~50-500 ms
    compile-or-cache-load cost out of the hot path. Without it the
    very first sweep frame in a freshly-spawned subprocess pays for
    JIT compilation, blowing past the 230 ms-per-frame producer
    budget once and burning a slot.

    Calling on a non-numba environment is a cheap no-op (the numpy
    fallback is fast on a 4 KB warmup buffer).
    """
    # Smallest legal frame: 1 row of 4 pixels' worth of pixel count.
    n = max(4, int(n_pixels))
    n -= n % 4
    if n <= 0:
        return
    arr = np.zeros(n, dtype=np.uint16).reshape(1, n)
    pack_u14(arr)


# --------------------------------------------------------------------------- writer


FORMAT_VERSION = 1


# Inline header layout
# --------------------
# We embed metadata as a small fixed-size header at the start of
# every .bay14 file rather than emitting a separate .bay14.json
# sidecar. Reasons (measured live):
#
# * Doubling the file-create rate (one .bay14 + one .bay14.json per
#   frame) cut sustained per-drive throughput from ~1.07 GB/s to
#   ~810 MB/s. NTFS metadata journaling, MFT updates and Defender
#   real-time scanning all amplify with every additional file
#   create -- two creates per frame is meaningfully slower than one
#   on this rig.
# * Single-file frames also simplify downstream tooling: there's
#   no chance of a sidecar getting orphaned from its data file.
#
# The header format is:
#
#     bytes  0.. 7 : magic ASCII "BAY14HDR"
#     bytes  8..11 : header length N (uint32, little-endian); always
#                    >= 16 and a multiple of 16. Includes everything
#                    from byte 0 up to but not including the pixel
#                    data.
#     bytes 12..15 : pixel-data length M (uint32 LE) for sanity
#                    checking; M = packed bytes count, NOT pixel
#                    count.
#     bytes 16..N-1: ASCII JSON, NUL-padded to multiple of 16. Has
#                    the same fields the old sidecar had:
#                    format_version, format, width, height, bit_depth,
#                    bayer_pattern, plus every key from ``meta`` and
#                    a ``gps`` block when present.
#     bytes N..N+M-1: packed pixel bytes (GenICam LSB-first 14-bit).
#
# Reading: the ``tools/bay14_to_tif.py`` tool parses the header,
# strips it, then unpacks the rest. Tools that don't know about
# the header can still locate the pixel data by skipping the first
# N bytes (read ``header_length`` from offset 8).
HEADER_MAGIC = b"BAY14HDR"
HEADER_ALIGN = 16  # pad header to a multiple of this many bytes


def _build_header_dict(
    *,
    width: int,
    height: int,
    meta: dict[str, Any],
    gps: Optional[dict[str, Any]] = None,
    compression: str = COMPRESSION_NONE,
    uncompressed_size: Optional[int] = None,
) -> dict[str, Any]:
    """Assemble the JSON header dict embedded in every .bay14 file.

    Field order is stable (a Python ``dict`` preserves insertion
    order). Downstream tooling doesn't depend on order, but a stable
    layout makes diffs between two captured frames easy to read.

    *compression* is :data:`COMPRESSION_NONE` or :data:`COMPRESSION_LZ4`.
    *uncompressed_size* is the size of the packed pixel data BEFORE
    compression (i.e. ``width * height * 7 // 4``). Including it
    explicitly means the offline reader doesn't have to recompute
    it from geometry -- a future format tweak that changes the
    pack ratio wouldn't silently break old files.
    """
    payload: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "format": "packed_u14_lsb_first_4_pixels_per_7_bytes",
        "width": int(width),
        "height": int(height),
        "bit_depth": 14,
        "bayer_pattern": "RG",
        "compression": compression,
    }
    if uncompressed_size is not None:
        payload["uncompressed_size"] = int(uncompressed_size)
    payload.update(meta)
    if gps is not None:
        payload["gps"] = gps
    return payload


def _build_header_bytes(payload: dict[str, Any], data_len: int) -> bytes:
    """Render the header dict to its on-disk byte sequence."""
    json_blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    # Reserve 16 bytes for the magic + lengths fixed prefix.
    fixed_prefix_len = 16
    payload_len = fixed_prefix_len + len(json_blob)
    # NUL-pad to multiple of HEADER_ALIGN.
    pad = (-payload_len) % HEADER_ALIGN
    header_len = payload_len + pad
    if header_len > 0xFFFFFFFF:  # pragma: no cover -- sanity only
        raise ValueError(f"header too large: {header_len} bytes")
    out = bytearray(header_len)
    out[0:8] = HEADER_MAGIC
    out[8:12] = int(header_len).to_bytes(4, "little")
    out[12:16] = int(data_len).to_bytes(4, "little")
    out[16:16 + len(json_blob)] = json_blob
    # Remaining bytes are 0 from bytearray() init.
    return bytes(out)


def parse_header(blob: bytes) -> tuple[dict[str, Any], int, int]:
    """Inverse of :func:`_build_header_bytes` for offline tooling.

    *blob* is at least the first 16 bytes of a .bay14 file. The
    function returns ``(metadata_dict, header_length, data_length)``.
    The caller then reads ``header_length`` more bytes (which holds
    the JSON we just parsed) plus ``data_length`` packed pixel
    bytes.
    """
    if len(blob) < 16:
        raise ValueError("input too short to contain a .bay14 header")
    if blob[:8] != HEADER_MAGIC:
        raise ValueError(
            f"not a .bay14 file: magic={blob[:8]!r}, "
            f"expected {HEADER_MAGIC!r}"
        )
    header_len = int.from_bytes(blob[8:12], "little")
    data_len = int.from_bytes(blob[12:16], "little")
    if header_len < 16 or header_len % HEADER_ALIGN != 0:
        raise ValueError(f"bogus header_len={header_len}")
    if len(blob) < header_len:
        raise ValueError(
            f"need {header_len} header bytes, got only {len(blob)}"
        )
    json_blob = blob[16:header_len].rstrip(b"\x00")
    meta = json.loads(json_blob.decode("utf-8"))
    return meta, header_len, data_len


def write_raw_bay14(
    path: str,
    arr_u16: np.ndarray,
    meta: dict[str, Any],
    gps: Optional[dict[str, Any]] = None,
    *,
    compression: Optional[str] = None,
) -> None:
    """Write a packed 14-bit raw frame with embedded JSON header.

    Convenience entry point used by offline tooling. The hot-path
    writer subprocesses use :func:`write_raw_bay14_into` with a
    pre-allocated pack buffer to avoid per-frame allocation +
    zero-fault overhead.

    *compression* defaults to :data:`DEFAULT_COMPRESSION` (resolved
    once at module import time from the ``BAY14_COMPRESSION`` env
    var). Pass an explicit value to force a specific codec for
    one-off offline writes.
    """
    n = arr_u16.size
    out_buf = np.empty(n * 7 // 4, dtype=np.uint8)
    write_raw_bay14_into(
        path, arr_u16, out_buf, meta, gps=gps, compression=compression,
    )


def write_raw_bay14_into(
    path: str,
    arr_u16: np.ndarray,
    pack_buf: np.ndarray,
    meta: dict[str, Any],
    gps: Optional[dict[str, Any]] = None,
    *,
    compression: Optional[str] = None,
) -> dict[str, Any]:
    """Write a packed 14-bit raw frame using a caller-owned pack buffer.

    *pack_buf* must be a 1-D contiguous ``uint8`` ``ndarray`` of size
    ``arr_u16.size * 7 // 4``; it is overwritten in place each call.
    Reusing the same buffer across frames keeps Windows from
    zero-faulting 213 MiB of pages every iteration -- the single
    biggest win we have for sustained writer throughput.

    *compression* is :data:`COMPRESSION_NONE` or :data:`COMPRESSION_LZ4`.
    ``None`` (the default) resolves to :data:`DEFAULT_COMPRESSION`,
    which honours the ``BAY14_COMPRESSION`` env var. The on-disk
    ``data_len`` field reflects the actual byte count after
    pack-then-maybe-compress, so readers that just skip
    ``header_length`` bytes find the right number of bytes regardless
    of which codec was used.

    *path* should end in ``.bay14``. Single file per frame.

    Returns a small dict with timings + sizes (``pack_size``,
    ``compressed_size``, ``compress_s``, ``codec``) the caller can
    fold into a rate-report log. Returning the breakdown -- rather
    than logging it inline -- lets the writer subprocess decide its
    own log cadence and lets unit tests assert on the numbers
    without scraping log output.

    Validates dtype / shape before any I/O so a malformed call
    doesn't leave a half-written frame on disk.

    Atomicity policy: writes directly to the target path -- a hard
    process kill mid-write can leave a short .bay14. Tools should
    use ``parse_header`` and the embedded ``data_len`` to detect
    truncation. We skip a tmp-file + ``os.replace`` dance because
    the extra metadata work was measurably expensive at our rates.
    """
    if arr_u16.dtype != np.uint16:
        raise TypeError(f"expected uint16 frame, got {arr_u16.dtype}")
    if arr_u16.ndim != 2:
        raise ValueError(f"expected 2-D frame, got shape {arr_u16.shape}")
    h, w = arr_u16.shape

    codec = (compression or DEFAULT_COMPRESSION).strip().lower()
    if codec == COMPRESSION_LZ4 and not _HAVE_LZ4:
        # Defensive: if env said lz4 but the module wasn't importable,
        # _DEFAULT_COMPRESSION_RAW already fell back to none. This
        # branch only fires if a caller passed compression='lz4'
        # explicitly on a build without imagecodecs.
        codec = COMPRESSION_NONE

    # Pack first; if pack throws we don't touch the disk at all.
    pack_u14_into(arr_u16, pack_buf)
    pack_size = int(pack_buf.size)

    # Compress on the just-packed buffer. For COMPRESSION_NONE we
    # skip the helper entirely -- handing pack_buf straight to
    # ``ndarray.tofile`` avoids a 213 MB ``tobytes()`` copy that
    # ``compress_packed`` would otherwise perform.
    compress_s = 0.0
    payload: Optional[bytes] = None
    if codec != COMPRESSION_NONE:
        t = time.perf_counter()
        payload = compress_packed(pack_buf, codec=codec)
        compress_s = time.perf_counter() - t
        on_disk_size = len(payload)
    else:
        on_disk_size = pack_size

    header_dict = _build_header_dict(
        width=w, height=h, meta=meta, gps=gps,
        compression=codec,
        uncompressed_size=pack_size,
    )
    header_bytes = _build_header_bytes(header_dict, data_len=on_disk_size)

    # Single file write: header + payload, in one
    # ``open()`` -> two ``write`` calls -> ``close()``. For the
    # uncompressed path we ``pack_buf.tofile(f)`` to skip the
    # 213 MiB ``tobytes()`` copy; for the compressed path the
    # payload is already a bytes object from imagecodecs.
    with open(path, "wb") as f:
        f.write(header_bytes)
        if payload is not None:
            f.write(payload)
        else:
            pack_buf.tofile(f)

    return {
        "codec": codec,
        "pack_size": pack_size,
        "compressed_size": on_disk_size,
        "compress_s": compress_s,
    }


# --------------------------------------------------------------------------- sweep writer
#
# Sweep-batched file format (.bay14sweep)
# =======================================
# Goal: cut file-create rate from ~36/s system-wide (one .bay14 per
# frame x 4.76 fps x 3 cameras) down to ~3/s (one .bay14sweep per
# sweep x 0.366 sweeps/sec x 3 cameras). Per the comment above on
# the per-frame format, doubling the create rate cost ~24 % of
# sustained drive throughput; cutting it by ~12x should give us
# enough headroom to absorb the ~0.6 % per-camera deficit that
# was eating frames after ~400 captures.
#
# Layout (offsets relative to start of file):
#
#   File header (sweep-level)
#     bytes  0.. 7 : magic ASCII "BAY14SWP"
#     bytes  8..11 : header length N (uint32 LE); always >= 16 and
#                    a multiple of SWEEP_HEADER_ALIGN (16). Includes
#                    everything from byte 0 up to but not including
#                    the index table.
#     bytes 12..15 : frame count K (uint32 LE)
#     bytes 16..N-1: ASCII JSON, NUL-padded to a multiple of 16.
#                    Sweep-level metadata: format_version, format,
#                    width, height, bit_depth, bayer_pattern,
#                    compression, plus camera_serial / project /
#                    line / sweep / capture_kind / etc. Per-frame
#                    fields (frame_in_sweep, acquire_timestamp, gps)
#                    live in the per-frame chunk header below.
#
#   Index table  (K entries, fixed 24 bytes each, immediately after
#                 the file header at byte N)
#     bytes 0.. 3 : frame_in_sweep (uint32 LE), 1..K
#     bytes 4..11 : byte_offset (uint64 LE) -- offset from start of
#                   file to the per-frame chunk's first byte.
#     bytes 12..19: payload_len (uint64 LE) -- packed (or compressed)
#                   bytes count for this frame, NOT including the
#                   per-frame chunk header.
#     bytes 20..23: uncompressed_packed_len (uint32 LE) -- handy when
#                   the codec is not "none" so the reader can
#                   pre-allocate the decode buffer without inspecting
#                   each chunk header.
#
#   Per-frame chunks (K of them, in order, starting at the offset
#                     recorded in the index)
#     Each chunk starts with a small header:
#       bytes 0.. 7: magic ASCII "BAY14FRM"
#       bytes 8..11: header length M (uint32 LE), >= 16, multiple of 16.
#       bytes 12..15: payload_len P (uint32 LE) -- mirrors the index
#                     entry; lets a corrupt-index recovery tool walk
#                     chunks linearly without trusting the index.
#       bytes 16..M-1: ASCII JSON per-frame meta (frame_in_sweep,
#                     capture_kind, acquire_timestamp, optional gps),
#                     NUL-padded to 16.
#     Then P bytes of pixel payload (packed bytes if codec="none",
#     LZ4-encoded packed bytes if codec="lz4", etc.).
#
# Why an index AND per-chunk headers?
#   * Random access: tools that want a single frame jump straight
#     to its offset without parsing 12 prior chunks.
#   * Resilience: if the index is truncated (kill -9 mid-write), the
#     per-chunk headers let a recovery tool walk the file linearly.
#
# Why two-pass write (index placeholder, then real index after all
# chunks land)?
#   * Streaming: we don't know each chunk's size until pack-and-
#     maybe-compress runs, and we want to release the SHM slot
#     immediately after pack. Pre-computing all sizes would either
#     hold every slot for the full sweep duration (bad pool pressure)
#     or pre-pack into private buffers (bad memory).
#
# Tools that don't know about .bay14sweep can fall back to a "skip
# the file header by reading bytes 8..11, skip K * INDEX_ENTRY_SIZE,
# then walk the chunk headers" strategy. The per-frame chunk header
# is intentionally similar to the per-frame .bay14 BAY14HDR layout
# so partial reuse of decode logic is possible.

SWEEP_HEADER_MAGIC = b"BAY14SWP"
SWEEP_HEADER_ALIGN = 16
SWEEP_FRAME_HEADER_MAGIC = b"BAY14FRM"
SWEEP_INDEX_ENTRY_SIZE = 24  # see layout comment above


def _build_sweep_header_bytes(
    *,
    width: int,
    height: int,
    n_frames: int,
    sweep_meta: dict[str, Any],
    compression: str,
) -> bytes:
    """Render the sweep file header (magic + length + JSON metadata).

    *sweep_meta* should contain everything constant across the 13
    frames in the sweep (camera_serial, project, line, sweep, etc.);
    per-frame fields go in the chunk header instead.
    """
    payload: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "format": "packed_u14_lsb_first_4_pixels_per_7_bytes",
        "container": "bay14sweep_v1",
        "width": int(width),
        "height": int(height),
        "bit_depth": 14,
        "bayer_pattern": "RG",
        "compression": compression,
        "n_frames": int(n_frames),
    }
    payload.update(sweep_meta)
    json_blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    fixed_prefix_len = 16
    payload_len = fixed_prefix_len + len(json_blob)
    pad = (-payload_len) % SWEEP_HEADER_ALIGN
    header_len = payload_len + pad
    out = bytearray(header_len)
    out[0:8] = SWEEP_HEADER_MAGIC
    out[8:12] = int(header_len).to_bytes(4, "little")
    out[12:16] = int(n_frames).to_bytes(4, "little")
    out[16:16 + len(json_blob)] = json_blob
    return bytes(out)


def parse_sweep_header(blob: bytes) -> tuple[dict[str, Any], int, int]:
    """Inverse of :func:`_build_sweep_header_bytes`.

    Returns ``(meta_dict, header_length, n_frames)``. The caller
    then reads ``n_frames * SWEEP_INDEX_ENTRY_SIZE`` index bytes
    and uses :func:`parse_sweep_index` to materialise the chunk
    table.
    """
    if len(blob) < 16:
        raise ValueError("input too short for a .bay14sweep header")
    if blob[:8] != SWEEP_HEADER_MAGIC:
        raise ValueError(
            f"not a .bay14sweep file: magic={blob[:8]!r}, "
            f"expected {SWEEP_HEADER_MAGIC!r}"
        )
    header_len = int.from_bytes(blob[8:12], "little")
    n_frames = int.from_bytes(blob[12:16], "little")
    if header_len < 16 or header_len % SWEEP_HEADER_ALIGN != 0:
        raise ValueError(f"bogus sweep header_len={header_len}")
    if len(blob) < header_len:
        raise ValueError(
            f"need {header_len} sweep header bytes, got only {len(blob)}"
        )
    json_blob = blob[16:header_len].rstrip(b"\x00")
    meta = json.loads(json_blob.decode("utf-8"))
    return meta, header_len, n_frames


def parse_sweep_index(
    blob: bytes,
    n_frames: int,
) -> list[tuple[int, int, int, int]]:
    """Parse ``n_frames`` index entries.

    Returns a list of ``(frame_in_sweep, byte_offset,
    payload_len, uncompressed_packed_len)`` tuples in file order.
    """
    expected = n_frames * SWEEP_INDEX_ENTRY_SIZE
    if len(blob) < expected:
        raise ValueError(
            f"sweep index truncated: need {expected} bytes, "
            f"got {len(blob)}"
        )
    out: list[tuple[int, int, int, int]] = []
    for i in range(n_frames):
        base = i * SWEEP_INDEX_ENTRY_SIZE
        f_in = int.from_bytes(blob[base:base + 4], "little")
        offset = int.from_bytes(blob[base + 4:base + 12], "little")
        payload = int.from_bytes(blob[base + 12:base + 20], "little")
        uncompressed = int.from_bytes(blob[base + 20:base + 24], "little")
        out.append((f_in, offset, payload, uncompressed))
    return out


def _build_frame_chunk_header(
    *,
    frame_meta: dict[str, Any],
    gps: Optional[dict[str, Any]],
    attitude: Optional[dict[str, Any]] = None,
    payload_len: int,
    alignment: int = SWEEP_HEADER_ALIGN,
) -> bytes:
    """Render a per-frame chunk header inside a .bay14sweep file.

    ``alignment`` controls how far the header is padded; the default
    (``SWEEP_HEADER_ALIGN``=16) matches the on-disk legacy layout. The
    direct-I/O writer passes ``alignment=4096`` (one sector) so each
    chunk header can be issued as a single ``WriteFile`` with
    sector-aligned size.
    """
    payload: dict[str, Any] = dict(frame_meta)
    if gps is not None:
        payload["gps"] = gps
    if attitude is not None:
        payload["attitude"] = attitude
    json_blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    fixed_prefix_len = 16
    blob_len = fixed_prefix_len + len(json_blob)
    align = max(int(alignment), SWEEP_HEADER_ALIGN)
    pad = (-blob_len) % align
    header_len = blob_len + pad
    out = bytearray(header_len)
    out[0:8] = SWEEP_FRAME_HEADER_MAGIC
    out[8:12] = int(header_len).to_bytes(4, "little")
    out[12:16] = int(payload_len).to_bytes(4, "little")
    out[16:16 + len(json_blob)] = json_blob
    return bytes(out)


def parse_frame_chunk_header(blob: bytes) -> tuple[dict[str, Any], int, int]:
    """Inverse of :func:`_build_frame_chunk_header`.

    Returns ``(meta_dict, header_length, payload_length)``.
    """
    if len(blob) < 16:
        raise ValueError("input too short for a frame chunk header")
    if blob[:8] != SWEEP_FRAME_HEADER_MAGIC:
        raise ValueError(
            f"not a frame chunk header: magic={blob[:8]!r}, "
            f"expected {SWEEP_FRAME_HEADER_MAGIC!r}"
        )
    header_len = int.from_bytes(blob[8:12], "little")
    payload_len = int.from_bytes(blob[12:16], "little")
    if header_len < 16 or header_len % SWEEP_HEADER_ALIGN != 0:
        raise ValueError(f"bogus chunk header_len={header_len}")
    if len(blob) < header_len:
        raise ValueError(
            f"need {header_len} chunk header bytes, got {len(blob)}"
        )
    json_blob = blob[16:header_len].rstrip(b"\x00")
    meta = json.loads(json_blob.decode("utf-8"))
    return meta, header_len, payload_len


# Per-frame iteration callback signature for the streaming sweep
# writer. The callback is invoked once per frame inside
# :func:`write_sweep_bay14_into`. It must:
#
#   * Pack the i-th frame's pixel data into ``pack_buf``.
#   * Optionally release any SHM slot held for this frame back to
#     the pool (the writer subprocess uses this hook so the SDK
#     callback can reuse the slot mid-sweep).
#   * Return ``(frame_meta, gps)`` for the chunk header.
#
# We pass it ``pack_buf`` rather than letting it allocate so the
# whole sweep reuses one 213 MB scratch buffer -- same one-touch
# page-fault optimisation as the per-frame path.
SweepFrameProvider = Any  # callable, signature documented above


def write_sweep_bay14_into(
    path: str,
    n_frames: int,
    width: int,
    height: int,
    pack_buf: np.ndarray,
    sweep_meta: dict[str, Any],
    frame_provider: SweepFrameProvider,
    *,
    compression: Optional[str] = None,
) -> dict[str, Any]:
    """Write all *n_frames* of a single sweep into one .bay14sweep file.

    *frame_provider* is a callable that, given an index ``i`` in
    ``range(n_frames)`` and the writer's reusable ``pack_buf``,
    fills ``pack_buf`` with the i-th frame's packed bytes and
    returns ``(frame_meta_dict, gps_dict_or_none)``. The provider
    is also responsible for releasing any per-frame SHM slot back
    to the pool before it returns -- the file-write step that
    follows is sequential and doesn't need the slot anymore.

    Layout: file header, then a placeholder index, then K chunks
    (each = chunk header + payload), then a final seek-back to
    overwrite the index with real offsets. We do NOT pre-pack
    every frame to compute sizes up front: that would either hold
    K SHM slots simultaneously (pool pressure) or copy K frames
    into private buffers (memory blowup). The two-pass index lets
    us stream through frames one at a time, packing then writing
    each, while still ending with a fully-populated index for
    random access.

    Returns a stats dict the caller can log: per-frame and per-
    sweep timings + sizes + codec.
    """
    if n_frames <= 0:
        raise ValueError(f"n_frames must be >= 1, got {n_frames}")
    codec = (compression or DEFAULT_COMPRESSION).strip().lower()
    if codec == COMPRESSION_LZ4 and not _HAVE_LZ4:
        codec = COMPRESSION_NONE
    expected_packed = packed_size_bytes(width, height)
    if pack_buf.size != expected_packed:
        raise ValueError(
            f"pack_buf size {pack_buf.size} != expected {expected_packed} "
            f"(geometry {height}x{width})"
        )

    sweep_header = _build_sweep_header_bytes(
        width=width, height=height, n_frames=n_frames,
        sweep_meta=sweep_meta, compression=codec,
    )
    index_size = n_frames * SWEEP_INDEX_ENTRY_SIZE
    # Placeholder zeros for the index; we'll seek back and rewrite
    # after all chunks land.
    index_placeholder = bytes(index_size)

    # Stats accumulators -- pack/compress timings exclude the disk
    # write so the caller can attribute time separately.
    sum_pack_s = 0.0
    sum_compress_s = 0.0
    sum_write_s = 0.0
    sum_payload = 0
    sum_pack_size = 0
    chunk_records: list[tuple[int, int, int, int]] = []  # (frame_in_sweep, offset, payload_len, uncompressed_len)

    # Open + write header + placeholder index + chunks streaming.
    # We use ``rb+`` semantics by opening write-binary then keeping
    # the handle so we can seek back to overwrite the index. With
    # ``buffering=0`` we'd be issuing tiny syscalls per write; the
    # default Python buffering aggregates writes into ~1 MB chunks
    # which is what we want. Big payload writes go straight through
    # via ``ndarray.tofile`` / ``f.write(bytes)`` with no extra copy.
    t_open = time.perf_counter()
    with open(path, "wb") as f:
        f.write(sweep_header)
        f.write(index_placeholder)

        for i in range(n_frames):
            t_pack = time.perf_counter()
            frame_meta, gps = frame_provider(i, pack_buf)
            sum_pack_s += time.perf_counter() - t_pack
            pack_size = int(pack_buf.size)
            sum_pack_size += pack_size

            payload: Optional[bytes] = None
            if codec != COMPRESSION_NONE:
                tc = time.perf_counter()
                payload = compress_packed(pack_buf, codec=codec)
                sum_compress_s += time.perf_counter() - tc
                payload_len = len(payload)
            else:
                payload_len = pack_size

            chunk_header = _build_frame_chunk_header(
                frame_meta=frame_meta, gps=gps, payload_len=payload_len,
            )
            # Record the chunk's *file* offset (start of its header).
            chunk_offset = f.tell()
            t_w = time.perf_counter()
            f.write(chunk_header)
            if payload is not None:
                f.write(payload)
            else:
                pack_buf.tofile(f)
            sum_write_s += time.perf_counter() - t_w
            sum_payload += payload_len

            chunk_records.append((
                int(frame_meta.get("frame_in_sweep", i + 1)),
                int(chunk_offset),
                int(payload_len),
                int(pack_size),
            ))

        # Seek back and write the real index. The caller already
        # paid the cost of the placeholder write so the file size
        # is unchanged; this is a small in-place overwrite of
        # ``n_frames * 24`` bytes.
        index_bytes = bytearray(index_size)
        for i, (f_in, offset, payload_len, unc) in enumerate(chunk_records):
            base = i * SWEEP_INDEX_ENTRY_SIZE
            index_bytes[base:base + 4] = int(f_in).to_bytes(4, "little")
            index_bytes[base + 4:base + 12] = int(offset).to_bytes(8, "little")
            index_bytes[base + 12:base + 20] = int(payload_len).to_bytes(8, "little")
            index_bytes[base + 20:base + 24] = int(unc).to_bytes(4, "little")
        f.seek(len(sweep_header))
        f.write(bytes(index_bytes))

    total_s = time.perf_counter() - t_open
    return {
        "codec": codec,
        "n_frames": n_frames,
        "pack_size_total": sum_pack_size,
        "payload_total": sum_payload,
        "pack_s_total": sum_pack_s,
        "compress_s_total": sum_compress_s,
        "write_s_total": sum_write_s,
        "wall_s": total_s,
        "header_size": len(sweep_header) + index_size,
    }


# --------------------------------------------------------------------------- per-line container
#
# .bay14line container layout
# ---------------------------
# One file per operator-defined "line". Goal: drop the file-create
# rate from ~14/s/cam (per-frame .bay14) to ~1 per line per camera,
# eliminating the NTFS metadata + Defender real-time-scan tax that
# was eating the last few % of drive headroom and causing drops at
# ~400 frames into long captures.
#
# Combined with blosc2 SHUFFLE+ZSTD-1 payload compression (~1.4-2x
# ratio on real Bayer scenes), expected per-camera disk demand at
# 4.76 fps drops from ~1063 MB/s (packed14, no compression) to
# ~700-850 MB/s, well under the ~1070 MB/s drive ceiling.
#
# File layout (offsets from start of file):
#
#   File header
#     bytes  0.. 7 : magic ASCII "BAY14LIN"
#     bytes  8..11 : header length N (uint32 LE); always >= 32 and a
#                    multiple of LINE_HEADER_ALIGN (16). Includes
#                    everything from byte 0 up to but not including
#                    the first frame chunk.
#     bytes 12..15 : frame count K (uint32 LE) -- patched on close;
#                    0 in a partially-written file.
#     bytes 16..23 : index_offset (uint64 LE) -- patched on close;
#                    0 means "no index, walk chunks linearly using
#                    BAY14FRM magic to recover".
#     bytes 24..27 : index_entry_size (uint32 LE) =
#                    LINE_INDEX_ENTRY_SIZE (32 today). Lets future
#                    readers handle larger entries without breaking.
#     bytes 28..31 : reserved (uint32 LE), must be 0.
#     bytes 32..N-1: ASCII JSON, NUL-padded to multiple of
#                    LINE_HEADER_ALIGN. Constants for the line:
#                    format_version, container, codec, codec_clevel,
#                    codec_filter, width, height, bit_depth,
#                    bayer_pattern, camera_serial, project, line,
#                    capture_kind ("sweep"), line_started_at,
#                    plus any extras the caller passes in.
#
#   Chunks (K of them, in arrival order; not necessarily sorted by
#           sweep/frame_in_sweep -- the index records the sweep/
#           frame_in_sweep so extraction tools can reorder)
#     Each chunk = BAY14FRM header (reused from .bay14sweep) + payload.
#     Payload is the codec-specific blob (blosc2 chunk, raw packed
#     bytes, or LZ4-encoded packed bytes).
#
#   Index table (appended after last chunk, on close)
#     K * LINE_INDEX_ENTRY_SIZE bytes; each entry is:
#       bytes  0.. 3 : sweep (uint32 LE)
#       bytes  4.. 7 : frame_in_sweep (uint32 LE)
#       bytes  8..15 : byte_offset (uint64 LE) -- offset to start of
#                      this chunk's BAY14FRM header.
#       bytes 16..23 : payload_len (uint64 LE) -- codec payload bytes,
#                      not including the BAY14FRM header.
#       bytes 24..31 : uncompressed_pixel_bytes (uint64 LE) =
#                      width * height * 2; redundant with the file
#                      header but lets per-chunk recovery tools work
#                      without re-reading the file header.
#
# Why an index AND per-chunk headers?
#   * Random access: extraction tools can jump straight to a frame
#     by index without scanning all prior chunks.
#   * Resilience: if the writer is killed before close (no index
#     written), the per-chunk BAY14FRM magic lets a recovery tool
#     walk the file linearly to rebuild the index.
#
# Why index at the END instead of after the file header (as in
# .bay14sweep)?
#   * We don't know K up front -- the operator decides when to end
#     the line by changing line numbers / leaving M1 / closing the app.
#   * Pre-allocating space for a worst-case index (~hundreds of
#     thousands of entries * 32 bytes = MBs) is wasteful.
#   * Streaming chunks then appending the index avoids the seek-back
#     cost in .bay14sweep's two-pass design.
#   * On close we patch the file header (a 32-byte fixed prefix) to
#     fill in K and index_offset. The JSON portion of the file header
#     stays untouched.

LINE_HEADER_MAGIC = b"BAY14LIN"
LINE_HEADER_ALIGN = 16
LINE_HEADER_FIXED_PREFIX = 32  # bytes before the JSON portion
LINE_INDEX_ENTRY_SIZE = 32


def _line_header_bayer_phase(line_meta: dict[str, Any]) -> str:
    """Two-letter phase for JSON (RG/BG/GR/GB) from GenICam or legacy keys."""
    gf = str(line_meta.get("genicam_pixel_format", "")).upper()
    if "BAYERBG" in gf:
        return "BG"
    if "BAYERGB" in gf:
        return "GB"
    if "BAYERGR" in gf:
        return "GR"
    if "BAYERRG" in gf:
        return "RG"
    bp = str(line_meta.get("bayer_pattern", "RG")).strip().upper()
    return bp[:2] if len(bp) >= 2 else "RG"


def _build_line_header_bytes(
    *,
    width: int,
    height: int,
    line_meta: dict[str, Any],
    codec: str,
    pixel_format: str,
    codec_extras: Optional[dict[str, Any]] = None,
    chunk_alignment: int = SWEEP_HEADER_ALIGN,
) -> bytes:
    """Render the .bay14line file header (without K / index_offset patched).

    K (frame count) and index_offset are zero in the returned bytes;
    :class:`LineWriter` patches them in place at close time. Any
    keys in *line_meta* start the merge; canonical geometry + codec
    fields win so operators can't accidentally override them.

    *chunk_alignment* records the per-chunk sector alignment used by
    the writer; the file header itself is padded to the same boundary
    so the first chunk starts on a sector-aligned offset. Legacy
    callers (the default 16) get the historical layout.
    """
    payload: dict[str, Any] = dict(line_meta)
    payload["format_version"] = FORMAT_VERSION
    payload["container"] = "bay14line_v1"
    payload["width"] = int(width)
    payload["height"] = int(height)
    payload["bit_depth"] = 14
    payload["bayer_pattern"] = _line_header_bayer_phase(payload)
    payload["index_entry_size"] = LINE_INDEX_ENTRY_SIZE
    payload["format"] = pixel_format
    payload["compression"] = codec
    payload["chunk_alignment"] = int(chunk_alignment)
    if codec_extras:
        payload["codec_extras"] = codec_extras
    json_blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_len = LINE_HEADER_FIXED_PREFIX + len(json_blob)
    align = max(int(chunk_alignment), LINE_HEADER_ALIGN)
    pad = (-payload_len) % align
    header_len = payload_len + pad
    if header_len > 0xFFFFFFFF:  # pragma: no cover -- sanity only
        raise ValueError(f"line header too large: {header_len} bytes")
    out = bytearray(header_len)
    out[0:8] = LINE_HEADER_MAGIC
    out[8:12] = int(header_len).to_bytes(4, "little")
    # Bytes 12..31 are zeros (K, index_offset, index_entry_size,
    # reserved); the writer patches K, index_offset, index_entry_size
    # at close. We pre-fill index_entry_size so even an unpatched
    # header is correct in that field.
    out[24:28] = int(LINE_INDEX_ENTRY_SIZE).to_bytes(4, "little")
    out[LINE_HEADER_FIXED_PREFIX:LINE_HEADER_FIXED_PREFIX + len(json_blob)] = json_blob
    return bytes(out)


def parse_line_header_prefix(
    blob: bytes,
) -> tuple[int, int, int, int]:
    """Parse just the 32-byte fixed prefix of a .bay14line header.

    Cheap peek: tells the reader the full header length, frame
    count, index offset, and index entry size without loading the
    JSON metadata. Useful for the recovery path -- if
    ``index_offset`` is 0 we know the writer was killed before
    close and we should fall back to :func:`recover_line_chunks`.

    Returns ``(header_length, n_frames, index_offset, index_entry_size)``.
    """
    if len(blob) < LINE_HEADER_FIXED_PREFIX:
        raise ValueError(
            f"input too short for .bay14line header prefix: "
            f"{len(blob)} bytes"
        )
    if blob[:8] != LINE_HEADER_MAGIC:
        raise ValueError(
            f"not a .bay14line file: magic={blob[:8]!r}, "
            f"expected {LINE_HEADER_MAGIC!r}"
        )
    header_len = int.from_bytes(blob[8:12], "little")
    n_frames = int.from_bytes(blob[12:16], "little")
    index_offset = int.from_bytes(blob[16:24], "little")
    index_entry_size = int.from_bytes(blob[24:28], "little")
    if header_len < LINE_HEADER_FIXED_PREFIX or header_len % LINE_HEADER_ALIGN != 0:
        raise ValueError(f"bogus line header_len={header_len}")
    if index_entry_size and index_entry_size != LINE_INDEX_ENTRY_SIZE:
        # Forward-compat hook; today we only know the v1 entry size.
        raise ValueError(
            f"unsupported index_entry_size={index_entry_size}, "
            f"this build only knows {LINE_INDEX_ENTRY_SIZE}"
        )
    return header_len, n_frames, index_offset, index_entry_size


def parse_line_header(
    blob: bytes,
) -> tuple[dict[str, Any], int, int, int]:
    """Parse a .bay14line file header (including its JSON metadata).

    *blob* must contain at least ``header_length`` bytes (use
    :func:`parse_line_header_prefix` first to discover the length).
    Returns ``(meta_dict, header_length, n_frames, index_offset)``.

    ``n_frames`` and ``index_offset`` are 0 if the file was not
    closed cleanly; in that case the caller should fall back to
    :func:`recover_line_chunks` to rebuild the index by walking
    BAY14FRM magic numbers.
    """
    header_len, n_frames, index_offset, _ = parse_line_header_prefix(blob)
    if len(blob) < header_len:
        raise ValueError(
            f"need {header_len} line-header bytes, got only {len(blob)}"
        )
    json_blob = blob[LINE_HEADER_FIXED_PREFIX:header_len].rstrip(b"\x00")
    meta = json.loads(json_blob.decode("utf-8")) if json_blob else {}
    return meta, header_len, n_frames, index_offset


def parse_line_index(
    blob: bytes,
    n_frames: int,
) -> list[tuple[int, int, int, int, int]]:
    """Parse ``n_frames`` entries from the appended index table.

    Returns a list of ``(sweep, frame_in_sweep, byte_offset,
    payload_len, uncompressed_pixel_bytes)`` tuples in file order.
    """
    expected = n_frames * LINE_INDEX_ENTRY_SIZE
    if len(blob) < expected:
        raise ValueError(
            f"line index truncated: need {expected} bytes, got {len(blob)}"
        )
    out: list[tuple[int, int, int, int, int]] = []
    for i in range(n_frames):
        base = i * LINE_INDEX_ENTRY_SIZE
        sweep = int.from_bytes(blob[base:base + 4], "little")
        f_in = int.from_bytes(blob[base + 4:base + 8], "little")
        offset = int.from_bytes(blob[base + 8:base + 16], "little")
        payload_len = int.from_bytes(blob[base + 16:base + 24], "little")
        unc = int.from_bytes(blob[base + 24:base + 32], "little")
        out.append((sweep, f_in, offset, payload_len, unc))
    return out


class LineWriter:
    """Streaming writer for one ``.bay14line`` container.

    Lifecycle::

        w = LineWriter(path, height=H, width=W,
                       line_meta=..., codec=COMPRESSION_BLOSC2_ZSTD1_SHUFFLE)
        w.open()
        for ... :
            w.append_chunk(payload, sweep, frame_in_sweep, frame_meta, gps)
        stats = w.close_with_index()

    The instance is single-use; create a new one per line. Methods
    are NOT thread-safe -- the caller (the io_writer subprocess) is
    expected to drain a queue and call ``append_chunk`` sequentially.
    """

    def __init__(
        self,
        path: str,
        *,
        height: int,
        width: int,
        line_meta: dict[str, Any],
        codec: str,
        pixel_format: Optional[str] = None,
        codec_extras: Optional[dict[str, Any]] = None,
        prealloc_bytes: int = 0,
        direct_io: bool = False,
    ) -> None:
        self.path = path
        self.height = int(height)
        self.width = int(width)
        self.codec = codec
        if pixel_format is not None:
            self.pixel_format = pixel_format
        elif codec == COMPRESSION_BLOSC2_ZSTD1_SHUFFLE:
            # blosc2 chunks contain the full uint16 plane; the file format
            # tag describes the *uncompressed* layout the chunk decodes to.
            self.pixel_format = LINE_PIXEL_FORMAT_UINT16_2D
        else:
            # ``codec=none`` writes packed-14 chunks (4 px -> 7 bytes,
            # GenICam LSB-first). The packer (``writer_proc.packer_main``)
            # produces those bytes; the file header must agree so the
            # offline reader picks the right unpack path.
            self.pixel_format = LINE_PIXEL_FORMAT_PACKED_U14
        self.uncompressed_bytes = self.height * self.width * 2  # uint16 plane
        self.prealloc_bytes = int(prealloc_bytes)
        self._prealloc_ok = False
        # Direct I/O (Windows FILE_FLAG_NO_BUFFERING) requires sector-
        # aligned writes; we pad every chunk to that boundary and
        # record the alignment in the file header so the offline reader
        # can walk new files via recover_line_chunks(). Legacy buffered
        # I/O keeps the historical 16-byte alignment.
        self.direct_io = bool(direct_io) and sys.platform == "win32"
        if self.direct_io:
            self.chunk_alignment = _DIRECT_IO_SECTOR_SIZE
        else:
            self.chunk_alignment = SWEEP_HEADER_ALIGN
        self._line_meta = dict(line_meta)
        self._codec_extras = dict(codec_extras) if codec_extras else None
        # Whether the hot streaming handle is the legacy buffered file
        # (``self._f``) or the Win32 direct-I/O wrapper
        # (``self._direct``). Mutually exclusive at any given time.
        self._f: Any = None
        self._direct: Optional[_DirectIOFile] = None
        self._records: list[tuple[int, int, int, int, int]] = []
        self._header_bytes: bytes = b""
        self._opened_at: float = 0.0
        self._sum_payload: int = 0
        self._sum_write_s: float = 0.0
        # Pre-allocated sector-aligned heap scratch for chunk-header
        # writes when running in direct-I/O mode. Allocated lazily on
        # first use so non-direct lines pay no overhead.
        self._chunk_hdr_scratch: Optional[bytearray] = None

    def open(self) -> None:
        """Create the file, write the file header (with K=0,
        index_offset=0). Subsequent ``append_chunk`` calls stream
        chunks straight after the header."""
        if self._f is not None or self._direct is not None:
            raise RuntimeError(f"LineWriter already open: {self.path!r}")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._header_bytes = _build_line_header_bytes(
            width=self.width, height=self.height,
            line_meta=self._line_meta,
            codec=self.codec,
            pixel_format=self.pixel_format,
            codec_extras=self._codec_extras,
            chunk_alignment=self.chunk_alignment,
        )
        self._opened_at = time.perf_counter()
        if self.direct_io:
            self._open_direct()
        else:
            self._open_buffered()

    def _open_buffered(self) -> None:
        # ``rb+`` would refuse to create a missing file; ``wb+`` does.
        self._f = open(self.path, "wb+")
        self._f.write(self._header_bytes)
        # Pre-allocate the file to ``prealloc_bytes`` to avoid per-write
        # NTFS extension during the hot streaming phase. Errors (e.g. a
        # full drive, a filesystem that rejects the operation) are
        # logged and ignored -- the writer can still proceed with
        # per-write extension, just slower.
        if self.prealloc_bytes > 0:
            try:
                self._f.flush()
                _prealloc_file_handle(self._f.fileno(), self.prealloc_bytes)
                # SetEndOfFile / fallocate moved the OS file pointer to
                # ``prealloc_bytes``; restore Python's view to right after
                # the header so the next ``write`` lands in the correct
                # place.
                self._f.seek(len(self._header_bytes))
                self._prealloc_ok = True
            except OSError as exc:
                log.warning(
                    "LineWriter pre-allocation of %d bytes failed for %s: "
                    "%s; continuing without prealloc",
                    self.prealloc_bytes, self.path, exc,
                )
                # Best-effort restore of position; ``seek`` shouldn't fail
                # on a freshly opened wb+ file but guard anyway.
                try:
                    self._f.seek(len(self._header_bytes))
                except OSError:
                    pass

    def _open_direct(self) -> None:
        # The header was built with chunk_alignment padding, so its size
        # is already sector-aligned -- it can be the very first
        # WriteFile call on the no-buffering handle.
        assert len(self._header_bytes) % self.chunk_alignment == 0, (
            len(self._header_bytes), self.chunk_alignment,
        )
        try:
            self._direct = _DirectIOFile(
                self.path, sector_size=self.chunk_alignment,
            )
        except OSError as exc:
            log.warning(
                "LineWriter direct-I/O open failed for %s: %s; "
                "falling back to buffered I/O",
                self.path, exc,
            )
            self.direct_io = False
            self.chunk_alignment = SWEEP_HEADER_ALIGN
            # Rebuild header at legacy alignment so the file's recorded
            # alignment matches the path we actually used.
            self._header_bytes = _build_line_header_bytes(
                width=self.width, height=self.height,
                line_meta=self._line_meta,
                codec=self.codec,
                pixel_format=self.pixel_format,
                codec_extras=self._codec_extras,
                chunk_alignment=self.chunk_alignment,
            )
            self._open_buffered()
            return
        if self.prealloc_bytes > 0:
            try:
                # Round prealloc up to sector boundary; SetEndOfFile on
                # a no-buffering handle insists on a multiple of the
                # sector size.
                prealloc = _align_up(self.prealloc_bytes, self.chunk_alignment)
                self._direct.set_end_of_file(prealloc)
                self._prealloc_ok = True
            except OSError as exc:
                log.warning(
                    "LineWriter direct-I/O prealloc of %d bytes failed "
                    "for %s: %s; continuing without prealloc",
                    self.prealloc_bytes, self.path, exc,
                )
        # The header is already sector-aligned bytes; write it via the
        # direct handle so the first chunk lands on a sector boundary.
        self._direct.write(bytes(self._header_bytes))
        # Allocate the chunk-header scratch buffer to one sector. Chunk
        # headers are < 1 KB, so one sector is always enough.
        self._chunk_hdr_scratch = _aligned_zero_buffer(
            self.chunk_alignment, alignment=self.chunk_alignment,
        )

    def append_chunk(
        self,
        payload: "bytes | memoryview | bytearray",
        *,
        sweep: int,
        frame_in_sweep: int,
        frame_meta: dict[str, Any],
        gps: Optional[dict[str, Any]] = None,
        attitude: Optional[dict[str, Any]] = None,
        payload_view_size: Optional[int] = None,
    ) -> None:
        """Append one frame chunk = chunk header + payload.

        ``payload`` may be a ``bytes`` object, a ``bytearray``, or a
        ``memoryview`` into a ``SharedMemory.buf`` slice. The io_writer
        hot path passes a memoryview directly so the 213 MB packed
        chunk goes from packed-SHM to disk without an intermediate
        heap memcpy.

        ``payload_view_size`` (optional) is the byte count to RECORD as
        the chunk's logical payload size. ``len(payload)`` is how many
        bytes get written to disk. The two differ in direct-I/O mode:
        the on-disk write is rounded up to a sector boundary, but the
        recorded ``payload_len`` is the real (pre-pad) byte count so
        offline readers unpack only valid bytes. Callers in direct-I/O
        mode are expected to pass a buffer whose tail bytes (between
        real payload end and sector boundary) are safe to write -- the
        packed SHM slot satisfies this since the slot is 1.14x larger
        than a packed-14 payload.

        Caller is responsible for keeping the SHM slot alive for the
        synchronous duration of this call (the io_writer releases the
        slot only after ``append_chunk`` returns).
        """
        if self._f is None and self._direct is None:
            raise RuntimeError(f"LineWriter not open: {self.path!r}")
        # Make sure sweep / frame_in_sweep are visible to per-chunk
        # recovery tools that don't trust the trailing index.
        per_frame: dict[str, Any] = dict(frame_meta)
        per_frame.setdefault("sweep", int(sweep))
        per_frame.setdefault("frame_in_sweep", int(frame_in_sweep))
        real_payload_len = (
            int(payload_view_size) if payload_view_size is not None
            else len(payload)
        )
        chunk_header = _build_frame_chunk_header(
            frame_meta=per_frame,
            gps=gps,
            attitude=attitude,
            payload_len=real_payload_len,
            alignment=self.chunk_alignment,
        )
        if self.direct_io:
            self._append_chunk_direct(
                payload, real_payload_len, chunk_header,
                sweep, frame_in_sweep,
            )
        else:
            self._append_chunk_buffered(
                payload, real_payload_len, chunk_header,
                sweep, frame_in_sweep,
            )

    def _append_chunk_buffered(
        self,
        payload: Any,
        real_payload_len: int,
        chunk_header: bytes,
        sweep: int,
        frame_in_sweep: int,
    ) -> None:
        offset = self._f.tell()
        t_w = time.perf_counter()
        self._f.write(chunk_header)
        # In buffered mode, write exactly real_payload_len bytes. If the
        # caller passed a larger buffer (a sector-aligned view), don't
        # emit the trailing pad.
        if len(payload) == real_payload_len:
            self._f.write(payload)
        else:
            mv = memoryview(payload)
            try:
                self._f.write(mv[:real_payload_len])
            finally:
                mv.release()
        self._sum_write_s += time.perf_counter() - t_w
        self._sum_payload += real_payload_len
        self._records.append((
            int(sweep), int(frame_in_sweep), int(offset),
            int(real_payload_len), int(self.uncompressed_bytes),
        ))

    def _append_chunk_direct(
        self,
        payload: Any,
        real_payload_len: int,
        chunk_header: bytes,
        sweep: int,
        frame_in_sweep: int,
    ) -> None:
        align = self.chunk_alignment
        # Chunk-header write: blit the variable-length chunk_header bytes
        # into a reusable sector-aligned scratch buffer and write that.
        scratch = self._chunk_hdr_scratch
        # The chunk header is already padded to ``align`` by
        # ``_build_frame_chunk_header``, so it fits in one sector.
        # _aligned_zero_buffer starts zeroed; we just need to overwrite
        # the prefix and zero the leftover trailing bytes since the
        # previous frame may have left stale content in them.
        scratch_len = len(scratch)
        n_hdr = len(chunk_header)
        assert n_hdr <= scratch_len, (n_hdr, scratch_len)
        scratch[:n_hdr] = chunk_header
        if n_hdr < scratch_len:
            # Zero-fill the trailing pad so old data doesn't leak between
            # chunks. This is a tiny memset (< 1 KB typically).
            for i in range(n_hdr, scratch_len):
                scratch[i] = 0
        # The chunk's on-disk start = current direct handle position.
        offset = self._direct.logical_position
        # Compute the sector-aligned payload write size. Caller must
        # have provided at least that many bytes in the payload view.
        write_size = _align_up(real_payload_len, align)
        if len(payload) < write_size:
            raise ValueError(
                f"direct-I/O append_chunk needs >= {write_size} bytes "
                f"in payload view, got {len(payload)}"
            )
        t_w = time.perf_counter()
        self._direct.write(scratch)
        self._direct.write(memoryview(payload)[:write_size])
        self._sum_write_s += time.perf_counter() - t_w
        self._sum_payload += real_payload_len
        self._records.append((
            int(sweep), int(frame_in_sweep), int(offset),
            int(real_payload_len), int(self.uncompressed_bytes),
        ))

    def n_frames(self) -> int:
        return len(self._records)

    def close_with_index(self, *, fsync: bool = True) -> dict[str, Any]:
        """Append the index, patch the file header (K + index_offset),
        and close the file. Returns a small stats dict for logging.

        For direct-I/O lines, the no-buffering handle closes first; the
        index + header patch happen via a regular buffered re-open
        (the header patch at offset 12 is not sector-aligned, so it
        can't go through the no-buffering handle).
        """
        if self._f is None and self._direct is None:
            raise RuntimeError(f"LineWriter not open: {self.path!r}")

        if self._direct is not None:
            data_end = self._direct.logical_position
            try:
                self._direct.close()
            finally:
                self._direct = None
            # Reopen with regular buffered I/O for the close-time
            # housekeeping (index append, header patch, truncate).
            try:
                self._f = open(self.path, "rb+")
            except OSError as exc:
                # We've already written all the data; if we can't reopen
                # to add the index, the file is still recoverable via
                # recover_line_chunks().
                log.warning(
                    "LineWriter direct-I/O close: reopen for index "
                    "failed for %s: %s; leaving file without trailing "
                    "index (recover_line_chunks still works)",
                    self.path, exc,
                )
                wall_s = time.perf_counter() - self._opened_at
                return {
                    "path": self.path,
                    "codec": self.codec,
                    "n_frames": len(self._records),
                    "payload_total": self._sum_payload,
                    "header_size": len(self._header_bytes),
                    "index_size": 0,
                    "write_s_total": self._sum_write_s,
                    "wall_s": wall_s,
                }
            self._f.seek(data_end)
            index_start = data_end
        else:
            index_start = self._f.tell()

        try:
            n = len(self._records)
            index_bytes = bytearray(n * LINE_INDEX_ENTRY_SIZE)
            for i, (sweep, f_in, offset, plen, unc) in enumerate(self._records):
                base = i * LINE_INDEX_ENTRY_SIZE
                index_bytes[base:base + 4] = sweep.to_bytes(4, "little")
                index_bytes[base + 4:base + 8] = f_in.to_bytes(4, "little")
                index_bytes[base + 8:base + 16] = offset.to_bytes(8, "little")
                index_bytes[base + 16:base + 24] = plen.to_bytes(8, "little")
                index_bytes[base + 24:base + 32] = unc.to_bytes(8, "little")
            self._f.write(bytes(index_bytes))
            final_size = index_start + len(index_bytes)
            # Patch K (bytes 12..15) and index_offset (16..23).
            self._f.seek(12)
            self._f.write(int(n).to_bytes(4, "little"))
            self._f.write(int(index_start).to_bytes(8, "little"))
            self._f.flush()
            # Shrink the file back to the actual written size when we
            # pre-allocated more than we used. ``ftruncate`` on Windows
            # safely shrinks the file (it just updates the EOF marker
            # and releases the over-allocated clusters); no zero-fill
            # is involved when shrinking.
            current_size = os.path.getsize(self.path)
            if self._prealloc_ok and current_size > final_size:
                try:
                    os.ftruncate(self._f.fileno(), final_size)
                except OSError as exc:
                    log.warning(
                        "LineWriter truncate-on-close failed for %s: %s",
                        self.path, exc,
                    )
            if fsync:
                try:
                    os.fsync(self._f.fileno())
                except OSError:
                    # Best-effort: not every Windows handle supports
                    # fsync (e.g. some network mounts). Don't fail
                    # the close.
                    pass
        finally:
            self._f.close()
            self._f = None
        wall_s = time.perf_counter() - self._opened_at
        return {
            "path": self.path,
            "codec": self.codec,
            "n_frames": n,
            "payload_total": self._sum_payload,
            "header_size": len(self._header_bytes),
            "index_size": n * LINE_INDEX_ENTRY_SIZE,
            "write_s_total": self._sum_write_s,
            "wall_s": wall_s,
        }

    def abort(self) -> None:
        """Close the file without writing an index.

        Used when a writer subprocess shuts down on a hard error.
        The file remains parseable via :func:`recover_line_chunks`
        because every chunk starts with the BAY14FRM magic.
        """
        if self._direct is not None:
            try:
                self._direct.close()
            except Exception:
                pass
            self._direct = None
        if self._f is not None:
            try:
                self._f.flush()
            except Exception:
                pass
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None


def recover_line_chunks(
    path: str,
) -> tuple[dict[str, Any], list[tuple[int, int, int, int, int]]]:
    """Walk a .bay14line file linearly, rebuilding the index from chunk
    magic numbers. Use when ``index_offset`` is 0 (writer killed
    mid-line).

    Returns ``(file_header_meta, [(sweep, frame_in_sweep, offset,
    payload_len, uncompressed_pixel_bytes), ...])`` for every chunk
    we could parse. Stops at the first byte sequence that doesn't
    start with the BAY14FRM magic (= corrupt tail; truncate there).
    """
    with open(path, "rb") as f:
        prefix = f.read(LINE_HEADER_FIXED_PREFIX)
        header_len, _n_frames_field, _idx_off, _ = parse_line_header_prefix(prefix)
        f.seek(0)
        full_hdr = f.read(header_len)
        meta_dict, header_len, _, _ = parse_line_header(full_hdr)
        # Files written via the direct-I/O writer pad each chunk
        # (header + payload) up to a sector boundary so each
        # ``WriteFile`` is sector-aligned. The padding sits in the gap
        # between (payload_end) and (next chunk's header_start). The
        # recovery walk needs to seek past that pad to land on the next
        # BAY14FRM magic; legacy files don't pad payloads, so we only
        # round-up the seek when the writer recorded an alignment
        # *larger* than the legacy SWEEP_HEADER_ALIGN.
        chunk_alignment = int(meta_dict.get("chunk_alignment", SWEEP_HEADER_ALIGN))
        pad_payload = chunk_alignment > SWEEP_HEADER_ALIGN
        records: list[tuple[int, int, int, int, int]] = []
        unc = int(meta_dict.get("width", 0)) * int(meta_dict.get("height", 0)) * 2
        f.seek(header_len)
        while True:
            here = f.tell()
            head_prefix = f.read(16)
            if len(head_prefix) < 16:
                break
            if head_prefix[:8] != SWEEP_FRAME_HEADER_MAGIC:
                break  # past last good chunk; truncate here
            hdr_len_chunk = int.from_bytes(head_prefix[8:12], "little")
            payload_len = int.from_bytes(head_prefix[12:16], "little")
            if hdr_len_chunk < 16 or hdr_len_chunk % SWEEP_HEADER_ALIGN != 0:
                break
            rest = f.read(hdr_len_chunk - 16)
            if len(rest) != hdr_len_chunk - 16:
                break
            json_blob = rest.rstrip(b"\x00")
            try:
                chunk_meta = json.loads(json_blob.decode("utf-8")) if json_blob else {}
            except Exception:
                chunk_meta = {}
            sweep = int(chunk_meta.get("sweep", 0))
            f_in = int(chunk_meta.get("frame_in_sweep", 0))
            if pad_payload:
                skip_bytes = _align_up(payload_len, chunk_alignment)
            else:
                skip_bytes = payload_len
            f.seek(skip_bytes, 1)  # SEEK_CUR -- skip payload (+ pad in direct-I/O)
            records.append((sweep, f_in, here, payload_len, unc))
        return meta_dict, records
