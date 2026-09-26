"""Faces: detection + embedding (YuNet/SFace, in workers) and grouping into people.

Grouping (SPEC section 9), run in the main process after each index:
1. Every face without a person is compared with the faces already accepted for each
   person (source 'cluster' or 'user'): score >= t_auto -> 'auto'; for *named*
   people, t_suggest <= score < t_auto -> 'suggested' ("É a Maria?").
2. Faces still without a person are clustered with DBSCAN; each cluster becomes a new
   unnamed person. Noise stays unassigned and is retried on the next run.
User decisions ('user' assignments, negatives) are never overwritten.
"""

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from aipdm.core.cluster import DEFAULT_BLOCK, dbscan_cosine

DETECTOR_FILE = "face_detection_yunet_2026may.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"
EMBEDDING_DIM = 128
ACCEPTED_SOURCES = ("cluster", "user")

Embedding = NDArray[np.float32]


@dataclass(frozen=True)
class FaceSettings:
    min_score: float = 0.8
    min_size: int = 40  # pixels, in original image coordinates
    # Calibrated on the real test folder (30 named people, 3,527 faces; see SPEC):
    # no two different people scored >= 0.687; at 0.60, 7.7% of faces have an impostor
    # above the bar, acceptable for a question. eps > 0.3 merged different people.
    t_auto: float = 0.7  # = 1 - cluster_eps, same bar as joining a cluster
    t_suggest: float = 0.6
    cluster_eps: float = 0.3  # cosine distance, i.e. similarity >= 0.7
    cluster_min_samples: int = 3
    cluster_block: int = DEFAULT_BLOCK


# User-tunable settings (Configurações screen), stored per folder in the meta table.
SETTINGS_META = {
    "t_auto": "faces_t_auto",
    "t_suggest": "faces_t_suggest",
    "cluster_eps": "faces_eps",
}
SETTINGS_RANGES = {"t_auto": (0.3, 0.99), "t_suggest": (0.2, 0.99), "cluster_eps": (0.05, 0.7)}


def load_settings(conn: sqlite3.Connection) -> FaceSettings:
    """Defaults, overridden by what the user saved for this folder."""
    settings = FaceSettings()
    for field, key in SETTINGS_META.items():
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is not None:
            settings = replace(settings, **{field: float(row[0])})  # type: ignore[arg-type]
    return settings


def save_settings(
    conn: sqlite3.Connection, *, t_auto: float, t_suggest: float, cluster_eps: float
) -> FaceSettings:
    """Validate and store. Raises ValueError with a pt-BR message."""
    values = {"t_auto": t_auto, "t_suggest": t_suggest, "cluster_eps": cluster_eps}
    for field, value in values.items():
        low, high = SETTINGS_RANGES[field]
        if not low <= value <= high:
            raise ValueError(f"{field} deve ficar entre {low} e {high}")
    if t_suggest > t_auto:
        raise ValueError("o limiar de sugestão não pode ser maior que o de atribuição automática")
    for field, value in values.items():
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SETTINGS_META[field], repr(float(value))),
        )
    conn.commit()
    return load_settings(conn)


def reset_settings(conn: sqlite3.Connection) -> FaceSettings:
    conn.executemany("DELETE FROM meta WHERE key = ?", [(k,) for k in SETTINGS_META.values()])
    conn.commit()
    return FaceSettings()


@dataclass(frozen=True)
class DetectedFace:
    bbox: tuple[int, int, int, int]  # x, y, w, h in original image pixels
    score: float
    embedding: Embedding  # L2-normalized


def normalize(x: NDArray[np.float32]) -> NDArray[np.float32]:
    norms = np.linalg.norm(x, axis=-1, keepdims=True)
    out: NDArray[np.float32] = (x / np.maximum(norms, 1e-12)).astype(np.float32)
    return out


class FaceModel:
    def __init__(self, models_dir: Path, settings: FaceSettings) -> None:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        self.settings = settings
        self.detector = cv2.FaceDetectorYN.create(
            str(models_dir / DETECTOR_FILE), "", (320, 320), settings.min_score, 0.3, 5000
        )
        self.recognizer = cv2.FaceRecognizerSF.create(str(models_dir / RECOGNIZER_FILE), "")

    def detect(self, rgb: NDArray[np.uint8], scale: float) -> list[DetectedFace]:
        """`rgb` is the downscaled image; `scale` = original size / downscaled size."""
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        self.detector.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, raw = self.detector.detect(bgr)
        found: list[DetectedFace] = []
        for row in [] if raw is None else raw:
            x, y, w, h = (float(v) * scale for v in row[:4])
            score = float(row[-1])
            if score < self.settings.min_score or min(w, h) < self.settings.min_size:
                continue
            feature = self.recognizer.feature(self.recognizer.alignCrop(bgr, row))
            found.append(
                DetectedFace(
                    (round(x), round(y), round(w), round(h)),
                    score,
                    normalize(np.asarray(feature, dtype=np.float32).reshape(EMBEDDING_DIM)),
                )
            )
        return found


def to_blob(embedding: Embedding) -> bytes:
    return np.asarray(embedding, dtype=np.float32).tobytes()


def from_blobs(blobs: list[bytes]) -> NDArray[np.float32]:
    if not blobs:
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    return np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(-1, EMBEDDING_DIM)


def person_scores(
    faces: NDArray[np.float32],
    references: NDArray[np.float32],
    reference_person: NDArray[np.int64],
    block: int = DEFAULT_BLOCK,
) -> tuple[NDArray[np.int64], NDArray[np.float32]]:
    """Max cosine similarity of each face to each person's reference faces.

    Returns (people ids, scores[faces, people]); computed in tiles.
    """
    people = np.unique(reference_person)
    scores = np.full((len(faces), len(people)), -1.0, dtype=np.float32)
    column = np.searchsorted(people, reference_person)
    for f0 in range(0, len(faces), block):
        out = scores[f0 : f0 + block]
        for r0 in range(0, len(references), block):
            sim = faces[f0 : f0 + block] @ references[r0 : r0 + block].T
            cols = column[r0 : r0 + block]
            for c in np.unique(cols):
                np.maximum(out[:, c], sim[:, cols == c].max(axis=1), out=out[:, c])
    return people, scores


@dataclass
class GroupingStats:
    auto: int = 0
    suggested: int = 0
    new_people: int = 0
    clustered: int = 0
    unassigned: int = 0


def group_faces(conn: sqlite3.Connection, settings: FaceSettings) -> GroupingStats:
    """Assign unassigned faces to people, then cluster the rest (see module docstring)."""
    stats = GroupingStats()
    rows = conn.execute(
        "SELECT fa.id, fa.embedding FROM faces fa JOIN files f ON f.id = fa.file_id"
        " WHERE fa.person_id IS NULL AND f.is_sticker = 0 AND f.status != 'missing'"
    ).fetchall()
    if not rows:
        return stats
    face_ids = np.array([r[0] for r in rows], dtype=np.int64)
    embeddings = from_blobs([r[1] for r in rows])

    refs = conn.execute(
        "SELECT fa.person_id, fa.embedding, p.name IS NOT NULL FROM faces fa"
        " JOIN people p ON p.id = fa.person_id WHERE fa.assign_source IN (?, ?)",
        ACCEPTED_SOURCES,
    ).fetchall()
    assigned = np.zeros(len(face_ids), dtype=bool)
    if refs:
        ref_person = np.array([r[0] for r in refs], dtype=np.int64)
        named = {int(r[0]) for r in refs if r[2]}
        people, scores = person_scores(
            embeddings, from_blobs([r[1] for r in refs]), ref_person, settings.cluster_block
        )
        for row, col in _negative_cells(conn, face_ids, people):
            scores[row, col] = -1.0
        best = scores.argmax(axis=1)
        best_score = scores[np.arange(len(face_ids)), best]
        updates = []
        for i, (col, score) in enumerate(zip(best, best_score, strict=True)):
            person = int(people[col])
            if score >= settings.t_auto:
                updates.append((person, "auto", float(score), int(face_ids[i])))
                stats.auto += 1
            elif score >= settings.t_suggest and person in named:
                updates.append((person, "suggested", float(score), int(face_ids[i])))
                stats.suggested += 1
            else:
                continue
            assigned[i] = True
        conn.executemany(
            "UPDATE faces SET person_id = ?, assign_source = ?, assign_score = ? WHERE id = ?",
            updates,
        )

    rest = ~assigned
    labels = dbscan_cosine(
        embeddings[rest],
        eps=settings.cluster_eps,
        min_samples=settings.cluster_min_samples,
        block=settings.cluster_block,
    )
    rest_ids = face_ids[rest]
    for label in range(int(labels.max()) + 1 if labels.size else 0):
        members = rest_ids[labels == label]
        cur = conn.execute("INSERT INTO people (name) VALUES (NULL)")
        person_id = cur.lastrowid
        conn.executemany(
            "UPDATE faces SET person_id = ?, assign_source = 'cluster', assign_score = NULL"
            " WHERE id = ?",
            [(person_id, int(f)) for f in members],
        )
        conn.execute(
            "UPDATE people SET cover_face_id = ? WHERE id = ?", (int(members[0]), person_id)
        )
        stats.new_people += 1
        stats.clustered += len(members)
    stats.unassigned = int((labels == -1).sum())
    conn.commit()
    return stats


def reset_groups(conn: sqlite3.Connection) -> int:
    """Undo automatic grouping of unnamed people so it can be redone with new settings.

    Named people, 'user' assignments and negatives are kept. Returns people removed.
    """
    conn.execute(
        "UPDATE faces SET person_id = NULL, assign_source = NULL, assign_score = NULL"
        " WHERE assign_source != 'user' AND person_id IN (SELECT id FROM people WHERE name IS NULL)"
    )
    conn.execute(
        "UPDATE faces SET person_id = NULL, assign_source = NULL, assign_score = NULL"
        " WHERE assign_source IN ('auto', 'suggested')"
    )
    removed = conn.execute(
        "DELETE FROM people WHERE name IS NULL"
        " AND id NOT IN (SELECT person_id FROM faces WHERE person_id IS NOT NULL)"
    ).rowcount
    conn.commit()
    return removed


def _negative_cells(
    conn: sqlite3.Connection, face_ids: NDArray[np.int64], people: NDArray[np.int64]
) -> list[tuple[int, int]]:
    """(row, column) cells of the score matrix vetoed by "não é esta pessoa"."""
    row_of = {int(f): i for i, f in enumerate(face_ids)}
    col_of = {int(p): j for j, p in enumerate(people)}
    return [
        (row_of[f], col_of[p])
        for f, p in conn.execute("SELECT face_id, person_id FROM face_negatives")
        if f in row_of and p in col_of
    ]


# --- User actions. Every one of them is final: automatic grouping never undoes them. ---


def name_person(conn: sqlite3.Connection, person_id: int, name: str | None) -> None:
    conn.execute(
        "UPDATE people SET name = ? WHERE id = ?", ((name or "").strip() or None, person_id)
    )
    conn.commit()


def set_hidden(conn: sqlite3.Connection, person_id: int, hidden: bool) -> None:
    conn.execute("UPDATE people SET hidden = ? WHERE id = ?", (int(hidden), person_id))
    conn.commit()


def merge_people(conn: sqlite3.Connection, keep: int, gone: int) -> None:
    """Move everything from `gone` into `keep` and delete `gone`."""
    if keep == gone:
        raise ValueError("não é possível mesclar uma pessoa com ela mesma")
    conn.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", (keep, gone))
    conn.execute(
        "UPDATE OR IGNORE face_negatives SET person_id = ? WHERE person_id = ?", (keep, gone)
    )
    conn.execute("DELETE FROM face_negatives WHERE person_id = ?", (gone,))
    # A face now inside `keep` can no longer be vetoed for `keep`.
    conn.execute(
        "DELETE FROM face_negatives WHERE person_id = ?"
        " AND face_id IN (SELECT id FROM faces WHERE person_id = ?)",
        (keep, keep),
    )
    conn.execute("DELETE FROM people WHERE id = ?", (gone,))
    conn.commit()


def assign_face(conn: sqlite3.Connection, face_id: int, person_id: int) -> None:
    """User says: this face is `person_id` (also confirms a suggestion)."""
    conn.execute(
        "UPDATE faces SET person_id = ?, assign_source = 'user', assign_score = NULL WHERE id = ?",
        (person_id, face_id),
    )
    conn.execute(
        "DELETE FROM face_negatives WHERE face_id = ? AND person_id = ?", (face_id, person_id)
    )
    conn.commit()


def assign_face_to_new_person(conn: sqlite3.Connection, face_id: int, name: str) -> int:
    person_id = conn.execute(
        "INSERT INTO people (name, cover_face_id) VALUES (?, ?)", (name.strip() or None, face_id)
    ).lastrowid
    assert person_id is not None
    assign_face(conn, face_id, person_id)
    return person_id


def remove_face(conn: sqlite3.Connection, face_id: int) -> None:
    """User says: this face is *not* its current person (also rejects a suggestion)."""
    row = conn.execute("SELECT person_id FROM faces WHERE id = ?", (face_id,)).fetchone()
    if row is None or row[0] is None:
        return
    conn.execute(
        "INSERT OR IGNORE INTO face_negatives (face_id, person_id) VALUES (?, ?)", (face_id, row[0])
    )
    conn.execute(
        "UPDATE faces SET person_id = NULL, assign_source = NULL, assign_score = NULL WHERE id = ?",
        (face_id,),
    )
    conn.execute(
        "UPDATE people SET cover_face_id = (SELECT MIN(id) FROM faces WHERE person_id = people.id)"
        " WHERE id = ? AND cover_face_id = ?",
        (row[0], face_id),
    )
    conn.commit()
