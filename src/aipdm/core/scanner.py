"""Folder scan, change detection and the per-file processing pipeline.

Only the main process writes to SQLite; workers return results. The indexed folder
is only ever opened for reading.
"""

import logging
import os
import signal
import sqlite3
import time
import zipfile
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from itertools import batched
from multiprocessing import get_context
from pathlib import Path

import xxhash

from aipdm.core import db
from aipdm.core.dates import resolve_date
from aipdm.core.documents import read_docx, read_pdf
from aipdm.core.images import read_image
from aipdm.core.paths import thumbs_dir_for

log = logging.getLogger(__name__)

IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "heic", "heif", "bmp"})
STAGES = "date,thumb,text"
BATCH_SIZE = 64
HASH_CHUNK = 1 << 20


HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"})


def kind_of(path: Path) -> str:
    if "." not in path.name:
        return sniff_kind(path)
    ext = path.name.rsplit(".", 1)[-1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    return ext if ext in ("pdf", "docx") else "other"


def sniff_kind(path: Path) -> str:
    """Kind from the file header, for names without an extension (e.g. WhatsApp 'Sent')."""
    try:
        with path.open("rb") as fh:
            head = fh.read(12)
            if head.startswith(b"%PDF-"):
                return "pdf"
            if (
                head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"BM"))
                or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
                or (head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS)
            ):
                return "image"
            if head.startswith(b"PK\x03\x04"):
                fh.seek(0)
                with zipfile.ZipFile(fh) as archive:
                    if "word/document.xml" in archive.namelist():
                        return "docx"
    except (OSError, zipfile.BadZipFile):
        pass
    return "other"


def is_sticker(name: str) -> bool:
    return name.startswith("STK-") and name.lower().endswith(".webp")


def walk(root: Path) -> Iterator[tuple[str, int, float]]:
    """Yield (posix rel_path, size, mtime) of regular files.

    Symlinks are not followed and hidden folders (".Links", ".Statuses"...) are skipped.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            log.warning("pasta ilegível %s: %s", current, exc)
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if not entry.name.startswith("."):
                    stack.append(Path(entry.path))
            elif entry.is_file():
                st = entry.stat()
                rel = Path(entry.path).relative_to(root).as_posix()
                yield rel, st.st_size, st.st_mtime


@dataclass
class ScanStats:
    new: int = 0
    changed: int = 0
    missing: int = 0
    reappeared: int = 0
    unchanged: int = 0
    ignored: int = 0


def in_hidden_folder(rel_path: str) -> bool:
    return any(part.startswith(".") for part in rel_path.split("/")[:-1])


def scan(conn: sqlite3.Connection, root: Path, *, force: bool = False) -> ScanStats:
    """Sync the files table with the folder. New/changed files become 'pending'."""
    stats = ScanStats()
    known = {
        row["rel_path"]: row
        for row in conn.execute(
            "SELECT id, rel_path, kind, size, mtime, status, error, stages_done FROM files"
        )
    }
    seen: set[str] = set()
    for rel, size, mtime in walk(root):
        seen.add(rel)
        row = known.get(rel)
        name = rel.rsplit("/", 1)[-1]
        kind = kind_of(root / rel)
        # "other" files are only listed (SPEC section 5), so they never go to the workers.
        fresh_status = "done" if kind == "other" else "pending"
        if row is None:
            stats.new += 1
            conn.execute(
                "INSERT INTO files (rel_path, kind, size, mtime, is_sticker, status)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (rel, kind, size, mtime, int(is_sticker(name)), fresh_status),
            )
        elif row["size"] != size or row["mtime"] != mtime or row["kind"] != kind:
            # A kind change on an unchanged file means the classification rules improved.
            stats.changed += 1
            conn.execute(
                "UPDATE files SET kind = ?, size = ?, mtime = ?, hash = NULL, status = ?,"
                " error = NULL, stages_done = '' WHERE id = ?",
                (kind, size, mtime, fresh_status, row["id"]),
            )
        elif row["status"] == "missing":
            stats.reappeared += 1
            if row["error"]:
                restored = "error"
            elif row["stages_done"] or kind == "other":
                restored = "done"
            else:
                restored = "pending"
            conn.execute("UPDATE files SET status = ? WHERE id = ?", (restored, row["id"]))
        else:
            stats.unchanged += 1
    # Rows indexed before hidden folders were skipped: drop them, they are not "missing".
    # ponytail: their thumbnails stay on disk; add a thumbs cleanup if the cache size matters.
    ignored = [(row["id"],) for rel, row in known.items() if in_hidden_folder(rel)]
    stats.ignored = len(ignored)
    conn.executemany("DELETE FROM texts WHERE file_id = ?", ignored)
    conn.executemany("DELETE FROM files WHERE id = ?", ignored)
    gone = [
        (row["id"],)
        for rel, row in known.items()
        if rel not in seen and row["status"] != "missing" and not in_hidden_folder(rel)
    ]
    stats.missing = len(gone)
    conn.executemany("UPDATE files SET status = 'missing' WHERE id = ?", gone)
    if force:
        conn.execute(
            "UPDATE files SET status = 'pending', error = NULL, stages_done = ''"
            " WHERE status IN ('done', 'error') AND kind != 'other'"
        )
    # Stale text of anything about to be (re)processed; one FTS scan instead of one per file.
    conn.execute(
        "DELETE FROM texts WHERE file_id IN (SELECT id FROM files WHERE status = 'pending')"
    )
    conn.commit()
    return stats


@dataclass(frozen=True)
class Task:
    file_id: int
    path: Path
    kind: str
    thumb_path: Path


@dataclass
class Result:
    file_id: int
    hash: str | None = None
    taken_at: str | None = None
    date_source: str | None = None
    width: int | None = None
    height: int | None = None
    pages: list[tuple[int, str]] = field(default_factory=list)
    error: str | None = None


def file_hash(path: Path) -> str:
    h = xxhash.xxh3_64()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def process(task: Task, today: date) -> Result:
    """Run all phase-1 stages for one file. Never raises: errors go into Result.error."""
    result = Result(task.file_id)
    try:
        st = task.path.stat()
        result.hash = file_hash(task.path)
        exif: str | None = None
        created: datetime | None = None
        if task.kind == "image":
            info = read_image(task.path, task.thumb_path)
            result.width, result.height, exif = info.width, info.height, info.exif_datetime
        elif task.kind == "pdf":
            doc = read_pdf(task.path, task.thumb_path)
            result.pages, created = doc.pages, doc.created
        elif task.kind == "docx":
            doc = read_docx(task.path)
            result.pages, created = doc.pages, doc.created
        result.taken_at, result.date_source = resolve_date(
            task.path.name, today=today, exif=exif, document=created, mtime=st.st_mtime
        )
    except Exception as exc:  # one bad file must not stop the run
        result.error = f"{type(exc).__name__}: {exc}"[:500]
    return result


def save(conn: sqlite3.Connection, results: list[Result]) -> None:
    for r in results:
        conn.execute(
            "UPDATE files SET hash = ?, taken_at = ?, date_source = ?, width = ?, height = ?,"
            " status = ?, error = ?, stages_done = ? WHERE id = ?",
            (
                r.hash,
                r.taken_at,
                r.date_source,
                r.width,
                r.height,
                "error" if r.error else "done",
                r.error,
                "" if r.error else STAGES,
                r.file_id,
            ),
        )
        conn.executemany(
            "INSERT INTO texts (content, file_id, page) VALUES (?, ?, ?)",
            [(text, r.file_id, page) for page, text in r.pages],
        )
    conn.commit()


@dataclass
class IndexStats:
    scan: ScanStats
    pending: int = 0
    processed: int = 0
    errors: int = 0
    seconds: float = 0.0
    interrupted: bool = False


def _ignore_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def index(
    root: Path,
    db_path: Path,
    *,
    workers: int,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> IndexStats:
    started = time.monotonic()
    root = root.resolve()
    conn = db.connect(db_path)
    try:
        db.set_meta(conn, "root_path", str(root))
        stats = IndexStats(scan(conn, root, force=force))
        thumbs = thumbs_dir_for(db_path)
        tasks = [
            Task(row["id"], root / row["rel_path"], row["kind"], thumbs / f"{row['id']}.jpg")
            for row in conn.execute("SELECT id, rel_path, kind FROM files WHERE status = 'pending'")
        ]
        stats.pending = len(tasks)
        work = partial(process, today=date.today())

        # Ctrl+C: finish the current batch, save it, stop cleanly.
        stop = False

        def request_stop(_sig: int, _frame: object) -> None:
            nonlocal stop
            stop = True

        previous = signal.signal(signal.SIGINT, request_stop)
        pool = (
            ProcessPoolExecutor(workers, get_context("spawn"), initializer=_ignore_sigint)
            if workers > 1
            else None
        )
        try:
            for batch in batched(tasks, BATCH_SIZE * max(workers, 1)):
                if stop:
                    break
                results = list(pool.map(work, batch) if pool else map(work, batch))
                save(conn, results)
                stats.processed += len(results)
                stats.errors += sum(1 for r in results if r.error)
                for r in results:
                    if r.error:
                        log.info("erro no arquivo id=%s: %s", r.file_id, r.error)
                if progress:
                    progress(stats.processed, stats.pending)
        finally:
            if pool:
                pool.shutdown(cancel_futures=True)
            signal.signal(signal.SIGINT, previous)
        stats.interrupted = stop and stats.processed < stats.pending
        stats.seconds = time.monotonic() - started
        db.set_meta(conn, "last_index_at", datetime.now().isoformat(timespec="seconds"))
        db.set_meta(conn, "last_index_seconds", f"{stats.seconds:.1f}")
        db.set_meta(conn, "last_index_processed", str(stats.processed))
        conn.commit()
        return stats
    finally:
        conn.close()
