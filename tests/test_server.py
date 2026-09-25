"""RNF-8/RNF-9: token on every request, loopback Host only, files only by id."""

import re
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from conftest import index_base
from fastapi.testclient import TestClient
from starlette.routing import Route

from aipdm.core import db
from aipdm.server.app import COOKIE, create_app

TOKEN = "segredo-de-teste"
HOST = "127.0.0.1:4321"


@pytest.fixture
def db_path(sample_dir: Path, tmp_path: Path) -> Path:
    path = tmp_path / "dados" / "ui.sqlite"
    index_base(sample_dir, path, workers=1)
    conn = db.connect(path)
    file_id = conn.execute(
        "SELECT id FROM files WHERE rel_path = 'WhatsApp Images/IMG-20230514-WA0001.jpg'"
    ).fetchone()[0]
    person = conn.execute("INSERT INTO people (name) VALUES (NULL)").lastrowid
    emb = np.ones(128, np.float32).tobytes()
    for _ in range(2):
        conn.execute(
            "INSERT INTO faces (file_id, bbox, det_score, embedding, person_id, assign_source)"
            " VALUES (?, '10,10,20,20', 0.9, ?, ?, 'cluster')",
            (file_id, emb, person),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def app_(db_path: Path):  # type: ignore[no-untyped-def]
    return create_app(db_path, TOKEN, allowed_host=HOST)


@pytest.fixture
def anon(app_) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(app_, base_url=f"http://{HOST}")


@pytest.fixture
def client(app_) -> TestClient:  # type: ignore[no-untyped-def]
    c = TestClient(app_, base_url=f"http://{HOST}")
    c.get(f"/?t={TOKEN}", follow_redirects=False)
    return c


def test_every_route_requires_token(app_, anon: TestClient) -> None:  # type: ignore[no-untyped-def]
    routes = [r for r in app_.routes if isinstance(r, Route)]
    assert len(routes) >= 15
    for route in routes:
        url = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods or ():
            if method == "HEAD":
                continue
            response = anon.request(method, url, json={})
            assert response.status_code == 401, (method, url)


def test_wrong_token_rejected(anon: TestClient) -> None:
    assert anon.get("/api/status", headers={"X-Token": "errado"}).status_code == 401
    anon.cookies.set(COOKIE, "errado")
    assert anon.get("/api/status").status_code == 401
    assert anon.get("/?t=errado", follow_redirects=False).status_code == 401


def test_token_via_url_sets_cookie(anon: TestClient) -> None:
    first = anon.get(f"/?t={TOKEN}", follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/"
    assert "httponly" in first.headers["set-cookie"].lower()
    assert "samesite=strict" in first.headers["set-cookie"].lower()
    assert anon.get("/").status_code == 200
    assert anon.get("/api/status").status_code == 200


def test_foreign_host_rejected_even_with_token(app_) -> None:  # type: ignore[no-untyped-def]
    evil = TestClient(app_, base_url="http://evil.example:4321")
    assert evil.get("/api/status", headers={"X-Token": TOKEN}).status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "/api/files/..%2F..%2Fetc%2Fpasswd/thumb",
        "/api/files/%2Fetc%2Fpasswd/thumb",
        "/api/files/camera.jpg/thumb",
        "/api/files/1/thumb/../../../../etc/passwd",
        "/api/faces/..%2Fx/crop",
        "/etc/passwd",
        "/app.js/../../../etc/passwd",
        "/api/files?path=/etc/passwd",
        "/api/files/thumb?path=camera.jpg",
        "/static/../../etc/passwd",
        "/api/files/999999/thumb",
    ],
)
def test_paths_are_never_served(client: TestClient, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 404, response.text
    assert b"root:" not in response.content


def test_files_served_by_id(client: TestClient, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    file_id = conn.execute("SELECT id FROM files WHERE rel_path = 'camera.jpg'").fetchone()[0]
    face_id = conn.execute("SELECT MIN(id) FROM faces").fetchone()[0]
    conn.close()
    thumb = client.get(f"/api/files/{file_id}/thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    crop = client.get(f"/api/faces/{face_id}/crop")
    assert crop.status_code == 200 and crop.content[:2] == b"\xff\xd8"


def test_people_flow(client: TestClient) -> None:
    [person] = client.get("/api/people").json()
    assert person["name"] is None and person["faces"] == 2
    face_a, face_b = (f["id"] for f in client.get(f"/api/people/{person['id']}").json()["faces"])

    assert client.post(f"/api/people/{person['id']}", json={"name": "Maria"}).status_code == 200
    assert client.get("/api/people").json()[0]["name"] == "Maria"

    client.post(f"/api/faces/{face_b}/remove")
    assert client.get("/api/status").json()["unassigned"] == 1
    new = client.post(f"/api/faces/{face_b}/assign", json={"name": "Ana"}).json()["person_id"]
    same = client.post(f"/api/faces/{face_b}/assign", json={"name": "ana"}).json()["person_id"]
    assert new == same  # existing name reused, case-insensitive

    assert (
        client.post(f"/api/people/{person['id']}/merge", json={"other_id": new}).status_code == 200
    )
    detail = client.get(f"/api/people/{person['id']}").json()
    assert {f["id"] for f in detail["faces"]} == {face_a, face_b}

    client.post(f"/api/people/{person['id']}", json={"hidden": True})
    assert client.get("/api/people").json() == []
    assert len(client.get("/api/people?hidden=true").json()) == 1
