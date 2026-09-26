"""Full pipeline with the real models (skipped when models/ is not populated)."""

import socket
import sqlite3
from pathlib import Path

import numpy as np
import open_clip
import pypdfium2 as pdfium
import pytest
from conftest import index_base, make_pdf, requires_models, set_tree_readonly
from PIL import Image
from test_readonly import snapshot

from aipdm.core import db
from aipdm.core.clip import load_tokenizer, preprocess, tokenize
from aipdm.core.paths import models_dir
from aipdm.core.scanner import index
from aipdm.core.search import search_text

pytestmark = requires_models

ACCENTED = "Ação São João Conceição"


def render_text(tmp: Path, text: str) -> Image.Image:
    pdf_path = tmp / "render.pdf"
    make_pdf(pdf_path, [text])
    return pdfium.PdfDocument(str(pdf_path))[0].render(scale=200 / 72).to_pil().convert("RGB")


@pytest.fixture
def text_dir(sample_dir: Path, tmp_path: Path) -> Path:
    """sample_dir plus a photo of text and a scanned (image-only) PDF."""
    page = render_text(tmp_path, ACCENTED)
    page.save(sample_dir / "print.png")
    page.save(sample_dir / "WhatsApp Documents" / "escaneado.pdf")  # no text layer
    return sample_dir


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tentativa de acesso à rede")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def rows(db_path: Path) -> dict[str, sqlite3.Row]:
    conn = db.connect(db_path)
    try:
        return {r["rel_path"]: r for r in conn.execute("SELECT * FROM files")}
    finally:
        conn.close()


def test_full_pipeline_offline(text_dir: Path, tmp_path: Path, no_network: None) -> None:
    db_path = tmp_path / "dados" / "full.sqlite"
    stats = index(text_dir, db_path, workers=1)
    assert stats.errors == 1  # only the broken jpeg
    files = rows(db_path)
    assert files["camera.jpg"]["stages_done"] == "date,gps,thumb,faces,clip,ocr"
    assert (
        files["WhatsApp Stickers/STK-20230101-WA0002.webp"]["stages_done"] == "date,gps,thumb,clip"
    )
    assert files["WhatsApp Documents/escaneado.pdf"]["stages_done"] == "date,thumb,text,ocr"

    conn = db.connect(db_path)
    clips = conn.execute("SELECT COUNT(*) FROM clip_embeddings").fetchone()[0]
    assert clips == 5  # 4 images + sticker; the broken jpeg has none
    found = {h.rel_path for h in search_text(conn, "conceicao sao joao")}
    assert found == {"print.png", "WhatsApp Documents/escaneado.pdf"}
    ocr_text = conn.execute(
        "SELECT content FROM texts t JOIN files f ON f.id = t.file_id WHERE f.rel_path = ?",
        ("print.png",),
    ).fetchone()[0]
    assert ocr_text == ACCENTED
    conn.close()

    assert index(text_dir, db_path, workers=1).processed == 0


def test_phase1_database_gets_only_missing_stages(text_dir: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "dados" / "up.sqlite"
    index_base(text_dir, db_path, workers=1)
    stats = index(text_dir, db_path, workers=1)
    # 5 images (faces/clip/ocr) + 2 PDFs (ocr); docx and the broken jpeg need nothing.
    assert stats.processed == 7
    assert "hash" not in stats.stage_seconds  # cheap stages were not redone
    conn = db.connect(db_path)
    per_file = conn.execute(
        "SELECT COUNT(*) FROM texts GROUP BY file_id, page HAVING COUNT(*) > 1"
    ).fetchall()
    assert per_file == []  # no duplicated text
    conn.close()


def test_only_limits_heavy_stages(sample_dir: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "dados" / "only.sqlite"
    index(sample_dir, db_path, workers=1, only=frozenset({"clip"}))
    assert rows(db_path)["camera.jpg"]["stages_done"] == "date,gps,thumb,clip"


def test_full_pipeline_is_read_only(text_dir: Path, tmp_path: Path) -> None:
    set_tree_readonly(text_dir, True)
    try:
        before = snapshot(text_dir)
        stats = index(text_dir, tmp_path / "dados" / "ro.sqlite", workers=2)
        assert snapshot(text_dir) == before
        assert stats.errors == 1
    finally:
        set_tree_readonly(text_dir, False)


def test_tokenizer_matches_open_clip() -> None:
    # Reference ids from open_clip's HFTokenizer (xlm-roberta-base, context 77).
    ids = tokenize(load_tokenizer(models_dir()), ["uma  foto de   praia"])[0]
    assert ids[:7].tolist() == [0, 788, 2011, 8, 63356, 2, 1]
    assert ids.shape == (77,) and set(ids[6:].tolist()) == {1}


@pytest.mark.parametrize("size", [(640, 480), (300, 900), (224, 224), (1000, 225)])
def test_preprocess_matches_open_clip(size: tuple[int, int]) -> None:
    rng = np.random.default_rng(0)
    img = Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))
    reference = open_clip.image_transform(
        224, is_train=False, mean=open_clip.OPENAI_DATASET_MEAN, std=open_clip.OPENAI_DATASET_STD
    )(img).numpy()
    assert np.abs(preprocess(img) - reference).max() < 1e-4


def test_embed_region_without_face_returns_none() -> None:
    from aipdm.core.faces import FaceModel, FaceSettings

    model = FaceModel(models_dir(), FaceSettings())
    blank = np.full((400, 600, 3), 200, dtype=np.uint8)
    assert model.embed_region(blank, (100, 100, 80, 80)) is None
    assert model.embed_region(blank, (598, 398, 1, 1)) is None  # degenerate box at the edge
