"""PCB mode state, with a single ``can_write()`` gate.

The unified app itself sends every mode command (``$M0*``/``$M1*``/``$M4*``),
so it always knows the commanded mode. Per the operator's spec:

* M0 and M1 are *recording* modes -- frames received while the PCB is in
  either of these is written to disk. There is no manual arm step: a missed
  capture is more costly than an extra one, so we always write in M0/M1.
* M3 (CollectionON) records only between manual ``?``/``!`` line brackets.
  The host sets ``m3_collecting`` when the operator presses Start line and
  clears it on End line (after the in-progress sweep finishes).
* M4 is the *focus* mode -- frames are previewed but not written by default.
  An optional toggle ("Save focus view") in the GUI can opt in to saving
  best-tile focus crops while in M4; that path is independent of this gate.

Keeping the recording decision in one place lets the UI, capture pipeline,
sweep counters, and writer threads all agree without duplicating logic.
"""

from __future__ import annotations

import threading
from enum import Enum


class Mode(Enum):
    UNKNOWN = "unknown"
    M0 = "M0"
    M1 = "M1"
    M3 = "M3"
    M4 = "M4"


# Operator-facing mode button labels (PCB commands remain $M0* / $M1* / …).
MODE_BUTTON_LABELS: dict[Mode, str] = {
    Mode.M0: "Normal",
    Mode.M1: "Continuous",
    Mode.M3: "CollectionON",
    Mode.M4: "Focus",
}

# Fixed button widths so longer labels do not reflow the mode row.
MODE_BUTTON_WIDTH_FULL: dict[Mode, int] = {
    Mode.M0: 58,
    Mode.M1: 76,
    Mode.M3: 92,
    Mode.M4: 52,
}

MODE_BUTTON_WIDTH_COMPACT: dict[Mode, int] = {
    Mode.M0: 48,
    Mode.M1: 62,
    Mode.M3: 78,
    Mode.M4: 42,
}


def mode_display_label(mode: Mode) -> str:
    """Human-readable mode name for GUI chrome (falls back to enum name)."""
    if mode == Mode.UNKNOWN:
        return "UNKNOWN"
    return MODE_BUTTON_LABELS.get(mode, mode.name)


class FocusSubMode(Enum):
    """M4 preview detail: decimated coarse tile vs full-res fine tile."""

    COARSE = "coarse"
    FINE = "fine"


_RECORDING_MODES = frozenset({Mode.M0, Mode.M1})


class ModeController:
    """Thread-safe commanded-mode tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = Mode.UNKNOWN
        self._focus_submode = FocusSubMode.COARSE
        self._m3_collecting = False

    @property
    def mode(self) -> Mode:
        with self._lock:
            return self._mode

    def set_mode(self, mode: Mode) -> None:
        with self._lock:
            self._mode = mode
            if mode != Mode.M3:
                self._m3_collecting = False

    def set_m3_collecting(self, active: bool) -> None:
        with self._lock:
            self._m3_collecting = bool(active) and self._mode == Mode.M3

    def m3_collecting(self) -> bool:
        with self._lock:
            return self._m3_collecting and self._mode == Mode.M3

    def _is_recording_locked(self) -> bool:
        if self._mode in _RECORDING_MODES:
            return True
        return self._mode == Mode.M3 and self._m3_collecting

    def can_write(self) -> bool:
        """True iff the writer should persist the next received frame.

        Equivalent to ``is_recording_mode()`` -- kept under this name so call
        sites in the writer thread read naturally (``if not modes.can_write():
        drop``).
        """
        with self._lock:
            return self._is_recording_locked()

    def is_recording_mode(self) -> bool:
        with self._lock:
            return self._is_recording_locked()

    def is_focus_mode(self) -> bool:
        with self._lock:
            return self._mode == Mode.M4

    def focus_submode(self) -> FocusSubMode:
        with self._lock:
            return self._focus_submode

    def set_focus_submode(self, sub: FocusSubMode) -> None:
        with self._lock:
            self._focus_submode = sub
