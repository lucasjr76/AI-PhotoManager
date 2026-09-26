"""Start the local server on 127.0.0.1:<random port> and show the UI (RNF-8).

The native window opens first with a "loading" page; the server (which imports the heavy
AI libraries) starts behind it. On a first run Windows Defender scans those libraries,
which can take a while: the user sees the window instead of nothing.
"""

import html
import logging
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uvicorn

TITLE = "AI-PhotoDocsManager"
ICON = Path(__file__).resolve().parents[1] / "ui" / "icon.png"
log = logging.getLogger(__name__)

PAGE_STYLE = (
    "font-family:system-ui,sans-serif;background:#17171a;color:#ececef;margin:0;"
    "display:flex;align-items:center;justify-content:center;height:100vh"
)
LOADING_HTML = (
    f"<html><body style='{PAGE_STYLE}'><div style='text-align:center'>"
    f"<h2>{TITLE}</h2><p>Abrindo…</p>"
    "<p style='color:#9a9aa2'>Na primeira vez pode levar um ou dois minutos.</p>"
    "</div></body></html>"
)


def error_html(message: str) -> str:
    from aipdm.paths_log import log_path

    return (
        f"<html><body style='{PAGE_STYLE}'><div style='max-width:720px'>"
        f"<h2>Não foi possível abrir o {TITLE}</h2>"
        f"<pre style='white-space:pre-wrap;color:#ff6b5e'>{html.escape(message)}</pre>"
        f"<p>Detalhes em: <code>{html.escape(str(log_path()))}</code></p>"
        "</div></body></html>"
    )


def start(db_path: Path | None) -> "tuple[str, str, uvicorn.Server, threading.Thread]":
    """Start serving in a background thread. Returns (base url, token, server, thread)."""
    import uvicorn

    from aipdm.server.app import create_app

    token = secrets.token_urlsafe(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))  # loopback only, port chosen by the OS
    port = sock.getsockname()[1]
    app = create_app(db_path, token, allowed_host=f"127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("o servidor local não iniciou")
        time.sleep(0.05)
    log.info("servidor pronto em http://127.0.0.1:%s", port)
    return f"http://127.0.0.1:{port}", token, server, thread


def stop(server: "uvicorn.Server", thread: threading.Thread) -> None:
    server.should_exit = True
    server.config.app.state.monitor_stop.set()
    thread.join(timeout=5)


class WindowApi:
    """Exposed to the page as window.pywebview.api (native dialogs only)."""

    def choose_folder(self) -> str | None:
        import webview

        chosen = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        return chosen[0] if chosen else None


def default_db() -> Path | None:
    """The most recently indexed folder, or None for the welcome screen."""
    from aipdm.core.paths import latest_db

    found = latest_db()
    return found if found is not None and found.exists() else None


def serve_default() -> None:
    serve(default_db())


def in_browser(db_path: Path | None) -> None:
    base, token, server, thread = start(db_path)
    webbrowser.open(f"{base}/?t={token}")
    print("Interface aberta no navegador. Ctrl+C para encerrar.")
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        stop(server, thread)


def serve(db_path: Path | None, *, browser: bool = False) -> None:
    if browser:
        in_browser(db_path)
        return
    # WebKitGTK + NVIDIA's proprietary driver crash the web process (seen: SIGSEGV in
    # libnvidia-gpucomp while compositing, SIGABRT in libEGL_nvidia at exit). Software
    # rendering avoids the GPU path; the UI is images and text, so it is plenty. Users
    # can still override either variable.
    if sys.platform.startswith("linux"):
        os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
        os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
    try:
        import webview  # needs GTK/WebKitGTK or WebView2; not required for --browser
    except Exception:
        log.exception("janela nativa indisponível; abrindo no navegador")
        in_browser(db_path)
        return

    running: list[tuple[uvicorn.Server, threading.Thread]] = []
    window = webview.create_window(
        TITLE, html=LOADING_HTML, width=1280, height=860, js_api=WindowApi()
    )

    def boot() -> None:  # runs in its own thread once the window is up
        try:
            log.info("janela aberta; iniciando servidor")
            base, token, server, thread = start(db_path)
            running.append((server, thread))
            window.load_url(f"{base}/?t={token}")
            log.info("interface carregada")
        except Exception as exc:
            log.exception("falha ao iniciar a interface")
            window.load_html(error_html(f"{type(exc).__name__}: {exc}"))

    try:
        webview.start(boot, icon=str(ICON) if ICON.exists() else None)
    except Exception:  # the native window itself failed (no WebKitGTK/WebView2 runtime)
        log.exception("janela nativa falhou; abrindo no navegador")
        for server, thread in running:
            stop(server, thread)
        in_browser(db_path)
        return
    for server, thread in running:
        stop(server, thread)
    log.info("janela fechada")
