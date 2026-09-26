"""Image loading and thumbnails. Source files are only ever opened for reading."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from aipdm.core.places import gps_from_exif

register_heif_opener()

# Pillow warns above ~89 MP and refuses above ~179 MP ("decompression bomb"). Phones now
# shoot 200 MP (16320x12240 = 200 MP), so allow up to 300 MP; beyond that stays refused.
# ponytail: a 200 MP HEIC decodes to ~600 MB of RGB per worker; downscale-on-decode if
# folders full of these exhaust RAM (JPEG draft mode works, HEIC has no equivalent).
MAX_PIXELS = 300_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

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


PREVIEW_MAX_SIDE = 2560  # in-app viewer: enough for a 4K screen


def save_preview(img: Image.Image, target: Path, max_side: int = PREVIEW_MAX_SIDE) -> None:
    """JPEG for the in-app viewer (any format Pillow/pillow-heif reads, HEIC included).

    Written to a temporary name and renamed, so a parallel request never reads half a file.
    """
    view = img.convert("RGB")
    view.thumbnail((max_side, max_side))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{id(view)}.tmp")
    view.save(tmp, "JPEG", quality=88)
    tmp.replace(target)


# EXIF tags shown in the viewer (base IFD and Exif sub-IFD).
MAKE, MODEL, SOFTWARE = 0x010F, 0x0110, 0x0131
EXPOSURE, F_NUMBER, ISO, FLASH = 0x829A, 0x829D, 0x8827, 0x9209
FOCAL, FOCAL_35MM, LENS = 0x920A, 0xA405, 0xA434


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if number == number and number > 0 else None  # drop NaN (0/0) and 0


def describe_exif(base: dict[int, object], exif: dict[int, object]) -> list[tuple[str, str]]:
    """Human-readable (label, value) pairs, pt-BR, only for fields that are present."""
    out: list[tuple[str, str]] = []
    camera = " ".join(
        str(v).strip("\x00 ")
        for v in (base.get(MAKE), base.get(MODEL))
        if v and str(v).strip("\x00 ")
    )
    if camera:
        out.append(("Câmera", camera))
    if lens := str(exif.get(LENS) or "").strip("\x00 "):
        out.append(("Lente", lens))
    if (t := _number(exif.get(EXPOSURE))) is not None:
        out.append(("Exposição", f"1/{round(1 / t)} s" if t < 1 else f"{t:g} s"))
    if (f := _number(exif.get(F_NUMBER))) is not None:
        out.append(("Abertura", f"f/{f:.1f}"))
    iso = exif.get(ISO)
    if isinstance(iso, tuple | list):
        iso = iso[0] if iso else None
    if (i := _number(iso)) is not None:
        out.append(("ISO", f"{i:.0f}"))
    if (mm := _number(exif.get(FOCAL))) is not None:
        eq = _number(exif.get(FOCAL_35MM))
        out.append(("Distância focal", f"{mm:.1f} mm" + (f" ({eq:.0f} mm equiv.)" if eq else "")))
    flash = exif.get(FLASH)
    if isinstance(flash, int):
        out.append(("Flash", "disparado" if flash & 1 else "não disparado"))
    if software := str(base.get(SOFTWARE) or "").strip("\x00 "):
        out.append(("Software", software))
    return out


def read_exif_summary(path: Path) -> tuple[str | None, list[tuple[str, str]]]:
    """(format, EXIF pairs) from the header only: no pixel decoding."""
    with path.open("rb") as fh, Image.open(fh) as img:
        base = img.getexif()
        try:
            details = dict(base.get_ifd(EXIF_IFD))
        except Exception:
            details = {}
        return img.format, describe_exif(dict(base), details)
