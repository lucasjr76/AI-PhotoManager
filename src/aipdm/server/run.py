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


def serve(db_path: Path, *, browser: bool = False) -> None:
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
    url = f"http://127.0.0.1:{port}/?t={token}"
    try:
        if browser:
            webbrowser.open(url)
            print("Interface aberta no navegador. Ctrl+C para encerrar.")
            thread.join()
        else:
            import webview  # imported late: needs GTK/WebView2, not required for --browser

            webview.create_window(TITLE, url, width=1280, height=860)
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)
