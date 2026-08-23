"""
desktop.py — run the Re:Zero Reader as a native desktop app (no browser).

Reuses the exact same Flask backend and dark web UI, but presents it inside a
native window via pywebview (Windows WebView2). Launch:

    python -m src.desktop          (or double-click ReZeroReader.bat)

The Flask server runs on 127.0.0.1 in a background thread and is only reachable
locally; closing the window shuts everything down.
"""
from __future__ import annotations

import socket
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import webview

from .app import app, start_background_warm

LOG = Path(__file__).resolve().parent.parent / "app.log"


def _free_port(preferred: int = 5000) -> int:
    """Use the preferred port if free, otherwise let the OS pick one."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def _serve(port: int) -> None:
    # threaded so audio prefetch + playback requests don't block each other.
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)


def _wait_until_up(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/config"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def main() -> int:
    port = _free_port(5000)
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    _wait_until_up(port)  # window still opens if this times out; UI shows the error
    start_background_warm()  # load the voice model while the window opens

    webview.create_window(
        "Re:Zero Reader",
        f"http://127.0.0.1:{port}/",
        width=1280,
        height=860,
        min_size=(920, 640),
        background_color="#0b0c10",
    )
    # Blocks on the GUI thread until the window is closed; daemon server then exits.
    webview.start()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # pythonw has no console, so record why the window failed to open.
        LOG.write_text("Re:Zero Reader failed to start:\n\n" + traceback.format_exc(),
                       encoding="utf-8")
        raise
