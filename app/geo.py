from __future__ import annotations

import math
import re

GRID_RE = re.compile(r"^[A-Ra-r]{2}[0-9]{2}(?:[A-Xa-x]{2})?(?:[0-9]{2})?$")


def locator_to_latlon(locator: str) -> tuple[float, float]:
    """Return centre latitude/longitude for 2/4/6/8-char Maidenhead locator."""
    loc = locator.strip().upper()
    if not GRID_RE.match(loc) or len(loc) not in (2, 4, 6, 8):
        raise ValueError(f"Invalid Maidenhead locator: {locator!r}")

    lon = -180.0 + (ord(loc[0]) - ord("A")) * 20.0
    lat = -90.0 + (ord(loc[1]) - ord("A")) * 10.0
    lon_size, lat_size = 20.0, 10.0

    if len(loc) >= 4:
        lon += int(loc[2]) * 2.0
        lat += int(loc[3]) * 1.0
        lon_size, lat_size = 2.0, 1.0
    if len(loc) >= 6:
        lon += (ord(loc[4]) - ord("A")) * (2.0 / 24.0)
        lat += (ord(loc[5]) - ord("A")) * (1.0 / 24.0)
        lon_size, lat_size = 2.0 / 24.0, 1.0 / 24.0
    if len(loc) >= 8:
        lon += int(loc[6]) * (2.0 / 240.0)
        lat += int(loc[7]) * (1.0 / 240.0)
        lon_size, lat_size = 2.0 / 240.0, 1.0 / 240.0

    return lat + lat_size / 2.0, lon + lon_size / 2.0


def latlon_to_locator4(lat: float, lon: float) -> str:
    lon = min(179.999999, max(-180.0, lon)) + 180.0
    lat = min(89.999999, max(-90.0, lat)) + 90.0
    field_lon = int(lon // 20)
    field_lat = int(lat // 10)
    rem_lon = lon - field_lon * 20
    rem_lat = lat - field_lat * 10
    sq_lon = int(rem_lon // 2)
    sq_lat = int(rem_lat // 1)
    return f"{chr(ord('A') + field_lon)}{chr(ord('A') + field_lat)}{sq_lon}{sq_lat}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def sector30(bearing: float) -> int:
    return int(((bearing + 15.0) % 360.0) // 30.0) * 30


def sector_label(sector: int | None) -> str:
    if sector is None:
        return "unbekannt"
    labels = {
        0: "N", 30: "NNE/NE", 60: "ENE/NE", 90: "E", 120: "ESE/SE", 150: "SSE/SE",
        180: "S", 210: "SSW/SW", 240: "WSW/SW", 270: "W", 300: "WNW/NW", 330: "NNW/NW",
    }
    return f"{labels.get(sector, '')} {sector:03d}°".strip()


def local_locator4_squares(qth_locator: str, radius_km: float) -> list[str]:
    """Return 4-char Maidenhead squares whose centres are close enough to cover radius.

    Adds 90 km margin because reports are filtered by 4-char square but weighted later
    using the precise receiver locator in the payload.
    """
    qlat, qlon = locator_to_latlon(qth_locator)
    result: list[str] = []
    threshold = radius_km + 90.0
    for a in range(18):
        for b in range(18):
            for x in range(10):
                for y in range(10):
                    loc = f"{chr(65+a)}{chr(65+b)}{x}{y}"
                    lat, lon = locator_to_latlon(loc)
                    if haversine_km(qlat, qlon, lat, lon) <= threshold:
                        result.append(loc)
    return sorted(result)
