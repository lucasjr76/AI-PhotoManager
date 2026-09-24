"""Proof that indexing never touches the source folder (RNF-1)."""

import hashlib
import os
from pathlib import Path

from aipdm.core.scanner import index


def snapshot(root: Path) -> dict[str, tuple[str, int, int, int]]:
    """rel_path -> (sha256, size, mtime_ns, mode) for every file and directory."""
    snap: dict[str, tuple[str, int, int, int]] = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(dirpath) / name
            st = path.lstat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
            snap[path.relative_to(root).as_posix()] = (
                digest,
                st.st_size,
                st.st_mtime_ns,
                st.st_mode,
            )
    st = root.stat()
    snap["."] = ("dir", st.st_size, st.st_mtime_ns, st.st_mode)
    return snap


def test_indexing_does_not_modify_source(readonly_sample: Path, tmp_path: Path) -> None:
    before = snapshot(readonly_sample)
    db_path = tmp_path / "dados" / "x.sqlite"

    stats = index(readonly_sample, db_path, workers=2)
    index(readonly_sample, db_path, workers=1, force=True)

    assert snapshot(readonly_sample) == before
    # Only the deliberately broken file may fail; a write attempt would show up here too.
    assert stats.errors == 1
    assert not db_path.is_relative_to(readonly_sample)
