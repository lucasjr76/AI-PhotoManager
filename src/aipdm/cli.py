"""Command line interface (phases 1-2)."""

import argparse
import hashlib
import logging
import os
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from aipdm.core import db, paths, scanner
from aipdm.core.search import search_text

RUNTIME_PACKAGES = ("pillow", "pillow-heif", "pypdfium2", "python-docx", "xxhash")


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

    stats = scanner.index(root, db_path, workers=args.workers, force=args.force, progress=progress)
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
    errors = conn.execute(
        "SELECT rel_path, error FROM files WHERE status = 'error' ORDER BY rel_path LIMIT 20"
    ).fetchall()
    if errors:
        print("\nErros (até 20):")
        for rel, err in errors:
            print(f"  {rel}: {err}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if not args.text:
        print("Nesta fase só há busca por texto: use --text.", file=sys.stderr)
        return 1
    conn = _open_db(args.db)
    hits = search_text(conn, args.text, args.limit)
    if not hits:
        print("Nenhum resultado.")
    for hit in hits:
        print(f"{hit.rel_path}  (p. {hit.page}, {hit.taken_at or 'sem data'})")
        print(f"    {' '.join(hit.snippet.split())}")
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
            print(f"  {pkg:<14} {version(pkg)}")
        except PackageNotFoundError:
            print(f"  {pkg:<14} AUSENTE")
            ok = False
    models = paths.models_dir()
    licenses = models / "LICENSES.md"
    print(f"\nModelos ({models}):")
    if not licenses.exists():
        print("  LICENSES.md ausente — rode: uv run python tools/fetch_models.py")
        return 1
    for line in licenses.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4 or not cells[0].endswith(".onnx"):
            continue
        name, license_id, _source, expected = cells
        file = models / name
        if not file.exists():
            state = "AUSENTE"
        elif hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            state = "SHA-256 DIVERGENTE"
        else:
            state = "ok"
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
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--force", action="store_true", help="reprocessa todos os arquivos")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("status", help="resumo do banco")
    p.add_argument("--db")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="busca")
    p.add_argument("--text")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--db")
    p.set_defaults(func=cmd_search)

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
