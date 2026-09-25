import gzip
from pathlib import Path

import numpy as np
import pytest

from aipdm.core.places import Gazetteer, Place, gps_from_exif

N, S, E, W = "N", "S", "E", "W"


def dms(value: float) -> tuple[float, float, float]:
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = (value - d - m / 60) * 3600
    return (float(d), float(m), round(s, 4))


def gps_ifd(lat: float, lon: float) -> dict[int, object]:
    return {1: S if lat < 0 else N, 2: dms(lat), 3: W if lon < 0 else E, 4: dms(lon)}


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(-27.5954, -48.548), (48.8566, 2.3522), (-33.8688, 151.2093), (40.7128, -74.006)],
)
def test_gps_all_hemispheres(lat: float, lon: float) -> None:
    got = gps_from_exif(gps_ifd(lat, lon))
    assert got is not None
    assert got == pytest.approx((lat, lon), abs=1e-4)


@pytest.mark.parametrize(
    "ifd",
    [
        {},
        {1: "N", 2: (10.0, 0.0, 0.0)},  # no longitude
        {1: "N", 2: (0.0, 0.0, 0.0), 3: "E", 4: (0.0, 0.0, 0.0)},  # (0,0): unset GPS
        {1: "N", 2: (95.0, 0.0, 0.0), 3: "E", 4: (10.0, 0.0, 0.0)},  # latitude > 90
        {1: "N", 2: (float("nan"), 0.0, 0.0), 3: "E", 4: (10.0, 0.0, 0.0)},  # 0/0 rational
        {1: "N", 2: "lixo", 3: "E", 4: (10.0, 0.0, 0.0)},
    ],
)
def test_gps_invalid(ifd: dict[int, object]) -> None:
    assert gps_from_exif(ifd) is None


def test_gps_decimal_degrees_single_value() -> None:
    assert gps_from_exif({1: "S", 2: 27.5, 3: "W", 4: 48.5}) == (-27.5, -48.5)


@pytest.fixture
def gazetteer(tmp_path: Path) -> Gazetteer:
    rows = [
        ("Florianópolis", "Santa Catarina", "Brazil", -27.5954, -48.548),
        ("São José", "Santa Catarina", "Brazil", -27.6136, -48.6366),
        ("Joinville", "Santa Catarina", "Brazil", -26.3045, -48.8487),
        ("Paris", "Île-de-France", "France", 48.8566, 2.3522),
    ]
    path = tmp_path / "places.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write("\t".join(map(str, row)) + "\n")
    return Gazetteer.load(path)


def test_nearest_place(gazetteer: Gazetteer) -> None:
    got = gazetteer.nearest(np.array([[-27.59, -48.55], [-26.3, -48.84], [48.85, 2.35]]))
    assert got == [
        Place("Florianópolis", "Santa Catarina", "Brazil"),
        Place("Joinville", "Santa Catarina", "Brazil"),
        Place("Paris", "Île-de-France", "France"),
    ]
    assert got[0].label == "Florianópolis, Santa Catarina, Brazil"


def test_far_from_everything_is_none(gazetteer: Gazetteer) -> None:
    assert gazetteer.nearest(np.array([[-40.0, -20.0]])) == [None]  # mid Atlantic


def test_blocks_do_not_change_result(gazetteer: Gazetteer) -> None:
    queries = np.random.default_rng(0).uniform([-30, -50], [-25, -45], size=(300, 2))
    assert gazetteer.nearest(queries, block=7) == gazetteer.nearest(queries, block=1000)
