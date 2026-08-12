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
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# One combined pattern so a run's lines are consumed strictly in log order.
LINE = re.compile(
    r"(?P<t>\d\d:\d\d:\d\d)\.\d+.*af2-(?:"
    r"acquire target=\S+ roi=\((?P<ax>\d+), (?P<ay>\d+), (?P<ax2>\d+), (?P<ay2>\d+)\)"
    r"|dive done pos=(?P<dpos>[+-]\d+)c dropped=\S+ raw=(?P<draw>-?\d+)"
    r"|climb (?:peak|cap) n=(?P<n>\d+) best_pos=(?P<best>[+-]\d+)c \S+ "
    r"contrast=(?P<con>[\d.]+) handovers=(?P<hand>\d+) improvement=(?P<imp>[\d.]+)x"
    r"|climb peak refine argmax=[+-]\d+c fit=(?P<fit>[+-]\d+)c"
    r"|climb handover #\d+ \S+ ratio=\S+ cum_scale=(?P<cum>[\d.e+-]+) "
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
        elif g["fit"] is not None:
            cur["fit"] = int(g["fit"])
        elif g["cum"] is not None:
            cur["cum"] = float(g["cum"])
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


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for arg in argv:
        report(Path(arg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
