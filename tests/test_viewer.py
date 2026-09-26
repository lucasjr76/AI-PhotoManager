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
    monkeypatch.setattr(server_app, "open_in_system", lambda _path: False)
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


class FakeFaceModel:
    """Finds a face in any region whose width is even, none otherwise."""

    def embed_region(self, rgb: object, box: tuple[int, int, int, int]) -> object:
        import numpy as np

        from aipdm.core.faces import DetectedFace

        x, y, w, h = box
        if w % 2:
            return None
        return DetectedFace((x + 1, y + 1, w - 2, h - 2), 0.66, np.eye(128, dtype=np.float32)[0])


@pytest.fixture
def marking(tmp_path: Path) -> TestClient:
    root = tmp_path / "pasta"
    root.mkdir()
    heic(root / "foto.heic", (800, 600), orientation=6)  # displayed 600 x 800
    make_pdf(root / "doc.pdf", ["texto"])
    app = create_app(
        None,
        TOKEN,
        HOST,
        index_only=frozenset(),
        workers=1,
        monitor_interval=None,
        face_model_factory=FakeFaceModel,  # type: ignore[arg-type]
    )
    c = TestClient(app, base_url=f"http://{HOST}")
    c.get(f"/?t={TOKEN}", follow_redirects=False)
    c.post("/api/folders/open", json={"path": str(root)})
    wait_index(c)
    return c


def test_mark_face_by_hand(marking: TestClient) -> None:
    files = ids(marking)
    photo = files["foto.heic"]
    empty = marking.get(f"/api/files/{photo}/faces").json()
    assert (empty["width"], empty["height"], empty["faces"]) == (600, 800, [])  # oriented size

    found = marking.post(
        f"/api/files/{photo}/faces", json={"bbox": [100, 120, 80, 90], "name": "Ana"}
    )
    assert found.status_code == 200 and found.json()["signature"] is True
    blank = marking.post(
        f"/api/files/{photo}/faces", json={"bbox": [300, 300, 81, 90], "name": "ana"}
    ).json()
    assert blank["signature"] is False and blank["person_id"] == found.json()["person_id"]

    listed = marking.get(f"/api/files/{photo}/faces").json()["faces"]
    assert [(f["bbox"], f["name"], f["manual"], f["signature"], f["source"]) for f in listed] == [
        ([101, 121, 78, 88], "Ana", True, True, "user"),  # detected box inside the drawn one
        ([300, 300, 81, 90], "Ana", True, False, "user"),
    ]
    renamed = marking.post(f"/api/faces/{listed[1]['id']}/assign", json={"name": "Bia"})
    assert renamed.status_code == 200
    assert marking.post(f"/api/faces/{listed[1]['id']}/delete").status_code == 200
    assert marking.post(f"/api/faces/{listed[1]['id']}/delete").status_code == 404
    assert len(marking.get(f"/api/files/{photo}/faces").json()["faces"]) == 1


@pytest.mark.parametrize(
    "bbox", [[-5, 10, 50, 50], [580, 10, 50, 50], [10, 10, 4, 50], [10, 790, 50, 50]]
)
def test_mark_face_outside_photo_is_rejected(marking: TestClient, bbox: list[int]) -> None:
    photo = ids(marking)["foto.heic"]
    assert (
        marking.post(f"/api/files/{photo}/faces", json={"bbox": bbox, "name": "X"}).status_code
        == 400
    )


def test_faces_only_for_photos(marking: TestClient) -> None:
    pdf = ids(marking)["doc.pdf"]
    assert marking.get(f"/api/files/{pdf}/faces").status_code == 404
    assert (
        marking.post(
            f"/api/files/{pdf}/faces", json={"bbox": [1, 1, 20, 20], "name": "X"}
        ).status_code
        == 404
    )


def test_open_in_system_linux_without_association(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_app.sys, "platform", "linux")
    monkeypatch.setattr(server_app, "has_default_app", lambda _path: False)
    launched: list[object] = []
    monkeypatch.setattr(server_app.subprocess, "Popen", lambda *a, **k: launched.append(a))
    assert server_app.open_in_system(Path("foto.heic")) is False
    assert launched == []  # nothing started when no program is associated
