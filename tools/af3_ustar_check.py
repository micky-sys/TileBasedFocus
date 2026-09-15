"""Is ``u*`` a better park target than D's vertex? Offline, on real runs.

AF3 parks at D's vertex, which is known to sit ABOVE the centre's true best focus:
D is largest where the centre scores well AND the edges score badly, and past the
centre's own peak the edges keep degrading faster than the centre does, so D climbs
for a while after the centre has already turned over. ``park_bias_counts`` exists to
subtract that offset and currently sits at 0 because nobody has measured it.

``u*`` -- the field position where a traverse's own score profile peaks, 0.5 being
dead centre -- is meant to avoid needing the offset at all. On a curved field the
sharpest field position walks inward as focus is approached and reaches frame centre
exactly when the lens reaches the centre's vertex, so "u* crosses 0.5" should be a
LESS biased estimate of best focus than "D peaks". ``autofocus_v3._selfcheck`` proves
that on a synthetic field; this asks whether it survives contact with the rig.

The reference for "true best focus" is where the raw centre-band score peaks, which
is the same reference the module docstring uses (centre vertex +1082 vs D peak +1290
on the field map). Treat it with suspicion: unlike the field map, a walk sees NEW
GROUND at every station, so the centre score carries scene content as well as focus
-- that contamination is the entire reason D was invented. Hence the spread figure
and the censoring flag below; a run whose score peak sits at the edge of the walk has
not seen the peak at all and its gap is a lower bound, not a measurement.

    py -3 tools/af3_ustar_check.py "host/Session logs/Iron661_observe_*.csv"
"""
from __future__ import annotations

import glob
import statistics as st
import sys
import warnings
from pathlib import Path

# The engine fits parabolas over 3-point windows, which numpy rightly calls poorly
# conditioned. Live that is a useful warning; replaying 8 runs it is 37 lines of noise
# in front of the answer, and the fit quality is already visible as the D/vertex gap.
warnings.filterwarnings("ignore", message="Polyfit may be poorly conditioned")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from host.capture.autofocus_v3 import AutoFocusConfigV3, _fit_peak  # noqa: E402
from tools.af3_run_report import (  # noqa: E402
    coerce, dwells, load, station_curve, travel_axis,
)


def cross_half(pts):
    """Where u* crosses 0.5, scanning by increasing lens position.

    Returns ``(position, direction)`` for the FIRST crossing, or None. Both
    directions are looked for: the synthetic model rises through 0.5, but which way
    u* travels on the rig depends on the sign of the encoder against the optics and
    on which side of focus the dive left the lens, and guessing that wrong would
    silently report "no crossing" on a run that has one.
    """
    for (xa, a), (xb, b) in zip(pts, pts[1:]):
        if (a < 0.5 <= b) or (a >= 0.5 > b):
            return xa + (xb - xa) * (0.5 - a) / (b - a), "up" if b > a else "down"
    return None


def analyse(path: Path):
    hdr, rows = load(path)
    if len(rows) < 20:
        return None
    coerce(rows)
    cfg = AutoFocusConfigV3()
    axis = travel_axis(rows)
    curve = station_curve(rows, dwells(rows), axis, cfg)
    # The WALK is the stations that banked a traverse. The dive (1-2 rows a dwell) and
    # the park excursion afterwards also look like dwells, and including them would
    # put the "score peak" somewhere the walk never measured D at all.
    walk = sorted((c for c in curve if c["n"] > 0), key=lambda c: c["pos"])
    if len(walk) < 4:
        return None

    d_pts = [(c["pos"], c["d"]) for c in walk]
    d_peak = max(d_pts, key=lambda p: p[1])
    xv = _fit_peak(d_pts, cfg.station_fit_window, log_domain=False)

    u_pts = [(c["pos"], c["u"]) for c in walk if c["u"] is not None]
    u_cross = cross_half(u_pts) if len(u_pts) >= 2 else None

    s_pts = [(c["pos"], c["s_ctr"]) for c in walk if c["s_ctr"] is not None]
    s_peak = max(s_pts, key=lambda p: p[1]) if s_pts else None
    # A peak at either end of the walk is where the data STOPPED, not where the curve
    # turned over. The early exit stops ~3 stations after D peaks, and the centre
    # score peaks later than D, so this is the expected case rather than a rare one.
    censored = bool(s_peak and s_peak[0] in (s_pts[0][0], s_pts[-1][0]))

    # Did D actually PEAK, or just wander? Judged by the engine's own arming
    # threshold, station_arm_rise -- the climb it demands out of D's pedestal before
    # it will believe a fall. Below that the "peak" is a noise max and its offset from
    # the score peak is meaningless: the aborted 7-station runs of 2026-09-10 report
    # gaps of +711 and +155 off D peaks of -0.136 and +0.075, and averaging those in
    # moved the median 128 and the spread to 649.
    d_rise = d_peak[1] - min(d for _, d in d_pts)

    return {
        "name": path.name, "n_walk": len(walk), "axis": axis,
        "d_rise": d_rise, "armed": d_rise >= cfg.station_arm_rise,
        "lo": walk[0]["pos"], "hi": walk[-1]["pos"],
        "d_peak": d_peak, "d_vertex": xv, "u_cross": u_cross,
        "s_peak": s_peak, "censored": censored,
        "u_spread": (max(u for _, u in u_pts) - min(u for _, u in u_pts))
        if len(u_pts) >= 2 else None,
        "s_rel_spread": (
            (max(s for _, s in s_pts) - min(s for _, s in s_pts))
            / max(1e-9, st.median([s for _, s in s_pts]))) if len(s_pts) >= 2 else None,
    }


def report(a) -> None:
    print(f"\n== {a['name']}")
    print(f"   walk        : {a['n_walk']} stations, raw {a['lo']:.0f} .. {a['hi']:.0f}"
          f"  (travel axis {a['axis']})")
    print(f"   D peak      : raw {a['d_peak'][0]:>8.0f}   D={a['d_peak'][1]:+.3f}"
          f"   (rose {a['d_rise']:.2f}"
          f"{'' if a['armed'] else ' -- NOT a real peak, run rejected'})")
    print(f"   D vertex    : raw "
          + ("     n/a   (parabola did not fit)" if a["d_vertex"] is None
             else f"{a['d_vertex']:>8.0f}   <- what the engine parks on"))
    if a["u_cross"] is None:
        print(f"   u* crossing : never reaches 0.5"
              + (f" (u* spans only {a['u_spread']:.3f})" if a["u_spread"] else ""))
    else:
        print(f"   u* crossing : raw {a['u_cross'][0]:>8.0f}   ({a['u_cross'][1]}ward "
              f"through 0.5, u* spans {a['u_spread']:.3f})")
    if a["s_peak"] is None:
        print("   score peak  : none")
        return
    flag = "  [CENSORED: at the edge of the walk, so this is a BOUND]" if a["censored"] else ""
    print(f"   score peak  : raw {a['s_peak'][0]:>8.0f}   score {a['s_peak'][1]:.0f}"
          f"{flag}")
    print(f"   score spread: {a['s_rel_spread']:.1f}x of its own median "
          f"(scene content, not just focus)")

    ref = a["s_peak"][0]
    dref = (a["d_vertex"] if a["d_vertex"] is not None else a["d_peak"][0]) - ref
    print(f"   -- D    - score peak : {dref:+8.0f} counts")
    if a["u_cross"] is None:
        print("   -- u* gives no answer on this run")
        return
    uref = a["u_cross"][0] - ref
    print(f"   -- u*   - score peak : {uref:+8.0f} counts")
    better = "u*" if abs(uref) < abs(dref) else "D"
    print(f"   -- verdict: {better} is closer to the score peak by "
          f"{abs(abs(dref) - abs(uref)):.0f} counts"
          + ("  (but the reference is censored)" if a["censored"] else ""))


def main(argv: list[str]) -> int:
    pats = argv[1:] or ["host/Session logs/Iron661_observe_*.csv"]
    paths = sorted({Path(p) for pat in pats for p in glob.glob(pat)})
    if not paths:
        print("no CSVs matched")
        return 1
    out = []
    for p in paths:
        try:
            a = analyse(p)
        except Exception as exc:                    # a short/aborted run is not an error
            print(f"\n== {p.name}\n   skipped: {exc}")
            continue
        if a is None:
            continue
        out.append(a)
        report(a)

    # Two SEPARATE requirements, and conflating them hid a good run: measuring the D
    # bias needs only an uncensored score peak, while comparing u* additionally needs
    # u* to actually cross 0.5 -- which on this rig it often never does.
    usable = [a for a in out if a["s_peak"] and not a["censored"] and a["armed"]]
    print(f"\n\n== {len(out)} runs analysed; "
          f"{sum(1 for a in out if not a['armed'])} rejected (D never rose "
          f"station_arm_rise out of its pedestal), "
          f"{len(usable)} left with an UNCENSORED score peak")
    if not usable:
        print("   No run saw the centre score turn over, so neither the D bias nor the")
        print("   u* rule can be scored yet. That is structural: the walk stops ~3")
        print("   stations after D peaks and the centre score peaks later than D.")
        print("   To settle it, one run needs station_descend_stop raised so the walk")
        print("   continues until the centre score rises, peaks and falls.")
        return 0
    dgaps = [(a["d_vertex"] if a["d_vertex"] is not None else a["d_peak"][0])
             - a["s_peak"][0] for a in usable]
    print(f"   D  - score peak: {['%+.0f' % g for g in dgaps]}")
    if len(dgaps) >= 2:
        print(f"   D  gap: median {st.median(dgaps):+.0f}  spread "
              f"{max(dgaps) - min(dgaps):.0f}  <- park_bias_counts candidate")
        print("   A bias is only usable if its SPREAD is small; a tight median with a")
        print("   wide spread means the offset is not a constant and cannot be baked in.")

    ucomp = [a for a in usable if a["u_cross"] is not None]
    print(f"\n   of those, {len(ucomp)} also have a u* crossing to compare")
    if not ucomp:
        print("   u* never reached frame centre on any of them -- no comparison possible")
        return 0
    ugaps = [a["u_cross"][0] - a["s_peak"][0] for a in ucomp]
    print(f"   u* - score peak: {['%+.0f' % g for g in ugaps]}")
    if len(ugaps) >= 2:
        print(f"   u* gap: median {st.median(ugaps):+.0f}  spread "
              f"{max(ugaps) - min(ugaps):.0f}")
    return 0


def _selfcheck() -> None:
    """``cross_half`` is the only thing here that could be quietly wrong: an off-by-one
    or a sign slip would report "never reaches 0.5" on a run that crosses, which reads
    as evidence against u* rather than as a bug."""
    up = [(0.0, 0.40), (100.0, 0.44), (200.0, 0.60)]
    x, d = cross_half(up)          # 100 + 100*(0.5-0.44)/(0.60-0.44) = 137.5
    assert d == "up" and abs(x - 137.5) < 1e-9, f"upward crossing wrong: {x}, {d}"

    down = [(0.0, 0.70), (100.0, 0.30)]
    x, d = cross_half(down)
    assert d == "down" and abs(x - 50.0) < 1e-9, f"downward crossing wrong: {x}, {d}"

    assert cross_half([(0.0, 0.40), (100.0, 0.45)]) is None, "invented a crossing"
    assert cross_half([(0.0, 0.60), (100.0, 0.70)]) is None, "crossed without crossing"
    # Exactly 0.5 counts as reached, not as still-below, or a run that lands dead on
    # centre would be reported as never getting there.
    assert cross_half([(0.0, 0.40), (100.0, 0.50)])[0] == 100.0, "0.5 itself missed"
    # First crossing wins: a later re-crossing must not overwrite the answer.
    x, _ = cross_half([(0.0, 0.4), (10.0, 0.6), (20.0, 0.4), (30.0, 0.6)])
    assert abs(x - 5.0) < 1e-9, f"took a later crossing: {x}"
    print("af3_ustar_check self-check OK")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        raise SystemExit(main(sys.argv))
