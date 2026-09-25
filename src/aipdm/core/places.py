"""Location: GPS from EXIF and offline reverse geocoding (GeoNames cities500, CC-BY 4.0).

The gazetteer is a gzip TSV (name, state, country, lat, lon) built by
tools/fetch_models.py; nearest city is found with unit-vector dot products in numpy.
"""

import gzip
import logging
import math
import sqlite3
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)

PLACES_FILE = "places.tsv.gz"
MAX_DISTANCE_KM = 60.0  # farther than this from any town: no place (sea, wilderness)
EARTH_RADIUS_KM = 6371.0
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4


@dataclass(frozen=True)
class Place:
    city: str
    state: str
    country: str

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.city, self.state, self.country) if p)


def _degrees(value: object) -> float:
    """EXIF GPS angle: (deg, min, sec) rationals, or a single decimal value."""
    if isinstance(value, tuple | list):
        parts = [float(v) for v in value] + [0.0, 0.0]
        return parts[0] + parts[1] / 60 + parts[2] / 3600
    return float(value)  # type: ignore[arg-type]


def gps_from_exif(ifd: dict[int, object]) -> tuple[float, float] | None:
    """(lat, lon) in decimal degrees from an EXIF GPS IFD, or None if absent/invalid."""
    try:
        lat = _degrees(ifd[GPS_LAT])
        lon = _degrees(ifd[GPS_LON])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if str(ifd.get(GPS_LAT_REF, "N")).upper().startswith("S"):
        lat = -lat
    if str(ifd.get(GPS_LON_REF, "E")).upper().startswith("W"):
        lon = -lon
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None
    return lat, lon


def _unit(latlon: NDArray[np.float64]) -> NDArray[np.float64]:
    lat, lon = np.radians(latlon[:, 0]), np.radians(latlon[:, 1])
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1)


class Gazetteer:
    def __init__(self, places: list[Place], latlon: NDArray[np.float64]) -> None:
        self.places = places
        self.points = _unit(latlon)
        self.min_cos = math.cos(MAX_DISTANCE_KM / EARTH_RADIUS_KM)

    @classmethod
    def load(cls, path: Path) -> "Gazetteer":
        places: list[Place] = []
        coords: list[tuple[float, float]] = []
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                name, state, country, lat, lon = line.rstrip("\n").split("\t")
                places.append(Place(name, state, country))
                coords.append((float(lat), float(lon)))
        return cls(places, np.array(coords, dtype=np.float64))

    def nearest(self, latlon: NDArray[np.float64], block: int = 64) -> list[Place | None]:
        """Nearest town for each (lat, lon) row; None when farther than MAX_DISTANCE_KM."""
        found: list[Place | None] = []
        queries = _unit(np.asarray(latlon, dtype=np.float64).reshape(-1, 2))
        for start in range(0, len(queries), block):
            cos = queries[start : start + block] @ self.points.T  # block x towns
            best = cos.argmax(axis=1)
            for row, index in enumerate(best):
                ok = cos[row, index] >= self.min_cos
                found.append(self.places[index] if ok else None)
        return found


PLACE_PAGE = 0  # texts row holding "city, state, country", so text search finds places


@cache
def load_gazetteer(path: Path) -> Gazetteer:
    return Gazetteer.load(path)


def resolve_places(conn: sqlite3.Connection, gazetteer_path: Path) -> int:
    """Name the place of every file with GPS but no place yet. Returns files resolved.

    Unresolvable points (far from any town) get empty strings, so they are not retried.
    """
    rows = conn.execute(
        "SELECT id, lat, lon FROM files WHERE lat IS NOT NULL AND city IS NULL"
    ).fetchall()
    if not rows:
        return 0
    if not gazetteer_path.exists():
        log.warning("base de lugares ausente (%s); rode tools/fetch_models.py", gazetteer_path)
        return 0
    found = load_gazetteer(gazetteer_path).nearest(np.array([(r[1], r[2]) for r in rows]))
    for (file_id, _, _), place in zip(rows, found, strict=True):
        p = place or Place("", "", "")
        conn.execute(
            "UPDATE files SET city = ?, state = ?, country = ? WHERE id = ?",
            (p.city, p.state, p.country, file_id),
        )
        if place:
            conn.execute(
                "INSERT INTO texts (content, file_id, page) VALUES (?, ?, ?)",
                (place.label, file_id, PLACE_PAGE),
            )
    conn.commit()
    return len(rows)
