"""Folder scan, change detection and the per-file processing pipeline.

Only the main process writes to SQLite; workers return results. The indexed folder
is only ever opened for reading.
"""

import logging
import os
import signal
import sqlite3
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache, partial
from itertools import batched
from multiprocessing import get_context
from pathlib import Path

import cv2
import numpy as np
import xxhash
from numpy.typing import NDArray

from aipdm.core import db
from aipdm.core.clip import ClipImageModel, preprocess
from aipdm.core.dates import resolve_date
from aipdm.core.documents import ocr_pdf, read_docx, read_pdf
from aipdm.core.faces import (
    DetectedFace,
    FaceModel,
    FaceSettings,
    GroupingStats,
    group_faces,
    load_settings,
)
from aipdm.core.images import load_image, read_gps, save_thumbnail, working_copy
from aipdm.core.ocr import OcrModel
from aipdm.core.paths import models_dir, thumbs_dir_for
from aipdm.core.places import PLACES_FILE, resolve_places

log = logging.getLogger(__name__)

IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "heic", "heif", "bmp"})
# Stage order per kind. date/thumb/text are cheap and always run together.
KIND_STAGES = {
    "image": ("date", "gps", "thumb", "faces", "clip", "ocr"),
    "pdf": ("date", "thumb", "text", "ocr"),
    "docx": ("date", "text"),
}
BASE_STAGES = frozenset({"date", "gps", "thumb", "text"})
PIXEL_STAGES = frozenset({"date", "thumb", "faces", "clip", "ocr"})  # need the decoded image
HEAVY_STAGES = ("faces", "clip", "ocr")
STICKER_SKIPS = frozenset({"faces", "ocr"})
IN_FLIGHT_PER_WORKER = 2  # queued chunks per worker; bounds memory and Ctrl+C latency
CHUNK_SIZE = 8  # files per worker call; CLIP runs batched inside a chunk
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


def pending_changes(conn: sqlite3.Connection, root: Path) -> frozenset[tuple[str, int, float]]:
    """Read-only: files that `scan` would pick up (new, changed, gone, back).

    Returns their (rel_path, size, mtime) — gone files as (rel_path, -1, 0). Comparing two
    results tells whether the folder is still changing (a copy in progress).
    """
    known = {
        rel: (size, mtime, status)
        for rel, size, mtime, status in conn.execute(
            "SELECT rel_path, size, mtime, status FROM files"
        )
    }
    changes: set[tuple[str, int, float]] = set()
    seen: set[str] = set()
    for rel, size, mtime in walk(root):
        seen.add(rel)
        row = known.get(rel)
        if row is None or row[0] != size or row[1] != mtime or row[2] == "missing":
            changes.add((rel, size, mtime))
    for rel, (_, _, status) in known.items():
        if rel not in seen and status != "missing" and not in_hidden_folder(rel):
            changes.add((rel, -1, 0.0))
    return frozenset(changes)


def scan(
    conn: sqlite3.Connection, root: Path, *, force: bool = False, retry_errors: bool = False
) -> ScanStats:
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
                " error = NULL, stages_done = '', lat = NULL, lon = NULL, city = NULL,"
                " state = NULL, country = NULL WHERE id = ?",
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
    if retry_errors:  # e.g. after an update that fixes what made them fail
        conn.execute(
            "UPDATE files SET status = 'pending', error = NULL, stages_done = ''"
            " WHERE status = 'error'"
        )
    if force:
        conn.execute(
            "UPDATE files SET status = 'pending', error = NULL, stages_done = '', lat = NULL,"
            " lon = NULL, city = NULL, state = NULL, country = NULL"
            " WHERE status IN ('done', 'error') AND kind != 'other'"
        )
    # Stale text of anything about to be (re)processed; one FTS scan instead of one per file.
    conn.execute(
        "DELETE FROM texts WHERE file_id IN (SELECT id FROM files WHERE status = 'pending')"
    )
    conn.commit()
    return stats


def required_stages(kind: str, sticker: bool, only: frozenset[str] | None) -> tuple[str, ...]:
    stages = KIND_STAGES.get(kind, ())
    return tuple(
        s
        for s in stages
        if (s in BASE_STAGES or only is None or s in only) and not (sticker and s in STICKER_SKIPS)
    )


@dataclass(frozen=True)
class Task:
    file_id: int
    path: Path
    kind: str
    thumb_path: Path
    stages: tuple[str, ...]  # to run now
    done: tuple[str, ...]  # already done before this run


@dataclass
class Result:
    file_id: int
    stages: tuple[str, ...] = ()  # completed stages, cumulative
    base_ran: bool = False  # hash + date were computed
    gps: tuple[float, float] | None = None
    gps_ran: bool = False
    hash: str | None = None
    taken_at: str | None = None
    date_source: str | None = None
    width: int | None = None
    height: int | None = None
    pages: list[tuple[int, str]] = field(default_factory=list)  # text layer + OCR
    faces: list[DetectedFace] | None = None  # None: faces stage did not run
    clip: bytes | None = None
    seconds: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class WorkerConfig:
    models: Path
    threads: int  # per inference session; 0 = library default
    faces: FaceSettings
    today: date


@dataclass(frozen=True)
class Models:
    faces: FaceModel
    clip: ClipImageModel
    ocr: OcrModel


@cache
def load_models(models: Path, threads: int, settings: FaceSettings) -> Models:
    """Once per worker process: models are big, and loading them dominates small batches."""
    if threads:
        cv2.setNumThreads(threads)
    return Models(
        FaceModel(models, settings), ClipImageModel(models, threads), OcrModel(models, threads)
    )


def file_hash(path: Path) -> str:
    h = xxhash.xxh3_64()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


class _Timer:
    def __init__(self, result: Result, stage: str) -> None:
        self.result, self.stage = result, stage

    def __enter__(self) -> None:
        self.start = time.perf_counter()

    def __exit__(self, *_: object) -> None:
        elapsed = time.perf_counter() - self.start
        self.result.seconds[self.stage] = self.result.seconds.get(self.stage, 0.0) + elapsed


def _process_one(
    task: Task, config: WorkerConfig, clip_inputs: list[tuple[Result, NDArray[np.float32]]]
) -> Result:
    result = Result(task.file_id)
    todo = set(task.stages)
    heavy = todo & set(HEAVY_STAGES)
    models = load_models(config.models, config.threads, config.faces) if heavy else None
    exif: str | None = None
    created: datetime | None = None
    if "date" in todo:
        result.base_ran = True
        with _Timer(result, "hash"):
            result.hash = file_hash(task.path)
    if task.kind == "image" and not todo & PIXEL_STAGES:
        # Only location is missing (folder indexed before GPS existed): header only.
        with _Timer(result, "gps"):
            result.gps, result.gps_ran = read_gps(task.path), True
    elif task.kind == "image":
        with _Timer(result, "decode"):
            loaded = load_image(task.path)
        img, exif = loaded.image, loaded.exif_datetime
        result.width, result.height = img.size
        if "gps" in todo:
            result.gps, result.gps_ran = loaded.gps, True
        if "thumb" in todo:
            with _Timer(result, "thumb"):
                save_thumbnail(img, task.thumb_path)
        if models and heavy & {"faces", "ocr"}:
            work, scale = working_copy(img)
            rgb = np.asarray(work)
            if "faces" in todo:
                with _Timer(result, "faces"):
                    result.faces = models.faces.detect(rgb, scale)
            if "ocr" in todo:
                with _Timer(result, "ocr"):
                    if text := models.ocr.read(rgb):
                        result.pages.append((1, text))
        if "clip" in todo:
            with _Timer(result, "clip"):
                clip_inputs.append((result, preprocess(img)))
    elif task.kind == "pdf":
        if todo & {"text", "thumb"}:
            with _Timer(result, "text"):
                doc = read_pdf(task.path, task.thumb_path)
            result.pages, created = doc.pages, doc.created
        if models and "ocr" in todo:
            with _Timer(result, "ocr"):
                result.pages += ocr_pdf(task.path, models.ocr.read)
    elif task.kind == "docx":
        with _Timer(result, "text"):
            docx_info = read_docx(task.path)
        result.pages, created = docx_info.pages, docx_info.created
    if "date" in todo:
        result.taken_at, result.date_source = resolve_date(
            task.path.name,
            today=config.today,
            exif=exif,
            document=created,
            mtime=task.path.stat().st_mtime,
        )
    return result


def process_chunk(tasks: tuple[Task, ...], config: WorkerConfig) -> list[Result]:
    """Worker entry point. Never raises: per-file errors go into Result.error."""
    results: list[Result] = []
    clip_inputs: list[tuple[Result, NDArray[np.float32]]] = []
    for task in tasks:
        try:
            result = _process_one(task, config, clip_inputs)
        except Exception as exc:  # one bad file must not stop the run
            result = Result(task.file_id, error=f"{type(exc).__name__}: {exc}"[:500])
            clip_inputs = [(r, x) for r, x in clip_inputs if r.file_id != task.file_id]
        results.append(result)
    if clip_inputs:
        models = load_models(config.models, config.threads, config.faces)
        started = time.perf_counter()
        try:
            embeddings = models.clip.encode(np.stack([x for _, x in clip_inputs]))
            for (result, _), emb in zip(clip_inputs, embeddings, strict=True):
                result.clip = emb.astype(np.float32).tobytes()
        except Exception as exc:
            for result, _ in clip_inputs:
                result.error = f"clip: {type(exc).__name__}: {exc}"[:500]
        share = (time.perf_counter() - started) / len(clip_inputs)
        for result, _ in clip_inputs:
            result.seconds["clip"] = result.seconds.get("clip", 0.0) + share
    by_id = {t.file_id: t for t in tasks}
    for result in results:
        task = by_id[result.file_id]
        if not result.error:
            ran = set(task.stages) | set(task.done)
            result.stages = tuple(s for s in KIND_STAGES[task.kind] if s in ran)
    return results


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - w * h
    return w * h / union if union else 0.0


def _save_faces(conn: sqlite3.Connection, file_id: int, faces: list[DetectedFace]) -> None:
    """Replace a file's faces, carrying user decisions over to the matching new box.

    Hand-marked faces the detector still misses are kept as they are.
    """
    kept = [
        (tuple(int(v) for v in bbox.split(",")), person_id)
        for bbox, person_id in conn.execute(
            "SELECT bbox, person_id FROM faces WHERE file_id = ? AND assign_source = 'user'",
            (file_id,),
        )
    ]
    manual = conn.execute(
        "SELECT bbox, det_score, embedding, person_id, assign_source FROM faces"
        " WHERE file_id = ? AND manual = 1",
        (file_id,),
    ).fetchall()
    conn.execute("DELETE FROM faces WHERE file_id = ?", (file_id,))
    for row in manual:
        box = tuple(int(v) for v in row[0].split(","))
        if not any(_iou(face.bbox, box) > 0.5 for face in faces):  # type: ignore[arg-type]
            conn.execute(
                "INSERT INTO faces (file_id, bbox, det_score, embedding, person_id,"
                " assign_source, manual) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (file_id, *row),
            )
    for face in faces:
        person, source = None, None
        for bbox, person_id in kept:
            if _iou(face.bbox, bbox) > 0.5:  # type: ignore[arg-type]
                person, source = person_id, "user"
        conn.execute(
            "INSERT INTO faces (file_id, bbox, det_score, embedding, person_id, assign_source)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                file_id,
                ",".join(map(str, face.bbox)),
                face.score,
                face.embedding.tobytes(),
                person,
                source,
            ),
        )


def save(conn: sqlite3.Connection, results: list[Result]) -> None:
    for r in results:
        if r.error:
            conn.execute(
                "UPDATE files SET status = 'error', error = ? WHERE id = ?", (r.error, r.file_id)
            )
            continue
        if r.base_ran:
            conn.execute(
                "UPDATE files SET hash = ?, taken_at = ?, date_source = ? WHERE id = ?",
                (r.hash, r.taken_at, r.date_source, r.file_id),
            )
        if r.gps_ran:
            lat, lon = r.gps or (None, None)
            conn.execute(
                "UPDATE files SET lat = ?, lon = ?, city = NULL, state = NULL, country = NULL"
                " WHERE id = ?",
                (lat, lon, r.file_id),
            )
        if r.width:
            conn.execute(
                "UPDATE files SET width = ?, height = ? WHERE id = ?",
                (r.width, r.height, r.file_id),
            )
        conn.execute(
            "UPDATE files SET status = 'done', error = NULL, stages_done = ? WHERE id = ?",
            (",".join(r.stages), r.file_id),
        )
        conn.executemany(
            "INSERT INTO texts (content, file_id, page) VALUES (?, ?, ?)",
            [(text, r.file_id, page) for page, text in r.pages],
        )
        if r.faces is not None:
            _save_faces(conn, r.file_id, r.faces)
        if r.clip is not None:
            conn.execute(
                "INSERT OR REPLACE INTO clip_embeddings (file_id, embedding) VALUES (?, ?)",
                (r.file_id, r.clip),
            )
    conn.commit()


@dataclass
class IndexStats:
    scan: ScanStats
    pending: int = 0
    processed: int = 0
    errors: int = 0
    seconds: float = 0.0
    stage_seconds: dict[str, float] = field(default_factory=dict)  # summed over workers
    grouping: GroupingStats | None = None
    places: int = 0  # files whose place was named in this run
    interrupted: bool = False


def default_workers() -> int:
    # ponytail: capped at 8 because each worker holds ~0.5 GB of models; make it
    # RAM-aware if machines with many cores but little memory show up.
    return max(1, min((os.cpu_count() or 2) - 1, 8))


def _ignore_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def collect_tasks(
    conn: sqlite3.Connection, root: Path, thumbs: Path, only: frozenset[str] | None
) -> list[Task]:
    """Pending files, plus done files missing a stage (e.g. a database from phase 1)."""
    tasks = []
    for row in conn.execute(
        "SELECT id, rel_path, kind, is_sticker, status, stages_done FROM files"
        " WHERE status IN ('pending', 'done') AND kind != 'other'"
    ):
        done = tuple(s for s in (row["stages_done"] or "").split(",") if s)
        if row["status"] == "pending":
            done = ()
        todo = tuple(
            s for s in required_stages(row["kind"], bool(row["is_sticker"]), only) if s not in done
        )
        if todo:
            tasks.append(
                Task(
                    row["id"],
                    root / row["rel_path"],
                    row["kind"],
                    thumbs / f"{row['id']}.jpg",
                    todo,
                    done,
                )
            )
    return tasks


def index(
    root: Path,
    db_path: Path,
    *,
    workers: int,
    force: bool = False,
    retry_errors: bool = False,
    only: frozenset[str] | None = None,
    face_settings: FaceSettings | None = None,
    models: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    stop: threading.Event | None = None,
) -> IndexStats:
    """`stop`: set it (e.g. from the UI) to finish the chunks in flight and return early.
    On the main thread, Ctrl+C sets it too."""
    started = time.monotonic()
    root = root.resolve()
    conn = db.connect(db_path)
    settings = face_settings or load_settings(conn)  # the folder's Configurações
    try:
        db.set_meta(conn, "root_path", str(root))
        stats = IndexStats(scan(conn, root, force=force, retry_errors=retry_errors))
        tasks = collect_tasks(conn, root, thumbs_dir_for(db_path), only)
        stats.pending = len(tasks)
        config = WorkerConfig(
            models or models_dir(), 1 if workers > 1 else 0, settings, date.today()
        )
        work = partial(process_chunk, config=config)

        # Ctrl+C: finish the chunks in flight, save them, stop cleanly.
        stop = stop or threading.Event()
        on_main_thread = threading.current_thread() is threading.main_thread()
        previous = (
            signal.signal(signal.SIGINT, lambda _sig, _frame: stop.set())
            if on_main_thread
            else None
        )
        pool = (
            ProcessPoolExecutor(workers, get_context("spawn"), initializer=_ignore_sigint)
            if workers > 1
            else None
        )
        try:
            chunks = iter(batched(tasks, CHUNK_SIZE))

            def handle(results: list[Result]) -> None:
                save(conn, results)  # commits: every finished chunk is durable
                stats.processed += len(results)
                for r in results:
                    for stage, sec in r.seconds.items():
                        stats.stage_seconds[stage] = stats.stage_seconds.get(stage, 0.0) + sec
                    if r.error:
                        stats.errors += 1
                        log.info("erro no arquivo id=%s: %s", r.file_id, r.error)
                if progress:
                    progress(stats.processed, stats.pending)

            if pool is None:
                for chunk in chunks:
                    if stop.is_set():
                        break
                    handle(work(chunk))
            else:
                # Stream chunks with a bounded queue, so one slow file (a long scanned
                # PDF) never leaves the other workers idle waiting for a batch to end.
                in_flight: set[Future[list[Result]]] = set()
                while True:
                    while not stop.is_set() and len(in_flight) < IN_FLIGHT_PER_WORKER * workers:
                        queued = next(chunks, None)
                        if queued is None:
                            break
                        in_flight.add(pool.submit(work, queued))
                    if not in_flight:
                        break
                    finished, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                    for future in finished:
                        handle(future.result())
        finally:
            if pool:
                pool.shutdown(cancel_futures=True)
            if on_main_thread:
                signal.signal(signal.SIGINT, previous)
        stats.interrupted = stop.is_set() and stats.processed < stats.pending
        places_started = time.perf_counter()
        stats.places = resolve_places(conn, (models or models_dir()) / PLACES_FILE)
        if stats.places:
            stats.stage_seconds["places"] = time.perf_counter() - places_started
        if not stats.interrupted and (only is None or "faces" in only):
            grouping_started = time.perf_counter()
            stats.grouping = group_faces(conn, settings)
            stats.stage_seconds["grouping"] = time.perf_counter() - grouping_started
        stats.seconds = time.monotonic() - started
        db.set_meta(conn, "last_index_at", datetime.now().isoformat(timespec="seconds"))
        db.set_meta(conn, "last_index_seconds", f"{stats.seconds:.1f}")
        db.set_meta(conn, "last_index_processed", str(stats.processed))
        conn.commit()
        return stats
    finally:
        conn.close()
