"""Qt wrapper around ``serial_comm.FocusController``.

The existing FocusController (repo root) is pure-pyserial and already has a
background reader thread. This wrapper

* exposes a ``line_received`` PySide6 Signal (queued, so slots run on the GUI
  thread);
* centralises the mode-send -> ``ModeController.set_mode()`` update so the
  rest of the app just calls ``send_mode(0)`` and trusts the gate;
* offers the same command set as ``clearcore_protocol.py``: ``$Mn``, ``$Cn``,
  ``$Sn``, ``+``/``-``, ``>``/``<``.
"""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QObject, Signal

from serial_comm import FocusController  # repo root; see sys.path bootstrap in app.py

from .focus_axis import EncoderTracker
from .mode_controller import Mode, ModeController


class SerialClient(QObject):
    """QObject facade over the pyserial-based FocusController."""

    line_received = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self, mode_controller: ModeController) -> None:
        super().__init__()
        self._ctrl = FocusController()
        self._modes = mode_controller
        # Accumulates the AMT203S ``$ENC=`` stream into an unwrapped motor count.
        # Fed on the reader thread (see _on_line) so EncoderAxis can read a fresh
        # position even while an AF step runs synchronously on the GUI thread.
        self._encoder = EncoderTracker()
        self._encoder_epoch = ""

    # ── encoder (AMT203S motor-shaft absolute position) ──────────────────

    @property
    def encoder_position(self) -> float:
        """Latest unwrapped motor-shaft count (0 until the first ``$ENC``)."""
        return float(self._encoder.position)

    @property
    def encoder_present(self) -> bool:
        """True once a valid (non-sentinel) ``$ENC`` reading has arrived."""
        return self._encoder.present

    @property
    def encoder_epoch(self) -> str:
        """Token identifying the current encoder zero. Counts are only
        comparable between two readings carrying the SAME epoch.

        The ClearCore re-references on connect, so the same physical lens
        position reads differently afterwards -- measured 2026-08-13, a run
        ended at raw=-366 and the next one after a relaunch started at
        raw=-3391 with nothing having driven the focus in between. A consumer
        that rebases across epochs invents thousands of counts of drift that
        never happened, so the epoch is stamped alongside every count rather
        than left for the reader to guess at."""
        return self._encoder_epoch

    # ── port enumeration ────────────────────────────────────────────────

    @staticmethod
    def list_ports() -> list[str]:
        return FocusController.list_ports()

    @staticmethod
    def auto_detect() -> str | None:
        return FocusController.find_clearcore()

    # ── connection ──────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._ctrl.connected

    def connect_port(self, port: str) -> None:
        self._ctrl.connect(port, on_response=self._on_line)
        self._encoder_epoch = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        self.connection_changed.emit(True)

    def disconnect_port(self) -> None:
        self._ctrl.disconnect()
        self.connection_changed.emit(False)

    # ── commands ────────────────────────────────────────────────────────

    def send_mode(self, mode_num: int, *, update_local: bool = True) -> None:
        """Send ``$Mn*`` and optionally update :class:`ModeController`.

        When *update_local* is False, only the UART command is sent; the
        MainWindow will apply ``ModeController`` + camera side-effects after
        the PCB finishes the in-flight sweep and starts its homing sequence
        (serial line) or after a timeout.  Used for M0<->M1 transitions so
        the host doesn't reset sweep / flip gates while triggers are still
        landing for the tail of the previous sweep mode.
        """
        if mode_num not in (0, 1, 2, 3, 4, 5, 6, 7):
            raise ValueError(f"mode {mode_num!r} out of range")
        self._ctrl.set_mode(mode_num)
        if update_local:
            mapping = {0: Mode.M0, 1: Mode.M1, 3: Mode.M3, 4: Mode.M4, 5: Mode.M5, 6: Mode.M6}
            self._modes.set_mode(mapping.get(mode_num, Mode.UNKNOWN))

    def select_camera(self, cam: int) -> None:
        self._ctrl.select_camera(cam)

    def set_strength(self, level: int) -> None:
        self._ctrl.set_strength(level)

    def focus_step(self, direction: str, count: int = 1) -> None:
        self._ctrl.focus_step(direction, count)

    def adjust_trigger_period(self, direction: str) -> None:
        self._ctrl.adjust_trigger_period(direction)

    def set_m5_positions(self, positions: int) -> None:
        """M5 ground test: set stepped-sweep positions per cycle ($P<n>*)."""
        self._ctrl.set_m5_positions(positions)

    def set_m5_sweep_angle(self, degrees: float) -> None:
        """M5 ground test: set total mirror sweep angle in degrees ($W<n>*)."""
        self._ctrl.set_m5_sweep_angle(degrees)

    def start_line(self) -> None:
        """Send ``?`` (M3 manual-line START) to the PCB."""
        self._ctrl.start_line()

    def end_line(self) -> None:
        """Send ``!`` (M3 manual-line END) to the PCB."""
        self._ctrl.end_line()

    def send_test_trigger(self) -> None:
        """Send one ``$T*`` to fire a single camera trigger without
        moving the motors (PCB ``testClick`` handler).

        Thin pass-through to :meth:`FocusController.test_trigger` so
        the rest of the app interacts with one consistent serial
        facade.
        """
        self._ctrl.test_trigger()

    def set_external_trigger(self, on: bool) -> None:
        """``$X1*``/``$X0*``: M4 fires only on host ``$T*``, or on its own timer.

        Bench only -- see :meth:`FocusController.set_external_trigger`.
        """
        self._ctrl.set_external_trigger(on)

    # ── internal ────────────────────────────────────────────────────────

    def _on_line(self, line: str) -> None:
        # Runs on the pyserial reader thread. Fold any $ENC reading in HERE (not in
        # the queued GUI slot) so the motor position stays current even while a
        # blocking AF step monopolises the GUI event loop. Qt.AutoConnection makes
        # the emit a queued hand-off to the GUI thread for logging/UI.
        self._encoder.update_line(line)
        self.line_received.emit(line)
