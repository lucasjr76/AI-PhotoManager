"""Date extraction, in SPEC section 6 priority order. Pure functions only."""

import re
from contextlib import suppress
from datetime import date, datetime, timedelta

MIN_DATE = date(1990, 1, 1)

_WA_ANDROID = re.compile(r"^(?:IMG|VID|AUD|PTT|DOC|STK)-(\d{8})-WA\d+")
_WA_DESKTOP = re.compile(
    r"WhatsApp (?:Image|Video) (\d{4}-\d{2}-\d{2}) at (\d{1,2}\.\d{2}\.\d{2})(?: ?([AP]M))?"
)
_CAMERA = re.compile(r"^(?:IMG_|PXL_|Screenshot_)?(\d{8})[_-](\d{6})")
_PDF_DATE = re.compile(r"^(?:D:)?(\d{8})(\d{6})?")

Found = date | datetime


def _in_range(value: Found, today: date) -> bool:
    day = value.date() if isinstance(value, datetime) else value
    return MIN_DATE <= day <= today + timedelta(days=1)


def _strptime(text: str, fmt: str) -> datetime | None:
    try:
        return datetime.strptime(text, fmt)
    except ValueError:
        return None


def _from_name(name: str) -> tuple[Found | None, str] | None:
    """Return (parsed value or None if invalid, source) for the first matching pattern."""
    if m := _WA_ANDROID.match(name):
        parsed = _strptime(m[1], "%Y%m%d")
        return (parsed.date() if parsed else None), "whatsapp_android"
    if m := _WA_DESKTOP.search(name):
        clock = m[2]
        if m[3]:
            parsed = _strptime(f"{m[1]} {clock} {m[3]}", "%Y-%m-%d %I.%M.%S %p")
        else:
            parsed = _strptime(f"{m[1]} {clock}", "%Y-%m-%d %H.%M.%S")
        return parsed, "whatsapp_desktop"
    if m := _CAMERA.match(name):
        return _strptime(m[1] + m[2], "%Y%m%d%H%M%S"), "camera_name"
    return None


def parse_exif_datetime(value: str) -> datetime | None:
    return _strptime(value.strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")


def parse_pdf_date(value: str) -> datetime | None:
    """Parse a PDF date like ``D:20230514210509-03'00'``. Timezone is ignored (local clock)."""
    m = _PDF_DATE.match(value.strip())
    if not m:
        return None
    return _strptime(m[1] + (m[2] or "000000"), "%Y%m%d%H%M%S")


def resolve_date(
    name: str,
    *,
    today: date,
    exif: str | None = None,
    document: datetime | None = None,
    mtime: float | None = None,
) -> tuple[str | None, str | None]:
    """Return (ISO 8601 taken_at, date_source); first valid source wins."""
    candidates: list[tuple[Found | None, str]] = []
    if (from_name := _from_name(name)) is not None:
        candidates.append(from_name)
    if exif:
        candidates.append((parse_exif_datetime(exif), "exif"))
    candidates.append((document, "document"))
    if mtime is not None:
        with suppress(OverflowError, OSError, ValueError):
            candidates.append((datetime.fromtimestamp(mtime).replace(microsecond=0), "mtime"))
    for value, source in candidates:
        if value is not None and _in_range(value, today):
            return value.isoformat(), source
    return None, None
