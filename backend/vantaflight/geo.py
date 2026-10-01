"""Local metres <-> latitude/longitude.

VantaFlight works in local ENU metres relative to home (``x`` east, ``y``
north). Anything that speaks GPS (PX4, QGroundControl plan files) converts at
the boundary with these functions: a flat-earth approximation around home,
accurate to centimetres over the few hundred metres a geofenced flight covers.
"""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_378_137.0


def local_to_global(
    x: float, y: float, home_lat: float, home_lon: float
) -> tuple[float, float]:
    """Metres east/north of home -> (latitude, longitude) in degrees."""
    lat = home_lat + math.degrees(y / EARTH_RADIUS_M)
    lon = home_lon + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(home_lat))))
    return lat, lon


def global_to_local(
    lat: float, lon: float, home_lat: float, home_lon: float
) -> tuple[float, float]:
    """(latitude, longitude) in degrees -> metres (east, north) of home."""
    y = math.radians(lat - home_lat) * EARTH_RADIUS_M
    x = math.radians(lon - home_lon) * EARTH_RADIUS_M * math.cos(math.radians(home_lat))
    return x, y
