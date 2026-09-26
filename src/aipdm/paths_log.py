"""Packaged app without a console (Windows): send stdout/stderr to a log file."""

import sys

from aipdm.core.paths import data_dir

LOG_NAME = "aipdm.log"


def redirect_output_if_windowed() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    log = (folder / LOG_NAME).open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log
