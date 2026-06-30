"""Sharpness analysis for tile-based focus.

Scores tiles by Laplacian variance on a 4× subsampled version
of each tile (~6 ms for 27 tiles).  Returns the index and score
of the best tile, plus all scores for optional UI overlay.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from tile_geometry import Tile, TileGrid


@dataclass
class SharpnessResult:
    """Outcome of analysing one frame's tiles."""
    best_index: int
    best_score: float
    scores: list[float]
    best_tile_full: np.ndarray  # full-res crop of the winning tile (uint8)


def score_tile_subsampled(
    frame: np.ndarray,
    tile: Tile,
    subsample: int = 4,
) -> float:
    """Compute Laplacian-variance sharpness on a subsampled tile.

    *frame* can be uint16 (raw Bayer) or uint8.  The tile region is
    subsampled by *subsample* in both axes before the Laplacian.
    """
    region = frame[tile.y0:tile.y1:subsample, tile.x0:tile.x1:subsample]
    if region.dtype == np.uint16:
        region = (region >> 6).astype(np.uint8)
    # Subtract a heavy blur to remove large-scale illumination gradients
    # (e.g. bright sky vs dark ground) and keep only local texture/edges.
    rf = region.astype(np.float32)
    low = cv2.GaussianBlur(rf, (0, 0), 7)
    return float(np.std(rf - low))


def find_best_tile(
    frame: np.ndarray,
    grid: TileGrid,
    subsample: int = 4,
) -> SharpnessResult:
    """Score every tile in *grid* and return the sharpest one.

    Parameters
    ----------
    frame : np.ndarray
        Full-resolution frame, uint16 (raw Bayer14) or uint8.
    grid : TileGrid
        Pre-computed tile layout.
    subsample : int
        Downsample factor for the sharpness pass (default 4).

    Returns
    -------
    SharpnessResult with the best tile index, all scores, and
    the full-resolution uint8 crop of the winning tile.
    """
    scores: list[float] = []
    for tile in grid:
        scores.append(score_tile_subsampled(frame, tile, subsample))

    best_idx = int(np.argmax(scores))
    best_tile = grid[best_idx]

    # Extract the winning tile at full resolution
    crop = frame[best_tile.y0:best_tile.y1, best_tile.x0:best_tile.x1]
    if crop.dtype == np.uint16:
        crop = (crop >> 6).astype(np.uint8)
    else:
        crop = crop.copy()

    return SharpnessResult(
        best_index=best_idx,
        best_score=scores[best_idx],
        scores=scores,
        best_tile_full=crop,
    )
