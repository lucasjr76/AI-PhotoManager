"""Diagnostics for the packaged app: log file, uncaught errors, no-console output."""

import logging
import sys
import threading
from pathlib import Path
from types import TracebackType

from aipdm.core.paths import data_dir

LOG_NAME = "aipdm.log"
MAX_LOG_BYTES = 5 * 1024 * 1024


def log_path() -> Path:
    return data_dir() / LOG_NAME


def setup_file_logging() -> Path:
    """Log INFO+ and every uncaught exception (main thread or not) to aipdm.log.

    The Windows build has no console: without this a startup error is invisible.
    """
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.unlink()  # ponytail: no rotation, just start over past 5 MB
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def excepthook(kind: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        logging.getLogger("aipdm").critical("erro não tratado", exc_info=(kind, exc, tb))

    sys.excepthook = excepthook
    threading.excepthook = lambda args: excepthook(
        args.exc_type, args.exc_value or args.exc_type(), args.exc_traceback
    )
    return path


def redirect_output_if_windowed() -> None:
    """No console (Windows window app): send stdout/stderr to the log file too."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream
