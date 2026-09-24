from datetime import date, datetime

import pytest

from aipdm.core.dates import parse_exif_datetime, parse_pdf_date, resolve_date

TODAY = date(2026, 9, 24)


def resolve(name: str, **kw: object) -> tuple[str | None, str | None]:
    return resolve_date(name, today=TODAY, **kw)  # type: ignore[arg-type]


# --- 1. WhatsApp Android --------------------------------------------------


@pytest.mark.parametrize("prefix", ["IMG", "VID", "AUD", "PTT", "DOC", "STK"])
def test_whatsapp_android_all_prefixes(prefix: str) -> None:
    assert resolve(f"{prefix}-20230514-WA0007.jpg") == ("2023-05-14", "whatsapp_android")


def test_whatsapp_android_requires_wa_suffix() -> None:
    assert resolve("IMG-20230514-XX0007.jpg") == (None, None)


# --- 2. WhatsApp desktop export -------------------------------------------


def test_whatsapp_desktop_24h() -> None:
    assert resolve("WhatsApp Image 2023-05-14 at 21.05.09.jpeg") == (
        "2023-05-14T21:05:09",
        "whatsapp_desktop",
    )


def test_whatsapp_desktop_video_single_digit_hour() -> None:
    assert resolve("WhatsApp Video 2023-05-14 at 9.05.09.mp4") == (
        "2023-05-14T09:05:09",
        "whatsapp_desktop",
    )


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("9.05.09 PM", "21:05:09"),
        ("12.00.00 PM", "12:00:00"),
        ("12.30.00 AM", "00:30:00"),
        ("9.05.09 AM", "09:05:09"),
    ],
)
def test_whatsapp_desktop_am_pm(clock: str, expected: str) -> None:
    assert resolve(f"WhatsApp Image 2023-05-14 at {clock}.jpeg") == (
        f"2023-05-14T{expected}",
        "whatsapp_desktop",
    )


def test_whatsapp_desktop_duplicate_suffix() -> None:
    assert resolve("WhatsApp Image 2023-05-14 at 21.05.09 (1).jpeg")[0] == "2023-05-14T21:05:09"


# --- 3. Camera names ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("IMG_20230514_210509.jpg", "2023-05-14T21:05:09"),
        ("PXL_20230514_210509123.jpg", "2023-05-14T21:05:09"),
        ("20230514_210509.jpg", "2023-05-14T21:05:09"),
        ("20230514_210509(0).jpg", "2023-05-14T21:05:09"),
        ("Screenshot_20230514-210509.png", "2023-05-14T21:05:09"),
        ("Screenshot_20230514-210509_WhatsApp.jpg", "2023-05-14T21:05:09"),
    ],
)
def test_camera_patterns(name: str, expected: str) -> None:
    assert resolve(name) == (expected, "camera_name")


# --- 4/5/6. Metadata fallbacks --------------------------------------------


def test_exif_used_when_name_has_no_date() -> None:
    assert resolve("foto.jpg", exif="2021:12:25 10:11:12") == ("2021-12-25T10:11:12", "exif")


def test_document_date_used_after_exif() -> None:
    doc = datetime(2020, 1, 2, 3, 4, 5)
    assert resolve("contrato.pdf", document=doc) == ("2020-01-02T03:04:05", "document")


def test_mtime_is_last_resort() -> None:
    mtime = datetime(2022, 6, 1, 8, 0, 0).timestamp()
    assert resolve("x.jpg", mtime=mtime) == ("2022-06-01T08:00:00", "mtime")


def test_no_source_at_all() -> None:
    assert resolve("x.jpg") == (None, None)


# --- Priority -------------------------------------------------------------


def test_name_wins_over_exif_and_mtime() -> None:
    got = resolve(
        "IMG-20230514-WA0001.jpg",
        exif="2021:12:25 10:11:12",
        document=datetime(2020, 1, 1),
        mtime=datetime(2024, 1, 1).timestamp(),
    )
    assert got == ("2023-05-14", "whatsapp_android")


def test_exif_wins_over_document_and_mtime() -> None:
    got = resolve(
        "x.jpg",
        exif="2021:12:25 10:11:12",
        document=datetime(2020, 1, 1),
        mtime=datetime(2024, 1, 1).timestamp(),
    )
    assert got[1] == "exif"


# --- Invalid dates fall through to the next source ------------------------


@pytest.mark.parametrize(
    "name",
    [
        "IMG-20231345-WA0001.jpg",  # month 13
        "IMG-20230230-WA0001.jpg",  # Feb 30
        "IMG-19891231-WA0001.jpg",  # before 1990
        "IMG-20260926-WA0001.jpg",  # after today + 1 day
        "WhatsApp Image 2023-05-14 at 25.05.09.jpeg",  # hour 25
        "IMG_20230514_246000.jpg",  # invalid time
        "Screenshot_20230532-100000.png",  # day 32
    ],
)
def test_invalid_name_date_falls_to_exif(name: str) -> None:
    assert resolve(name, exif="2021:12:25 10:11:12") == ("2021-12-25T10:11:12", "exif")


def test_today_plus_one_day_is_accepted() -> None:
    assert resolve("IMG-20260925-WA0001.jpg") == ("2026-09-25", "whatsapp_android")


def test_first_day_of_1990_is_accepted() -> None:
    assert resolve("IMG-19900101-WA0001.jpg") == ("1990-01-01", "whatsapp_android")


def test_invalid_exif_falls_to_document() -> None:
    doc = datetime(2020, 1, 2)
    assert resolve("x.jpg", exif="0000:00:00 00:00:00", document=doc)[1] == "document"


def test_out_of_range_document_falls_to_mtime() -> None:
    mtime = datetime(2022, 6, 1).timestamp()
    got = resolve("x.pdf", document=datetime(1980, 1, 1), mtime=mtime)
    assert got[1] == "mtime"


def test_out_of_range_mtime_gives_none() -> None:
    assert resolve("x.jpg", mtime=0.0) == (None, None)


# --- Parsers --------------------------------------------------------------


def test_parse_exif_datetime() -> None:
    assert parse_exif_datetime("2021:12:25 10:11:12") == datetime(2021, 12, 25, 10, 11, 12)
    assert parse_exif_datetime("2021:12:25 10:11:12\x00") == datetime(2021, 12, 25, 10, 11, 12)
    assert parse_exif_datetime("    :  :     :  :  ") is None
    assert parse_exif_datetime("garbage") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("D:20230514210509-03'00'", datetime(2023, 5, 14, 21, 5, 9)),
        ("D:20230514210509Z", datetime(2023, 5, 14, 21, 5, 9)),
        ("D:20230514", datetime(2023, 5, 14)),
        ("20230514210509", datetime(2023, 5, 14, 21, 5, 9)),
    ],
)
def test_parse_pdf_date(raw: str, expected: datetime) -> None:
    assert parse_pdf_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "D:", "D:2023", "D:20231399", "lixo"])
def test_parse_pdf_date_invalid(raw: str) -> None:
    assert parse_pdf_date(raw) is None
