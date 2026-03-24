from __future__ import annotations

import logging
import threading
import time

from app.runtime_debug import record_event, setup_logging
from app.service import sync_once
from app.settings_store import get_setting

logger = logging.getLogger("kabootar.background")
_STATE_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STARTED_AT = 0.0


def _interval_minutes() -> int:
    try:
        value = int(get_setting("sync_interval_minutes", "1") or "1")
    except Exception:
        value = 1
    return max(1, min(59, value))


def _loop() -> None:
    record_event("background_sync_loop_started", interval_minutes=_interval_minutes())
    while True:
        started = time.time()
        source_mode = (get_setting("source_mode", "dns") or "dns").strip().lower()
        force_server_refresh = source_mode == "dns"
        try:
            record_event(
                "background_sync_tick",
                interval_minutes=_interval_minutes(),
                mode=source_mode,
                force_server_refresh=force_server_refresh,
            )
            sync_once(force_server_refresh=force_server_refresh)
        except Exception as exc:
            logger.exception("background sync loop failed")
            record_event("background_sync_loop_error", level="error", error=str(exc))

        wait_seconds = max(1.0, (_interval_minutes() * 60.0) - (time.time() - started))
        time.sleep(wait_seconds)


def start_background_sync_loop() -> bool:
    global _THREAD, _STARTED_AT
    with _STATE_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        setup_logging()
        _THREAD = threading.Thread(target=_loop, name="kabootar-background-sync", daemon=True)
        _THREAD.start()
        _STARTED_AT = time.time()
        return True


def background_sync_status() -> dict[str, object]:
    with _STATE_LOCK:
        thread = _THREAD
        return {
            "running": bool(thread and thread.is_alive()),
            "started_at": int(_STARTED_AT) if _STARTED_AT else 0,
            "interval_minutes": _interval_minutes(),
        }
