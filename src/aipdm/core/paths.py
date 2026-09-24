"""Per-OS data directory and per-folder database location."""

import hashlib
import os
import sys
from pathlib import Path

APP_DIR_LINUX = "ai-photodocsmanager"
APP_DIR_WINDOWS = "AI-PhotoDocsManager"


def data_dir() -> Path:
    if override := os.environ.get("AIPDM_DATA_DIR"):
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_WINDOWS
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_LINUX


def db_path_for(root: Path) -> Path:
    """One database per indexed folder, named after a hash of its absolute path."""
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    return data_dir() / f"{digest}.sqlite"


def latest_db() -> Path | None:
    """Most recently used database; lets `status`/`search` run without arguments."""
    # ponytail: picks by file mtime; store an explicit "current folder" if multi-folder UX needs it
    dbs = list(data_dir().glob("*.sqlite"))
    return max(dbs, key=lambda p: p.stat().st_mtime) if dbs else None


def thumbs_dir_for(db_path: Path) -> Path:
    return db_path.with_suffix(".thumbs")


def models_dir() -> Path:
    if override := os.environ.get("AIPDM_MODELS_DIR"):
        return Path(override)
    return Path(__file__).resolve().parents[3] / "models"
