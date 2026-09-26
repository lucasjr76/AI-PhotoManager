"""Entry point of the packaged app (PyInstaller). No arguments: open the interface."""

import logging
import multiprocessing
import sys


def run() -> None:
    # Indexing workers are spawned processes; in a frozen app they re-enter here.
    multiprocessing.freeze_support()
    from aipdm import paths_log

    paths_log.redirect_output_if_windowed()
    log_file = paths_log.setup_file_logging()
    log = logging.getLogger("aipdm.launcher")
    log.info("iniciando (%s) args=%s log=%s", sys.executable, sys.argv[1:], log_file)
    if not sys.argv[1:]:
        # The interface path avoids the CLI module, which imports the heavy AI libraries
        # up front: the window must appear first.
        from aipdm.server.run import serve_default

        serve_default()
        return
    from aipdm.cli import main

    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    run()
