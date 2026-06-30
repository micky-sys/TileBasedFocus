"""Per-camera frame-in-sweep counter (1..13 with rollover) + sweep number.

The user chose "count only" for sweep boundary detection: we trust the
hardware to fire exactly 13 triggers per sweep and roll the frame
counter over after 13. A manual reset (exposed here and wired to
``Line up`` / ``Line down`` buttons in the GUI) guards against drift
if the user ever sees a mis-count.

The sweep number ``S`` (1-indexed) advances every time the frame
counter rolls 13 -> 1, so it counts how many sweeps have completed
*at the start of* this frame for the current line. ``reset()`` puts
both back to ``S1, F1`` so a Line up / Line down click also restarts
sweep numbering for the new line -- which is exactly what the
filename scheme ``S{i}_{j}`` wants.
"""

from __future__ import annotations

import threading

SWEEP_SIZE = 13


class SweepCounter:
    """Thread-safe (sweep, frame) counter with explicit reset.

    ``next_position()`` returns the ``(sweep, frame)`` pair to assign
    to the frame being processed, then advances internal state. The
    frame counter rolls 1..N, and the sweep counter increments once
    per rollover.

    ``next_index()`` is kept for source compatibility with older code
    that only wanted the frame index; it returns the frame component
    of ``next_position()`` and is otherwise identical.
    """

    def __init__(self, sweep_size: int = SWEEP_SIZE) -> None:
        if sweep_size < 1:
            raise ValueError("sweep_size must be >= 1")
        self._sweep_size = sweep_size
        self._lock = threading.Lock()
        self._next_frame = 1
        self._next_sweep = 1

    def next_position(self) -> tuple[int, int]:
        """Return ``(sweep, frame)`` for the next frame and advance state.

        ``frame`` is 1..sweep_size; ``sweep`` is 1.. and bumps every
        time ``frame`` rolls back to 1.
        """
        with self._lock:
            sweep = self._next_sweep
            frame = self._next_frame
            self._next_frame = frame + 1
            if self._next_frame > self._sweep_size:
                self._next_frame = 1
                self._next_sweep = sweep + 1
            return sweep, frame

    def next_index(self) -> int:
        """Backwards-compatible accessor returning just the frame."""
        return self.next_position()[1]

    def peek(self) -> int:
        """Return the frame index that ``next_position`` would assign next."""
        with self._lock:
            return self._next_frame

    def peek_position(self) -> tuple[int, int]:
        """Return the ``(sweep, frame)`` ``next_position`` would assign next."""
        with self._lock:
            return self._next_sweep, self._next_frame

    def reset(self) -> None:
        """Force the next position back to ``(S1, F1)`` (e.g. on line change)."""
        with self._lock:
            self._next_frame = 1
            self._next_sweep = 1
