"""Content-independent PSF width per frame from natural step edges (10-90% rise of the edge spread function).
Full method, calibration and caveats: HANDOFF.md sec.52. The metric itself lives in host/capture/psf_w10.py (so the
autofocus can import it); this file is the file readers + CLI and re-exports edge_widths / array_edges unchanged.
Per frame: w10 (10th percentile = sharpest edges ~ PSF), w20, w50, n edges, per-ROI w10, w20 per edge-normal
orientation bin (30 deg), and the lens encoder count from the frame's ImageDescription `focus` block (NaN if absent).
    py flight_edges.py <out.csv> <tif_or_folder>... [--step N]
Self-check: tools/test_flight_edges_floor.py (calibration), tools/test_psf_w10_identical.py (speed-up identity).
Bench V-curve from the CSV: tools/w10_vcurve.py."""
import os, sys, csv, json, glob, numpy as np, tifffile

_HOST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host")
if _HOST not in sys.path:
    sys.path.insert(0, _HOST)
from capture.psf_w10 import (  # noqa: E402,F401  (re-exported for the tests and older scripts)
    BANDS, MIN_H, MIN_W, NB, ROI, S, TT, array_edges, edge_widths,
)


def frame_edges(path, **kw):
    """One exported TIFF: 16-bit Bayer or RGB (Export Studio / Bay14Reader), `focus` block in the ImageDescription JSON.
    Refuses resized previews and 8-bit data (neither is comparable); un-flips `flipped_lr` exports; lifts unshifted
    14-bit data to the 16-bit scale. The `src` column records exactly what was done. ``kw`` goes to ``array_edges``."""
    with tifffile.TiffFile(path) as t:
        p = t.pages[0]; off = p.dataoffsets[0]; shape = p.shape; desc = p.description; dt = np.dtype(p.dtype)
    meta = {}
    try: meta = json.loads(desc) or {}
    except (ValueError, TypeError, AttributeError): pass
    h, w = shape[:2]
    if w < MIN_W or h < MIN_H: raise ValueError(f"frame {w}x{h} too small for the ROIs -- resized preview?")
    if dt != np.uint16: raise ValueError(f"{dt} TIFF -- only 16-bit data is comparable")
    rgb = len(shape) == 3
    demo = str(meta.get("demosaic_opencv_rgb", ""))
    if rgb and demo and demo not in ("BayerBG2RGB", "BayerRG2RGB"):   # GB/GR phases put green on the other diagonal
        raise ValueError(f"RGB demosaiced as {demo}: green plane differs from the raw pipeline")
    mm = np.memmap(path, dtype=dt, mode="r", offset=int(off), shape=shape)
    try:
        # 14-bit data left unshifted (legacy bay14_to_tif) tops out at 16383; exports say so in value_linear_scale
        scale = 1 if meta.get("value_linear_scale") or int(mm[::97, ::89].max()) > 16383 else 4
        flip = bool(meta.get("flipped_lr"))
        out = array_edges(mm, meta.get("focus"), flip_lr=flip, scale=scale, **kw)
    finally:
        del mm
    out["src"] = ("rgb:" + (demo or "?") if rgb else "bayer") + (":unflipped" if flip else "") + (f":x{scale}" if scale != 1 else "")
    out["ts"] = meta.get("acquire_timestamp", "")
    return out


def iter_sources(srcs, step):
    """Yield (label, callable -> stats) for every frame in TIFF files/folders and .bay14line containers, every Nth."""
    files = []
    for s in srcs:
        if any(c in s for c in "*?["): files += sorted(glob.glob(s))
        elif os.path.isdir(s): files += sorted(os.path.join(s, f) for f in os.listdir(s) if f.lower().endswith((".tif", ".tiff", ".bay14line")))
        else: files.append(s)
    n = 0
    for path in files:
        if path.lower().endswith(".bay14line"):
            # score the capture container directly -- raw 14-bit frames, so apply the export's << 2 (HANDOFF sec.52.2 scale)
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from bay14_export_core import iter_line_frames, shift_14_to_16
            i = -1
            for i, (meta, arr) in enumerate(iter_line_frames(path, recovery_notice=None)):
                if (n + i) % step == 0:
                    yield (f"{os.path.basename(path)}#S{meta['sweep']}_F{meta['frame_in_sweep']}",
                           lambda a=arr, m=meta: dict(array_edges(shift_14_to_16(a), m.get("focus")), src="bay14line",
                                                      ts=m.get("acquire_timestamp", "")))
            n += i + 1
        else:
            if n % step == 0:
                yield os.path.basename(path), lambda p=path: frame_edges(p)
            n += 1


KEYS = (["file", "src", "ts", "n_edges", "n_roi", "med_mean", "w10", "w20", "w50", "encoder", "enc_stale"]
        + [f"r{k}_w10" for k in range(5)] + [f"w20_ang{b * 180 // NB}" for b in range(NB)] + ["err"])


def main():
    """py flight_edges.py <out.csv> <tif|bay14line|folder|glob> [...] [--step N] [--list FILE]
    Folders expand to their *.tif / *.bay14line (sorted), globs are expanded (quote them); --step N keeps every Nth
    frame; --list FILE adds one path per line of FILE. One CSV row per frame."""
    args = sys.argv[1:]; step = 1
    if "--step" in args:
        i = args.index("--step"); step = int(args[i + 1]); del args[i:i + 2]
    if "--list" in args:
        i = args.index("--list"); lst = args[i + 1]; del args[i:i + 2]
        args += [ln.strip() for ln in open(lst, encoding="utf-8") if ln.strip()]
    out, srcs = args[0], args[1:]
    with open(out, "w", newline="") as fh:
        wtr = csv.DictWriter(fh, fieldnames=KEYS, extrasaction="ignore"); wtr.writeheader()
        for i, (label, score) in enumerate(iter_sources(srcs, step)):
            rec = dict(file=label)
            try: rec.update(score())
            except Exception as ex: rec["err"] = str(ex)
            wtr.writerow(rec)
            if i % 20 == 0: print(i, label, flush=True); fh.flush()
    print("wrote", out)


if __name__ == "__main__":
    main()
