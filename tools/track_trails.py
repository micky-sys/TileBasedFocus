"""Track ground patches across successive F7 frames and plot their score trails.

This is the raw material AF3's D is built from: one patch of ground, scored
repeatedly as the aircraft carries it across the field. Plotting it answers by
eye what the coefficients only imply -- how much of the spread is the patch's own
content (which D cancels) versus its position in the field (which D measures).

Objects are chained with the same registration ``field_pairs`` uses: a global
partial affine per frame pair, then a per-object local re-match, so an object
stays on ITS ground rather than on the global shift. Scoring is the shipped
``focus_measure_green`` on the native green sub-grid -- never interpolated.

    py -3 tools/track_trails.py "H:/MickyCrazy/*_F7.tif" --out trails.png
    py -3 tools/track_trails.py --self-check
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "tools"))

from field_flatness import CLIP_LEVEL  # noqa: E402
import field_pairs as fp  # noqa: E402

SEED_STEP = 1600     # full-res px between seeded objects
MIN_TRAIL = 2        # an object seen once says nothing about the field


def _score(frame: dict, y: int, x: int):
    """Shipped focus measure on the native green sub-grid, or None if unusable."""
    from capture.autofocus import focus_measure_green
    g = fp.sub_grid(frame["plane"], y, x, fp.PATCH)
    if g.max() >= CLIP_LEVEL:
        return None
    s = float(focus_measure_green(g))
    return None if s < fp.MIN_SCORE else s


def track(frames: list[tuple[int, dict]], seed_step: int = SEED_STEP) -> list[dict]:
    """Chain objects through consecutive frames. Returns one dict per object.

    Each object carries ``xs``/``ys`` (normalised field position at each sighting)
    and ``scores``. New objects are seeded in every frame, so a patch that enters
    late still contributes the part of its traverse that was observed.
    """
    objects: list[dict] = []
    live: list[dict] = []
    for i, (sweep, cur) in enumerate(frames):
        h, w = cur["plane"].shape
        if i > 0:
            prev_sweep, prev = frames[i - 1]
            M, n_inl, _ = fp.global_affine(prev, cur)
            angle = (0.0 if M is None
                     else float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))))
            carried: list[dict] = []
            for obj in live:
                if M is None:
                    break
                ya, xa = obj["y"], obj["x"]
                yb0, xb0 = fp.predict(M, ya, xa)
                if not (0 <= yb0 <= h - fp.PATCH and 0 <= xb0 <= w - fp.PATCH):
                    continue          # left the field of view: traverse is over
                yb, xb, ncc = fp.refine(prev["p2"], cur["p2"], ya, xa, yb0, xb0, angle)
                if ncc < fp.MIN_NCC or not (0 <= yb <= h - fp.PATCH
                                            and 0 <= xb <= w - fp.PATCH):
                    continue
                s = _score(cur, yb, xb)
                if s is None:
                    continue
                obj["y"], obj["x"] = yb, xb
                x_n, y_n = fp.norm_xy(yb + fp.PATCH // 2, xb + fp.PATCH // 2, h, w)
                obj["xs"].append(x_n)
                obj["ys"].append(y_n)
                obj["scores"].append(s)
                obj["sweeps"].append(sweep)
                carried.append(obj)
            live = carried
            print(f"  S{prev_sweep}->S{sweep}: {len(live)} objects carried"
                  + ("" if M is not None else "   (no registration -- chain broken)"))

        # Seed fresh objects on a grid, skipping cells an existing object holds.
        held = {(o["y"] // seed_step, o["x"] // seed_step) for o in live}
        for y in range(0, h - fp.PATCH, seed_step):
            for x in range(0, w - fp.PATCH, seed_step):
                if (y // seed_step, x // seed_step) in held:
                    continue
                s = _score(cur, y, x)
                if s is None:
                    continue
                x_n, y_n = fp.norm_xy(y + fp.PATCH // 2, x + fp.PATCH // 2, h, w)
                obj = {"y": y, "x": x, "xs": [x_n], "ys": [y_n],
                       "scores": [s], "sweeps": [sweep], "born": sweep}
                objects.append(obj)
                live.append(obj)
    return [o for o in objects if len(o["scores"]) >= MIN_TRAIL]


def write_trails(trails: list[dict], path: Path) -> None:
    """One row per SIGHTING, grouped by object id -- scanning re-reads 11 GB."""
    import csv as _csv
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["oid", "line", "sweep", "x", "y", "score"])
        for oid, t in enumerate(trails):
            for sweep, x, y, s in zip(t["sweeps"], t["xs"], t["ys"], t["scores"]):
                w.writerow([oid, t["line"], sweep, f"{x:.6f}", f"{y:.6f}", f"{s:.6f}"])


def read_trails(path: Path) -> list[dict]:
    import csv as _csv
    groups: dict[int, dict] = {}
    with open(path, newline="") as fh:
        for r in _csv.DictReader(fh):
            t = groups.setdefault(int(r["oid"]), {"line": int(r["line"]), "xs": [],
                                                  "ys": [], "scores": [], "sweeps": []})
            t["xs"].append(float(r["x"]))
            t["ys"].append(float(r["y"]))
            t["scores"].append(float(r["score"]))
            t["sweeps"].append(int(r["sweep"]))
    return list(groups.values())


def plot(trails: list[dict], out: Path, label: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lens = [len(t["scores"]) for t in trails]
    fig, ax = plt.subplots(1, 3, figsize=(16.5, 5.0))
    cmap = plt.get_cmap("viridis")

    for t in trails:
        ln = np.log(t["scores"])
        c = cmap(0.5 * (t["ys"][0] + 1.0))
        ax[0].plot(t["xs"], ln, "-o", ms=3, lw=1.0, color=c, alpha=0.55)
        ax[1].plot(t["xs"], ln - ln.mean(), "-o", ms=3, lw=1.0, color=c, alpha=0.55)

    ax[0].set_title(f"raw ln(score) per tracked object\n"
                    f"{len(trails)} objects, {sum(lens)} sightings", fontsize=10)
    ax[0].set_ylabel("ln(score)")

    # Per-object mean removed: this is exactly the quantity D works in, since each
    # patch's own content scale k becomes a constant that subtracts out.
    ax[1].set_title("same trails, each object's own mean removed\n"
                    "(= what D sees: content cancelled)", fontsize=10)
    ax[1].set_ylabel("ln(score) - object mean")
    ax[1].axhline(0, color="k", lw=0.6)

    # Averaging those de-meaned trails IS the along-track field profile, measured
    # content-free. If D were viable along this axis there would be a clear hump
    # here; its absence is the same statement as curv_x being unresolved.
    xs = np.array([x for t in trails for x in t["xs"]])
    dv = np.array([v for t in trails
                   for v in (np.log(t["scores"]) - np.log(t["scores"]).mean())])
    edges = np.linspace(-1, 1, 11)
    idx = np.digitize(xs, edges) - 1
    bx, by, be = [], [], []
    for b in range(len(edges) - 1):
        m = idx == b
        if m.sum() < 15:
            continue
        bx.append(0.5 * (edges[b] + edges[b + 1]))
        by.append(float(dv[m].mean()))
        be.append(float(dv[m].std(ddof=1) / np.sqrt(m.sum())))
    ax[1].errorbar(bx, by, yerr=be, fmt="-s", color="crimson", lw=2.2, ms=6,
                   capsize=3, zorder=5, label="binned mean +- SE\n(= along-track "
                                              "field profile)")
    ax[1].legend(fontsize=8, loc="lower right")
    print("\nalong-track field profile, content-cancelled (binned mean of all trails):")
    for x, y, e in zip(bx, by, be):
        print(f"  x = {x:+.1f}   {y:+.4f} +- {e:.4f}")
    print(f"  peak-to-peak across the traverse: {max(by) - min(by):.4f} log units, "
          f"typical SE {np.mean(be):.4f}")

    # Now form D itself out of these real trails, using the SHIPPED band edges.
    # profile_metric's bands are fractions of the travel axis, so u=(x+1)/2:
    # centre |u-0.5|<=centre_frac, near u<=edge_frac, far u>=1-edge_frac.
    from capture.autofocus_v3 import AutoFocusConfigV3
    cfg = AutoFocusConfigV3()
    u = 0.5 * (xs + 1.0)
    bands = {"centre": np.abs(u - 0.5) <= cfg.profile_centre_frac,
             "near": u <= cfg.profile_edge_frac,
             "far": u >= 1.0 - cfg.profile_edge_frac}
    mu = {k: float(dv[m].mean()) for k, m in bands.items()}
    sem = {k: float(dv[m].std(ddof=1) / np.sqrt(m.sum())) for k, m in bands.items()}
    d = mu["centre"] - 0.5 * (mu["near"] + mu["far"])
    d_se = float(np.sqrt(sem["centre"] ** 2
                         + 0.25 * (sem["near"] ** 2 + sem["far"] ** 2)))
    print("\nD formed from these real trails, with the shipped band edges:")
    for k in ("centre", "near", "far"):
        print(f"  {k:<7} {mu[k]:+.4f} +- {sem[k]:.4f}   ({int(bands[k].sum())} sightings)")
    print(f"  D = centre - (near+far)/2 = {d:+.4f} +- {d_se:.4f} log units "
          f"({abs(d) / max(d_se, 1e-9):.1f} sigma)")
    print(f"  AF3 needs a rise of {cfg.station_arm_rise:.2f} to arm -- this is "
          f"{cfg.station_arm_rise / max(abs(d), 1e-9):.0f}x short.")
    print("  the along-track profile is a RAMP (tilt), and D is built to cancel "
          "exactly that;\n  what D wants is a HUMP (curvature), and there isn't one.")
    ax[1].annotate(f"D from these trails = {d:+.4f} +- {d_se:.4f}\n"
                   f"(AF3 arms at {cfg.station_arm_rise:.2f})",
                   xy=(0.03, 0.05), xycoords="axes fraction", fontsize=8.5,
                   bbox=dict(fc="white", ec="crimson", alpha=0.85))

    for a in ax[:2]:
        a.set_xlabel("along-track field position  x  (-1 .. +1)")
        a.set_xlim(-1.05, 1.05)

    raw_sd = float(np.std([np.log(s) for t in trails for s in t["scores"]]))
    dev = np.array([v for t in trails
                    for v in (np.log(t["scores"]) - np.log(t["scores"]).mean())])
    ax[2].hist(np.log([s for t in trails for s in t["scores"]])
               - np.mean(np.log([s for t in trails for s in t["scores"]])),
               bins=60, alpha=0.55, label=f"between objects (content)\nsd {raw_sd:.3f}")
    ax[2].hist(dev, bins=60, alpha=0.75,
               label=f"within an object (field + noise)\nsd {dev.std():.3f}")
    ax[2].set_title("why content cancellation is the whole game", fontsize=10)
    ax[2].set_xlabel("ln(score) deviation")
    ax[2].set_ylabel("sightings")
    ax[2].legend(fontsize=8)

    fig.suptitle(f"Tracked object score trails, {label} "
                 f"(colour = across-track position)", fontsize=12)
    fig.tight_layout()
    fig.savefig(out, dpi=125, bbox_inches="tight")
    print(f"\nwrote {out.resolve()}")
    print(f"  trail length: median {int(np.median(lens))}, max {max(lens)} sightings")
    print(f"  between-object spread (content): sd {raw_sd:.3f} log units")
    print(f"  within-object spread (field+noise): sd {dev.std():.3f} log units")
    print(f"  content is {raw_sd / max(dev.std(), 1e-9):.1f}x the within-object "
          f"variation -- which is what D cancels and a bare score cannot")


def _self_check() -> None:
    """A synthetic pan: one object must be followed across three frames."""
    fp.PATCH, fp.TEMPLATE, fp.REFINE_WIN = 256, 192, 48
    a, _ = fp._synth(0.0, h=1200, w=2000)
    shift = 300
    frames = []
    for i in range(3):
        # slide the same ground left by `shift` each frame, so every patch has a
        # known, identical displacement and must be trackable
        view = np.roll(a, -i * shift, axis=1)[:, : 2000 - 2 * shift]
        frames.append((i, fp._frame(np.ascontiguousarray(view))))
    trails = track(frames, seed_step=300)
    assert trails, "nothing tracked across a clean synthetic pan"
    full = [t for t in trails if len(t["scores"]) == 3]
    assert len(full) >= 3, f"only {len(full)} objects survived all three frames"
    # Same ground, flat field, so a tracked object's score must barely move.
    worst = max(float(np.ptp(np.log(t["scores"]))) for t in full)
    assert worst < 0.35, f"a tracked object's score moved {worst:.3f} on flat ground"
    print(f"self-check OK: {len(full)} objects tracked through 3 frames, worst "
          f"within-object score swing {worst:.3f} log units on a flat field")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glob", nargs="?")
    ap.add_argument("--out", type=Path, default=Path("trails.png"))
    ap.add_argument("--line", type=int, help="only this flight line")
    ap.add_argument("--csv", type=Path, help="cache the trails (scanning re-reads 11 GB)")
    ap.add_argument("--from-csv", type=Path, help="replot from a cached scan")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        _self_check()
        return
    if args.from_csv:
        trails = read_trails(args.from_csv)
        print(f"reloaded {len(trails)} trails from {args.from_csv}")
        label = f"line {args.line}" if args.line else "lines " + "+".join(
            str(x) for x in sorted({t["line"] for t in trails}))
        if args.line:
            trails = [t for t in trails if t["line"] == args.line]
        plot(trails, args.out, label)
        return
    if not args.glob:
        ap.error("need a glob of TIFs, --from-csv, or --self-check")

    import tifffile
    paths = sorted(Path(args.glob).parent.glob(Path(args.glob).name))
    if not paths:
        ap.error(f"no files matched {args.glob}")

    by_line: dict[int, dict[int, Path]] = defaultdict(dict)
    for p in paths:
        m = fp.FRAME_RE.search(p.name)
        if m:
            by_line[int(m.group(1))][int(m.group(2))] = p

    keep, trails = [], []
    for line in sorted(by_line):
        if args.line and line != args.line:
            continue
        sweeps = sorted(by_line[line])
        print(f"\nline {line}: {len(sweeps)} frames")
        frames = []
        for s in sweeps:
            img = tifffile.memmap(by_line[line][s], mode="r")
            keep.append(img)
            frames.append((s, fp._frame(img[:, :, 1])))
        got = track(frames)
        for t in got:
            t["line"] = line
        trails.extend(got)
        print(f"  {len(got)} objects with >= {MIN_TRAIL} sightings")
        keep.clear()

    if not trails:
        print("no trackable objects")
        return
    if args.csv:
        write_trails(trails, args.csv)
        print(f"wrote {args.csv}")
    label = f"line {args.line}" if args.line else "lines " + "+".join(
        str(x) for x in sorted({t["line"] for t in trails}))
    plot(trails, args.out, label)


if __name__ == "__main__":
    main()
