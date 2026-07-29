"""
Serial line helpers for Teknic ClearCore focus / flight sketch.
See docs/clearcore_serial_protocol.md for behavior and firmware caveats.
"""

from __future__ import annotations

BAUD_RATE = 115200
LINE_END = "\n"

# Commands (send with newline terminator)
CMD_FOCUS_MODE = "$M4*"
CMD_CAMERA_1 = "$C1*"
CMD_CAMERA_2 = "$C2*"
CMD_CAMERA_3 = "$C3*"
CMD_STRENGTH_EXTRAFINE = "$S0*"  # focusStrength 3500 (V2_4+ firmware; autofocus)
CMD_STRENGTH_COARSE = "$S1*"  # focusStrength 7000 in firmware
CMD_STRENGTH_MEDIUM = "$S2*"  # 10000
CMD_STRENGTH_FINE = "$S3*"  # 14000

# Frame spacing adjust (mode 4); firmware clamps: min 250, max 1500 ms
CMD_PIC_DELAY_LONGER = ">"
CMD_PIC_DELAY_SHORTER = "<"


def line(payload: str) -> bytes:
    """Encode one outgoing message (no embedded newlines in payload)."""
    if "\n" in payload or "\r" in payload:
        raise ValueError("payload must not contain line breaks")
    return (payload + LINE_END).encode("ascii")


def focus_mode() -> bytes:
    return line(CMD_FOCUS_MODE)


def select_camera(index: int) -> bytes:
    if index not in (1, 2, 3):
        raise ValueError("camera index must be 1, 2, or 3")
    return line(f"$C{index}*")


def set_strength(level: int) -> bytes:
    # 0 = extra-fine (autofocus; needs V2_4+ firmware with the $S0* handler).
    if level not in (0, 1, 2, 3):
        raise ValueError("strength level must be 0, 1, 2, or 3")
    return line(f"$S{level}*")


def focus_nudge(direction: int, repeats: int = 1) -> bytes:
    """
    One line of '+' or '-' characters. Firmware scales pulse by line length
    (10 ms per character in mode 4). Flight: keep repeats=1 (one character per line).
    repeats>1 matches bench-style longer pulses; not used in flight.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    ch = "+" if direction == 1 else "-"
    return line(ch * repeats)


def pic_delay_step(longer: bool) -> bytes:
    return line(CMD_PIC_DELAY_LONGER if longer else CMD_PIC_DELAY_SHORTER)
