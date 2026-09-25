"""Local HTTP API + static UI (SPEC section 12).

Security (RNF-8/9): every request needs the session token (cookie set by
`/?t=<token>`, or the X-Token header) and a Host header equal to the one we bound;
files are only ever served by database id, never by a client-supplied path.
"""

import hmac
import os
import sqlite3
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from aipdm.core import db, faces
from aipdm.core.images import load_image
from aipdm.core.paths import thumbs_dir_for

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


def create_app(db_path: Path, token: str, allowed_host: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    faces_dir = db_path.with_suffix(".faces")

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # A non-numeric id in the URL is a path-like probe: answer "not found", not 422.
        if any(err.get("loc", ("",))[0] == "path" for err in exc.errors()):
            return JSONResponse({"detail": "não encontrado"}, status_code=404)
        return JSONResponse({"detail": "requisição inválida"}, status_code=422)

    thumbs_dir = thumbs_dir_for(db_path)

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

    def conn() -> Iterator[sqlite3.Connection]:
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    Conn = Depends(conn)

    def root_of(c: sqlite3.Connection) -> Path:
        return Path(db.get_meta(c, "root_path") or "")

    def file_row(c: sqlite3.Connection, file_id: int) -> sqlite3.Row:
        row = c.execute(
            "SELECT id, rel_path, kind FROM files WHERE id = ? AND status != 'missing'", (file_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404)
        return row

    for route, name in UI_FILES.items():

        def page(name: str = name) -> FileResponse:
            return FileResponse(UI_DIR / name, headers={"Cache-Control": "no-store"})

        app.get(route, include_in_schema=False)(page)

    @app.get("/api/status")
    def status(c: sqlite3.Connection = Conn) -> dict[str, object]:
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
            face.save(cached, "JPEG", quality=85)
        return FileResponse(cached, media_type="image/jpeg")

    @app.get("/api/files/{file_id}/thumb")
    def thumb(file_id: int, c: sqlite3.Connection = Conn) -> FileResponse:
        file_row(c, file_id)
        path = thumbs_dir / f"{file_id}.jpg"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/files/{file_id}/open")
    def open_file(file_id: int, c: sqlite3.Connection = Conn) -> dict[str, str]:
        path = root_of(c) / file_row(c, file_id)["rel_path"]
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)], start_new_session=True)
        return {"ok": "sim"}

    return app
