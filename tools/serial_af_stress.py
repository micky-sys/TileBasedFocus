"""Serial-only AF-load stress: exercise the REAL FocusController TX thread +
$ENC reader with an autofocus-like pulse storm, WITH NO CAMERA / GRABBER.

Bisection: Fine mode (grabber DMA-hold) survived an hour, so the crash lives in
what AF adds on top. AF's one driver-level addition over Fine is the ClearCore
USB-serial traffic (paced pulse bursts + $ENC stream). This script reproduces
that traffic in isolation. If the box BSODs here, the fault is serial/USB/
firmware -- not the grabber, not the frame pipeline. If it runs clean, serial is
exonerated and the trigger is the grabber-under-AF combination or hardware load.

Motion is a small ping-pong (net ~0) so the lens just wiggles near its current
focus and can't walk into a hard stop while running unattended.

Run:  py -3 tools/serial_af_stress.py [COM38] [seconds] [camera 1..3]
(camera: which focus motor/encoder to drive; firmware default is 1)
"""
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "host"))

from serial_comm import FocusController  # repo root  # noqa: E402
from capture.focus_axis import _ENC_RE  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM38"
DURATION_S = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0
CAMERA = int(sys.argv[3]) if len(sys.argv) > 3 else None  # None: leave firmware default

# AF cadence: ~one climb step per frame at ~5 fps, in bursts of 2 pulses.
STEP_PULSES = 2
STEP_INTERVAL_S = 0.2
PINGPONG_STEPS = 15  # reverse every N steps -> lens stays within ~뿯½(2*15) pulses


def main() -> int:
    last_lines: list[str] = []
    enc_count = [0]
    # Encoder-stream health while the motor turns (2026-11-24, HANDOFF 59.4): a $ENC of -1 is a
    # read the firmware could not confirm (the old equality rule failed every in-motion read);
    # the largest raw step between consecutive GOOD readings, mod 4096, is how close the stream
    # came to the host's half-turn (2048 c) unwrap limit.
    enc_fail = [0]
    enc_last = [None]
    enc_max_step = [0]
    # Longest silence between two consecutive $ENC lines (any value): the unwrap can only fold
    # if the shaft moves >2048 c inside one such gap, so this -- not the mean rate -- is the
    # envelope. Host-side timestamps, so USB latency jitter is included (conservative).
    enc_t_last = [None]
    enc_max_gap_ms = [0.0]
    gap_armed = [False]  # only once the pulse storm is running (homing is a legitimate silence)

    def on_line(line: str) -> None:
        if "$ENC" in line:
            enc_count[0] += 1
            now = time.monotonic()
            if gap_armed[0] and enc_t_last[0] is not None:
                enc_max_gap_ms[0] = max(enc_max_gap_ms[0], (now - enc_t_last[0]) * 1000.0)
            enc_t_last[0] = now
            m = _ENC_RE.search(line)
            raw = int(m.group(2)) if m else -1
            if raw < 0:
                enc_fail[0] += 1
            else:
                if enc_last[0] is not None:
                    d = abs(raw - enc_last[0])
                    enc_max_step[0] = max(enc_max_step[0], min(d, 4096 - d))
                enc_last[0] = raw
        last_lines.append(line)
        del last_lines[:-4]

    fc = FocusController()
    print(f"connecting {PORT} ...", flush=True)
    fc.connect(PORT, on_response=on_line)
    time.sleep(1.0)
    fc.set_mode(4)  # M4 focus mode: motor active + $ENC streaming
    time.sleep(6.0)  # $M4* homes the mirrors (~5 s) before loop() services commands again
    if CAMERA is not None:
        fc.select_camera(CAMERA)  # $Cn*: which lens the pulses drive and whose encoder reports
        time.sleep(1.0)
    fc.set_strength(0)  # $S0* (extra-fine; needs V2_4+ firmware)
    print(f"driving AF-like pulse storm for {DURATION_S:.0f}s "
          f"({STEP_PULSES} pulses / {STEP_INTERVAL_S*1000:.0f} ms, "
          f"ping-pong every {PINGPONG_STEPS})", flush=True)

    t0 = time.time()
    t_report = t0
    gap_armed[0] = True
    direction = "+"
    steps = 0
    sent = 0
    while time.time() - t0 < DURATION_S:
        fc.focus_step(direction, STEP_PULSES)
        sent += 1
        steps += 1
        if steps >= PINGPONG_STEPS:
            steps = 0
            direction = "-" if direction == "+" else "+"
        now = time.time()
        if now - t_report >= 15.0:
            t_report = now
            print(f"[{now - t0:6.1f}s] pulses_sent={sent} enc_lines={enc_count[0]} enc_fail={enc_fail[0]} "
                  f"max_step={enc_max_step[0]} tx_dropped={fc._tx_dropped} "
                  f"last={last_lines[-1] if last_lines else '<none>'}",
                  flush=True)
        time.sleep(STEP_INTERVAL_S)

    # Return the lens roughly where it started (in case the loop ended mid-swing).
    print(f"DONE: pulses_sent={sent} enc_lines={enc_count[0]} enc_fail={enc_fail[0]} "
          f"({100.0 * enc_fail[0] / max(enc_count[0], 1):.1f}% of reports) max_step={enc_max_step[0]} c "
          f"(unwrap folds past 2048) max_gap={enc_max_gap_ms[0]:.0f} ms "
          f"(rate {enc_count[0] / max(time.time() - t0, 1e-9):.1f}/s) tx_dropped={fc._tx_dropped}",
          flush=True)
    fc.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
