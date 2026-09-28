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
import logging

from PySide6.QtCore import QObject, Signal

# repo root; see sys.path bootstrap in app.py
from serial_comm import PCB_FOCUS_CAMERA_SLOT_BY_SERIAL, FocusController

from .focus_axis import MultiEncoderTracker
from .mode_controller import Mode, ModeController

log = logging.getLogger(__name__)

# Stands for "this app camera is wired to no PCB slot". Real slots are 1-3, and
# MultiEncoderTracker has no accumulator for 0, so it reads back as absent.
_NO_SLOT = 0


class SerialClient(QObject):
    """QObject facade over the pyserial-based FocusController.

    TWO CAMERA NUMBERINGS MEET HERE, AND ONLY ONE LEAVES.

    The ClearCore numbers cameras by PCB slot -- which relay drives which lens,
    fixed by the rig wiring. The app numbers them by grabber enumeration order,
    which depends on what the grabber happened to find: plug in one camera and
    it is "cam1" whatever slot it occupies.

    They are not the same number. Serial 2439008 sits in PCB slot 3, so a rig
    running that camera alone has the app calling it cam1 while every ``$ENC``
    it produces is tagged 3. Crediting that reading to app-camera 3 leaves the
    real camera looking like it has no encoder at all, which is exactly what it
    did: frames went out with no ``focus`` block while the GUI cheerfully
    displayed "enc: <count> (cam3)".

    So this class translates at its own edges and every method above it speaks
    APP camera numbers. The wire, and :class:`MultiEncoderTracker` behind it,
    stay in PCB slots -- the tracker is one accumulator per physical encoder on
    a shared bus, so slots are the honest key there.

    The map comes from :meth:`set_camera_serial`, which the app calls as each
    camera opens. Until a camera is registered the two numberings are assumed
    equal, which is right for a full three-camera rig in wiring order and is
    the best guess available for an unrecognised serial.
    """

    line_received = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self, mode_controller: ModeController) -> None:
        super().__init__()
        self._ctrl = FocusController()
        self._modes = mode_controller
        # Accumulates the AMT203S ``$ENC=`` stream into an unwrapped motor count,
        # one accumulator per camera. Fed on the reader thread (see _on_line) so
        # EncoderAxis can read a fresh position even while an AF step runs
        # synchronously on the GUI thread.
        self._encoders = MultiEncoderTracker()
        self._encoder_epoch = ""
        # APP camera number the host last asked for with ``$Cn*``. Used to
        # credit readings from the older untagged firmware, and as the fallback
        # answer for "which camera is selected" before the PCB has reported one.
        self._selected_camera = 1
        # app camera number <-> PCB slot, filled in by set_camera_serial().
        self._slot_by_cam: dict[int, int] = {}
        self._cam_by_slot: dict[int, int] = {}

    # ── camera numbering (app <-> PCB slot) ──────────────────────────────

    def set_camera_serial(self, camera_number: int, camera_serial: str) -> None:
        """Register that app camera *camera_number* is the given Iron661 serial.

        Called once per camera as it opens. Resolves the serial to its PCB slot
        via the fixed rig wiring table so ``$ENC`` tags can be credited to the
        camera the rest of the app is talking about.
        """
        cam = int(camera_number)
        slot = PCB_FOCUS_CAMERA_SLOT_BY_SERIAL.get(str(camera_serial).strip())
        if slot is None:
            # Unknown serial: assume the numberings coincide rather than drop
            # the encoder entirely, and say so -- a silent identity mapping is
            # how this class of bug hides.
            log.warning(
                "cam%d serial %r is not in the PCB slot table %s; assuming PCB "
                "slot %d. Encoder readings may be credited to the wrong camera.",
                cam, camera_serial, sorted(PCB_FOCUS_CAMERA_SLOT_BY_SERIAL), cam,
            )
            slot = cam
        self._slot_by_cam[cam] = int(slot)
        self._cam_by_slot[int(slot)] = cam
        log.info("cam%d (serial %s) -> PCB slot %d", cam, camera_serial, slot)

    def pcb_slot_for(self, camera_number: int) -> int:
        """PCB ``$Cn*`` slot driving app camera *camera_number*.

        An app camera that was never registered gets ``_NO_SLOT`` once any
        other camera has been, rather than falling back to its own number: on
        a one-camera rig the identity guess would alias app-camera 3 onto slot
        3, which is the camera the app calls cam1, and hand out its encoder
        under the wrong name. Before any camera opens there is nothing to
        collide with, so identity is still the best guess.
        """
        cam = int(camera_number)
        slot = self._slot_by_cam.get(cam)
        if slot is not None:
            return slot
        return _NO_SLOT if self._slot_by_cam else cam

    def camera_for_pcb_slot(self, slot: int) -> int:
        """App camera number sitting in PCB *slot*."""
        return self._cam_by_slot.get(int(slot), int(slot))

    # ── encoder (AMT203S motor-shaft absolute position) ──────────────────

    @property
    def selected_camera(self) -> int:
        """Camera whose focus motor AND encoder the relays are currently on.

        Prefers what the PCB actually reported over what we last asked for: the
        firmware tags every ``$ENC`` with the live relay state, so after a
        reconnect or an out-of-band ``$Cn*`` the PCB is the authority."""
        reported = self._encoders.reported_camera
        if reported is None:
            return int(self._selected_camera)
        return self.camera_for_pcb_slot(int(reported))

    @property
    def encoder_position(self) -> float:
        """Unwrapped count for the SELECTED camera (0 until its first ``$ENC``)."""
        return self.encoder_position_for(self.selected_camera)

    @property
    def encoder_present(self) -> bool:
        """True once the SELECTED camera has given a valid ``$ENC`` reading."""
        return self.encoder_present_for(self.selected_camera)

    def encoder_position_for(self, cam: int) -> float:
        """Unwrapped count for app camera *cam*, which may be stale -- see
        :meth:`encoder_age_for`.

        Only the selected camera's encoder is readable (one SPI bus, relay
        selected), so the other two answer with their last known count."""
        return float(self._encoders.position(self.pcb_slot_for(cam)))

    def encoder_present_for(self, cam: int) -> bool:
        """True once app camera *cam* has given a valid reading this epoch."""
        return self._encoders.present(self.pcb_slot_for(cam))

    def encoder_age_for(self, cam: int) -> float | None:
        """Seconds since app camera *cam*'s count was refreshed, or None."""
        return self._encoders.age_s(self.pcb_slot_for(cam))

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
        # New epoch means the old counts are not comparable, so drop them rather
        # than let a pre-reconnect count masquerade as current.
        self._encoders.reset()
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
        """``$Cn*``: point the H-bridge -- and the encoder SPI bus -- at app
        camera *cam*.

        Takes an APP camera number and sends the PCB slot that camera is wired
        to. One set of relays does both jobs, so this also changes which
        encoder can be read; the other two cameras' counts start going stale
        from here."""
        self._ctrl.select_camera(self.pcb_slot_for(cam))
        self._selected_camera = int(cam)

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

    def fire_event(self) -> None:
        """``$E*``: one GNSS EXT_EVENT pulse, no camera, no motors.

        The probe used to decide whether the NMEA bridge on COM3 is really there
        -- see :meth:`FocusController.fire_event`."""
        self._ctrl.fire_event()

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
        # default_cam only matters for the older untagged firmware; tagged lines
        # carry the live relay state and are believed over our own record of it.
        # The tracker is keyed by PCB slot, which is what a ``$ENC`` tag
        # carries, so the untagged-firmware fallback must be a slot too.
        self._encoders.update_line(
            line, default_cam=self.pcb_slot_for(self._selected_camera)
        )
        self.line_received.emit(line)
