"""Put every AF run in a capture log into ONE session-wide position frame.

``AutoFocusEngineV2.start()`` calls ``self._axis.reset()``, and ``EncoderAxis.reset()``
sets the origin to the CURRENT encoder reading -- so every ``best_pos`` / ``af2-park
pos`` in the log is measured from wherever the lens sat when that button press landed,
which is the previous run's parked position. Reading those numbers across runs as if
they shared a zero silently compares deltas, which is exactly how a freeze test once
got read backwards.

``raw=`` (logged on the dive and park lines) is the pre-origin count from
EncoderTracker, which accumulates for the life of the process. That IS a common frame,
so this rebases everything onto it as ``P = -raw`` -- negated because
``af_counts_per_pulse`` is negative on this rig, i.e. a '+' pulse drives the raw count
DOWN, and we want P to increase on a '+' drive like the engine's own position does.

Self-check: with that model, each run's start MUST equal the previous run's park. The
``chain`` column prints the discrepancy; anything beyond a count or two means the model
(or the polarity) is wrong and the rest of the table should not be trusted.

Run:  py -3 tools/af_rebase_runs.py <terminal-or-session-log> [...]

      py -3 tools/af_rebase_runs.py <log> --fieldmaps [--scenes=BB:2,PH:2,BB:2,PH:2]
      Subtract the field geometry before comparing runs. The screen is tilted ~165c
      corner to corner, so two runs that peaked on different parts of the frame
      disagree for reasons that are not AF's fault; each run is scored against the
      field map's prediction AT THE PATCH IT PEAKED ON instead of against a single
      number. ``--scenes`` labels consecutive runs so an A/B can be summarised --
      spelled out rather than inferred from timing, which has misgrouped runs before.
"""

from __future__ import annotations

import csv
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling: fieldmap_report

COARSE_TO_FULL = 4  # KyfgCamera.SUBSAMPLE_FACTOR

# One combined pattern so a run's lines are consumed strictly in log order.
LINE = re.compile(
    r"(?P<t>\d\d:\d\d:\d\d)\.\d+.*af2-(?:"
    r"acquire target=\S+ roi=\((?P<ax>\d+), (?P<ay>\d+), (?P<ax2>\d+), (?P<ay2>\d+)\)"
    r"|dive done pos=(?P<dpos>[+-]\d+)c dropped=\S+ raw=(?P<draw>-?\d+)"
    r"|climb (?:peak|cap) n=(?P<n>\d+) best_pos=(?P<best>[+-]\d+)c \S+ "
    r"contrast=(?P<con>[\d.]+) handovers=(?P<hand>\d+) improvement=(?P<imp>[\d.]+)x"
    r"(?: peak_roi=\((?P<px>\d+), (?P<py>\d+), (?P<px2>\d+), (?P<py2>\d+)\))?"
    r"|climb peak refine argmax=[+-]\d+c fit=(?P<fit>[+-]\d+)c"
    r"|climb handover #\d+ \S+ ratio=\S+ cum_scale=(?P<cum>[\d.e+-]+) "
    r"|climb primary changed (?P<reanchor>\S+)->\S+, no stitchable"
    r"|park arrived pos=[+-]\d+c target=(?P<tgt>[+-]\d+)c miss=(?P<miss>[+-]\d+)c"
    r"|park pos=(?P<ppos>[+-]\d+)c raw=(?P<praw>-?\d+) \S+ \S+ ratio=(?P<ratio>[\d.]+)"
    r")"
)

PULSE = 24.0  # counts per str0 pulse, for the human-readable conversions only


def parse(path: Path) -> list[dict]:
    runs: list[dict] = []
    cur: dict | None = None
    for line in path.read_text(errors="replace").splitlines():
        m = LINE.search(line)
        if not m:
            continue
        g = m.groupdict()
        if g["ax"] is not None:
            # The acquire line opens a run; the dive line a moment later carries the
            # origin. Tracking the tile matters because best focus is region-dependent
            # on a non-flat field, so two runs are only comparable on the same patch.
            cur = {"t": g["t"], "tile": (int(g["ax"]), int(g["ay"]),
                                         int(g["ax2"]), int(g["ay2"]))}
            runs.append(cur)
        elif cur is None:
            continue
        elif g["dpos"] is not None:
            # pos = -(raw - origin)  =>  origin = raw + pos
            cur["origin"] = int(g["draw"]) + int(g["dpos"])
        elif g["best"] is not None:
            cur.update(best=int(g["best"]), hand=int(g["hand"]),
                       imp=float(g["imp"]), con=float(g["con"]))
            if g["px"] is not None:
                cur["peak_roi"] = (int(g["px"]), int(g["py"]),
                                   int(g["px2"]), int(g["py2"]))
        elif g["fit"] is not None:
            cur["fit"] = int(g["fit"])
        elif g["cum"] is not None:
            cur["cum"] = float(g["cum"])  # last one wins = the scale the curve ended on
        elif g["reanchor"] is not None:
            # An unstitchable promotion: the climb continues on an unknown scale, so
            # these are silent seams in the curve the peak is fitted to.
            cur["reanc"] = cur.get("reanc", 0) + 1
        elif g["tgt"] is not None:
            cur.update(tgt=int(g["tgt"]), miss=int(g["miss"]))
        elif g["ppos"] is not None:
            cur.update(ppos=int(g["ppos"]), praw=int(g["praw"]), ratio=float(g["ratio"]))
    return [r for r in runs if "praw" in r and "best" in r and "origin" in r]


def report(path: Path) -> None:
    runs = parse(path)
    if not runs:
        return
    print(f"===== {path.name} =====")
    print(f"{'time':>9} {'scene':>7} {'tile x,y':>13} {'startP':>7} {'argmaxP':>8} {'fitP':>7} "
          f"{'landedP':>8} {'miss':>5} {'drain':>6} {'hand':>5} {'impr':>7} {'ratio':>6} "
          f"{'chain':>6}")
    prev = None
    for r in runs:
        start = -r["origin"]
        landed = -r["praw"]
        fit = start + r["fit"] if "fit" in r else None
        # Where the creep stopped vs where the lens finally came to rest: the last
        # step's pulses keep draining after 'arrived', always upward.
        drain = landed - (start + r["tgt"]) if "tgt" in r else None
        chain = "" if prev is None else f"{start - prev:+d}"
        tx, ty = r["tile"][0], r["tile"][1]
        print(f"{r['t']:>9} {'moving' if r['hand'] else 'frozen':>7} {f'{tx},{ty}':>13} "
              f"{start:>+7d} {start + r['best']:>+8d} {'none' if fit is None else f'{fit:+d}':>7} "
              f"{landed:>+8d} {r.get('miss', 0):>+5d} "
              f"{'--' if drain is None else f'{drain:+d}':>6} "
              f"{r['hand']:>5d} {r['imp']:>6.1f}x {r.get('ratio', 0):>6.2f} {chain:>6}")
        prev = landed

    # A frozen run's argmax estimates true focus FOR ITS OWN TILE. Grouping by tile is
    # the whole point: if focus tracks the tile's position rather than the frame's
    # content, the spread is optics (tilt / field curvature), not estimator noise.
    # A contaminated run gives itself away by parking far below its own declared peak.
    good = [r for r in runs if not r["hand"] and r.get("ratio", 0) > 0.5]
    by_tile: dict = {}
    for r in good:
        by_tile.setdefault(r["tile"][:2], []).append(-r["origin"] + r["best"])
    if by_tile:
        print("\n  frozen-scene focus by TILE (argmax, counts):")
        for tile, est in sorted(by_tile.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            spread = max(est) - min(est)
            print(f"    x={tile[0]:>5} y={tile[1]:>5}  n={len(est)}  {est}  "
                  f"mean={sum(est) / len(est):+.0f}c" +
                  (f"  spread={spread}c ({spread / PULSE:.1f}p)" if len(est) > 1 else ""))
        allest = [v for est in by_tile.values() for v in est]
        within = [max(e) - min(e) for e in by_tile.values() if len(e) > 1]
        print(f"    across all tiles: spread={max(allest) - min(allest)}c "
              f"({(max(allest) - min(allest)) / PULSE:.1f} pulses)")
        if within:
            print(f"    within a tile   : worst={max(within)}c "
                  f"({max(within) / PULSE:.1f} pulses)  <- repeat noise floor")
            print("    If 'across' >> 'within', best focus depends on WHERE in the frame "
                  "you measure\n    (screen tilt / field curvature), not on the estimator.")
    drains = [(-r["praw"]) - (-r["origin"] + r["tgt"]) for r in runs if "tgt" in r]
    if drains:
        print(f"  post-arrival drain: {drains} counts  "
              f"mean={sum(drains) / len(drains):+.0f}c "
              f"({sum(drains) / len(drains) / PULSE:+.1f} pulses)")
    print()


# ── field geometry ───────────────────────────────────────────────────────────
def _centre(roi) -> tuple[float, float]:
    return ((roi[0] + roi[2]) / 2.0, (roi[1] + roi[3]) / 2.0)


def _sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


class FieldModel:
    """Best focus as a function of full-res sensor position, from one sweep, in P.

    The separable quadratic through centre/top/bottom/left/right reproduces all five
    measured vertices exactly, so this is the tilt+curvature split fieldmap_report
    already prints rather than a second model that could disagree with it. Curvature
    is kept, not dropped for a plane: it reached 122c on this rig, as large as the
    effect an A/B is trying to resolve, so a plane would hand that back as error.
    """

    NEEDED = ("centre", "top", "bottom", "left", "right")

    def __init__(self, path: Path, window: int) -> None:
        import fieldmap_report as fr

        rows = list(csv.reader(open(path, encoding="utf-8")))
        head = ",".join(rows[0])
        self.rois = {
            m[0]: tuple(int(v) for v in m[1:])
            for m in re.findall(r"(\w+)=\((\d+), (\d+), (\d+), (\d+)\)", head)
        }
        if not set(self.NEEDED) <= set(self.rois):
            raise ValueError(f"{path.name}: expected tiles {self.NEEDED}")
        # pos = origin - raw, so P = -raw = pos - origin: the runs' frame exactly.
        origin = float(rows[2][2]) + float(rows[2][1])
        _pos, tiles = fr.read_tiles(str(path), window)
        self.P = {k: tiles[k]["vertex"] - origin for k in self.NEEDED}
        self.suspect = [k for k in self.NEEDED
                        if tiles[k]["clipped"] or tiles[k]["contrast"] < 5.0]
        self.stamp = fr.stamp_of(str(path))
        self.name = path.name

        self.cx, self.cy = _centre(self.rois["centre"])
        self.hx = _centre(self.rois["right"])[0] - self.cx
        self.hy = _centre(self.rois["bottom"])[1] - self.cy
        c = self.P["centre"]
        self.tiltH = (self.P["right"] - self.P["left"]) / 2.0
        self.curvH = (self.P["left"] + self.P["right"]) / 2.0 - c
        self.tiltV = (self.P["bottom"] - self.P["top"]) / 2.0
        self.curvV = (self.P["top"] + self.P["bottom"]) / 2.0 - c

    def predict(self, x: float, y: float) -> tuple[float, float]:
        """Expected best-focus P at a full-res sensor point, plus how far outside the
        measured tile span it sits (<=1 interior, >1 extrapolated)."""
        u = (x - self.cx) / self.hx if self.hx else 0.0
        v = (y - self.cy) / self.hy if self.hy else 0.0
        p = (self.P["centre"] + self.tiltH * u + self.curvH * u * u
             + self.tiltV * v + self.curvV * v * v)
        return p, max(abs(u), abs(v))


def load_models(paths: list[str], window: int) -> list[FieldModel]:
    import fieldmap_report as fr

    if not paths:
        paths = fr.all_csvs()
    out = []
    for p in paths:
        try:
            out.append(FieldModel(Path(p), window))
        except Exception as exc:  # a half-written or aborted sweep must not stop the rest
            print(f"  skipping {Path(p).name}: {exc}")
    return sorted(out, key=lambda m: m.stamp)


def report_ab(runs: list[dict], models: list[FieldModel], scenes: list[str]) -> None:
    """Score each run against the field map's prediction where it actually peaked."""
    if not models:
        print("no usable field maps -- nothing to correct against")
        return
    print("  field maps in play:")
    for m in models:
        print(f"    {m.stamp:%H:%M:%S} {m.name}  tiltH={m.tiltH:+.0f}c "
              f"tiltV={m.tiltV:+.0f}c curvH={m.curvH:+.0f}c curvV={m.curvV:+.0f}c"
              + (f"  SUSPECT TILES: {','.join(m.suspect)}" if m.suspect else ""))

    print(f"\n{'time':>9} {'scene':>6} {'peak x,y':>13} {'landedP':>8} {'predP':>8} "
          f"{'err':>7} {'edge':>5} {'hand':>5} {'reanc':>6} {'cum':>8} {'impr':>7} "
          f"{'contr':>6}")
    rows = []
    for i, r in enumerate(runs):
        roi = r.get("peak_roi") or r["tile"]
        x, y = (v * COARSE_TO_FULL for v in _centre(roi))
        m = next((mm for mm in reversed(models)
                  if mm.stamp.strftime("%H:%M:%S") <= r["t"]), models[0])
        pred, edge = m.predict(x, y)
        landed = -r["praw"]
        err = landed - pred
        scene = scenes[i] if i < len(scenes) else "?"
        rows.append({"scene": scene, "err": err, "landed": float(landed), "run": r})
        print(f"{r['t']:>9} {scene:>6} {f'{x:.0f},{y:.0f}':>13} {landed:>+8d} "
              f"{pred:>+8.0f} {err:>+7.0f} {edge:>5.2f} {r['hand']:>5d} "
              f"{r.get('reanc', 0):>6d} {r.get('cum', 1.0):>8.3g} "
              f"{r['imp']:>6.1f}x {r['con']:>6.2f}"
              + ("" if r.get("peak_roi") else "  (acquire ROI)"))

    by_scene: dict[str, list[dict]] = {}
    for row in rows:
        by_scene.setdefault(row["scene"], []).append(row)
    print(f"\n  {'scene':>6} {'n':>3} {'err mean':>9} {'err sd':>7} {'raw sd':>7} "
          f"{'hand':>6} {'reanc':>6} {'drift':>6} {'impr':>7} {'contr':>6}")
    for scene, rs in by_scene.items():
        errs = [x["err"] for x in rs]
        mean = lambda f: sum(f(x["run"]) for x in rs) / len(rs)  # noqa: E731
        print(f"  {scene:>6} {len(rs):>3} {sum(errs) / len(errs):>+9.0f} "
              f"{_sd(errs):>7.0f} {_sd([x['landed'] for x in rs]):>7.0f} "
              f"{mean(lambda r: r['hand']):>6.1f} "
              f"{mean(lambda r: r.get('reanc', 0)):>6.1f} "
              f"{mean(lambda r: abs(math.log10(max(r.get('cum', 1.0), 1e-12)))):>6.2f} "
              f"{mean(lambda r: r['imp']):>6.1f}x "
              f"{mean(lambda r: r['con']):>6.2f}")
    print("  'err sd' below 'raw sd' means the field correction removed real geometry;\n"
          "  above it means the correction is adding noise and should not be trusted.\n"
          "  'reanc' counts unstitchable handovers (curve continues on an unknown scale)\n"
          "  and 'drift' is mean |log10 cum_scale|: 0.00 ideal, 0.30 = the curve's two\n"
          "  ends differ 2x in scale, which moves the fitted peak, not just its height.")


def expand_scenes(spec: str) -> list[str]:
    """'BB:2,PH:2' -> ['BB','BB','PH','PH']. Explicit because inferring scene
    boundaries from timing has silently merged two scenes before."""
    out: list[str] = []
    for part in spec.split(","):
        name, _, n = part.partition(":")
        out.extend([name] * (int(n) if n else 1))
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    flags = [a for a in argv if a.startswith("--")]
    logs = [a for a in argv if not a.startswith("--")]
    window = next((int(f.split("=")[1]) for f in flags if f.startswith("--window=")), 20)
    scenes = next((expand_scenes(f.split("=", 1)[1])
                   for f in flags if f.startswith("--scenes=")), [])
    want_field = any(f.startswith("--fieldmaps") for f in flags) or bool(scenes)

    for arg in logs:
        report(Path(arg))
        if want_field:
            import glob as _glob

            pats = [f.split("=", 1)[1] for f in flags
                    if f.startswith("--fieldmaps=")]
            paths = [p for pat in pats for p in sorted(_glob.glob(pat))]
            report_ab(parse(Path(arg)), load_models(paths, window), scenes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
