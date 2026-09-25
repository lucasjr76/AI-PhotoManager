"""SQLite schema, versioned migrations and data access."""

import sqlite3
from pathlib import Path

# Each entry migrates from version i to i + 1. Never edit a released migration; append.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE files (
      id INTEGER PRIMARY KEY,
      rel_path TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL,
      size INTEGER NOT NULL,
      mtime REAL NOT NULL,
      hash TEXT,
      taken_at TEXT,
      date_source TEXT,
      width INTEGER, height INTEGER,
      is_sticker INTEGER DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'pending',
      error TEXT,
      stages_done TEXT DEFAULT ''
    );
    CREATE INDEX files_taken_at ON files(taken_at);
    CREATE INDEX files_status ON files(status);
    CREATE VIRTUAL TABLE texts USING fts5(
      content, file_id UNINDEXED, page UNINDEXED,
      tokenize = 'unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TABLE people (
      id INTEGER PRIMARY KEY,
      name TEXT,
      hidden INTEGER DEFAULT 0,
      cover_face_id INTEGER
    );
    CREATE TABLE faces (
      id INTEGER PRIMARY KEY,
      file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
      bbox TEXT NOT NULL,
      det_score REAL NOT NULL,
      embedding BLOB NOT NULL,
      person_id INTEGER REFERENCES people(id),
      assign_source TEXT,
      assign_score REAL
    );
    CREATE INDEX faces_person ON faces(person_id);
    CREATE INDEX faces_file ON faces(file_id);
    CREATE TABLE face_negatives (
      face_id INTEGER NOT NULL REFERENCES faces(id) ON DELETE CASCADE,
      person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
      PRIMARY KEY (face_id, person_id)
    );
    CREATE TABLE clip_embeddings (
      file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
      embedding BLOB NOT NULL
    );
    """,
    """
    ALTER TABLE files ADD COLUMN lat REAL;
    ALTER TABLE files ADD COLUMN lon REAL;
    ALTER TABLE files ADD COLUMN city TEXT;
    ALTER TABLE files ADD COLUMN state TEXT;
    ALTER TABLE files ADD COLUMN country TEXT;
    CREATE INDEX files_place ON files(country, state, city);
    """,
)


def connect(path: Path, *, cross_thread: bool = False) -> sqlite3.Connection:
    """`cross_thread`: the connection may be closed by another thread than the one that
    opened it (FastAPI runs a dependency's setup and teardown on different pool threads).
    It must still be used by one thread at a time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=not cross_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> None:
    for version in range(schema_version(conn), len(MIGRATIONS)):
        # executescript commits first; the version bump rides in the same script.
        conn.executescript(
            f"BEGIN; {MIGRATIONS[version]} PRAGMA user_version = {version + 1}; COMMIT;"
        )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
