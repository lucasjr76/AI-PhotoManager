"""In-app viewer: previews of any indexed photo format and PDF pages, served by id."""

from pathlib import Path

import pytest
from conftest import make_docx, make_pdf
from fastapi.testclient import TestClient
from PIL import Image
from test_server import HOST, TOKEN, wait_index

from aipdm.core.images import PREVIEW_MAX_SIDE, load_image, save_preview
from aipdm.server import app as server_app
from aipdm.server.app import create_app

ORIENTATION = 0x0112


def heic(path: Path, size: tuple[int, int], orientation: int = 1) -> None:
    exif = Image.Exif()
    exif[ORIENTATION] = orientation
    Image.new("RGB", size, "orange").save(path, format="HEIF", exif=exif.tobytes())


def test_preview_heic_rotated_and_downscaled(tmp_path: Path) -> None:
    src = tmp_path / "foto.heic"
    heic(src, (4000, 1000), orientation=6)  # stored landscape, displayed portrait
    target = tmp_path / "preview.jpg"
    save_preview(load_image(src).image, target)
    with Image.open(target) as out:
        assert out.format == "JPEG"
        assert out.size == (PREVIEW_MAX_SIDE // 4, PREVIEW_MAX_SIDE)  # rotated, fits 2560


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    root = tmp_path / "pasta"
    root.mkdir()
    heic(root / "20260527_151731.heic", (800, 600))
    Image.new("RGB", (300, 200), "blue").save(root / "foto.webp")
    make_pdf(root / "doc.pdf", ["Página um", "Página dois"])
    make_docx(root / "relatorio.docx")
    app = create_app(None, TOKEN, HOST, index_only=frozenset(), workers=1, monitor_interval=None)
    c = TestClient(app, base_url=f"http://{HOST}")
    c.get(f"/?t={TOKEN}", follow_redirects=False)
    c.post("/api/folders/open", json={"path": str(root)})
    wait_index(c)
    return c


def ids(c: TestClient) -> dict[str, int]:
    hits = c.get("/api/search", params={"limit": 50}).json()["hits"]
    return {h["rel_path"]: h["file_id"] for h in hits}


def test_view_photos_and_pdf_pages(client: TestClient) -> None:
    files = ids(client)
    for name in ("20260527_151731.heic", "foto.webp"):
        got = client.get(f"/api/files/{files[name]}/view")
        assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
        assert got.content[:2] == b"\xff\xd8"
    pdf = files["doc.pdf"]
    assert client.get(f"/api/files/{pdf}/view", params={"page": 2}).status_code == 200
    assert client.get(f"/api/files/{pdf}/view", params={"page": 3}).status_code == 404
    assert client.get(f"/api/files/{files['relatorio.docx']}/view").status_code == 404


def test_preview_is_cached(client: TestClient, tmp_path: Path) -> None:
    file_id = ids(client)["20260527_151731.heic"]
    client.get(f"/api/files/{file_id}/view")
    [cached] = list((tmp_path / "dados").glob("*.previews/*.jpg"))
    stamp = cached.stat().st_mtime_ns
    client.get(f"/api/files/{file_id}/view")
    assert cached.stat().st_mtime_ns == stamp  # served from cache, not re-rendered


def test_info(client: TestClient) -> None:
    files = ids(client)
    photo = client.get(f"/api/files/{files['20260527_151731.heic']}/info").json()
    assert photo["kind"] == "image" and photo["width"] == 800 and photo["people"] == []
    assert photo["taken_at"] == "2026-05-27T15:17:31"
    assert photo["format"] == "HEIF" and photo["date_source"] == "camera_name"
    assert client.get(f"/api/files/{files['doc.pdf']}/info").json()["pages"] == 2
    docx = client.get(f"/api/files/{files['relatorio.docx']}/info").json()
    assert "João da Silva" in docx["text"] and "Florianópolis" in docx["text"]


def test_open_without_system_app_is_reported(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_app, "has_default_app", lambda _path: False)
    file_id = ids(client)["20260527_151731.heic"]
    got = client.post(f"/api/files/{file_id}/open")
    assert got.status_code == 409 and "Nenhum programa" in got.json()["detail"]


def test_describe_exif() -> None:
    from PIL.TiffImagePlugin import IFDRational

    from aipdm.core.images import describe_exif

    base = {0x010F: "samsung", 0x0110: "Galaxy S26 Ultra\x00", 0x0131: "S938BXXU1"}
    exif = {
        0x829A: IFDRational(1, 120),
        0x829D: IFDRational(17, 10),
        0x8827: 50,
        0x920A: IFDRational(63, 10),
        0xA405: 24,
        0xA434: "Galaxy S26 Ultra Rear Camera",
        0x9209: 0,
    }
    assert describe_exif(base, exif) == [
        ("Câmera", "samsung Galaxy S26 Ultra"),
        ("Lente", "Galaxy S26 Ultra Rear Camera"),
        ("Exposição", "1/120 s"),
        ("Abertura", "f/1.7"),
        ("ISO", "50"),
        ("Distância focal", "6.3 mm (24 mm equiv.)"),
        ("Flash", "não disparado"),
        ("Software", "S938BXXU1"),
    ]
    # Empty/garbage values (0/0 rationals, blank strings) are left out.
    assert describe_exif({0x010F: "\x00"}, {0x829A: IFDRational(0, 0), 0x8827: (0,)}) == []
    assert describe_exif({}, {0x829A: 2.0})[0] == ("Exposição", "2 s")
