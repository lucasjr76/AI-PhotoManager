"""Combined search (SPEC section 10) on a synthetic database with a fake CLIP encoder."""

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from aipdm.core import db
from aipdm.core.search import CLIP_MIN, Query, fold, fts_query, search

BEACH, CAKE = np.eye(512, dtype=np.float32)[0], np.eye(512, dtype=np.float32)[1]


def fake_encoder(text: str) -> np.ndarray:
    return BEACH if "praia" in text else CAKE


def unit(*parts: tuple[np.ndarray, float]) -> bytes:
    v = sum(w * x for x, w in parts)
    return (v / np.linalg.norm(v)).astype(np.float32).tobytes()


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "s.sqlite")
    files = [
        # rel_path, kind, taken_at, sticker, clip embedding, text
        ("praia1.jpg", "image", "2023-01-10", 0, unit((BEACH, 1.0)), None),
        ("praia2.jpg", "image", "2023-06-01", 0, unit((BEACH, 0.6), (CAKE, 0.8)), None),
        ("bolo.jpg", "image", "2022-12-25", 0, unit((CAKE, 1.0)), None),
        ("stk.webp", "image", "2023-02-02", 1, unit((BEACH, 1.0)), None),
        ("placa.jpg", "image", "2023-03-03", 0, unit((CAKE, 1.0)), "Bem-vindo à praia"),
        ("ata.pdf", "pdf", "2021-05-05", 0, None, "Reunião com Maria Souza sobre a praia"),
        ("outro.pdf", "pdf", "2021-06-06", 0, None, "Nada a ver"),
    ]
    for rel, kind, taken, sticker, emb, text in files:
        fid = c.execute(
            "INSERT INTO files (rel_path, kind, size, mtime, taken_at, is_sticker, status)"
            " VALUES (?, ?, 1, 1, ?, ?, 'done')",
            (rel, kind, taken, sticker),
        ).lastrowid
        if emb:
            c.execute("INSERT INTO clip_embeddings VALUES (?, ?)", (fid, emb))
        if text:
            c.execute("INSERT INTO texts (content, file_id, page) VALUES (?, ?, 1)", (text, fid))
    maria = c.execute("INSERT INTO people (name) VALUES ('Maria Souza')").lastrowid
    joao = c.execute("INSERT INTO people (name) VALUES ('João')").lastrowid
    ids = {r[1]: r[0] for r in c.execute("SELECT id, rel_path FROM files")}
    for rel, person, source in [
        ("praia1.jpg", maria, "cluster"),
        ("praia1.jpg", joao, "user"),
        ("praia2.jpg", maria, "auto"),
        ("bolo.jpg", maria, "suggested"),  # suggestions never count as the person
    ]:
        c.execute(
            "INSERT INTO faces (file_id, bbox, det_score, embedding, person_id, assign_source)"
            " VALUES (?, '0,0,1,1', 1, x'', ?, ?)",
            (ids[rel], person, source),
        )
    c.commit()
    return c


def paths(results: list) -> list[str]:  # type: ignore[type-arg]
    return [h.rel_path for h in results]


def test_clip_and_fts_are_combined(conn: sqlite3.Connection) -> None:
    got = search(conn, Query(text="praia"), fake_encoder).hits
    # praia1 best by CLIP; placa/ata only by text; praia2 weaker CLIP; stickers excluded.
    assert paths(got)[0] == "praia1.jpg"
    assert set(paths(got)) == {"praia1.jpg", "praia2.jpg", "placa.jpg", "ata.pdf"}
    assert all(0.0 <= h.score <= 1.0 for h in got)
    placa = next(h for h in got if h.rel_path == "placa.jpg")
    assert placa.snippet and "[praia]" in placa.snippet


def test_clip_below_threshold_is_dropped(conn: sqlite3.Connection) -> None:
    assert CLIP_MIN > 0
    got = paths(search(conn, Query(text="bolo"), fake_encoder).hits)
    assert "praia1.jpg" not in got  # similarity 0 with CAKE
    assert got[0] in {"bolo.jpg", "placa.jpg"}


def test_without_encoder_only_fts(conn: sqlite3.Connection) -> None:
    got = paths(search(conn, Query(text="praia"), None).hits)
    assert set(got) == {"placa.jpg", "ata.pdf"}


def test_filters(conn: sqlite3.Connection) -> None:
    def run(**kw: object) -> set[str]:
        return set(paths(search(conn, Query(text="praia", **kw), fake_encoder).hits))  # type: ignore[arg-type]

    assert run(date_from="2023-01-01", date_to="2023-03-03") == {"praia1.jpg", "placa.jpg"}
    assert run(kinds=("pdf",)) == {"ata.pdf"}
    assert "stk.webp" in run(stickers=True)
    maria, joao = (r[0] for r in conn.execute("SELECT id FROM people ORDER BY id"))
    assert run(people_ids=(maria,)) == {"praia1.jpg", "praia2.jpg"}
    assert run(people_ids=(maria, joao)) == {"praia1.jpg"}  # AND: both in the same photo


def test_suggested_faces_do_not_count(conn: sqlite3.Connection) -> None:
    maria = conn.execute("SELECT id FROM people WHERE name = 'Maria Souza'").fetchone()[0]
    got = paths(search(conn, Query(people_ids=(maria,)), None).hits)
    assert set(got) == {"praia1.jpg", "praia2.jpg"}


def test_person_name_gives_photos_and_documents(conn: sqlite3.Connection) -> None:
    results = search(conn, Query(text="maria souza"), fake_encoder)
    assert results.person == "Maria Souza"
    assert set(paths(results.hits)) == {"praia1.jpg", "praia2.jpg"}
    assert paths(results.mentions) == ["ata.pdf"]


def test_person_name_is_accent_insensitive(conn: sqlite3.Connection) -> None:
    assert search(conn, Query(text="JOAO"), None).person == "João"
    assert fold("  João   da  Silva ") == "joao da silva"


def test_order_by_date(conn: sqlite3.Connection) -> None:
    got = paths(search(conn, Query(text="praia", order="date"), fake_encoder).hits)
    assert got == ["praia2.jpg", "placa.jpg", "praia1.jpg", "ata.pdf"]


def test_no_text_lists_filtered_files(conn: sqlite3.Connection) -> None:
    got = paths(search(conn, Query(kinds=("pdf",)), None).hits)
    assert got == ["outro.pdf", "ata.pdf"]


def test_user_text_is_never_fts_syntax(conn: sqlite3.Connection) -> None:
    assert fts_query('a "b" OR c*') == '"a" """b""" "OR" "c*"'
    assert search(conn, Query(text='AND OR "( NEAR'), None).hits == []
