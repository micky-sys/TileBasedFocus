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

Run:  py -3 tools/serial_af_stress.py [COM38] [seconds]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from serial_comm import FocusController  # repo root

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM38"
DURATION_S = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0

# AF cadence: ~one climb step per frame at ~5 fps, in bursts of 2 pulses.
STEP_PULSES = 2
STEP_INTERVAL_S = 0.2
PINGPONG_STEPS = 15  # reverse every N steps -> lens stays within ~뿯½(2*15) pulses


def main() -> int:
    last_lines: list[str] = []
    enc_count = [0]

    def on_line(line: str) -> None:
        if "$ENC" in line:
            enc_count[0] += 1
        last_lines.append(line)
        del last_lines[:-4]

    fc = FocusController()
    print(f"connecting {PORT} ...", flush=True)
    fc.connect(PORT, on_response=on_line)
    time.sleep(1.0)
    fc.set_mode(4)  # M4 focus mode: motor active + $ENC streaming
    time.sleep(1.0)
    fc.set_strength(0)  # $S0* (extra-fine; needs V2_4+ firmware)
    print(f"driving AF-like pulse storm for {DURATION_S:.0f}s "
          f"({STEP_PULSES} pulses / {STEP_INTERVAL_S*1000:.0f} ms, "
          f"ping-pong every {PINGPONG_STEPS})", flush=True)

    t0 = time.time()
    t_report = t0
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
            print(f"[{now - t0:6.1f}s] pulses_sent={sent} enc_lines={enc_count[0]} "
                  f"tx_dropped={fc._tx_dropped} last={last_lines[-1] if last_lines else '<none>'}",
                  flush=True)
        time.sleep(STEP_INTERVAL_S)

    # Return the lens roughly where it started (in case the loop ended mid-swing).
    print(f"DONE: pulses_sent={sent} enc_lines={enc_count[0]} tx_dropped={fc._tx_dropped}",
          flush=True)
    fc.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
