"""Live check that the camera-selection relays really switch the encoder bus.

Talks to the ClearCore ONLY -- no KAYA, no Qt, no writer subprocesses, no shared
memory. The capture app commits ~3.8 GB of SHM and spawns 6 subprocesses *per
camera* before a single frame arrives, which is what makes a low-RAM box crawl;
none of that is needed to answer "does $Cn* move the encoder bus".

Two things are being measured.

CORRECTNESS (cycle mode, the default): $C1*/$C2*/$C3* in turn, checking that
every $ENC is tagged for the camera we asked for and that the cameras report
DIFFERENT counts. Identical counts across two cameras would mean the relay
switches the motor but not the SPI chip-select, so one shaft answers for all.

INTEGRITY (both modes): the shafts are stationary on the bench, so the true
count is constant and ANY deviation is a bus error. Two kinds, and they are not
equally bad:

  * ``-1``      -- the encoder never echoed rd_pos. Harmless: the host treats it
                   as a sentinel and marks the encoder absent.
  * an outlier  -- a wrong but plausible count (e.g. 513 where the shaft sits at
                   519). Dangerous: it is silently stamped into frame metadata
                   and folded into the unwrap accumulator as a real move.

Errors are timestamped relative to the $Cn* that selected the camera, so the
report can distinguish "only just after a relay switch" (a settle-time problem,
fix relaySettleMs) from "uniformly spread" (ambient noise or marginal
signalling, fix the wiring). Use ``--park`` for the control: sit on ONE camera
with no switching at all. If errors persist at the same rate while parked, the
relay switch is not implicated.

Usage::

    py tools/encoder_toggle_smoke.py                      # auto-detect, cams 1-3
    py tools/encoder_toggle_smoke.py --cams 1,3 --cycles 4
    py tools/encoder_toggle_smoke.py --park 1 --seconds 120   # no-switch control
    py tools/encoder_toggle_smoke.py --raw                # echo every PCB line
    py tools/encoder_toggle_smoke.py --self-check         # no hardware needed
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "host")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import serial_comm  # noqa: E402  (needs the sys.path bootstrap above)
from capture.focus_axis import _ENC_RE, MultiEncoderTracker  # noqa: E402

# The firmware only reports encoders every 2 s outside the focus modes
# (encoderReportSlowMs); M4 drops that to 100 ms. Without it a 3 s dwell yields
# one sample per camera and nothing can be concluded about stability.
FAST_REPORT_MODE = 4
BAD_READ = -1
# $M4* kicks off the PCB's homing sequence, which blocks loop() -- and so
# reportEncoder() -- for several seconds. Wait for encoder traffic to resume
# rather than guessing a sleep.
HOMING_TIMEOUT_S = 25.0
# After $Cn* the relay takes time to settle, and a reading already in flight
# still carries the OLD camera tag. Treat the switch as complete when the first
# correctly-tagged reading lands; anything mistagged after that is a real fault.
SWITCH_TIMEOUT_S = 5.0
# Upper edges (seconds since $Cn*) for the error-timing histogram.
TIME_BUCKETS = (0.5, 1.0, 2.0, 5.0)


@dataclass(frozen=True)
class Reading:
    """One $ENC line, timed from the $Cn* that selected this camera."""

    t: float
    pos: int
    tag: int


@dataclass
class CamSamples:
    """Readings credited to one camera during one dwell."""

    asked: int
    readings: list[Reading] = field(default_factory=list)
    settle_s: Optional[float] = None

    @property
    def good(self) -> list[Reading]:
        return [r for r in self.readings if r.pos != BAD_READ]

    @property
    def bad(self) -> list[Reading]:
        return [r for r in self.readings if r.pos == BAD_READ]

    @property
    def mistagged(self) -> list[Reading]:
        return [r for r in self.readings if r.tag != self.asked]

    @property
    def modal(self) -> Optional[int]:
        """The count the shaft actually sits at -- the most common good read.

        Safe on the bench because nothing is driving the motor; it would be
        meaningless during a focus move.
        """
        good = self.good
        return Counter(r.pos for r in good).most_common(1)[0][0] if good else None

    @property
    def outliers(self) -> list[Reading]:
        """Good-looking reads that disagree with the stationary shaft."""
        m = self.modal
        return [] if m is None else [r for r in self.good if r.pos != m]


def bucket_errors(errors: list[tuple[int, Reading, str]]) -> list[str]:
    """Histogram error times since $Cn*, so a settle problem is visible."""
    if not errors:
        return []
    edges = list(TIME_BUCKETS)
    counts = [0] * (len(edges) + 1)
    for _cam, r, _kind in errors:
        for i, hi in enumerate(edges):
            if r.t < hi:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    labels = []
    lo = 0.0
    for hi in edges:
        labels.append(f"{lo:g}-{hi:g}s")
        lo = hi
    labels.append(f">{edges[-1]:g}s")
    return [f"  {lab:>8}: {n}" for lab, n in zip(labels, counts) if n or True]


def summarise(samples: list[CamSamples]) -> tuple[list[str], list[str]]:
    """Render one cycle's readings. Returns (report lines, problem lines).

    Split so the caller can print the table always but exit non-zero only on
    problems -- and so the logic is testable without a PCB (--self-check).
    """
    rows: list[str] = []
    problems: list[str] = []
    for s in samples:
        settle = "  settle=--" if s.settle_s is None else f"  settle={s.settle_s * 1000:.0f}ms"
        if not s.readings:
            rows.append(f"  cam{s.asked}: NO $ENC LINES")
            problems.append(f"cam{s.asked}: PCB sent no encoder readings at all")
            continue
        good, bad, out = s.good, s.bad, s.outliers
        rows.append(
            f"  cam{s.asked}: n={len(s.readings):4d}  ok={len(good) - len(out):4d}  "
            f"bad={len(bad):3d}  outlier={len(out):3d}  at={s.modal}{settle}"
        )
        for r in out:
            rows.append(f"      outlier {r.pos} (vs {s.modal}) at t+{r.t:.2f}s")
        if not good:
            problems.append(
                f"cam{s.asked}: every read returned -1 (encoder never echoed rd_pos)"
            )
        elif bad:
            problems.append(f"cam{s.asked}: {len(bad)}/{len(s.readings)} reads returned -1")
        if out:
            problems.append(
                f"cam{s.asked}: {len(out)}/{len(s.readings)} reads disagreed with the "
                f"stationary shaft at {s.modal} -- silently corrupt"
            )
        if s.mistagged:
            problems.append(
                f"cam{s.asked}: {len(s.mistagged)} reading(s) tagged for another camera "
                "-- the PCB did not honour $Cn*"
            )

    # Two shafts reading the same count is the signature of a relay that switches
    # the motor but not the chip-select: one encoder answers for everybody.
    settled = {s.asked: s.modal for s in samples if s.modal is not None}
    for a, b in ((a, b) for a in settled for b in settled if a < b):
        if settled[a] == settled[b]:
            problems.append(
                f"cam{a} and cam{b} both read {settled[a]} -- identical counts "
                "suggest the relay is not switching the SPI chip-select"
            )
    return rows, problems


def _parse_enc(line: str, default_cam: int) -> Optional[tuple[int, int]]:
    """``(camera, position)`` from an ``$ENC`` line, or None if not one.

    ``_ENC_RE`` groups are positional: (1) the optional camera tag, absent on
    the older single-encoder firmware, (2) the position.
    """
    m = _ENC_RE.search(line)
    if not m:
        return None
    tag = m.group(1)
    return (int(tag) if tag else default_cam, int(m.group(2)))


def _self_check() -> int:
    """Assert the summariser + parser verdicts. No hardware, no serial port."""
    assert _parse_enc("$ENC=2,1234*", 1) == (2, 1234)
    assert _parse_enc("$ENC=-1*", 3) == (3, BAD_READ), "untagged firmware falls back"
    assert _parse_enc("PCB< Camera 3 set", 1) is None

    def rd(t, pos, tag=1):
        return Reading(t=t, pos=pos, tag=tag)

    clean = CamSamples(asked=1, readings=[rd(0.1, 519), rd(0.2, 519), rd(0.3, 519)],
                       settle_s=0.12)
    rows, probs = summarise([clean])
    assert not probs, probs
    assert "at=519" in rows[0] and "settle=120ms" in rows[0], rows

    # A lone wrong-but-plausible value must be caught, not averaged away.
    corrupt = CamSamples(asked=1, readings=[rd(0.1, 519), rd(0.2, 513), rd(0.3, 519)])
    rows, probs = summarise([corrupt])
    assert any("silently corrupt" in p for p in probs), probs
    assert any("outlier 513 (vs 519)" in r for r in rows), rows

    dead = CamSamples(asked=2, readings=[rd(t / 10, BAD_READ, 2) for t in range(4)])
    _, probs = summarise([dead])
    assert any("every read returned -1" in p for p in probs), probs

    silent = CamSamples(asked=1)
    _, probs = summarise([silent])
    assert any("no encoder readings" in p for p in probs), probs

    mistag = CamSamples(asked=1, readings=[rd(0.1, 5), rd(0.2, 5, tag=3)])
    _, probs = summarise([mistag])
    assert any("did not honour" in p for p in probs), probs

    same = [
        CamSamples(asked=1, readings=[rd(0.1, 42)]),
        CamSamples(asked=3, readings=[rd(0.1, 42, tag=3)]),
    ]
    _, probs = summarise(same)
    assert any("identical counts" in p for p in probs), probs

    distinct = [
        CamSamples(asked=1, readings=[rd(0.1, 42)]),
        CamSamples(asked=3, readings=[rd(0.1, 999, tag=3)]),
    ]
    _, probs = summarise(distinct)
    assert not probs, probs

    # -1 must never be mistaken for a real position when comparing shafts.
    both_dead = [
        CamSamples(asked=1, readings=[rd(0.1, BAD_READ)]),
        CamSamples(asked=3, readings=[rd(0.1, BAD_READ, tag=3)]),
    ]
    _, probs = summarise(both_dead)
    assert not any("identical counts" in p for p in probs), probs

    errs = [(1, rd(0.2, BAD_READ), "-1"), (1, rd(3.0, 513), "outlier"),
            (3, rd(0.4, BAD_READ, 3), "-1")]
    hist = bucket_errors(errs)
    assert "0-0.5s: 2" in " ".join(hist), hist
    assert "2-5s: 1" in " ".join(hist), hist
    assert not bucket_errors([])

    print("encoder_toggle_smoke self-check: OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default=None, help="ClearCore COM port (auto-detect if omitted)")
    ap.add_argument("--cams", default="1,2,3", help="Comma-separated camera slots to cycle")
    ap.add_argument("--dwell", type=float, default=3.0, help="Seconds to sit on each camera")
    ap.add_argument("--cycles", type=int, default=3, help="Number of full passes")
    ap.add_argument(
        "--park", type=int, default=None,
        help="Control run: select this camera ONCE and sample without switching",
    )
    ap.add_argument("--seconds", type=float, default=120.0, help="Duration of a --park run")
    ap.add_argument("--raw", action="store_true", help="Echo every line the PCB sends")
    ap.add_argument(
        "--no-mode", action="store_true",
        help=f"Do not send $M{FAST_REPORT_MODE}* first (encoder cadence stays at 2 s)",
    )
    ap.add_argument("--self-check", action="store_true", help="Run offline assertions and exit")
    args = ap.parse_args()

    if args.self_check:
        return _self_check()

    if args.park is not None:
        if args.park not in serial_comm.VALID_CAMERAS:
            print(f"--park must be one of {serial_comm.VALID_CAMERAS}", file=sys.stderr)
            return 2
        cams = [args.park]
    else:
        try:
            cams = [int(c) for c in args.cams.split(",") if c.strip()]
        except ValueError:
            print(f"bad --cams {args.cams!r}", file=sys.stderr)
            return 2
        if not cams or any(c not in serial_comm.VALID_CAMERAS for c in cams):
            print(f"--cams must be from {serial_comm.VALID_CAMERAS}", file=sys.stderr)
            return 2

    port = args.port or serial_comm.FocusController.find_clearcore()
    if not port:
        print("No ClearCore found. Pass --port COMnn.", file=sys.stderr)
        return 2

    inbox: "queue.Queue[str]" = queue.Queue()
    tracker = MultiEncoderTracker()
    ctrl = serial_comm.FocusController()
    ctrl.connect(port, on_response=inbox.put)
    if args.park is not None:
        print(f"connected to {port}; PARKED on cam{args.park} for {args.seconds:g}s "
              "(no relay switching)\n")
    else:
        print(f"connected to {port}; cycling cams {cams} x{args.cycles} @ {args.dwell}s\n")

    def next_line(timeout: float) -> Optional[str]:
        try:
            line = inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if args.raw:
            print(f"    < {line.strip()}")
        return line

    def collect(cam: int, duration: float) -> CamSamples:
        """Select *cam*, wait out the relay, then sample for *duration*."""
        ctrl.select_camera(cam)
        sent = time.monotonic()
        s = CamSamples(asked=cam)
        # Discard readings still tagged for the previous camera: they were in
        # flight before the relay moved, and counting them as mistags would
        # flag a fault on every single switch.
        while time.monotonic() - sent < SWITCH_TIMEOUT_S:
            line = next_line(0.2)
            if line is None:
                continue
            parsed = _parse_enc(line, cam)
            if parsed is None or parsed[0] != cam:
                continue
            s.settle_s = time.monotonic() - sent
            tracker.update_line(line, default_cam=cam)
            s.readings.append(Reading(t=s.settle_s, pos=parsed[1], tag=parsed[0]))
            break
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            line = next_line(0.2)
            if line is None:
                continue
            parsed = _parse_enc(line, cam)
            if parsed is None:
                continue
            # Feed the real host parser too, so this exercises the same routing
            # the app uses rather than a parallel reimplementation.
            tracker.update_line(line, default_cam=cam)
            s.readings.append(
                Reading(t=time.monotonic() - sent, pos=parsed[1], tag=parsed[0])
            )
        return s

    problems: list[str] = []
    errors: list[tuple[int, Reading, str]] = []
    total_reads = 0
    try:
        if not args.no_mode:
            ctrl.set_mode(FAST_REPORT_MODE)
            print(f"sent $M{FAST_REPORT_MODE}*; waiting for homing to finish...")
            t0 = time.monotonic()
            while time.monotonic() - t0 < HOMING_TIMEOUT_S:
                line = next_line(0.2)
                if line is not None and _parse_enc(line, cams[0]) is not None:
                    print(f"encoder traffic resumed after {time.monotonic() - t0:.1f}s\n")
                    break
            else:
                print("WARNING: no encoder traffic after homing timeout\n")

        passes = 1 if args.park is not None else args.cycles
        for cycle in range(1, passes + 1):
            samples = [
                collect(cam, args.seconds if args.park is not None else args.dwell)
                for cam in cams
            ]
            rows, probs = summarise(samples)
            print(f"cycle {cycle}/{passes}")
            for r in rows:
                print(r)
            for p in probs:
                print(f"  ! {p}")
            print()
            problems.extend(probs)
            for s in samples:
                total_reads += len(s.readings)
                errors.extend((s.asked, r, "-1") for r in s.bad)
                errors.extend((s.asked, r, "outlier") for r in s.outliers)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        ctrl.disconnect()

    print(f"== {len(errors)} error(s) in {total_reads} reads", end="")
    if total_reads:
        print(f" ({100.0 * len(errors) / total_reads:.2f}%)")
    else:
        print()
    by_cam = Counter(cam for cam, _r, _k in errors)
    by_kind = Counter(kind for _c, _r, kind in errors)
    if errors:
        print(f"   by kind: {dict(by_kind)}")
        print(f"   by cam:  {dict(by_cam)}")
        print("   time since $Cn*:")
        for line in bucket_errors(errors):
            print(line)

    if problems:
        return 1
    print("PASS: every camera reported its own encoder cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
