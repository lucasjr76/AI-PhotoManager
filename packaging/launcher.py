"""Entry point of the packaged app (PyInstaller). No arguments: open the interface."""

import multiprocessing
import sys


def run() -> None:
    # Indexing workers are spawned processes; in a frozen app they re-enter here.
    multiprocessing.freeze_support()
    from aipdm import paths_log

    paths_log.redirect_output_if_windowed()
    from aipdm.cli import main

    sys.exit(main(sys.argv[1:] or ["ui"]))


if __name__ == "__main__":
    run()
