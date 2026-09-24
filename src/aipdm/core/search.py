"""Search. Phase 1: full-text (FTS5 bm25) over document text."""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    file_id: int
    rel_path: str
    kind: str
    taken_at: str | None
    page: int
    snippet: str
    score: float  # bm25: lower is better


def fts_query(text: str) -> str:
    """Quote every term so user input is never parsed as FTS5 syntax (implicit AND)."""
    return " ".join('"' + term.replace('"', '""') + '"' for term in text.split())


def search_text(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[Hit]:
    query = fts_query(text)
    if not query:
        return []
    rows = conn.execute(
        "SELECT f.id, f.rel_path, f.kind, f.taken_at, t.page,"
        " snippet(texts, 0, '[', ']', '…', 12) AS snip, bm25(texts) AS score"
        " FROM texts t JOIN files f ON f.id = t.file_id"
        " WHERE texts MATCH ? AND f.status = 'done'"
        " ORDER BY score LIMIT ?",
        (query, limit * 5),
    )
    best: dict[int, Hit] = {}  # best page per file
    for row in rows:
        if row[0] not in best:
            best[row[0]] = Hit(*row)
    return list(best.values())[:limit]
