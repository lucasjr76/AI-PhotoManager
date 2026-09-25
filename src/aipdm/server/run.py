"""Start the local server on 127.0.0.1:<random port> and show the UI (RNF-8)."""

import secrets
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from aipdm.server.app import create_app

TITLE = "AI-PhotoDocsManager"


def start(db_path: Path | None) -> tuple[str, str, uvicorn.Server, threading.Thread]:
    """Start serving in a background thread. Returns (base url, token, server, thread)."""
    token = secrets.token_urlsafe(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))  # loopback only, port chosen by the OS
    port = sock.getsockname()[1]
    app = create_app(db_path, token, allowed_host=f"127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", token, server, thread


class WindowApi:
    """Exposed to the page as window.pywebview.api (native dialogs only)."""

    def choose_folder(self) -> str | None:
        import webview

        chosen = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        return chosen[0] if chosen else None


def serve(db_path: Path | None, *, browser: bool = False) -> None:
    base, token, server, thread = start(db_path)
    url = f"{base}/?t={token}"
    try:
        if browser:
            webbrowser.open(url)
            print("Interface aberta no navegador. Ctrl+C para encerrar.")
            thread.join()
        else:
            import webview  # imported late: needs GTK/WebView2, not required for --browser

            webview.create_window(TITLE, url, width=1280, height=860, js_api=WindowApi())
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)
