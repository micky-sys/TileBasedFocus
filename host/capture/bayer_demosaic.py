"""Pick an OpenCV Bayer→RGB demosaic code from capture metadata.

GenICam ``BayerRG`` naming does not always match OpenCV's ``COLOR_BayerRG*``
constants for a given vendor buffer layout. On this rig (KAYA + Iron 661),
``PixelFormat=BayerRG14`` buffers align with OpenCV's **BG** phase for hue —
using ``COLOR_BayerRG2RGB`` swaps red/blue.

Environment overrides (optional):

* ``BAY14_DEBAYER_OPENCV`` — e.g. ``BayerRG2RGB`` (must exist as ``cv2.COLOR_*``).
* ``BAY14_DEBAYER_STRICT_PFNC`` — if ``1`` / ``true``, map GenICam BayerRG →
  ``COLOR_BayerRG2RGB`` (for synthetic / bench frames).
"""

from __future__ import annotations

import os
from typing import Any

import cv2

_FALSE = frozenset({"0", "", "false", "no", "off"})


def opencv_bayer_rgb_demosaic_code(meta: dict[str, Any]) -> int:
    """Return ``cv2.COLOR_Bayer*2RGB`` for this frame's embedded metadata."""
    name = os.environ.get("BAY14_DEBAYER_OPENCV", "").strip()
    if name:
        attr = f"COLOR_{name}" if not name.startswith("COLOR_") else name
        try:
            return int(getattr(cv2, attr))
        except AttributeError as exc:
            raise ValueError(f"unknown OpenCV demosaic {attr!r}") from exc

    strict = (
        os.environ.get("BAY14_DEBAYER_STRICT_PFNC", "0").strip().lower()
        not in _FALSE
    )

    gf = str(meta.get("genicam_pixel_format", "")).upper()
    bp = str(meta.get("bayer_pattern", "RG")).strip().upper()

    def from_genicam(s: str) -> int | None:
        if not s:
            return None
        if "BAYERBG" in s:
            return cv2.COLOR_BayerBG2RGB
        if "BAYERGB" in s:
            return cv2.COLOR_BayerGB2RGB
        if "BAYERGR" in s:
            return cv2.COLOR_BayerGR2RGB
        if "BAYERRG" in s:
            if strict:
                return cv2.COLOR_BayerRG2RGB
            # KAYA / Iron 661: RG GenICam label → OpenCV BG phase (natural hues).
            return cv2.COLOR_BayerBG2RGB
        return None

    code = from_genicam(gf)
    if code is not None:
        return code

    # Legacy headers (no genicam_pixel_format): bayer_pattern was forced "RG".
    letter = bp[:2] if len(bp) >= 2 else "RG"
    if letter == "BG":
        return cv2.COLOR_BayerBG2RGB
    if letter == "GB":
        return cv2.COLOR_BayerGB2RGB
    if letter == "GR":
        return cv2.COLOR_BayerGR2RGB
    # "RG" from older writers → same KAYA convention as GenICam BayerRG above.
    if letter == "RG" and not strict:
        return cv2.COLOR_BayerBG2RGB
    return cv2.COLOR_BayerRG2RGB


_OPENCV_BAYER_RGB_LABELS: dict[int, str] = {
    cv2.COLOR_BayerRG2RGB: "BayerRG2RGB",
    cv2.COLOR_BayerBG2RGB: "BayerBG2RGB",
    cv2.COLOR_BayerGR2RGB: "BayerGR2RGB",
    cv2.COLOR_BayerGB2RGB: "BayerGB2RGB",
}


def opencv_bayer_rgb_demosaic_label(meta: dict[str, Any]) -> str:
    """``cv2.COLOR_*`` name from :func:`opencv_bayer_rgb_demosaic_code`."""
    code = int(opencv_bayer_rgb_demosaic_code(meta))
    return _OPENCV_BAYER_RGB_LABELS.get(code, str(code))
