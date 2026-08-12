"""Field map: one motor sweep, several fixed tiles, one focus curve each.

Autofocus answers "where is focus?" for ONE patch. This answers a different
question: **does focus live in the same place everywhere in the frame?** A tilted
screen or a curved field puts the centre and the corners at different best-focus
positions, and until that is measured in counts it silently contaminates every AF
comparison -- two AF runs that happened to pick tiles at different heights
disagree for optical reasons that have nothing to do with the estimator.

Measuring it by running AF once per region is the slow, noisy way: each run
re-zeros the encoder, re-seats the lash, re-parks, and lands minutes apart. Every
one of those is a difference between the runs that is NOT optics.

So instead: pin K tiles at fixed sensor coordinates, sweep the motor DOWN once and
back UP once, and on every frame score all K tiles from the SAME full-res crop
batch. All K curves then share identical encoder positions, identical frames and
identical mechanical state, so the difference between their vertices is optics and
nothing else. It also never has to park, which keeps the whole park bias out of
the measurement.

**The scene must be frozen.** Fixed pixel ROIs only mean something if the content
under them holds still. That is no loss: field curvature is a property of the
optics, not of the picture.

The pipeline already ships several full-res crops from one held slot
(``set_preview_rois`` / ``raw_focus_preview_crops``, built for the two-object
preview but never limited to two), so this module only has to choose the ROIs,
pace the motor, score, and fit.
"""

from __future__ import annotations

import csv
import datetime as _dt
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .autofocus import focus_measure_full_res
from .autofocus_v2 import _fit_peak
from .focus_axis import FocusAxis
from .session_log import default_log_dir

log = logging.getLogger(__name__)

Roi = tuple[int, int, int, int]

TILE_NAMES: tuple[str, ...] = ("centre", "top", "bottom", "left", "right")


@dataclass
class FieldMapConfig:
    """Sweep geometry. Defaults mirror the AF engine's own dive/climb pacing so a
    field map covers the same focus range AF searches."""

    tile_px: int = 600
    """Square tile side in FULL-RES sensor pixels. Ample for a Laplacian variance
    and small enough that K of them stay cheap: the AF winning-tile ROI is ~2820 x
    2004 (11 MB), which at five tiles would copy 56 MB per frame."""
    inset: float = 0.15
    """Edge tiles sit this fraction in from the frame edge -- far enough out to see
    real field variation, far enough in to clear the bench screen's bezel and the
    optical ground-truth strip."""

    dive_strength: int = 3
    """str3 for the dive: ~850 counts/pulse, so the sweep floor is reached in a few
    pulses instead of a hundred."""
    dive_pulses: int = 4
    """str3 pulses down. One per burst, so the encoder is re-read between them and a
    stall or end stop shows up immediately."""
    dive_burst_interval_s: float = 0.7
    """Wall-clock gap between dive pulses, so each fully executes and the ~10 Hz
    encoder registers it before the next."""

    climb_strength: int = 0
    """str0 for the climb: ~24 counts/pulse, the finest step the hardware has."""
    climb_step_pulses: int = 2
    """str0 pulses per frame on the way up -- ~48 counts between samples."""
    climb_pulses: int = 260
    """TOTAL str0 pulses climbed, ~6200 counts. Must clear the dive (~3400 counts)
    and then carry every tile well past its own peak, since tiles peak at different
    places and the whole point is to see all the vertices.

    Unlike AF, the climb does NOT stop when a score peaks and falls: stopping on the
    first tile to crest would truncate the curves of the tiles that focus higher up,
    which are exactly the measurement."""

    return_step_pulses: int = 6
    """str0 pulses per frame on the way back down to the start, so a field map is
    non-destructive -- the lens ends where it began instead of parked ~2800 counts
    above focus, which would cost an AF run to undo after every sweep.

    Paced steps rather than one absolute ``move_to``: a single burst that size is
    what used to yank the lens around on AF stop and leave pulses draining into the
    next run's dive. Nothing is measured here, so the step can be coarse."""
    return_max_frames: int = 200
    """Give up returning after this many frames, so a stalled motor ends the sweep
    instead of stepping down forever."""

    fit_window: int = 3
    """Samples each side of the argmax used for the per-tile parabola fit."""


def five_tile_rois(
    full_w: int, full_h: int, cfg: Optional[FieldMapConfig] = None
) -> list[Roi]:
    """Centre + the four mid-edge tiles, in FULL-RES sensor pixels.

    Centre-plus-mid-edges rather than corners because it separates the two failure
    modes: a TILTED screen makes focus vary linearly, so top and bottom sit on
    opposite sides of centre, while a CURVED field is symmetric, so top and bottom
    move the same way. Corners confound the two.

    Origins are forced even so ``crop[0::2, 1::2]`` (the green sub-grid every score
    is taken on) lands on the same Bayer colour in every tile.
    """
    cfg = cfg or FieldMapConfig()
    side = max(8, min(int(cfg.tile_px), full_w, full_h))
    ins = min(max(float(cfg.inset), 0.0), 0.49)
    centres = {
        "centre": (0.5, 0.5),
        "top": (0.5, ins),
        "bottom": (0.5, 1.0 - ins),
        "left": (ins, 0.5),
        "right": (1.0 - ins, 0.5),
    }
    rois: list[Roi] = []
    for name in TILE_NAMES:
        fx, fy = centres[name]
        x0 = int(round(fx * full_w - side / 2.0)) & ~1
        y0 = int(round(fy * full_h - side / 2.0)) & ~1
        x0 = max(0, min(x0, full_w - side)) & ~1
        y0 = max(0, min(y0, full_h - side)) & ~1
        rois.append((x0, y0, x0 + side, y0 + side))
    return rois


@dataclass
class TileResult:
    name: str
    roi: Roi
    vertex: Optional[float]   # best-focus position, encoder counts (run-relative)
    argmax: Optional[float]   # highest-scoring sampled position, counts
    peak_score: float
    n: int


class FieldMapSweep:
    """Dive once, climb once, scoring K fixed tiles per frame.

    Driven entirely by :meth:`feed`, called with one batch of full-res crops (one
    per ROI, ``None`` where the pipeline had nothing). Qt-free: the caller owns the
    signal plumbing, this owns the motor pacing and the arithmetic.
    """

    def __init__(
        self,
        *,
        axis: FocusAxis,
        set_strength: Optional[Callable[[int], None]] = None,
        status: Optional[Callable[[str], None]] = None,
        finished: Optional[Callable[[bool, dict], None]] = None,
        config: Optional[FieldMapConfig] = None,
        names: Sequence[str] = TILE_NAMES,
        csv_dir: Optional[str] = None,
    ) -> None:
        self.cfg = config or FieldMapConfig()
        self._axis = axis
        self._set_strength = set_strength
        self._status = status or (lambda _m: None)
        self._finished = finished or (lambda _ok, _r: None)
        self._names = tuple(names)
        self._csv_dir = csv_dir
        self.rois: list[Roi] = []
        self.state = "idle"
        self._samples: list[tuple[float, float, list[float]]] = []
        self._dive_left = 0
        self._dive_last_t = 0.0
        self._climb_done = 0
        self._return_frames = 0
        self._start_raw = 0.0

    @property
    def running(self) -> bool:
        return self.state in ("dive", "climb", "return")

    def start(self, rois: Sequence[Roi]) -> None:
        """Zero the axis, drop to the sweep floor, and begin collecting."""
        self.rois = [tuple(int(v) for v in r) for r in rois]  # type: ignore[misc]
        self._samples = []
        self._climb_done = 0
        self._return_frames = 0
        self._axis.reset()
        self._start_raw = float(getattr(self._axis, "raw_count", 0.0))
        self._set_strength_safe(self.cfg.dive_strength)
        self._dive_left = max(1, int(self.cfg.dive_pulses))
        self._dive_last_t = 0.0  # 0 => first pulse fires on the next feed
        self.state = "dive"
        self._status("field map: diving to the sweep floor")
        log.info(
            "fieldmap start tiles=%d raw=%d dive=%dp@str%d climb=%dp@str%d",
            len(self.rois), int(self._start_raw), self.cfg.dive_pulses,
            self.cfg.dive_strength, self.cfg.climb_pulses, self.cfg.climb_strength,
        )

    def stop(self, reason: str = "stopped") -> None:
        """Halt where we are -- no motor move. Whatever was collected is reported."""
        if not self.running:
            return
        log.info("fieldmap %s at %d samples", reason, len(self._samples))
        self._finish(reason)

    def feed(self, crops: Sequence) -> None:
        """One frame's worth of full-res tile crops. Paces the motor and samples."""
        if self.state == "dive":
            self._dive_tick()
        elif self.state == "climb":
            self._climb_tick(crops)
        elif self.state == "return":
            self._return_tick()

    # -- phases -----------------------------------------------------------

    def _dive_tick(self) -> None:
        now = time.monotonic()
        if self._dive_last_t and (now - self._dive_last_t) < self.cfg.dive_burst_interval_s:
            return
        self._dive_last_t = now
        if self._dive_left <= 0:
            self._begin_climb()
            return
        self._axis.step_pulses(-1, 1)
        self._dive_left -= 1
        log.info("fieldmap dive pos=%+dc left=%d",
                 int(round(float(self._axis.position))), self._dive_left)

    def _begin_climb(self) -> None:
        self._set_strength_safe(self.cfg.climb_strength)
        self.state = "climb"
        self._status("field map: climbing")
        log.info("fieldmap climb begin floor=%+dc",
                 int(round(float(self._axis.position))))

    def _climb_tick(self, crops: Sequence) -> None:
        pos = float(self._axis.position)
        raw = float(getattr(self._axis, "raw_count", 0.0))
        scores = [
            focus_measure_full_res(crops[i]) if i < len(crops) else 0.0
            for i in range(len(self.rois))
        ]
        self._samples.append((pos, raw, scores))
        if self._climb_done >= self.cfg.climb_pulses:
            self.state = "return"
            self._status("field map: returning to start")
            log.info("fieldmap return begin from=%+dc", int(round(pos)))
            return
        step = min(self.cfg.climb_step_pulses, self.cfg.climb_pulses - self._climb_done)
        self._axis.step_pulses(1, step)
        self._climb_done += step

    def _return_tick(self) -> None:
        pos = float(self._axis.position)
        step = max(1, int(self.cfg.return_step_pulses))
        if pos <= step * self._cpp():
            self._finish("complete")
            return
        if self._return_frames >= self.cfg.return_max_frames:
            log.warning("fieldmap return gave up at %+dc (motor stalled?)",
                        int(round(pos)))
            self._finish("complete")
            return
        self._return_frames += 1
        self._axis.step_pulses(-1, step)

    def _cpp(self) -> float:
        return float(getattr(self._axis, "counts_per_pulse_mag", 1.0)) or 1.0

    # -- results ----------------------------------------------------------

    def _finish(self, reason: str) -> None:
        self.state = "done"
        results = self.results()
        csv_path = self.write_csv()
        report = {
            "reason": reason,
            "samples": len(self._samples),
            "start_raw": self._start_raw,
            "tiles": results,
            "csv": csv_path,
            "lines": self.summary_lines(results, csv_path),
        }
        for line in report["lines"]:
            log.info("%s", line)
        self._status(f"field map: {reason} ({len(self._samples)} samples)")
        self._finished(reason == "complete", report)

    def results(self) -> list[TileResult]:
        """Fit one vertex per tile from the shared sweep."""
        out: list[TileResult] = []
        for i, roi in enumerate(self.rois):
            curve = [(p, s[i]) for p, _raw, s in self._samples if i < len(s)]
            name = self._names[i] if i < len(self._names) else f"tile{i}"
            if not curve:
                out.append(TileResult(name, roi, None, None, 0.0, 0))
                continue
            best = max(curve, key=lambda ps: ps[1])
            vertex = _fit_peak(curve, self.cfg.fit_window)
            out.append(
                TileResult(name, roi, vertex, best[0], best[1], len(curve))
            )
        return out

    def summary_lines(
        self, results: list[TileResult], csv_path: Optional[str]
    ) -> list[str]:
        """Human-readable verdict: each tile's vertex, its offset from centre, and
        the total spread -- which IS the field error, in encoder counts."""
        span = 0.0
        if self._samples:
            span = self._samples[-1][0] - self._samples[0][0]
        lines = [
            f"field-map {len(self._samples)} samples over {span:+.0f}c "
            f"(raw origin {self._start_raw:.0f})"
        ]
        by_name = {r.name: r for r in results}
        ref = by_name.get("centre")
        ref_v = ref.vertex if ref is not None else None
        for r in results:
            if r.vertex is None:
                lines.append(f"field-map {r.name:<7} no peak (n={r.n})")
                continue
            rel = ""
            if ref_v is not None and r.name != "centre":
                rel = f" d={r.vertex - ref_v:+.0f}c vs centre"
            lines.append(
                f"field-map {r.name:<7} vertex={r.vertex:+.0f}c "
                f"argmax={r.argmax:+.0f}c n={r.n}{rel}"
            )
        got = [r for r in results if r.vertex is not None]
        if len(got) >= 2:
            hi = max(got, key=lambda r: r.vertex)  # type: ignore[arg-type]
            lo = min(got, key=lambda r: r.vertex)  # type: ignore[arg-type]
            cpp = self._cpp()
            spread = hi.vertex - lo.vertex  # type: ignore[operator]
            lines.append(
                f"field-map SPREAD {spread:.0f}c ({spread / cpp:.1f} str0 pulses): "
                f"{hi.name} focuses highest, {lo.name} lowest"
            )
        if csv_path:
            lines.append(f"field-map csv {csv_path}")
        return lines

    def write_csv(self, log_dir: Optional[str] = None) -> Optional[str]:
        """Dump every sample. Written once at the end rather than flushed per row:
        the sweep is only ~130 rows, and a per-row flush onto the Google-Drive
        mount is exactly what took the OrthoSim ground-truth logger down."""
        if not self._samples:
            return None
        directory = log_dir or self._csv_dir or default_log_dir()
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(directory, f"Iron661_fieldmap_{stamp}.csv")
        names = [
            self._names[i] if i < len(self._names) else f"tile{i}"
            for i in range(len(self.rois))
        ]
        try:
            os.makedirs(directory, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["# roi", *(f"{n}={r}" for n, r in zip(names, self.rois))])
                w.writerow(["sample", "pos_counts", "raw_counts", *names])
                for n, (pos, raw, scores) in enumerate(self._samples):
                    w.writerow([n, f"{pos:.1f}", f"{raw:.1f}",
                                *(f"{s:.6g}" for s in scores)])
            return path
        except Exception as exc:  # pragma: no cover - I/O environment dependent
            print(f"FieldMapSweep: could not write {path!r}: {exc!r}", file=sys.stderr)
            return None

    def _set_strength_safe(self, level: int) -> None:
        if self._set_strength is None:
            return
        try:
            self._set_strength(int(level))
        except Exception:  # pragma: no cover - actuator guard
            log.exception("field map: set_strength failed")
