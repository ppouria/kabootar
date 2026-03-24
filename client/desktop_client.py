#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time
from pathlib import Path


def _prepare_local_env() -> None:
    home = Path.home() / ".kabootar_client"
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(data / 'app.db').as_posix()}")
    os.environ.setdefault("APP_HOST", "127.0.0.1")
    os.environ.setdefault("APP_PORT", "18765")


_prepare_local_env()

from app.runtime_debug import record_event, setup_logging  # noqa: E402
from app.web import create_app  # noqa: E402


def main() -> None:
    setup_logging()
    app = create_app()
    url = f"http://127.0.0.1:{os.getenv('APP_PORT','18765')}"
    record_event("desktop_client_start", url=url)

    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=int(os.getenv("APP_PORT", "18765")), debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()

    try:
        import webview  # type: ignore

        webview.create_window("Kabootar Client", url, width=1180, height=860)
        webview.start()
    except Exception as exc:
        record_event("desktop_webview_fallback", level="warning", error=str(exc), url=url)
        import webbrowser

        webbrowser.open(url)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
