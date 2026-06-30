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

from PySide6.QtCore import QObject, Signal

from serial_comm import FocusController  # repo root; see sys.path bootstrap in app.py

from .mode_controller import Mode, ModeController


class SerialClient(QObject):
    """QObject facade over the pyserial-based FocusController."""

    line_received = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self, mode_controller: ModeController) -> None:
        super().__init__()
        self._ctrl = FocusController()
        self._modes = mode_controller

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
            mapping = {0: Mode.M0, 1: Mode.M1, 3: Mode.M3, 4: Mode.M4}
            self._modes.set_mode(mapping.get(mode_num, Mode.UNKNOWN))

    def select_camera(self, cam: int) -> None:
        self._ctrl.select_camera(cam)

    def set_strength(self, level: int) -> None:
        self._ctrl.set_strength(level)

    def focus_step(self, direction: str, count: int = 1) -> None:
        self._ctrl.focus_step(direction, count)

    def adjust_trigger_period(self, direction: str) -> None:
        self._ctrl.adjust_trigger_period(direction)

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

    # ── internal ────────────────────────────────────────────────────────

    def _on_line(self, line: str) -> None:
        # Runs on the pyserial reader thread. Qt.AutoConnection makes this
        # a queued emit because the sender (self) lives on the GUI thread.
        self.line_received.emit(line)
