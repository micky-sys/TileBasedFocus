"""Serial communication with the ClearCore focus-control PCB.

Protocol (115200 baud, newline-terminated):
  $Mn*\\n          -> set PCB mode n (0/1 = recording, 3 = manual line, 4 = focus)
  $Cn*\\n          -> select PCB camera slot n (1-3); host maps Iron661 serial
                     to n via ``PCB_FOCUS_CAMERA_SLOT_BY_SERIAL`` (2439006→1,
                     2439007→2, 2439008→3).
  $Sn*\\n          -> set focus strength n (1=7k, 2=10k, 3=14k)
  + or - (repeated count times) + \\n  -> H-bridge pulse
  > or < + \\n     -> adjust trigger period
  ? + \\n          -> M3 only: mark START of a manual line
  ! + \\n          -> M3 only: mark END of a manual line
"""

import threading
import logging
from typing import Callable, Optional

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)

BAUD_RATE = 115200
VALID_CAMERAS = (1, 2, 3)
VALID_STRENGTHS = (1, 2, 3)

# PCB ``$Cn*`` in focus mode: logical ClearCore camera slot by Iron661 serial
# (fixed rig wiring). UI cam1/cam2/cam3 slots may reorder; UART must send the
# slot that matches this serial.
PCB_FOCUS_CAMERA_SLOT_BY_SERIAL: dict[str, int] = {
    "2439006": 1,
    "2439007": 2,
    "2439008": 3,
}


class FocusController:
    """Wraps pyserial to send focus-control commands to the ClearCore PCB."""

    def __init__(self) -> None:
        self._ser: Optional[serial.Serial] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._on_response: Optional[Callable[[str], None]] = None

    # ── Port enumeration ──────────────────────────────────────────────

    CLEARCORE_ID = "Teknic ClearCore"

    @staticmethod
    def list_ports() -> list[str]:
        return [p.device for p in serial.tools.list_ports.comports()]

    @staticmethod
    def find_clearcore() -> Optional[str]:
        """Return the COM port of the first Teknic ClearCore device, or None."""
        for p in serial.tools.list_ports.comports():
            if FocusController.CLEARCORE_ID.lower() in (p.description or "").lower():
                log.info("Found ClearCore on %s (%s)", p.device, p.description)
                return p.device
        return None

    # ── Connection ────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def connect(self, port: str, on_response: Optional[Callable[[str], None]] = None) -> None:
        if self.connected:
            self.disconnect()
        self._ser = serial.Serial(port, BAUD_RATE, timeout=0.1)
        self._on_response = on_response
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        log.info("Connected to %s", port)

    def disconnect(self) -> None:
        self._running = False
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        if self._ser is not None:
            self._ser.close()
            self._ser = None
        log.info("Disconnected")

    # ── Commands ──────────────────────────────────────────────────────

    def _send(self, data: str) -> None:
        if not self.connected:
            log.warning("Not connected — command ignored: %r", data)
            return
        self._ser.write(data.encode("ascii"))
        log.debug("TX: %r", data)

    def set_mode(self, mode: int) -> None:
        self._send(f"$M{mode}*\n")

    def select_camera(self, cam: int) -> None:
        if cam not in VALID_CAMERAS:
            raise ValueError(f"Camera must be one of {VALID_CAMERAS}")
        self._send(f"$C{cam}*\n")

    def set_strength(self, strength: int) -> None:
        if strength not in VALID_STRENGTHS:
            raise ValueError(f"Strength must be one of {VALID_STRENGTHS}")
        self._send(f"$S{strength}*\n")

    def focus_step(self, direction: str, count: int = 1) -> None:
        """Send an H-bridge pulse. *direction* is '+' or '-'.
        *count* controls pulse duration (number of 10 ms increments on the PCB).
        """
        if direction not in ("+", "-"):
            raise ValueError("direction must be '+' or '-'")
        self._send(direction * count + "\n")

    def adjust_trigger_period(self, direction: str) -> None:
        """Lengthen ('>') or shorten ('<') the trigger period."""
        if direction not in (">", "<"):
            raise ValueError("direction must be '>' or '<'")
        self._send(direction + "\n")

    def start_line(self) -> None:
        """M3-only: send ``?\\n`` to mark the START of a manual line.

        The PCB interprets ``?`` as 'begin manual line'. Host gates this
        button so it can only fire while in M3, but the wire-level command
        itself is a single byte + newline.
        """
        self._send("?\n")

    def end_line(self) -> None:
        """M3-only: send ``!\\n`` to mark the END of a manual line."""
        self._send("!\n")

    def test_trigger(self) -> None:
        """Fire one $T* command: a single trigger pulse to all cameras
        without disturbing the focus / nav-point motors.

        Implemented on the PCB (Parallax_CLRCR_V2_4) as ``testClick()``:
        drives ``camTrigPin`` high for ``picDelayCurr`` ms, then low for
        ``picsInPlaceDelay`` ms. End-to-end one click is ~310-1800 ms
        depending on the current speed setting, but the GUI doesn't
        block on it -- ``_send`` is a fire-and-forget byte write.

        Useful as a non-invasive liveness probe after camera recovery:
        click N times, watch each camera's ``acq:N`` label tick up.
        A camera that doesn't tick is still stuck and needs a deeper
        reset.
        """
        self._send("$T*\n")

    # ── Reader thread ─────────────────────────────────────────────────

    def _read_loop(self) -> None:
        while self._running:
            try:
                if self._ser is None or not self._ser.is_open:
                    break
                line = self._ser.readline()
                if line:
                    text = line.decode("ascii", errors="replace").strip()
                    if text:
                        log.debug("RX: %s", text)
                        if self._on_response:
                            self._on_response(text)
            except Exception:
                if self._running:
                    log.exception("Serial read error")
                break
