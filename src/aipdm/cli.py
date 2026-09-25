"""Command line interface (phases 1-2)."""

import argparse
import hashlib
import logging
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from aipdm.core import db, paths, scanner
from aipdm.core.clip import ClipTextModel
from aipdm.core.faces import FaceSettings, group_faces, reset_groups
from aipdm.core.images import load_image
from aipdm.core.search import Hit, Query, find_person, search

RUNTIME_PACKAGES = (
    "pillow",
    "pillow-heif",
    "pypdfium2",
    "python-docx",
    "xxhash",
    "numpy",
    "opencv-python-headless",
    "onnxruntime",
    "rapidocr",
    "tokenizers",
)
STAGE_LABELS = {
    "hash": "hash",
    "decode": "leitura de imagem",
    "thumb": "thumbnail",
    "text": "texto PDF/DOCX",
    "faces": "rostos",
    "clip": "CLIP",
    "ocr": "OCR",
    "grouping": "agrupamento de rostos",
}


def _open_db(arg: str | None) -> sqlite3.Connection:
    path = Path(arg) if arg else paths.latest_db()
    if path is None or not path.exists():
        sys.exit("Nenhum banco encontrado. Rode primeiro: aipdm index <pasta>")
    return db.connect(path)


def cmd_index(args: argparse.Namespace) -> int:
    root = Path(args.pasta)
    if not root.is_dir():
        print(f"Pasta não encontrada: {root}", file=sys.stderr)
        return 1
    db_path = Path(args.db) if args.db else paths.db_path_for(root)

    def progress(done: int, total: int) -> None:
        print(f"\rProcessando {done}/{total}", end="", file=sys.stderr, flush=True)

    only = None
    if args.only is not None:
        only = frozenset(s for s in args.only.split(",") if s)
        unknown = only - set(scanner.HEAVY_STAGES)
        if unknown:
            print(f"Estágio desconhecido: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
    stats = scanner.index(
        root,
        db_path,
        workers=args.workers,
        force=args.force,
        retry_errors=args.retry_errors,
        only=only,
        progress=progress,
    )
    if stats.pending:
        print(file=sys.stderr)
    s = stats.scan
    print(f"Banco: {db_path}")
    print(
        f"Varredura: {s.new} novos, {s.changed} alterados, {s.missing} ausentes, "
        f"{s.reappeared} reapareceram, {s.unchanged} sem mudança"
    )
    if s.ignored:
        print(f"Removidos do banco (pastas ocultas): {s.ignored}")
    print(f"Processados: {stats.processed} de {stats.pending} (erros: {stats.errors})")
    if stats.grouping:
        g = stats.grouping
        print(
            f"Rostos: {g.auto} atribuídos automaticamente, {g.suggested} sugeridos, "
            f"{g.clustered} agrupados em {g.new_people} novas pessoas, {g.unassigned} sem grupo"
        )
    if stats.stage_seconds:
        print("Tempo por estágio (soma dos processos):")
        for stage, sec in sorted(stats.stage_seconds.items(), key=lambda kv: -kv[1]):
            print(f"  {STAGE_LABELS.get(stage, stage):<24} {sec:>9.1f} s")
    print(f"Tempo total: {stats.seconds:.1f} s")
    if stats.interrupted:
        print("Interrompido. Rode o mesmo comando para continuar de onde parou.")
    return 0


def _print_counts(conn: sqlite3.Connection, title: str, column: str, where: str = "") -> None:
    print(f"\n{title}:")
    # column comes from a fixed whitelist in cmd_status, never from user input
    sql = (
        f"SELECT COALESCE({column}, '(nenhuma)'), COUNT(*) FROM files {where}"
        " GROUP BY 1 ORDER BY 2 DESC"
    )
    for value, count in conn.execute(sql):
        print(f"  {value:<20} {count:>8}")


def cmd_status(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    print(f"Pasta: {db.get_meta(conn, 'root_path')}")
    print(f"Arquivos: {total}")
    print(
        f"Última indexação: {db.get_meta(conn, 'last_index_at')} — "
        f"{db.get_meta(conn, 'last_index_processed')} processados em "
        f"{db.get_meta(conn, 'last_index_seconds')} s"
    )
    _print_counts(conn, "Por tipo", "kind")
    _print_counts(conn, "Por status", "status")
    _print_counts(
        conn, "Por fonte de data (exceto 'other')", "date_source", "WHERE kind != 'other'"
    )
    stickers = conn.execute("SELECT COUNT(*) FROM files WHERE is_sticker = 1").fetchone()[0]
    pages = conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]
    print(f"\nFigurinhas: {stickers}   Páginas/trechos com texto: {pages}")
    faces, people, named, clips = conn.execute(
        "SELECT (SELECT COUNT(*) FROM faces), (SELECT COUNT(*) FROM people),"
        " (SELECT COUNT(*) FROM people WHERE name IS NOT NULL),"
        " (SELECT COUNT(*) FROM clip_embeddings)"
    ).fetchone()
    print(f"Rostos: {faces}   Pessoas: {people} ({named} com nome)   Embeddings CLIP: {clips}")
    errors = conn.execute(
        "SELECT rel_path, error FROM files WHERE status = 'error' ORDER BY rel_path LIMIT 20"
    ).fetchall()
    if errors:
        print("\nErros (até 20):")
        for rel, err in errors:
            print(f"  {rel}: {err}")
    return 0


def _print_hits(hits: list[Hit]) -> None:
    for hit in hits:
        where = f"p. {hit.page}, " if hit.page else ""
        print(f"{hit.rel_path}  ({where}{hit.taken_at or 'sem data'}, relevância {hit.score:.2f})")
        if hit.snippet:
            print(f"    {' '.join(hit.snippet.split())}")


def cmd_search(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    people_ids = []
    for name in args.person or []:
        found = find_person(conn, name)
        if found is None:
            print(f"Pessoa não encontrada: {name}", file=sys.stderr)
            return 1
        people_ids.append(found[0])
    query = Query(
        text=args.text,
        people_ids=tuple(people_ids),
        date_from=args.date_from,
        date_to=args.date_to,
        kinds=tuple(args.kind or ()),
        stickers=args.stickers,
        order=args.order,
        limit=args.limit,
    )
    encoder = None
    if args.text:
        try:
            encoder = ClipTextModel(paths.models_dir()).encode
        except Exception as exc:  # models missing: text search still works via FTS
            print(f"CLIP indisponível ({exc}); usando só o texto.", file=sys.stderr)
    results = search(conn, query, encoder)
    if results.person:
        print(f"Fotos de {results.person}:")
    _print_hits(results.hits)
    if results.person:
        print(f"\nDocumentos que citam {results.person}:")
        _print_hits(results.mentions)
    if not results.hits and not results.mentions:
        print("Nenhum resultado.")
    return 0


def cmd_people_list(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    rows = conn.execute(
        "SELECT p.id, p.name, p.hidden,"
        " SUM(fa.assign_source != 'suggested'), COUNT(DISTINCT fa.file_id),"
        " SUM(fa.assign_source = 'suggested')"
        " FROM people p LEFT JOIN faces fa ON fa.person_id = p.id"
        " GROUP BY p.id ORDER BY p.name IS NULL, 4 DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    print(f"{'id':>6}  {'nome':<30} {'rostos':>7} {'fotos':>6} {'sugeridos':>9}")
    for pid, name, hidden, faces, files, suggested in rows:
        label = (name or "(sem nome)") + (" [oculta]" if hidden else "")
        print(f"{pid:>6}  {label:<30} {faces or 0:>7} {files:>6} {suggested or 0:>9}")
    return 0


def cmd_people_name(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    cur = conn.execute("UPDATE people SET name = ? WHERE id = ?", (args.nome.strip(), args.id))
    conn.commit()
    if not cur.rowcount:
        print(f"Pessoa {args.id} não existe.", file=sys.stderr)
        return 1
    print(f"Pessoa {args.id} agora é {args.nome.strip()}.")
    return 0


def cmd_people_merge(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    keep, gone = args.id, args.outro
    if (
        keep == gone
        or conn.execute("SELECT COUNT(*) FROM people WHERE id IN (?, ?)", (keep, gone)).fetchone()[
            0
        ]
        != 2
    ):
        print("Informe dois ids de pessoas diferentes e existentes.", file=sys.stderr)
        return 1
    conn.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", (keep, gone))
    conn.execute(
        "UPDATE OR IGNORE face_negatives SET person_id = ? WHERE person_id = ?", (keep, gone)
    )
    conn.execute("DELETE FROM people WHERE id = ?", (gone,))
    conn.commit()
    print(f"Pessoa {gone} mesclada em {keep}.")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from aipdm.server.run import serve  # server deps load only for this command

    if args.pasta:
        path = paths.db_path_for(Path(args.pasta))
    else:
        path = Path(args.db) if args.db else paths.latest_db()
    if path is not None and not path.exists():
        path = None  # the welcome screen lets the user pick and index a folder
    serve(path, browser=args.browser)
    return 0


def cmd_faces_regroup(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    removed = reset_groups(conn)
    g = group_faces(conn, FaceSettings())
    print(f"{removed} grupos sem nome desfeitos.")
    print(
        f"Rostos: {g.auto} atribuídos automaticamente, {g.suggested} sugeridos, "
        f"{g.clustered} agrupados em {g.new_people} novas pessoas, {g.unassigned} sem grupo"
    )
    return 0


def cmd_faces_export(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    root = Path(db.get_meta(conn, "root_path") or "")
    out = Path(args.dir_saida).resolve()
    if out.is_relative_to(root.resolve()):
        print("A pasta de saída não pode ficar dentro da pasta indexada.", file=sys.stderr)
        return 1
    rows = conn.execute(
        "SELECT fa.id, fa.bbox, f.rel_path FROM faces fa JOIN files f ON f.id = fa.file_id"
        " WHERE fa.person_id = ? ORDER BY fa.assign_score DESC",
        (args.person_id,),
    ).fetchall()
    out.mkdir(parents=True, exist_ok=True)
    for face_id, bbox, rel in rows:
        x, y, w, h = (int(v) for v in bbox.split(","))
        margin = w // 4
        img = load_image(root / rel).image
        crop = img.crop((max(0, x - margin), max(0, y - margin), x + w + margin, y + h + margin))
        crop.save(out / f"{face_id}.jpg", "JPEG", quality=90)
    print(f"{len(rows)} recortes em {out}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    ok = True
    print(f"Python {sys.version.split()[0]}")
    fts = sqlite3.connect(":memory:")
    try:
        fts.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        print(f"SQLite {sqlite3.sqlite_version} com FTS5: ok")
    except sqlite3.OperationalError:
        print(f"SQLite {sqlite3.sqlite_version} SEM FTS5: FALHA")
        ok = False
    print("\nBibliotecas:")
    for pkg in RUNTIME_PACKAGES:
        try:
            print(f"  {pkg:<24} {version(pkg)}")
        except PackageNotFoundError:
            print(f"  {pkg:<24} AUSENTE")
            ok = False
    models = paths.models_dir()
    licenses = models / "LICENSES.md"
    print(f"\nModelos ({models}):")
    if not licenses.exists():
        print("  LICENSES.md ausente — rode: uv run python tools/fetch_models.py")
        return 1
    for line in licenses.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4 or not cells[0].endswith((".onnx", ".json", ".gz")):
            continue
        name, license_id, _source, expected = cells
        file = models / name
        if not file.exists():
            state = "AUSENTE"
        else:
            with file.open("rb") as fh:
                digest = hashlib.file_digest(fh, "sha256").hexdigest()
            state = "ok" if digest == expected else "SHA-256 DIVERGENTE"
        ok &= state == "ok"
        print(f"  {name:<40} {license_id:<12} {state}")
    print("\nTudo certo." if ok else "\nHá problemas acima.")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aipdm", description="AI-PhotoDocsManager")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs detalhados")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="indexa (ou re-varre) uma pasta, sem alterá-la")
    p.add_argument("pasta")
    p.add_argument("--db", help="caminho do banco (padrão: diretório de dados)")
    p.add_argument("--workers", type=int, default=scanner.default_workers())
    p.add_argument("--force", action="store_true", help="reprocessa todos os arquivos")
    p.add_argument(
        "--retry-errors", action="store_true", help="tenta de novo os arquivos que deram erro"
    )
    p.add_argument(
        "--only",
        help="só estes estágios pesados: faces,clip,ocr (vazio = só data/thumb/texto)",
    )
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("status", help="resumo do banco")
    p.add_argument("--db")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="busca")
    p.add_argument("--text")
    p.add_argument("--person", action="append", help="nome da pessoa (repetível: todas juntas)")
    p.add_argument("--from", dest="date_from", metavar="AAAA-MM-DD")
    p.add_argument("--to", dest="date_to", metavar="AAAA-MM-DD")
    p.add_argument("--kind", action="append", choices=["image", "pdf", "docx"])
    p.add_argument("--stickers", action="store_true", help="incluir figurinhas")
    p.add_argument("--order", choices=["relevance", "date"], default="relevance")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--db")
    p.set_defaults(func=cmd_search)

    people = sub.add_parser("people", help="pessoas").add_subparsers(dest="action", required=True)
    p = people.add_parser("list", help="lista pessoas")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--db")
    p.set_defaults(func=cmd_people_list)
    p = people.add_parser("name", help="dá nome a uma pessoa")
    p.add_argument("id", type=int)
    p.add_argument("nome")
    p.add_argument("--db")
    p.set_defaults(func=cmd_people_name)
    p = people.add_parser("merge", help="mescla a segunda pessoa na primeira")
    p.add_argument("id", type=int)
    p.add_argument("outro", type=int)
    p.add_argument("--db")
    p.set_defaults(func=cmd_people_merge)

    faces = sub.add_parser("faces", help="rostos").add_subparsers(dest="action", required=True)
    p = faces.add_parser("export", help="salva recortes dos rostos de uma pessoa")
    p.add_argument("person_id", type=int)
    p.add_argument("dir_saida")
    p.add_argument("--db")
    p.set_defaults(func=cmd_faces_export)
    p = faces.add_parser("regroup", help="refaz os grupos sem nome com os limiares atuais")
    p.add_argument("--db")
    p.set_defaults(func=cmd_faces_regroup)

    p = sub.add_parser("ui", help="abre a interface")
    p.add_argument("pasta", nargs="?", help="pasta já indexada (padrão: a mais recente)")
    p.add_argument("--db")
    p.add_argument("--browser", action="store_true", help="abre no navegador em vez da janela")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("doctor", help="verifica modelos, versões e licenças")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
