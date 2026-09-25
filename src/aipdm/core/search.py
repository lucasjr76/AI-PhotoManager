"""Combined search (SPEC section 10): SQL filters, then CLIP + FTS5 signals."""

import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

CLIP_WEIGHT = 0.5  # FTS gets 1 - CLIP_WEIGHT
CLIP_MIN = 0.2  # cosine text-image similarity below this is not a match; calibrate

TextEncoder = Callable[[str], NDArray[np.float32]]


@dataclass(frozen=True)
class Query:
    text: str | None = None
    people_ids: tuple[int, ...] = ()  # AND: all of them in the same photo
    date_from: str | None = None  # YYYY-MM-DD, inclusive
    date_to: str | None = None  # YYYY-MM-DD, inclusive
    kinds: tuple[str, ...] = ()
    stickers: bool = False
    order: str = "relevance"  # or "date"
    limit: int = 20


@dataclass(frozen=True)
class Hit:
    file_id: int
    rel_path: str
    kind: str
    taken_at: str | None
    score: float  # 0..1, higher is better (1.0 when there is no text)
    page: int | None = None
    snippet: str | None = None


@dataclass
class Results:
    hits: list[Hit]
    person: str | None = None  # set when the text matched a person's name
    mentions: list[Hit] = field(default_factory=list)  # documents citing that name


def fts_query(text: str) -> str:
    """Quote every term so user input is never parsed as FTS5 syntax (implicit AND)."""
    return " ".join('"' + term.replace('"', '""') + '"' for term in text.split())


def fold(text: str) -> str:
    """Case- and accent-insensitive form, for matching person names."""
    decomposed = unicodedata.normalize("NFKD", text)
    return " ".join(
        "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().split()
    )


def _filters(query: Query, people_ids: tuple[int, ...]) -> tuple[str, list[object]]:
    conds = ["f.status = 'done'", "f.kind != 'other'"]
    params: list[object] = []
    if not query.stickers:
        conds.append("f.is_sticker = 0")
    if query.kinds:
        conds.append(f"f.kind IN ({','.join('?' * len(query.kinds))})")
        params += query.kinds
    if query.date_from:
        conds.append("substr(f.taken_at, 1, 10) >= ?")
        params.append(query.date_from)
    if query.date_to:
        conds.append("substr(f.taken_at, 1, 10) <= ?")
        params.append(query.date_to)
    for person_id in people_ids:
        conds.append(
            "EXISTS (SELECT 1 FROM faces fa WHERE fa.file_id = f.id AND fa.person_id = ?"
            " AND fa.assign_source != 'suggested')"
        )
        params.append(person_id)
    return " AND ".join(conds), params


def _minmax(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low < 1e-9:
        return dict.fromkeys(values, 1.0)
    return {k: (v - low) / (high - low) for k, v in values.items()}


def _files(conn: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    rows: dict[int, sqlite3.Row] = {}
    for start in range(0, len(ids), 900):  # SQLite parameter limit
        part = ids[start : start + 900]
        marks = ",".join("?" * len(part))
        sql = f"SELECT id, rel_path, kind, taken_at FROM files WHERE id IN ({marks})"
        for row in conn.execute(sql, part):
            rows[row[0]] = row
    return rows


def _fts(
    conn: sqlite3.Connection, text: str, where: str, params: list[object]
) -> dict[int, tuple[float, int, str]]:
    """file_id -> (bm25 as higher-is-better, page, snippet) of the best page."""
    match = fts_query(text)
    if not match:
        return {}
    best: dict[int, tuple[float, int, str]] = {}
    for file_id, score, page, snip in conn.execute(
        "SELECT f.id, -bm25(texts), t.page, snippet(texts, 0, '[', ']', '…', 12)"
        f" FROM texts t JOIN files f ON f.id = t.file_id WHERE texts MATCH ? AND {where}",
        [match, *params],
    ):
        if file_id not in best or score > best[file_id][0]:
            best[file_id] = (score, page, snip)
    return best


def _clip(
    conn: sqlite3.Connection, embedding: NDArray[np.float32], where: str, params: list[object]
) -> dict[int, float]:
    # ponytail: loads all candidate embeddings per query; keep a cached matrix for the UI
    rows = conn.execute(
        f"SELECT f.id, c.embedding FROM clip_embeddings c JOIN files f ON f.id = c.file_id"
        f" WHERE {where}",
        params,
    ).fetchall()
    if not rows:
        return {}
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    sims = matrix @ embedding
    return {rows[i][0]: float(s) for i, s in enumerate(sims) if s >= CLIP_MIN}


def _rank(hits: list[Hit], query: Query) -> list[Hit]:
    if query.order == "date":
        hits.sort(key=lambda h: h.taken_at or "", reverse=True)
    else:
        hits.sort(key=lambda h: (-h.score, h.taken_at or ""))
    return hits[: query.limit]


def _plain(conn: sqlite3.Connection, query: Query, people_ids: tuple[int, ...]) -> list[Hit]:
    where, params = _filters(query, people_ids)
    rows = conn.execute(
        f"SELECT f.id, f.rel_path, f.kind, f.taken_at FROM files f WHERE {where}"
        " ORDER BY f.taken_at DESC LIMIT ?",
        [*params, query.limit],
    )
    return [Hit(r[0], r[1], r[2], r[3], 1.0) for r in rows]


def search_text(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[Hit]:
    """Full-text only (documents and OCR), best page per file."""
    return search(conn, Query(text=text, limit=limit), encode_text=None).hits


def find_person(conn: sqlite3.Connection, text: str) -> tuple[int, str] | None:
    wanted = fold(text)
    for person_id, name in conn.execute("SELECT id, name FROM people WHERE name IS NOT NULL"):
        if fold(name) == wanted:
            return int(person_id), str(name)
    return None


def search(conn: sqlite3.Connection, query: Query, encode_text: TextEncoder | None) -> Results:
    """`encode_text` is the CLIP text encoder; None disables the CLIP signal."""
    text = (query.text or "").strip()
    if not text:
        return Results(_plain(conn, query, query.people_ids))

    person = find_person(conn, text)
    if person:
        # The name is both a person filter (photos) and a text query (documents).
        photos = _plain(conn, query, (*query.people_ids, person[0]))
        where, params = _filters(query, query.people_ids)
        found = _fts(conn, text, where, params)
        norm = _minmax({f: v[0] for f, v in found.items()})
        mentions = [
            Hit(f, row[1], row[2], row[3], norm[f], found[f][1], found[f][2])
            for f, row in _files(conn, list(found)).items()
        ]
        return Results(photos, person[1], _rank(mentions, query))

    where, params = _filters(query, query.people_ids)
    fts = _fts(conn, text, where, params)
    clip = _clip(conn, encode_text(text), where, params) if encode_text else {}
    fts_norm = _minmax({f: v[0] for f, v in fts.items()})
    clip_norm = _minmax(clip)
    files = _files(conn, sorted(set(fts) | set(clip)))
    hits = []
    for file_id, row in files.items():
        score = CLIP_WEIGHT * clip_norm.get(file_id, 0.0) + (1 - CLIP_WEIGHT) * fts_norm.get(
            file_id, 0.0
        )
        page, snip = (fts[file_id][1], fts[file_id][2]) if file_id in fts else (None, None)
        hits.append(Hit(file_id, row[1], row[2], row[3], score, page, snip))
    return Results(_rank(hits, query))
