"""Face grouping and assignment rules (SPEC section 9), with synthetic embeddings."""

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from aipdm.core import db
from aipdm.core.faces import DetectedFace, FaceSettings, group_faces, normalize, reset_groups
from aipdm.core.scanner import _save_faces

SETTINGS = FaceSettings(t_auto=0.8, t_suggest=0.6, cluster_eps=0.1, cluster_min_samples=3)

RNG = np.random.default_rng(0)
IDENTITIES = normalize(RNG.normal(size=(3, 128)).astype(np.float32))


def near(identity: np.ndarray, similarity: float) -> np.ndarray:
    """A unit vector with the given cosine similarity to `identity`."""
    noise = RNG.normal(size=128).astype(np.float32)
    noise -= noise @ identity * identity
    noise /= np.linalg.norm(noise)
    return normalize(similarity * identity + np.sqrt(1 - similarity**2) * noise)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "faces.sqlite")


def add_face(conn: sqlite3.Connection, emb: np.ndarray, sticker: bool = False) -> int:
    file_id = conn.execute(
        "INSERT INTO files (rel_path, kind, size, mtime, is_sticker, status)"
        " VALUES (?, 'image', 1, 1, ?, 'done')",
        (f"f{RNG.integers(1 << 62)}.jpg", int(sticker)),
    ).lastrowid
    face_id = conn.execute(
        "INSERT INTO faces (file_id, bbox, det_score, embedding) VALUES (?, '0,0,50,50', 0.9, ?)",
        (file_id, emb.astype(np.float32).tobytes()),
    ).lastrowid
    conn.commit()
    assert face_id is not None
    return face_id


def person_of(conn: sqlite3.Connection, face_id: int) -> tuple[int | None, str | None]:
    row = conn.execute(
        "SELECT person_id, assign_source FROM faces WHERE id = ?", (face_id,)
    ).fetchone()
    return row[0], row[1]


def test_first_run_clusters_and_leaves_noise(conn: sqlite3.Connection) -> None:
    group_a = [add_face(conn, near(IDENTITIES[0], 0.99)) for _ in range(4)]
    group_b = [add_face(conn, near(IDENTITIES[1], 0.99)) for _ in range(3)]
    lonely = add_face(conn, IDENTITIES[2])
    sticker = add_face(conn, near(IDENTITIES[0], 0.99), sticker=True)

    stats = group_faces(conn, SETTINGS)

    assert (stats.new_people, stats.clustered, stats.unassigned) == (2, 7, 1)
    people_a = {person_of(conn, f)[0] for f in group_a}
    people_b = {person_of(conn, f)[0] for f in group_b}
    assert len(people_a) == len(people_b) == 1 and people_a != people_b
    assert person_of(conn, group_a[0])[1] == "cluster"
    assert person_of(conn, lonely) == (None, None)
    assert person_of(conn, sticker) == (None, None)  # stickers never grouped
    assert conn.execute("SELECT COUNT(*) FROM people WHERE name IS NULL").fetchone()[0] == 2


def test_new_faces_auto_suggested_or_reclustered(conn: sqlite3.Connection) -> None:
    for _ in range(4):
        add_face(conn, near(IDENTITIES[0], 0.99))
    group_faces(conn, SETTINGS)
    maria = conn.execute("SELECT id FROM people").fetchone()[0]
    conn.execute("UPDATE people SET name = 'Maria' WHERE id = ?", (maria,))

    strong = add_face(conn, near(IDENTITIES[0], 0.95))
    weak = add_face(conn, near(IDENTITIES[0], 0.75))
    stranger = add_face(conn, IDENTITIES[1])
    stats = group_faces(conn, SETTINGS)

    assert person_of(conn, strong) == (maria, "auto")
    assert person_of(conn, weak) == (maria, "suggested")
    assert person_of(conn, stranger) == (None, None)
    assert (stats.auto, stats.suggested) == (1, 1)


def test_unnamed_people_get_auto_but_never_suggested(conn: sqlite3.Connection) -> None:
    for _ in range(4):
        add_face(conn, near(IDENTITIES[0], 0.99))
    group_faces(conn, SETTINGS)
    weak = add_face(conn, near(IDENTITIES[0], 0.75))
    group_faces(conn, SETTINGS)
    assert person_of(conn, weak) == (None, None)  # "É ...?" needs a name


def test_negative_blocks_that_person(conn: sqlite3.Connection) -> None:
    for _ in range(4):
        add_face(conn, near(IDENTITIES[0], 0.99))
    group_faces(conn, SETTINGS)
    maria = conn.execute("SELECT id FROM people").fetchone()[0]
    conn.execute("UPDATE people SET name = 'Maria' WHERE id = ?", (maria,))
    face = add_face(conn, near(IDENTITIES[0], 0.95))
    conn.execute("INSERT INTO face_negatives (face_id, person_id) VALUES (?, ?)", (face, maria))
    conn.commit()

    group_faces(conn, SETTINGS)
    assert person_of(conn, face)[0] != maria


def test_user_assignment_is_never_overwritten(conn: sqlite3.Connection) -> None:
    for _ in range(4):
        add_face(conn, near(IDENTITIES[0], 0.99))
    group_faces(conn, SETTINGS)
    other = conn.execute("INSERT INTO people (name) VALUES ('João')").lastrowid
    face = add_face(conn, near(IDENTITIES[0], 0.99))
    conn.execute(
        "UPDATE faces SET person_id = ?, assign_source = 'user' WHERE id = ?", (other, face)
    )
    conn.commit()

    group_faces(conn, SETTINGS)
    assert person_of(conn, face) == (other, "user")


def test_reprocessing_a_file_keeps_user_labels(conn: sqlite3.Connection) -> None:
    face = add_face(conn, IDENTITIES[0])
    file_id = conn.execute("SELECT file_id FROM faces WHERE id = ?", (face,)).fetchone()[0]
    person = conn.execute("INSERT INTO people (name) VALUES ('Ana')").lastrowid
    conn.execute(
        "UPDATE faces SET person_id = ?, assign_source = 'user', bbox = '10,10,100,100'"
        " WHERE id = ?",
        (person, face),
    )
    detected = [
        DetectedFace((12, 11, 98, 101), 0.95, IDENTITIES[0]),  # same box, re-detected
        DetectedFace((300, 300, 80, 80), 0.9, IDENTITIES[1]),
    ]
    _save_faces(conn, file_id, detected)

    rows = conn.execute(
        "SELECT bbox, person_id, assign_source FROM faces WHERE file_id = ? ORDER BY id",
        (file_id,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("12,11,98,101", person, "user"),
        ("300,300,80,80", None, None),
    ]


def test_reset_groups_keeps_names_and_user_decisions(conn: sqlite3.Connection) -> None:
    group = [add_face(conn, near(IDENTITIES[0], 0.99)) for _ in range(3)]
    other = [add_face(conn, near(IDENTITIES[1], 0.99)) for _ in range(3)]
    group_faces(conn, SETTINGS)
    named = person_of(conn, group[0])[0]
    conn.execute("UPDATE people SET name = 'Maria' WHERE id = ?", (named,))
    unnamed = person_of(conn, other[0])[0]
    conn.execute("UPDATE faces SET assign_source = 'user' WHERE id = ?", (other[1],))
    auto = add_face(conn, near(IDENTITIES[0], 0.95))
    group_faces(conn, SETTINGS)
    assert person_of(conn, auto) == (named, "auto")

    reset_groups(conn)

    assert person_of(conn, group[0]) == (named, "cluster")  # named group untouched
    assert person_of(conn, auto) == (None, None)  # automatic assignment undone
    assert person_of(conn, other[0]) == (None, None)  # unnamed group undone
    assert person_of(conn, other[1]) == (unnamed, "user")  # user decision kept
