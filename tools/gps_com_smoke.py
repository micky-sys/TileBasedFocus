"""Live SBF smoke test against a Septentrio receiver on a COM port.

Opens the given port at 115200 8N1, feeds raw bytes into the same
:class:`SbfParser` the capture app uses, and prints every decoded
``ExtEventPVTGeodetic`` block (lat / lon / fix / sats / UTC), plus a
distance-from-target reading so you can sanity-check the fix without a
map. Also prints byte/sync counters so a port with non-SBF traffic
(e.g. NMEA, or a wrong port entirely) is obvious from the first second.

Usage (from repo root)::

    py tools/gps_com_smoke.py --port COM3 --seconds 30
    py tools/gps_com_smoke.py --port COM16 --target-lat 32.17025 --target-lon 34.83136

For the RS232 NMEA bridge (no SBF on the wire), ``--raw`` dumps the sentences
themselves plus the gap between them, which is what tells you whether events
arrive one-per-trigger or on a fixed schedule::

    py tools/gps_com_smoke.py --port COM3 --raw --seconds 20
    py tools/gps_com_smoke.py --port COM3 --raw --baud 38400 --seconds 20

Pure side-thread is unused on purpose -- we just block-read in the main
thread so Ctrl-C works cleanly without a Qt loop. Standalone of the
GUI; only depends on ``pyserial`` and ``host/capture/sbf_parser.py``.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    host_dir = os.path.join(repo_root, "host")
    for p in (repo_root, host_dir):
        if p and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_paths()

import serial  # noqa: E402
import serial.tools.list_ports  # noqa: E402

from capture.sbf_parser import SbfExtEvent, SbfParser  # noqa: E402


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Accurate to ~0.5% which is
    plenty for a "is this fix near the right building?" smoke check."""
    r_earth = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_earth * math.asin(math.sqrt(a))


def _format_event(ev: SbfExtEvent, target: tuple[float, float] | None) -> str:
    utc = ev.utc_datetime()
    utc_s = utc.strftime("%Y-%m-%d %H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z" if utc else "?"
    if ev.has_position:
        lat, lon = ev.latitude_deg, ev.longitude_deg
        coord = f"lat={lat:+.7f} lon={lon:+.7f}"
        if target is not None:
            d = _haversine_m(lat, lon, target[0], target[1])
            coord += f"  dist_from_target={d:8.1f} m"
    else:
        coord = "lat=?         lon=?         (Do-Not-Use)"
    h = f"{ev.height_m:7.2f} m" if ev.height_m is not None else "  ?    m"
    nsv = f"{ev.n_sv:2d}" if ev.n_sv is not None else "??"
    return (
        f"  tow={ev.tow_ms:>10d} ms  wnc={ev.wnc:>4d}  "
        f"fix={ev.fix_solution}  err={ev.error}  nsv={nsv}  "
        f"h={h}  {coord}  utc={utc_s}"
    )


def _list_septentrio_ports() -> list[str]:
    hints = ("septentrio", "polarx", "mosaic", "asterx", "altus")
    out = []
    for p in serial.tools.list_ports.comports():
        haystack = " ".join(filter(None, [p.description, p.manufacturer])).lower()
        if any(h in haystack for h in hints):
            out.append(f"{p.device} -- {p.description}")
    return out


def _raw_dump(ser, seconds: float) -> int:
    """Print every line arriving on the port, with arrival time and inter-line gap.

    For the NMEA bridge: we need to know WHICH sentences it emits and whether they
    arrive one-per-trigger or on a fixed schedule. The gap column answers the
    second question -- a PCB sweep gives ~200 ms between triggers and ~300 ms
    across the inter-sweep pause, whereas a periodic talker gives a flat rate no
    matter what the ClearCore does.

    Uses ``readline`` so each line is timestamped when ITS newline arrives rather
    than when a 4 KB chunk does; gaps are good to a few ms, which is plenty to
    tell 200 ms from 300 ms.
    """
    t0 = time.monotonic()
    deadline = t0 + seconds
    last: float | None = None
    counts: dict[str, int] = {}
    n_lines = 0
    n_binary = 0
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        now = time.monotonic()
        line = raw.rstrip(b"\r\n")
        if not line:
            continue
        n_lines += 1
        gap = "         " if last is None else f"+{(now - last) * 1000:7.1f}ms"
        last = now
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            n_binary += 1
            text = f"<non-ASCII, {len(line)} bytes> {bytes(line[:24])!r}"
        key = text.split(",", 1)[0] if text.startswith("$") else "<other>"
        counts[key] = counts.get(key, 0) + 1
        print(f"  t+{now - t0:7.3f}s {gap}  {text}")

    print()
    print(f"[raw] {n_lines} line(s) in {seconds:.1f}s"
          + (f", {n_binary} non-ASCII (binary, not NMEA)" if n_binary else ""))
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"[raw]   {key:<12} x{n}  ({n / seconds:.2f}/s)")
    if not n_lines:
        print("[raw] RESULT: nothing arrived -- wrong port, wrong baud, "
              "or the bridge is not transmitting.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", required=True, help="COM port (e.g. COM3, COM16).")
    ap.add_argument("--raw", action="store_true",
                    help="Dump raw lines + arrival gaps instead of SBF-decoding. "
                         "Use this on the NMEA bridge.")
    ap.add_argument("--seconds", type=float, default=20.0,
                    help="How long to listen before exiting (default 20).")
    ap.add_argument("--target-lat", type=float, default=32.170250,
                    help="Reference latitude (decimal degrees).")
    ap.add_argument("--target-lon", type=float, default=34.831361,
                    help="Reference longitude (decimal degrees).")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    target = (args.target_lat, args.target_lon)
    print(f"[smoke] opening {args.port} @ {args.baud} 8N1 ...")
    print(f"[smoke] target reference: lat={target[0]:+.6f} lon={target[1]:+.6f}")
    if args.port.upper() == "COM3":
        sept = _list_septentrio_ports()
        if sept:
            print(f"[smoke] note: Septentrio receivers also visible at: {sept}")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as exc:
        print(f"[smoke] open failed: {exc}", file=sys.stderr)
        return 2

    if args.raw:
        try:
            return _raw_dump(ser, args.seconds)
        finally:
            ser.close()

    parser = SbfParser()
    total_bytes = 0
    last_tick = time.monotonic()
    deadline = last_tick + args.seconds
    n_events = 0
    last_event: SbfExtEvent | None = None

    try:
        while time.monotonic() < deadline:
            chunk = ser.read(4096)
            if chunk:
                total_bytes += len(chunk)
                events = parser.feed(chunk)
                for ev in events:
                    n_events += 1
                    last_event = ev
                    print(_format_event(ev, target))
            now = time.monotonic()
            if now - last_tick >= 1.0:
                last_tick = now
                s = parser.stats()
                print(
                    f"[smoke] t+{int(args.seconds - (deadline - now)):>2d}s: "
                    f"bytes={total_bytes:>7d}  good={s['good_blocks']}  "
                    f"events={s['events_decoded']}  "
                    f"badCRC={s['bad_crc']}  badLen={s['bad_length']}  "
                    f"skipped={s['skipped_bytes']}  buf={s['buffered']}"
                )
    except KeyboardInterrupt:
        print("[smoke] interrupted by user", file=sys.stderr)
    finally:
        ser.close()

    print()
    print(f"[smoke] done. {n_events} ExtEventPVTGeodetic block(s) decoded "
          f"in {args.seconds:.1f}s, {total_bytes} bytes read total.")
    print(f"[smoke] parser stats: {parser.stats()}")

    if last_event is None:
        print("[smoke] RESULT: no events decoded -- either wrong port, "
              "wrong baud, no external trigger fired, or non-SBF traffic "
              "on this port (looks like NMEA? try the other Septentrio "
              "virtual COM port).")
        return 1

    if last_event.has_position:
        d = _haversine_m(
            last_event.latitude_deg, last_event.longitude_deg,
            target[0], target[1],
        )
        verdict = "WITHIN 200 m of target" if d < 200 else f"{d:.0f} m from target"
        print(f"[smoke] RESULT: last fix at "
              f"lat={last_event.latitude_deg:+.7f} "
              f"lon={last_event.longitude_deg:+.7f} -- {verdict}")
    else:
        print("[smoke] RESULT: events decoded but no position fix yet "
              "(receiver hasn't locked? check sky view / antenna).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
