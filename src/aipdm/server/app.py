"""Local HTTP API + static UI (SPEC section 12).

Security (RNF-8/9): every request needs the session token (cookie set by
`/?t=<token>`, or the X-Token header) and a Host header equal to the one we bound;
files are only ever served by database id, never by a client-supplied path.
"""

import hmac
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from aipdm.core import db, faces, paths, scanner
from aipdm.core import search as searching
from aipdm.core.documents import pdf_page_count, render_pdf_page
from aipdm.core.images import PREVIEW_MAX_SIDE, load_image, read_exif_summary, save_preview
from aipdm.core.paths import thumbs_dir_for

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
UI_FILES = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
COOKIE = "aipdm_token"
CROP_SIZE = 192


class PersonUpdate(BaseModel):
    name: str | None = None
    hidden: bool | None = None


class Merge(BaseModel):
    other_id: int


class Assign(BaseModel):
    person_id: int | None = None
    name: str | None = None  # new person when person_id is None


class OpenFolder(BaseModel):
    id: str | None = None  # a recent folder, by database id
    path: str | None = None  # a folder picked by the user (only ever indexed, never served)


class StartIndex(BaseModel):
    force: bool = False
    retry_errors: bool = False


DB_ID = re.compile(r"^[0-9a-f]{16}$")


@dataclass
class IndexJob:
    running: bool = True
    done: int = 0
    total: int = 0
    started: float = field(default_factory=time.time)
    seconds: float = 0.0
    summary: dict[str, object] | None = None
    error: str | None = None
    stop: threading.Event = field(default_factory=threading.Event, repr=False)


class Settings(BaseModel):
    t_auto: float
    t_suggest: float
    cluster_eps: float
    apply: bool = False  # regroup now (keeps names and user decisions)
    reset: bool = False  # back to the defaults


class MonitorSwitch(BaseModel):
    enabled: bool


@dataclass
class Monitor:
    """Periodic check of the open folder (SPEC backlog: "monitoramento da pasta")."""

    last_check: float | None = None
    changes: int = 0
    # Changes seen on the previous check. Indexing starts only when two checks in a row
    # see the same changes: the folder has settled (no copy/sync in progress).
    pending: frozenset[tuple[str, int, float]] | None = None


@dataclass
class State:
    db: Path | None
    job: IndexJob | None = None
    monitor: Monitor = field(default_factory=Monitor)
    encoder: searching.TextEncoder | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def has_default_app(path: Path) -> bool:
    """Linux: does xdg-open have an application for this file type?"""
    if not shutil.which("xdg-mime"):
        return True  # cannot tell; let xdg-open try
    try:
        mime = subprocess.run(
            ["xdg-mime", "query", "filetype", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
        app = subprocess.run(
            ["xdg-mime", "query", "default", mime],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return True
    return bool(mime and app)


def default_encoder() -> searching.TextEncoder | None:
    from aipdm.core.clip import ClipTextModel

    try:
        return ClipTextModel(paths.models_dir()).encode
    except Exception:  # models missing: search falls back to text only
        return None


def create_app(
    db_path: Path | None,
    token: str,
    allowed_host: str,
    *,
    encoder_factory: Callable[[], searching.TextEncoder | None] = default_encoder,
    index_only: frozenset[str] | None = None,
    workers: int | None = None,
    monitor_interval: float | None = 60.0,
) -> FastAPI:
    """`db_path` None: no folder yet (welcome screen). The keyword arguments exist for tests."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    state = State(db_path)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # A non-numeric id in the URL is a path-like probe: answer "not found", not 422.
        if any(err.get("loc", ("",))[0] == "path" for err in exc.errors()):
            return JSONResponse({"detail": "não encontrado"}, status_code=404)
        return JSONResponse({"detail": "requisição inválida"}, status_code=422)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") != allowed_host:
            return JSONResponse({"detail": "host inválido"}, status_code=400)
        if request.url.path == "/" and "t" in request.query_params:
            if not hmac.compare_digest(request.query_params["t"], token):
                return JSONResponse({"detail": "não autorizado"}, status_code=401)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
            return response
        sent = request.cookies.get(COOKIE) or request.headers.get("x-token") or ""
        if not hmac.compare_digest(sent, token):
            return JSONResponse({"detail": "não autorizado"}, status_code=401)
        return await call_next(request)

    def current_db() -> Path:
        if state.db is None:
            raise HTTPException(409, "nenhuma pasta aberta")
        return state.db

    def conn() -> Iterator[sqlite3.Connection]:
        c = db.connect(current_db(), cross_thread=True)
        try:
            yield c
        finally:
            c.close()

    Conn = Depends(conn)

    def root_of(c: sqlite3.Connection) -> Path:
        return Path(db.get_meta(c, "root_path") or "")

    def file_row(c: sqlite3.Connection, file_id: int) -> sqlite3.Row:
        row = c.execute(
            "SELECT id, rel_path, kind, hash, mtime FROM files"
            " WHERE id = ? AND status != 'missing'",
            (file_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404)
        return row

    for route, name in UI_FILES.items():

        def page(name: str = name) -> FileResponse:
            return FileResponse(UI_DIR / name, headers={"Cache-Control": "no-store"})

        app.get(route, include_in_schema=False)(page)

    @app.get("/api/status")
    def status() -> dict[str, object]:
        if state.db is None:
            return {"root": None}
        c = db.connect(state.db, cross_thread=True)
        try:
            return _status(c)
        finally:
            c.close()

    def _status(c: sqlite3.Connection) -> dict[str, object]:
        faces_n, people_n, named, suggested, orphans = c.execute(
            "SELECT (SELECT COUNT(*) FROM faces), (SELECT COUNT(*) FROM people),"
            " (SELECT COUNT(*) FROM people WHERE name IS NOT NULL),"
            " (SELECT COUNT(*) FROM faces WHERE assign_source = 'suggested'),"
            " (SELECT COUNT(*) FROM faces fa JOIN files f ON f.id = fa.file_id"
            "  WHERE fa.person_id IS NULL AND f.is_sticker = 0)"
        ).fetchone()
        return {
            "root": str(root_of(c)),
            "faces": faces_n,
            "people": people_n,
            "named": named,
            "suggested": suggested,
            "unassigned": orphans,
            "files": c.execute("SELECT COUNT(*) FROM files WHERE kind != 'other'").fetchone()[0],
            "errors": c.execute("SELECT COUNT(*) FROM files WHERE status = 'error'").fetchone()[0],
        }

    @app.get("/api/people")
    def people(hidden: bool = False, c: sqlite3.Connection = Conn) -> list[dict[str, object]]:
        rows = c.execute(
            "SELECT p.id, p.name, p.hidden, COALESCE(p.cover_face_id, MIN(fa.id)),"
            " SUM(fa.assign_source != 'suggested'), COUNT(DISTINCT fa.file_id)"
            " FROM people p JOIN faces fa ON fa.person_id = p.id"
            " WHERE p.hidden = ? OR ? GROUP BY p.id"
            " ORDER BY p.name IS NULL, p.name COLLATE NOCASE, 5 DESC",
            (0, int(hidden)),
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "hidden": bool(r[2]),
                "cover": r[3],
                "faces": r[4],
                "files": r[5],
            }
            for r in rows
        ]

    @app.get("/api/people/{person_id}")
    def person(person_id: int, c: sqlite3.Connection = Conn) -> dict[str, object]:
        row = c.execute("SELECT id, name, hidden FROM people WHERE id = ?", (person_id,)).fetchone()
        if row is None:
            raise HTTPException(404)
        face_rows = c.execute(
            "SELECT id, file_id, assign_source, assign_score FROM faces WHERE person_id = ?"
            " ORDER BY assign_source = 'suggested', assign_score IS NULL, assign_score, id",
            (person_id,),
        ).fetchall()
        return {
            "id": row[0],
            "name": row[1],
            "hidden": bool(row[2]),
            "faces": [{"id": f[0], "file": f[1], "source": f[2], "score": f[3]} for f in face_rows],
        }

    @app.post("/api/people/{person_id}")
    def update_person(
        person_id: int, body: PersonUpdate, c: sqlite3.Connection = Conn
    ) -> dict[str, str]:
        if body.name is not None:
            faces.name_person(c, person_id, body.name)
        if body.hidden is not None:
            faces.set_hidden(c, person_id, body.hidden)
        return {"ok": "sim"}

    @app.post("/api/people/{person_id}/merge")
    def merge(person_id: int, body: Merge, c: sqlite3.Connection = Conn) -> dict[str, str]:
        if body.other_id == person_id:
            raise HTTPException(400, "escolha outra pessoa")
        faces.merge_people(c, person_id, body.other_id)
        return {"ok": "sim"}

    @app.get("/api/suggestions")
    def suggestions(c: sqlite3.Connection = Conn) -> list[dict[str, object]]:
        rows = c.execute(
            "SELECT fa.id, fa.file_id, fa.assign_score, p.id, p.name,"
            " COALESCE(p.cover_face_id, (SELECT MIN(id) FROM faces WHERE person_id = p.id"
            "  AND assign_source != 'suggested'))"
            " FROM faces fa JOIN people p ON p.id = fa.person_id"
            " WHERE fa.assign_source = 'suggested' ORDER BY fa.assign_score DESC LIMIT 200"
        ).fetchall()
        return [
            {"face": r[0], "file": r[1], "score": r[2], "person": r[3], "name": r[4], "cover": r[5]}
            for r in rows
        ]

    @app.get("/api/faces/unassigned")
    def unassigned(
        offset: int = 0, limit: int = 200, c: sqlite3.Connection = Conn
    ) -> list[dict[str, object]]:
        rows = c.execute(
            "SELECT fa.id, fa.file_id FROM faces fa JOIN files f ON f.id = fa.file_id"
            " WHERE fa.person_id IS NULL AND f.is_sticker = 0 AND f.status != 'missing'"
            " ORDER BY fa.det_score DESC LIMIT ? OFFSET ?",
            (min(limit, 500), offset),
        ).fetchall()
        return [{"id": r[0], "file": r[1]} for r in rows]

    @app.post("/api/faces/{face_id}/assign")
    def assign(face_id: int, body: Assign, c: sqlite3.Connection = Conn) -> dict[str, object]:
        if body.person_id is not None:
            faces.assign_face(c, face_id, body.person_id)
            return {"person_id": body.person_id}
        if not (body.name or "").strip():
            raise HTTPException(400, "informe uma pessoa ou um nome")
        existing = c.execute(
            "SELECT id FROM people WHERE name = ? COLLATE NOCASE", ((body.name or "").strip(),)
        ).fetchone()
        if existing:
            faces.assign_face(c, face_id, existing[0])
            return {"person_id": existing[0]}
        return {"person_id": faces.assign_face_to_new_person(c, face_id, body.name or "")}

    @app.post("/api/faces/{face_id}/remove")
    def remove(face_id: int, c: sqlite3.Connection = Conn) -> dict[str, str]:
        faces.remove_face(c, face_id)
        return {"ok": "sim"}

    @app.get("/api/faces/{face_id}/crop")
    def crop(face_id: int, c: sqlite3.Connection = Conn) -> FileResponse:
        faces_dir = current_db().with_suffix(".faces")
        cached = faces_dir / f"{face_id}.jpg"
        if not cached.exists():
            row = c.execute(
                "SELECT fa.bbox, f.rel_path FROM faces fa JOIN files f ON f.id = fa.file_id"
                " WHERE fa.id = ? AND f.status != 'missing'",
                (face_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404)
            x, y, w, h = (int(v) for v in row[0].split(","))
            margin = max(w, h) // 3
            img = load_image(root_of(c) / row[1]).image
            face = img.crop((x - margin, y - margin, x + w + margin, y + h + margin))
            face.thumbnail((CROP_SIZE, CROP_SIZE))
            faces_dir.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: parallel requests for the same face never see half a file.
            tmp = cached.with_suffix(f".{threading.get_ident()}.tmp")
            face.save(tmp, "JPEG", quality=85)
            tmp.replace(cached)
        return FileResponse(cached, media_type="image/jpeg")

    @app.get("/api/files/{file_id}/thumb")
    def thumb(file_id: int, c: sqlite3.Connection = Conn) -> FileResponse:
        file_row(c, file_id)
        path = thumbs_dir_for(current_db()) / f"{file_id}.jpg"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/files/{file_id}/open")
    def open_file(file_id: int, c: sqlite3.Connection = Conn) -> dict[str, str]:
        path = root_of(c) / file_row(c, file_id)["rel_path"]
        no_app = HTTPException(409, "Nenhum programa do sistema abre este tipo de arquivo.")
        if sys.platform == "win32":
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except OSError as exc:  # no association for the extension
                raise no_app from exc
            return {"ok": "sim"}
        if not has_default_app(path):
            raise no_app
        subprocess.Popen(["xdg-open", str(path)], start_new_session=True)
        return {"ok": "sim"}

    # --- in-app viewer -----------------------------------------------------------

    @app.get("/api/files/{file_id}/view")
    def view(file_id: int, page: int = 1, c: sqlite3.Connection = Conn) -> FileResponse:
        """The file as a JPEG for the in-app viewer: photos of any format, PDF pages."""
        row = file_row(c, file_id)
        if row["kind"] not in ("image", "pdf"):
            raise HTTPException(404)
        page = max(1, page) if row["kind"] == "pdf" else 1
        version = row["hash"] or int(row["mtime"])  # a changed file gets a new preview
        cached = current_db().with_suffix(".previews") / f"{file_id}-{page}-{version}.jpg"
        if not cached.exists():
            path = root_of(c) / row["rel_path"]
            try:
                if row["kind"] == "image":
                    img = load_image(path).image
                else:
                    img = render_pdf_page(path, page, PREVIEW_MAX_SIDE)
            except IndexError as exc:
                raise HTTPException(404) from exc
            except Exception as exc:  # unreadable or protected file
                raise HTTPException(422, "não foi possível abrir este arquivo") from exc
            save_preview(img, cached)
        return FileResponse(cached, media_type="image/jpeg")

    @app.get("/api/files/{file_id}/info")
    def info(file_id: int, c: sqlite3.Connection = Conn) -> dict[str, object]:
        row = c.execute(
            "SELECT id, rel_path, kind, taken_at, date_source, city, state, country, lat, lon,"
            " width, height, size FROM files WHERE id = ? AND status != 'missing'",
            (file_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404)
        people_rows = c.execute(
            "SELECT p.id, p.name, MIN(fa.assign_source = 'auto'), MAX(fa.assign_score)"
            " FROM faces fa JOIN people p ON p.id = fa.person_id"
            " WHERE fa.file_id = ? AND fa.assign_source != 'suggested' AND p.name IS NOT NULL"
            " GROUP BY p.id ORDER BY p.name",
            (file_id,),
        ).fetchall()
        data: dict[str, object] = {
            "id": row["id"],
            "rel_path": row["rel_path"],
            "kind": row["kind"],
            "taken_at": row["taken_at"],
            "place": ", ".join(p for p in (row["city"], row["state"], row["country"]) if p) or None,
            "width": row["width"],
            "height": row["height"],
            "size": row["size"],
            # ai: identified by the app against already-named people, not confirmed by the user
            "people": [
                {"id": r[0], "name": r[1], "ai": bool(r[2]), "score": r[3]} for r in people_rows
            ],
            "date_source": row["date_source"],
            "lat": row["lat"],
            "lon": row["lon"],
        }
        path = root_of(c) / row["rel_path"]
        if row["kind"] == "image":
            try:
                data["format"], data["exif"] = read_exif_summary(path)
            except Exception:  # unreadable header: show what the database knows
                data["format"], data["exif"] = None, []
        if row["kind"] == "pdf":
            try:
                data["pages"] = pdf_page_count(path)
            except Exception:
                data["pages"] = 0
        if row["kind"] == "docx":
            texts = c.execute(
                "SELECT content FROM texts WHERE file_id = ? AND page >= 1 ORDER BY page",
                (file_id,),
            ).fetchall()
            data["text"] = "\n\n".join(t[0] for t in texts)[:100_000]
        return data

    # --- search -------------------------------------------------------------

    @app.get("/api/search")
    def search(
        text: str = "",
        person: list[int] = Query(default=[]),  # noqa: B008
        date_from: str | None = None,
        date_to: str | None = None,
        kind: list[str] = Query(default=[]),  # noqa: B008
        stickers: bool = False,
        order: str = "relevance",
        limit: int = 120,
        offset: int = 0,
        country: str | None = None,
        region: str | None = Query(default=None, alias="state"),  # "state" is taken below
        city: str | None = None,
        c: sqlite3.Connection = Conn,
    ) -> dict[str, object]:
        if text.strip():
            with state.lock:  # load the CLIP text model once, on first use
                if state.encoder is None:
                    state.encoder = encoder_factory()
        query = searching.Query(
            text=text,
            people_ids=tuple(person),
            date_from=date_from or None,
            date_to=date_to or None,
            kinds=tuple(k for k in kind if k in ("image", "pdf", "docx")),
            stickers=stickers,
            order="date" if order == "date" else "relevance",
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
            country=country,
            state=region,
            city=city,
        )
        results = searching.search(c, query, state.encoder)
        return {
            "total": results.total,
            "without_text": results.without_text,
            "person": results.person,
            "hits": [asdict(h) for h in results.hits],
            "mentions": [asdict(h) for h in results.mentions],
        }

    @app.get("/api/tree")
    def tree(
        person: list[int] = Query(default=[]),  # noqa: B008
        stickers: bool = False,
        c: sqlite3.Connection = Conn,
    ) -> dict[str, object]:
        """Photo counts by year and month (optionally for some people), for browsing."""
        counts = searching.date_counts(
            c, searching.Query(kinds=("image",), stickers=stickers), tuple(person)
        )
        years: dict[str, dict[str, object]] = {}
        undated = 0
        for ym, n in counts:
            if ym is None:
                undated = n
                continue
            year = years.setdefault(ym[:4], {"year": ym[:4], "count": 0, "months": []})
            year["count"] = int(year["count"]) + n  # type: ignore[call-overload]
            year["months"].append({"month": ym, "count": n})  # type: ignore[attr-defined]
        return {
            "total": sum(n for _, n in counts),
            "undated": undated,
            "years": list(years.values()),
        }

    @app.get("/api/places")
    def places(stickers: bool = False, c: sqlite3.Connection = Conn) -> list[dict[str, Any]]:
        """Located photos as a country > state > city tree with counts, biggest first."""
        query = searching.Query(kinds=("image",), stickers=stickers)
        countries: list[dict[str, Any]] = []
        for country, state, city, n in searching.place_counts(c, query):  # sorted by place
            if not countries or countries[-1]["country"] != country:
                countries.append({"country": country, "count": 0, "states": []})
            states = countries[-1]["states"]
            if not states or states[-1]["state"] != state:
                states.append({"state": state, "count": 0, "cities": []})
            countries[-1]["count"] += n
            states[-1]["count"] += n
            states[-1]["cities"].append({"city": city, "count": n})
        for node in countries:
            node["states"].sort(key=lambda x: -x["count"])
            for st in node["states"]:
                st["cities"].sort(key=lambda x: -x["count"])
        return sorted(countries, key=lambda x: -x["count"])

    # --- folders and indexing -------------------------------------------------

    def folder_info(path: Path) -> dict[str, object]:
        c = db.connect(path, cross_thread=True)
        try:
            return {
                "id": path.stem,
                "root": db.get_meta(c, "root_path"),
                "last_index": db.get_meta(c, "last_index_at"),
                "files": c.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                "current": path == state.db,
            }
        finally:
            c.close()

    @app.get("/api/folders")
    def folders() -> list[dict[str, object]]:
        found = sorted(paths.data_dir().glob("*.sqlite"), key=lambda p: -p.stat().st_mtime)
        return [folder_info(p) for p in found if DB_ID.match(p.stem)]

    @app.post("/api/folders/open")
    def open_folder(body: OpenFolder) -> dict[str, object]:
        with state.lock:
            if state.job and state.job.running:
                raise HTTPException(409, "aguarde a indexação atual terminar")
            state.monitor = Monitor()  # pending changes belonged to the previous folder
            if body.id is not None:
                if (
                    not DB_ID.match(body.id)
                    or not (paths.data_dir() / f"{body.id}.sqlite").exists()
                ):
                    raise HTTPException(404)
                state.db = paths.data_dir() / f"{body.id}.sqlite"
                return {**folder_info(state.db), "new": False}
            folder = Path(body.path or "").expanduser()
            if not body.path or not folder.is_dir():
                raise HTTPException(400, "pasta não encontrada")
            state.db = paths.db_path_for(folder)
            new = not state.db.exists()
            if new:
                _start_index(folder.resolve(), force=False)
            return {"id": state.db.stem, "root": str(folder.resolve()), "new": new}

    def _start_index(root: Path, *, force: bool, retry_errors: bool = False) -> IndexJob:
        job = IndexJob()
        target = current_db()

        def progress(done: int, total: int) -> None:
            job.done, job.total = done, total

        def run() -> None:
            try:
                stats = scanner.index(
                    root,
                    target,
                    workers=workers or scanner.default_workers(),
                    force=force,
                    retry_errors=retry_errors,
                    only=index_only,
                    progress=progress,
                    stop=job.stop,
                )
                job.summary = {
                    "scan": asdict(stats.scan),
                    "processed": stats.processed,
                    "pending": stats.pending,
                    "errors": stats.errors,
                    "stage_seconds": stats.stage_seconds,
                    "grouping": asdict(stats.grouping) if stats.grouping else None,
                    "interrupted": stats.interrupted,
                }
            except Exception as exc:
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.seconds = time.time() - job.started
                job.running = False

        state.job = job
        threading.Thread(target=run, daemon=True, name="aipdm-index").start()
        return job

    @app.post("/api/index")
    def start_index(body: StartIndex, c: sqlite3.Connection = Conn) -> dict[str, object]:
        with state.lock:
            if state.job and state.job.running:
                raise HTTPException(409, "já existe uma indexação em andamento")
            root = root_of(c)
            if not root.is_dir():
                raise HTTPException(400, "a pasta não está acessível")
            _start_index(root, force=body.force, retry_errors=body.retry_errors)
        return index_status()

    @app.post("/api/index/cancel")
    def cancel_index() -> dict[str, object]:
        if state.job and state.job.running:
            state.job.stop.set()
        return index_status()

    @app.get("/api/index")
    def index_status() -> dict[str, object]:
        job = state.job
        if job is None:
            return {"running": False}
        data: dict[str, object] = {
            "running": job.running,
            "done": job.done,
            "total": job.total,
            "seconds": job.seconds,
            "summary": job.summary,
            "error": job.error,
        }
        if job.running:
            data["seconds"] = time.time() - job.started
        return data

    # --- settings -------------------------------------------------------------

    def settings_view(current: faces.FaceSettings) -> dict[str, object]:
        default = faces.FaceSettings()
        keys = ("t_auto", "t_suggest", "cluster_eps")
        return {
            **{k: getattr(current, k) for k in keys},
            "defaults": {k: getattr(default, k) for k in keys},
            "ranges": faces.SETTINGS_RANGES,
        }

    @app.get("/api/settings")
    def get_settings(c: sqlite3.Connection = Conn) -> dict[str, object]:
        return settings_view(faces.load_settings(c))

    @app.post("/api/settings")
    def set_settings(body: Settings, c: sqlite3.Connection = Conn) -> dict[str, object]:
        if body.reset:
            current = faces.reset_settings(c)
        else:
            try:
                current = faces.save_settings(
                    c, t_auto=body.t_auto, t_suggest=body.t_suggest, cluster_eps=body.cluster_eps
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        result = settings_view(current)
        if body.apply:
            with state.lock:
                if state.job and state.job.running:
                    raise HTTPException(409, "aguarde a indexação terminar para reagrupar")
                faces.reset_groups(c)
                result["grouping"] = asdict(faces.group_faces(c, current))
        return result

    # --- folder monitoring ----------------------------------------------------

    def monitor_enabled(c: sqlite3.Connection) -> bool:
        return db.get_meta(c, "monitor") != "0"  # on unless the user turned it off

    def monitor_tick() -> None:
        """One check; the background thread calls it every `monitor_interval` seconds."""
        target, mon = state.db, state.monitor
        if target is None or (state.job and state.job.running):
            return
        c = db.connect(target, cross_thread=True)
        try:
            root = root_of(c)
            if not monitor_enabled(c) or not root.is_dir():
                return
            changes = scanner.pending_changes(c, root)
        finally:
            c.close()
        mon.last_check, mon.changes = time.time(), len(changes)
        if not changes:
            mon.pending = None
        elif changes == mon.pending:
            with state.lock:
                if state.db == target and not (state.job and state.job.running):
                    _start_index(root, force=False)
            mon.pending = None
        else:
            mon.pending = changes

    app.state.monitor_tick = monitor_tick  # tests drive it without the thread
    app.state.monitor_stop = stop_monitor = threading.Event()

    if monitor_interval:

        def monitor_loop() -> None:
            while not stop_monitor.wait(monitor_interval):
                try:
                    monitor_tick()
                except Exception:  # a failed check must not kill monitoring
                    log.exception("falha ao verificar a pasta")

        threading.Thread(target=monitor_loop, daemon=True, name="aipdm-monitor").start()

    @app.get("/api/monitor")
    def monitor(c: sqlite3.Connection = Conn) -> dict[str, object]:
        mon = state.monitor
        return {
            "enabled": monitor_enabled(c),
            "interval": monitor_interval,
            "last_check": mon.last_check,
            "changes": mon.changes,
            "waiting": mon.pending is not None,
        }

    @app.post("/api/monitor")
    def set_monitor(body: MonitorSwitch, c: sqlite3.Connection = Conn) -> dict[str, object]:
        db.set_meta(c, "monitor", "1" if body.enabled else "0")
        c.commit()
        return monitor(c)

    return app
