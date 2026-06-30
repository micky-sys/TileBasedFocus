"""Reconstruct a classic uint16 TIFF from a captured .bay14 file.

The live capture pipeline writes each sweep frame as a single
``.bay14`` file with a small embedded JSON header (magic ``BAY14HDR``)
followed by raw 14-bit-packed Bayer data. Most downstream tooling
expects classic uncompressed uint16 TIFFs, so this tool reads the
header, unpacks the pixel bytes into a 2-D uint16 ndarray, and
writes a TIFF preserving the original metadata in the
``ImageDescription`` JSON.

Usage::

    python tools/bay14_to_tif.py D:\\my_project\\Cam2439008_20260428_my_project_L1_S1_1.bay14
    python tools/bay14_to_tif.py D:\\my_project\\*.bay14 --out-dir D:\\my_project_tif

For an interactive viewer + bulk export across all 3 cameras at once,
see ``tools/bay14_viewer.py``.

Notes:

* The pack format and header layout are documented in
  ``host/capture/raw_bay_writer.py``.
* The unpacker is the reference implementation (correctness over
  speed); a 13400x9528 frame takes ~150-300 ms on a modern CPU.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import tifffile

# Add the host folder to sys.path so we can import the unpacker
# and header parser. We keep both living next to the writer so
# they can never drift.
_HOST = Path(__file__).resolve().parent.parent / "host"
if str(_HOST) not in sys.path:
    sys.path.insert(0, str(_HOST))

from bay14_export_core import (  # noqa: E402
    iter_line_frames,
    write_tiff_bayer_u16,
)
from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_NONE,
    LINE_HEADER_MAGIC,
    SWEEP_HEADER_MAGIC,
    SWEEP_INDEX_ENTRY_SIZE,
    decompress_packed,
    parse_frame_chunk_header,
    parse_header,
    parse_sweep_header,
    parse_sweep_index,
    unpack_u14,
)


def _expand_inputs(patterns: list[str]) -> list[Path]:
    """Expand user-supplied patterns into a list of ``.bay14`` paths."""
    out: list[Path] = []
    for pat in patterns:
        if any(c in pat for c in "*?["):
            out.extend(Path(p) for p in sorted(glob.glob(pat)))
        else:
            out.append(Path(pat))
    return out


def _read_with_header(bay_path: Path) -> tuple[dict, np.ndarray]:
    """Read a .bay14 file: parse its embedded header, return
    ``(metadata_dict, packed_uint8_ndarray)``.

    The returned ``packed`` array is always the UNCOMPRESSED packed-14
    pixel bytes (i.e. the input to :func:`unpack_u14`). When the file
    was written with ``compression='lz4'`` (or anything else
    non-trivial), this function decompresses transparently so the
    caller never has to know which codec the producer used.

    Tolerates files written by an older sidecar-based version of
    the writer for backwards compatibility: if the file does not
    start with ``BAY14HDR``, falls back to looking for a
    ``<path>.json`` sidecar (always uncompressed; compression was
    only ever wired into the embedded-header branch).
    """
    raw = bay_path.read_bytes()
    if raw[:8] == b"BAY14HDR":
        meta, header_len, data_len = parse_header(raw[:max(64, len(raw))])
        on_disk = bytes(raw[header_len:header_len + data_len])
        if len(on_disk) != data_len:
            raise ValueError(
                f"{bay_path}: data length mismatch -- header says "
                f"{data_len} bytes, file has {len(on_disk)}"
            )
        codec = str(meta.get("compression", COMPRESSION_NONE))
        # Older files (pre-compression) didn't carry uncompressed_size;
        # fall back to deriving it from geometry, which is always correct
        # for the packed-14 layout.
        h = int(meta["height"])
        w = int(meta["width"])
        expected_pack = h * w * 7 // 4
        uncompressed = int(meta.get("uncompressed_size", expected_pack))
        packed = decompress_packed(on_disk, codec=codec,
                                   expected_size=uncompressed)
        return meta, packed
    sidecar = bay_path.with_suffix(bay_path.suffix + ".json")
    if not sidecar.exists():
        raise FileNotFoundError(
            f"{bay_path}: no embedded header AND no sidecar at {sidecar}"
        )
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    packed = np.frombuffer(raw, dtype=np.uint8)
    return meta, packed


def convert_one(bay_path: Path, *, out_dir: Path | None = None) -> Path:
    """Convert a single ``.bay14`` to a ``.tif`` next to it (or in
    *out_dir* if given). Returns the written ``.tif`` path.
    """
    meta, packed = _read_with_header(bay_path)

    h = int(meta["height"])
    w = int(meta["width"])
    n_pixels = h * w
    expected = n_pixels * 7 // 4
    if packed.size != expected:
        raise ValueError(
            f"{bay_path}: packed size {packed.size} != expected {expected} "
            f"(geometry {h}x{w})"
        )

    arr = unpack_u14(packed, n_pixels).reshape(h, w)

    if out_dir is None:
        out_path = bay_path.with_suffix(".tif")
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (bay_path.stem + ".tif")

    description = json.dumps(meta, separators=(",", ":"))
    tifffile.imwrite(
        str(out_path),
        arr,
        photometric="minisblack",
        compression=None,
        bigtiff=False,
        description=description,
    )
    return out_path


def _is_sweep_file(path: Path) -> bool:
    """Return True if *path* looks like a ``.bay14sweep`` (extension or magic).

    We check the extension first (cheap) and fall back to peeking
    the first 8 bytes for the magic. Lets the tool transparently
    accept files renamed for some reason while still rejecting
    truly random bytes.
    """
    if path.suffix.lower() == ".bay14sweep":
        return True
    try:
        with open(path, "rb") as f:
            return f.read(len(SWEEP_HEADER_MAGIC)) == SWEEP_HEADER_MAGIC
    except OSError:
        return False


def convert_sweep(
    sweep_path: Path,
    *,
    out_dir: Path | None = None,
) -> list[Path]:
    """Expand one ``.bay14sweep`` into N ``.tif`` files (one per frame).

    Output naming: ``{sweep_stem}_F{frame_in_sweep}.tif`` so the per-
    frame outputs sort naturally next to each other and don't collide
    with any pre-batching ``.bay14``-derived ``.tif`` files in the
    same directory.

    Returns the list of written .tif paths in frame-in-sweep order.
    Raises on header / index / payload corruption -- partial writes
    are NOT silently elided so the operator finds out about the bad
    file rather than ending up with a quietly-missing frame.
    """
    raw = sweep_path.read_bytes()
    sweep_meta, header_len, n = parse_sweep_header(
        raw[:max(64, len(raw))]
    )
    index_blob = raw[header_len:header_len + n * SWEEP_INDEX_ENTRY_SIZE]
    index = parse_sweep_index(index_blob, n)
    h = int(sweep_meta["height"])
    w = int(sweep_meta["width"])
    n_pixels = h * w
    expected_pack = n_pixels * 7 // 4
    codec = str(sweep_meta.get("compression", COMPRESSION_NONE))

    out_paths: list[Path] = []
    for f_in, offset, payload_len, uncompressed in index:
        # Read this chunk's header + payload.
        chunk_blob = raw[offset:offset + 4096]  # 4 KB is plenty for the chunk header
        cmeta, c_hdr_len, c_payload_len = parse_frame_chunk_header(chunk_blob)
        if c_payload_len != payload_len:
            raise ValueError(
                f"{sweep_path}: chunk @offset {offset} payload_len mismatch "
                f"(chunk header says {c_payload_len}, sweep index says "
                f"{payload_len})"
            )
        payload_start = offset + c_hdr_len
        payload = bytes(raw[payload_start:payload_start + payload_len])
        if uncompressed != expected_pack:
            raise ValueError(
                f"{sweep_path}: index uncompressed_size {uncompressed} "
                f"!= expected {expected_pack} for {h}x{w}"
            )
        packed = decompress_packed(
            payload, codec=codec, expected_size=uncompressed,
        )
        arr = unpack_u14(packed, n_pixels).reshape(h, w)

        # Merge the sweep-level + per-frame meta so each TIFF is
        # self-describing and the operator doesn't lose context
        # about which sweep this frame came from.
        merged_meta = dict(sweep_meta)
        merged_meta.update(cmeta)
        # ``frame_in_sweep`` is the per-chunk authoritative value;
        # belt-and-braces overlay so the index value can't shadow it.
        merged_meta["frame_in_sweep"] = int(f_in)
        description = json.dumps(merged_meta, separators=(",", ":"))

        out_name = f"{sweep_path.stem}_F{f_in}.tif"
        if out_dir is None:
            out_path = sweep_path.with_name(out_name)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / out_name

        tifffile.imwrite(
            str(out_path),
            arr,
            photometric="minisblack",
            compression=None,
            bigtiff=False,
            description=description,
        )
        out_paths.append(out_path)
    return out_paths


def _is_line_file(path: Path) -> bool:
    """Return True if *path* looks like a ``.bay14line`` (extension or magic)."""
    if path.suffix.lower() == ".bay14line":
        return True
    try:
        with open(path, "rb") as f:
            return f.read(len(LINE_HEADER_MAGIC)) == LINE_HEADER_MAGIC
    except OSError:
        return False


def convert_line(
    line_path: Path,
    *,
    out_dir: Path | None = None,
) -> list[Path]:
    """Expand one ``.bay14line`` into N ``.tif`` files (one per frame).

    Output naming: ``{line_stem}_S{sweep}_F{frame_in_sweep}.tif`` so
    extracted frames sort naturally and can't collide with the
    .bay14 / .bay14sweep tools' outputs in the same directory.

    If the file's index trailer is intact (``index_offset > 0`` in
    the file header), we read the index and walk frames in file
    order. If it is missing or corrupt -- e.g. the writer was killed
    mid-line -- we fall back inside :func:`iter_line_frames` to the same
    BAY14FRM linear recovery path used by :func:`recover_line_chunks`.
    operator gets every recoverable frame either way.

    Returns the list of written .tif paths in the order they were
    extracted (which is acquisition / arrival order, not necessarily
    sorted by sweep + frame_in_sweep -- the caller can re-sort by
    inspecting each TIFF's embedded JSON if strict order matters).
    """
    out_paths: list[Path] = []
    for merged_meta, arr in iter_line_frames(line_path):
        sweep = merged_meta["sweep"]
        f_in = merged_meta["frame_in_sweep"]
        out_name = f"{line_path.stem}_S{sweep}_F{f_in}.tif"
        if out_dir is None:
            out_path = line_path.with_name(out_name)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / out_name
        write_tiff_bayer_u16(out_path, arr, merged_meta, scale_14_to_16=False)
        out_paths.append(out_path)
    return out_paths


def convert_input(
    path: Path, *, out_dir: Path | None = None,
) -> list[Path]:
    """Dispatch to per-frame, per-sweep, or per-line converter by file type."""
    if _is_line_file(path):
        return convert_line(path, out_dir=out_dir)
    if _is_sweep_file(path):
        return convert_sweep(path, out_dir=out_dir)
    return [convert_one(path, out_dir=out_dir)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "inputs", nargs="+",
        help="One or more .bay14 / .bay14sweep / .bay14line paths or globs.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional output directory. Default: write each .tif next to its source file.",
    )
    args = parser.parse_args(argv)

    paths = _expand_inputs(args.inputs)
    if not paths:
        print("no .bay14 / .bay14sweep / .bay14line inputs matched",
              file=sys.stderr)
        return 1

    n_ok = 0
    for p in paths:
        try:
            out_paths = convert_input(p, out_dir=args.out_dir)
            for out_path in out_paths:
                print(f"  {p}  ->  {out_path}")
            n_ok += 1
        except Exception as exc:
            print(f"  {p}  ERROR: {exc}", file=sys.stderr)
    print(f"converted {n_ok}/{len(paths)} files")
    return 0 if n_ok == len(paths) else 2


if __name__ == "__main__":
    raise SystemExit(main())
