"""Tile geometry engine for fine-focus mode.

Divides the centre of the frame into tiles sized so that each tile,
displayed at a given zoom factor, fills the display area.  Tile centres
are constrained to lie within a circle whose area is a configurable
fraction of the total frame area.
"""

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Tile:
    """An axis-aligned rectangular region of the full-resolution frame."""
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def extract(self, frame: np.ndarray) -> np.ndarray:
        """Slice this tile out of *frame* (H×W or H×W×C). Returns a view."""
        return frame[self.y0:self.y1, self.x0:self.x1]


class TileGrid:
    """Compute a grid of tiles covering the centre of the frame.

    Parameters
    ----------
    frame_width, frame_height:
        Full-resolution frame dimensions (e.g. 13400 × 9528).
    display_width, display_height:
        Display area in screen pixels (e.g. 1920 × 1080).
    zoom:
        Display zoom factor.  A tile at this zoom fills the display.
        Default 1.5 (150 %).
    circle_fraction:
        The selection circle has this fraction of the total frame area.
        Default 0.2 (1/5).
    overlap:
        Fractional overlap between adjacent tiles (0 = none, 0.5 = 50 %).
        Default 0 (non-overlapping grid).
    region:
        Tile selection region. ``"circle"`` (default) lays tiles inside the
        centre circle. ``"edge"`` lays them along the *leading edge* band of the
        frame (the side facing flight direction), so a target picked there has
        maximum dwell time in the FOV before it exits the trailing edge.
    lead_edge:
        Which edge is the leading edge when ``region="edge"``: one of
        ``"top"``, ``"bottom"``, ``"left"``, ``"right"``.
    edge_band:
        Band depth as a fraction of the perpendicular frame dimension when
        ``region="edge"`` (e.g. 0.25 = the leading 25 % of the frame).
    """

    VALID_EDGES = ("top", "bottom", "left", "right")

    def __init__(
        self,
        frame_width: int,
        frame_height: int,
        display_width: int = 1920,
        display_height: int = 1080,
        zoom: float = 1.5,
        circle_fraction: float = 0.2,
        overlap: float = 0.0,
        region: str = "circle",
        lead_edge: str = "top",
        edge_band: float = 0.25,
    ) -> None:
        self.frame_w = frame_width
        self.frame_h = frame_height

        # Tile size in original-image pixels (even, for Bayer alignment)
        self.tile_w = int(display_width / zoom) & ~1
        self.tile_h = int(display_height / zoom) & ~1

        # Centre circle
        frame_area = frame_width * frame_height
        circle_area = frame_area * circle_fraction
        self.circle_r = math.sqrt(circle_area / math.pi)
        self.circle_cx = frame_width / 2
        self.circle_cy = frame_height / 2

        self._overlap = overlap
        self.region = region if region in ("circle", "edge") else "circle"
        self.lead_edge = lead_edge if lead_edge in self.VALID_EDGES else "top"
        self.edge_band = float(edge_band)
        self.tiles: list[Tile] = self._build_grid()

    # ------------------------------------------------------------------

    def _build_grid(self) -> list[Tile]:
        if self.region == "edge":
            return self._build_grid_edge()
        return self._build_grid_circle()

    def _build_grid_circle(self) -> list[Tile]:
        step_x = max(1, int(self.tile_w * (1 - self._overlap)))
        step_y = max(1, int(self.tile_h * (1 - self._overlap)))
        half_w = self.tile_w // 2
        half_h = self.tile_h // 2

        r2 = self.circle_r ** 2
        tiles: list[Tile] = []

        # Start the grid so that centres are symmetric about the frame centre
        first_cx = self.circle_cx - ((int(self.circle_r) // step_x) * step_x)
        first_cy = self.circle_cy - ((int(self.circle_r) // step_y) * step_y)

        cy = first_cy
        while cy <= self.circle_cy + self.circle_r:
            cx = first_cx
            while cx <= self.circle_cx + self.circle_r:
                dx = cx - self.circle_cx
                dy = cy - self.circle_cy
                if dx * dx + dy * dy <= r2:
                    x0 = (int(cx) - half_w) & ~1
                    y0 = (int(cy) - half_h) & ~1
                    x1 = x0 + self.tile_w
                    y1 = y0 + self.tile_h
                    if x0 >= 0 and y0 >= 0 and x1 <= self.frame_w and y1 <= self.frame_h:
                        tiles.append(Tile(x0, y0, x1, y1))
                cx += step_x
            cy += step_y

        return tiles

    def _build_grid_edge(self) -> list[Tile]:
        """Lay tiles across the leading-edge band of the frame.

        The band spans the full perpendicular extent of the frame and is
        ``edge_band`` deep (clamped to at least one tile). Tile origins are
        Bayer-aligned (even).
        """
        step_x = max(1, int(self.tile_w * (1 - self._overlap)))
        step_y = max(1, int(self.tile_h * (1 - self._overlap)))

        if self.lead_edge in ("top", "bottom"):
            depth = max(self.tile_h, int(round(self.frame_h * self.edge_band)))
            bx0, bx1 = 0, self.frame_w
            if self.lead_edge == "top":
                by0, by1 = 0, min(self.frame_h, depth)
            else:
                by0, by1 = max(0, self.frame_h - depth), self.frame_h
        else:  # left / right
            depth = max(self.tile_w, int(round(self.frame_w * self.edge_band)))
            by0, by1 = 0, self.frame_h
            if self.lead_edge == "left":
                bx0, bx1 = 0, min(self.frame_w, depth)
            else:
                bx0, bx1 = max(0, self.frame_w - depth), self.frame_w

        y_positions = list(range(by0, max(by0 + 1, by1 - self.tile_h + 1), step_y))
        x_positions = list(range(bx0, max(bx0 + 1, bx1 - self.tile_w + 1), step_x))
        if not y_positions:
            y_positions = [by0]
        if not x_positions:
            x_positions = [bx0]

        tiles: list[Tile] = []
        for y in y_positions:
            for x in x_positions:
                x0 = x & ~1
                y0 = y & ~1
                x1 = x0 + self.tile_w
                y1 = y0 + self.tile_h
                if x0 >= 0 and y0 >= 0 and x1 <= self.frame_w and y1 <= self.frame_h:
                    tiles.append(Tile(x0, y0, x1, y1))
        return tiles

    def reconfigure(
        self,
        display_width: int,
        display_height: int,
        zoom: float = 1.5,
    ) -> None:
        """Recalculate tile sizes if the display area changes."""
        self.tile_w = int(display_width / zoom) & ~1
        self.tile_h = int(display_height / zoom) & ~1
        self.tiles = self._build_grid()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.tiles)

    def __iter__(self):
        return iter(self.tiles)

    def __getitem__(self, idx: int) -> Tile:
        return self.tiles[idx]

    def summary(self) -> str:
        return (
            f"TileGrid: {len(self.tiles)} tiles of {self.tile_w}×{self.tile_h} px, "
            f"circle r={self.circle_r:.0f} centred at "
            f"({self.circle_cx:.0f}, {self.circle_cy:.0f})"
        )
