"""Image loading and thumbnails. Source files are only ever opened for reading."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from aipdm.core.places import gps_from_exif

register_heif_opener()

THUMB_SIZE = 256
WORK_MAX_SIDE = 1600  # faces and OCR run on this size (SPEC section 8)
EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 0x9003
GPS_IFD = 0x8825


@dataclass(frozen=True)
class LoadedImage:
    image: Image.Image  # RGB, EXIF orientation applied; "original" coordinates
    exif_datetime: str | None
    gps: tuple[float, float] | None = None


def _gps(img: Image.Image) -> tuple[float, float] | None:
    try:
        return gps_from_exif(dict(img.getexif().get_ifd(GPS_IFD)))
    except Exception:  # malformed EXIF must not fail the file
        return None


def load_image(path: Path) -> LoadedImage:
    with path.open("rb") as fh, Image.open(fh) as img:
        raw = img.getexif().get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL)
        gps = _gps(img)
        oriented = ImageOps.exif_transpose(img).convert("RGB")
    return LoadedImage(oriented, raw if isinstance(raw, str) else None, gps)


def read_gps(path: Path) -> tuple[float, float] | None:
    """GPS only, from the header: no pixel decoding (cheap upgrade of indexed folders)."""
    with path.open("rb") as fh, Image.open(fh) as img:
        return _gps(img)


def working_copy(img: Image.Image) -> tuple[Image.Image, float]:
    """Downscale to WORK_MAX_SIDE; returns (image, original/working scale)."""
    longest = max(img.size)
    if longest <= WORK_MAX_SIDE:
        return img, 1.0
    work = img.copy()
    work.thumbnail((WORK_MAX_SIDE, WORK_MAX_SIDE))
    return work, longest / max(work.size)


def save_thumbnail(img: Image.Image, thumb_path: Path) -> None:
    thumb = img.convert("RGB")
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb.save(thumb_path, "JPEG", quality=80)
