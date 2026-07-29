"""Self-check for FineFocusTracker (leading-edge tile lock + follow)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))

from capture.autofocus import FineFocusTracker  # noqa: E402


def _textured(h: int, w: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 4096, (h, w), dtype=np.uint16)
    stripes = ((np.arange(w) // 8) % 2).astype(np.uint16) * 800
    return np.clip(base + stripes[None, :], 0, 16383).astype(np.uint16)


def main() -> None:
    h, w = 240, 320
    tr = FineFocusTracker()
    f0 = _textured(h, w, 1)
    roi0 = tr.update(f0)
    assert roi0 is not None, "first lock failed"

    f = f0
    rois = [roi0]
    for _ in range(8):
        f = np.roll(f, shift=2, axis=1)
        roi = tr.update(f)
        assert roi is not None, "track lost"
        rois.append(roi)

    # Tile should move with content (same size, not stuck at origin forever).
    assert any(r != rois[0] for r in rois[1:]), "ROI never moved across frames"

    print("FineFocusTracker self-check OK")


if __name__ == "__main__":
    main()
