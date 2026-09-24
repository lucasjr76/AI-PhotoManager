"""Image metadata and thumbnails. Source files are only ever opened for reading."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

THUMB_SIZE = 256
EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 0x9003


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    exif_datetime: str | None


def read_image(path: Path, thumb_path: Path) -> ImageInfo:
    with path.open("rb") as fh, Image.open(fh) as img:
        width, height = img.size
        raw = img.getexif().get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL)
        save_thumbnail(ImageOps.exif_transpose(img), thumb_path)
    return ImageInfo(width, height, raw if isinstance(raw, str) else None)


def save_thumbnail(img: Image.Image, thumb_path: Path) -> None:
    thumb = img.convert("RGB")
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb.save(thumb_path, "JPEG", quality=80)
