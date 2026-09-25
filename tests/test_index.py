import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from conftest import index_base as index
from conftest import make_docx, make_pdf
from PIL import Image

from aipdm.cli import main
from aipdm.core import db
from aipdm.core.scanner import in_hidden_folder
from aipdm.core.search import search_text


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "dados" / "test.sqlite"


def rows(db_path: Path) -> dict[str, sqlite3.Row]:
    conn = db.connect(db_path)
    try:
        return {r["rel_path"]: r for r in conn.execute("SELECT * FROM files")}
    finally:
        conn.close()


def test_first_index_classifies_and_dates(sample_dir: Path, db_path: Path) -> None:
    stats = index(sample_dir, db_path, workers=1)
    assert stats.scan.new == 8
    assert stats.pending == 7  # the .mp4 is only listed
    assert stats.errors == 1

    files = rows(db_path)
    wa = files["WhatsApp Images/IMG-20230514-WA0001.jpg"]
    assert (wa["kind"], wa["taken_at"], wa["date_source"]) == (
        "image",
        "2023-05-14",
        "whatsapp_android",
    )
    assert (wa["width"], wa["height"], wa["status"]) == (64, 48, "done")
    assert wa["hash"] and wa["stages_done"] == "date,gps,thumb"

    desktop = files["WhatsApp Images/WhatsApp Image 2022-01-02 at 9.05.09 PM.png"]
    assert desktop["taken_at"] == "2022-01-02T21:05:09"
    assert files["camera.jpg"]["date_source"] == "exif"
    assert files["WhatsApp Documents/contrato.pdf"]["date_source"] == "document"
    assert files["WhatsApp Documents/contrato.pdf"]["taken_at"] == "2021-03-04T05:06:07"
    assert files["WhatsApp Documents/relatorio.docx"]["date_source"] == "document"
    assert files["WhatsApp Stickers/STK-20230101-WA0002.webp"]["is_sticker"] == 1

    mp4 = files["VID-20230514-WA0003.mp4"]
    assert (mp4["kind"], mp4["status"]) == ("other", "done")
    broken = files["corrompida.jpg"]
    assert broken["status"] == "error" and broken["error"]

    thumbs = db_path.with_suffix(".thumbs")
    assert (thumbs / f"{wa['id']}.jpg").exists()
    assert (thumbs / f"{files['WhatsApp Documents/contrato.pdf']['id']}.jpg").exists()


def test_reindex_without_changes_processes_zero(sample_dir: Path, db_path: Path) -> None:
    index(sample_dir, db_path, workers=1)
    stats = index(sample_dir, db_path, workers=1)
    assert stats.processed == 0
    assert stats.scan.unchanged == 8


def test_changed_missing_and_reappeared(sample_dir: Path, db_path: Path) -> None:
    index(sample_dir, db_path, workers=1)
    target = sample_dir / "camera.jpg"
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    moved = sample_dir.parent / "fora.pdf"
    (sample_dir / "WhatsApp Documents" / "contrato.pdf").rename(moved)

    stats = index(sample_dir, db_path, workers=1)
    assert (stats.scan.changed, stats.scan.missing, stats.processed) == (1, 1, 1)
    assert rows(db_path)["WhatsApp Documents/contrato.pdf"]["status"] == "missing"

    moved.rename(sample_dir / "WhatsApp Documents" / "contrato.pdf")
    stats = index(sample_dir, db_path, workers=1)
    assert (stats.scan.reappeared, stats.processed) == (1, 0)
    assert rows(db_path)["WhatsApp Documents/contrato.pdf"]["status"] == "done"


def test_force_reprocesses_without_duplicating_text(sample_dir: Path, db_path: Path) -> None:
    index(sample_dir, db_path, workers=1)
    stats = index(sample_dir, db_path, workers=1, force=True)
    assert stats.processed == 7
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0] == 3  # 2 pdf pages + docx
    conn.close()


def test_search_text_ignores_accents(sample_dir: Path, db_path: Path) -> None:
    index(sample_dir, db_path, workers=1)
    conn = db.connect(db_path)
    found = {h.rel_path for h in search_text(conn, "joao")}
    assert found == {"WhatsApp Documents/contrato.pdf", "WhatsApp Documents/relatorio.docx"}
    [hit] = search_text(conn, "jurerê")
    assert hit.page == 2
    assert search_text(conn, "florianopolis")[0].kind == "docx"
    assert search_text(conn, 'AND OR "(') == []  # user text is never FTS syntax
    conn.close()


def test_cli_index_status_search(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["index", str(sample_dir), "--workers", "1", "--only", ""]) == 0
    assert "Processados: 7 de 7 (erros: 1)" in capsys.readouterr().out
    assert main(["index", str(sample_dir), "--workers", "1", "--only", ""]) == 0
    assert "Processados: 0 de 0" in capsys.readouterr().out

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "whatsapp_android" in out and "corrompida.jpg" in out

    assert main(["search", "--text", "Maria"]) == 0
    assert "contrato.pdf" in capsys.readouterr().out


def test_migration_is_idempotent(db_path: Path) -> None:
    db.connect(db_path).close()
    conn = db.connect(db_path)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    conn.close()


@pytest.fixture
def extensionless_dir(tmp_path: Path) -> Path:
    root = tmp_path / "sem_extensao"
    root.mkdir()
    make_pdf(root / "DOC-20220823-WA0056", ["Recibo de João"])
    make_docx(root / "PROPOSTA-BLD---50MB")
    Image.new("RGB", (10, 10)).save(root / "004d66509d39a28a", "JPEG")
    Image.new("RGB", (10, 10)).save(root / "semnome_png", "PNG")
    Image.new("RGB", (10, 10)).save(root / "semnome_webp", "WEBP")
    with zipfile.ZipFile(root / "planilha", "w") as archive:
        archive.writestr("xl/workbook.xml", "<x/>")
    (root / "desconhecido").write_bytes(b"qualquer coisa")
    (root / "vazio").write_bytes(b"")
    return root


def test_extensionless_files_are_sniffed(extensionless_dir: Path, db_path: Path) -> None:
    stats = index(extensionless_dir, db_path, workers=1)
    files = rows(db_path)
    assert {rel: r["kind"] for rel, r in files.items()} == {
        "DOC-20220823-WA0056": "pdf",
        "PROPOSTA-BLD---50MB": "docx",
        "004d66509d39a28a": "image",
        "semnome_png": "image",
        "semnome_webp": "image",
        "planilha": "other",
        "desconhecido": "other",
        "vazio": "other",
    }
    assert stats.errors == 0
    pdf = files["DOC-20220823-WA0056"]
    assert (pdf["taken_at"], pdf["date_source"]) == ("2022-08-23", "whatsapp_android")
    conn = db.connect(db_path)
    assert {h.rel_path for h in search_text(conn, "joao")} == {
        "DOC-20220823-WA0056",
        "PROPOSTA-BLD---50MB",
    }
    conn.close()


def test_existing_rows_are_reclassified(extensionless_dir: Path, db_path: Path) -> None:
    """A database built before sniffing existed picks up the new kind on the next scan."""
    index(extensionless_dir, db_path, workers=1)
    conn = db.connect(db_path)
    conn.execute("UPDATE files SET kind = 'other', stages_done = '' WHERE kind != 'other'")
    conn.commit()
    conn.close()

    stats = index(extensionless_dir, db_path, workers=1)
    assert (stats.scan.changed, stats.processed) == (5, 5)
    assert rows(db_path)["DOC-20220823-WA0056"]["kind"] == "pdf"
    assert index(extensionless_dir, db_path, workers=1).processed == 0


def test_hidden_folders_are_skipped(sample_dir: Path, db_path: Path) -> None:
    links = sample_dir / ".Links"
    links.mkdir()
    Image.new("RGB", (10, 10)).save(links / "004d66509d39a28a", "JPEG")
    nested = sample_dir / "WhatsApp Documents" / ".oculta"
    nested.mkdir()
    make_pdf(nested / "segredo.pdf", ["João"])
    (sample_dir / ".nomedia").write_bytes(b"")  # hidden *file*: still listed

    stats = index(sample_dir, db_path, workers=1)
    assert stats.scan.new == 9
    files = rows(db_path)
    assert ".nomedia" in files
    assert not any(in_hidden_folder(rel) for rel in files)


def test_rows_from_hidden_folders_are_purged(sample_dir: Path, db_path: Path) -> None:
    """A database built before hidden folders were skipped loses those rows, not 'missing'."""
    index(sample_dir, db_path, workers=1)
    conn = db.connect(db_path)
    cur = conn.execute(
        "INSERT INTO files (rel_path, kind, size, mtime, status) VALUES (?, 'image', 1, 1, 'done')",
        (".Links/004d66509d39a28a",),
    )
    conn.execute("INSERT INTO texts (content, file_id, page) VALUES ('x', ?, 1)", (cur.lastrowid,))
    conn.commit()
    conn.close()

    stats = index(sample_dir, db_path, workers=1)
    assert (stats.scan.ignored, stats.scan.missing, stats.processed) == (1, 0, 0)
    assert ".Links/004d66509d39a28a" not in rows(db_path)
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM texts WHERE content = 'x'").fetchone()[0] == 0
    conn.close()


def gps_jpeg(path: Path, lat: float, lon: float) -> None:
    def dms(v: float) -> tuple[float, float, float]:
        v = abs(v)
        return (float(int(v)), float(int(v * 60 % 60)), round(v * 3600 % 60, 3))

    exif = Image.Exif()
    exif.get_ifd(0x8825).update(
        {1: "S" if lat < 0 else "N", 2: dms(lat), 3: "W" if lon < 0 else "E", 4: dms(lon)}
    )
    Image.new("RGB", (32, 32), "white").save(path, exif=exif)


@pytest.fixture
def places_dir(tmp_path: Path) -> Path:
    import gzip

    models = tmp_path / "modelos"
    models.mkdir()
    with gzip.open(models / "places.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("Florianópolis\tSanta Catarina\tBrazil\t-27.59667\t-48.54917\n")
        fh.write("Curitiba\tParaná\tBrazil\t-25.42778\t-49.27306\n")
    return models


def test_gps_location_is_named_and_searchable(
    sample_dir: Path, db_path: Path, places_dir: Path
) -> None:
    gps_jpeg(sample_dir / "praia.jpg", -27.60, -48.55)
    gps_jpeg(sample_dir / "oceano.jpg", -30.0, -20.0)
    stats = index(sample_dir, db_path, workers=1, models=places_dir)
    assert stats.places == 2
    files = rows(db_path)
    praia = files["praia.jpg"]
    assert praia["lat"] == pytest.approx(-27.60, abs=1e-3)
    assert (praia["city"], praia["state"], praia["country"]) == (
        "Florianópolis",
        "Santa Catarina",
        "Brazil",
    )
    assert files["oceano.jpg"]["city"] == ""  # too far from any town, not retried
    assert files["camera.jpg"]["lat"] is None  # no GPS in EXIF
    conn = db.connect(db_path)
    found = {h.rel_path for h in search_text(conn, "florianopolis")}
    assert found == {"praia.jpg", "WhatsApp Documents/relatorio.docx"}  # the docx cites it too
    conn.close()
    assert index(sample_dir, db_path, workers=1, models=places_dir).places == 0


def test_indexed_folder_gets_only_gps(sample_dir: Path, db_path: Path, places_dir: Path) -> None:
    gps_jpeg(sample_dir / "praia.jpg", -25.43, -49.27)
    index(sample_dir, db_path, workers=1, models=places_dir)
    conn = db.connect(db_path)  # as if indexed before the gps stage existed
    conn.execute(
        "UPDATE files SET stages_done = 'date,thumb', lat = NULL, lon = NULL, city = NULL"
        " WHERE kind = 'image'"
    )
    conn.execute("DELETE FROM texts WHERE page = 0")
    conn.commit()
    conn.close()

    stats = index(sample_dir, db_path, workers=1, models=places_dir)
    assert stats.processed == 5  # the 5 good images; the broken one stays in error
    assert "decode" not in stats.stage_seconds and "hash" not in stats.stage_seconds
    assert rows(db_path)["praia.jpg"]["city"] == "Curitiba"
