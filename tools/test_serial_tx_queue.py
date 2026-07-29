"""Self-checks for the non-blocking serial TX path (no hardware).

The M4 crash was a blocking ``serial.Serial.write`` on the GUI thread: when the
ClearCore stopped draining, ``write`` hung for seconds, backing up 8 MB focus
frames until the box died. ``FocusController`` now queues every command to a
background TX thread, so a stalled port can NEVER block the caller. These checks
pin the three properties that fix depends on:

* ``_send`` returns immediately even while the port's ``write`` is blocked;
* commands are delivered in FIFO order (focus-pulse bursts stay sequential);
* a wedged port drops on a full queue instead of growing without bound.

Run: ``py -3 tools/test_serial_tx_queue.py``
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import serial_comm  # noqa: E402
from serial_comm import FocusController  # noqa: E402


class _FakeSerial:
    """Stand-in for ``serial.Serial``: records writes and can be told to block
    each ``write`` on a gate, simulating a PCB that has stopped draining."""

    def __init__(self) -> None:
        self.is_open = True
        self.writes: list[bytes] = []
        self._gate = threading.Event()
        self._gate.set()  # open by default -> writes pass straight through
        self._lock = threading.Lock()

    def block(self) -> None:
        self._gate.clear()

    def unblock(self) -> None:
        self._gate.set()

    def write(self, data: bytes) -> int:
        # Block here while the gate is closed -- like a full OS TX buffer.
        self._gate.wait()
        with self._lock:
            self.writes.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.is_open = False


def _wire(ctrl: FocusController, ser: _FakeSerial) -> None:
    """Attach a fake port and start only the TX thread (skip the real reader)."""
    ctrl._ser = ser
    ctrl._running = True
    ctrl._tx_dropped = 0
    ctrl._tx_thread = threading.Thread(target=ctrl._tx_loop, daemon=True)
    ctrl._tx_thread.start()


def _teardown(ctrl: FocusController) -> None:
    ctrl._running = False
    try:
        ctrl._tx_q.put_nowait(None)
    except Exception:
        pass
    if ctrl._tx_thread is not None:
        ctrl._tx_thread.join(timeout=2)


def test_send_never_blocks_even_when_write_is_stalled() -> None:
    ctrl = FocusController()
    ser = _FakeSerial()
    ser.block()  # port is wedged: every write() will hang
    _wire(ctrl, ser)
    try:
        t0 = time.monotonic()
        ctrl.focus_step("+", 5)  # would hang the caller under the old blocking path
        dt = time.monotonic() - t0
        assert dt < 0.2, f"_send blocked for {dt:.3f}s while the port was stalled"
        # Nothing got through yet (write is parked on the gate).
        assert ser.writes == [], f"unexpected early writes: {ser.writes!r}"
        # Release the port -> the queued command drains.
        ser.unblock()
        deadline = time.monotonic() + 2
        while not ser.writes and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ser.writes == [b"+++++\n"], f"drained writes wrong: {ser.writes!r}"
    finally:
        _teardown(ctrl)
    print("ok: _send is non-blocking; queued command drains once the port frees")


def test_commands_preserve_fifo_order() -> None:
    ctrl = FocusController()
    ser = _FakeSerial()
    ser.block()  # hold everything so we can enqueue a known sequence first
    _wire(ctrl, ser)
    try:
        ctrl.set_mode(4)
        ctrl.set_strength(0)
        ctrl.focus_step("-", 3)
        ctrl.focus_step("+", 1)
        ser.unblock()
        deadline = time.monotonic() + 2
        while len(ser.writes) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ser.writes == [b"$M4*\n", b"$S0*\n", b"---\n", b"+\n"], (
            f"FIFO order not preserved: {ser.writes!r}"
        )
    finally:
        _teardown(ctrl)
    print("ok: commands are delivered in FIFO order")


def test_full_queue_drops_instead_of_growing() -> None:
    # Shrink the queue so the test is fast; the real cap is 256.
    ctrl = FocusController()
    import queue as _queue

    ctrl._tx_q = _queue.Queue(maxsize=4)
    ser = _FakeSerial()
    ser.block()  # nothing drains -> the queue fills and then must DROP
    _wire(ctrl, ser)
    try:
        for _ in range(50):
            ctrl.focus_step("+", 1)  # far more than the queue can hold
        assert ctrl._tx_q.qsize() <= 4, f"queue grew past cap: {ctrl._tx_q.qsize()}"
        assert ctrl._tx_dropped >= 46, f"expected drops, got {ctrl._tx_dropped}"
    finally:
        ser.unblock()
        _teardown(ctrl)
    print("ok: a wedged port drops commands on a full queue (bounded memory)")


if __name__ == "__main__":
    test_send_never_blocks_even_when_write_is_stalled()
    test_commands_preserve_fifo_order()
    test_full_queue_drops_instead_of_growing()
    print("\nall serial-TX-queue self-checks passed")
