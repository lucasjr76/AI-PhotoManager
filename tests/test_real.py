"""Runs against the real folder in AIPDM_TEST_DIR (read-only). Skipped when unset."""

import os
from pathlib import Path

import pytest

from aipdm.core.scanner import index, walk

REAL = os.environ.get("AIPDM_TEST_DIR")

pytestmark = [
    pytest.mark.real,
    pytest.mark.skipif(not REAL, reason="AIPDM_TEST_DIR não definida"),
]


def test_real_folder_untouched_and_incremental(tmp_path: Path) -> None:
    root = Path(REAL or "")
    before = sorted(walk(root))  # (rel_path, size, mtime) of every file
    db_path = tmp_path / "real.sqlite"

    index(root, db_path, workers=max(1, (os.cpu_count() or 2) - 1))
    second = index(root, db_path, workers=1)

    assert second.processed == 0
    assert sorted(walk(root)) == before
