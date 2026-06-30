"""Geographic fix data shared between the GNSS receiver, correlator, and TIFF
writer.

A separate module so we don't drag pyserial / pynmea2 imports into the writer
and don't drag tifffile imports into the receiver.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass


@dataclass(frozen=True)
class GpsFix:
    """One GNSS fix, derived from a GGA + (optionally) the matching RMC.

    All fields are optional so we can also represent partial fixes (e.g. when
    only RMC has arrived and we don't yet know altitude, or when we've only
    got GGA and don't yet have the date).
    """

    latitude_deg: float | None = None  # +N, -S
    longitude_deg: float | None = None  # +E, -W
    altitude_m: float | None = None  # WGS-84 ellipsoid height (or geoid; GGA-defined)
    timestamp_utc: _dt.datetime | None = None  # combined date + time, UTC
    satellites: int | None = None  # number of SVs used
    hdop: float | None = None  # horizontal dilution of precision
    fix_quality: int | None = None  # GGA quality indicator (0=invalid, 1=GPS, 2=DGPS, ...)

    @property
    def has_position(self) -> bool:
        return self.latitude_deg is not None and self.longitude_deg is not None

    @property
    def is_valid(self) -> bool:
        """A position fix that downstream tools can trust.

        ``fix_quality == 0`` from a GGA explicitly means *no* fix; we also
        require both lat and lon to be present.
        """
        if not self.has_position:
            return False
        if self.fix_quality is not None and self.fix_quality == 0:
            return False
        return True

    def merged_with(self, other: "GpsFix") -> "GpsFix":
        """Return a new fix that fills missing fields from *other*.

        Used when a GGA arrived with no date (NMEA GGA does not carry the
        date) and the matching RMC arrives shortly after with the date+time;
        we keep our fields and only fill the gaps from the second sentence.
        """
        return GpsFix(
            latitude_deg=self.latitude_deg if self.latitude_deg is not None else other.latitude_deg,
            longitude_deg=self.longitude_deg if self.longitude_deg is not None else other.longitude_deg,
            altitude_m=self.altitude_m if self.altitude_m is not None else other.altitude_m,
            timestamp_utc=self.timestamp_utc if self.timestamp_utc is not None else other.timestamp_utc,
            satellites=self.satellites if self.satellites is not None else other.satellites,
            hdop=self.hdop if self.hdop is not None else other.hdop,
            fix_quality=self.fix_quality if self.fix_quality is not None else other.fix_quality,
        )

    def to_dict(self) -> dict:
        """Plain-Python representation for embedding in TIFF ImageDescription."""
        return {
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "altitude_m": self.altitude_m,
            "timestamp_utc": (
                self.timestamp_utc.isoformat(timespec="milliseconds")
                if self.timestamp_utc is not None
                else None
            ),
            "satellites": self.satellites,
            "hdop": self.hdop,
            "fix_quality": self.fix_quality,
        }
