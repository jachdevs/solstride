"""Solar position (NOAA general solar position approximation, ~0.5 deg accuracy)."""

from __future__ import annotations

import math
from datetime import datetime, timezone


def sun_position(lat: float, lng: float, when: datetime | None = None) -> tuple[float, float]:
    """Return (altitude_deg, azimuth_deg) for a location and UTC time.

    Azimuth is measured clockwise from true north (90 = east, 180 = south).
    """
    when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600

    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )

    true_solar_min = hour * 60 + eqtime + 4 * lng
    ha = math.radians(true_solar_min / 4 - 180)
    phi = math.radians(lat)

    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    altitude = 90 - math.degrees(zen)

    az_from_south = math.atan2(
        math.sin(ha), math.cos(ha) * math.sin(phi) - math.tan(decl) * math.cos(phi)
    )
    azimuth = (math.degrees(az_from_south) + 180) % 360
    return altitude, azimuth
