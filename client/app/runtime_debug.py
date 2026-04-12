from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote

from app.versioning import app_meta

_SETUP_LOCK = threading.Lock()
_SETUP_DONE = False
_EVENT_LOCK = threading.Lock()
_EVENTS: deque[dict[str, object]] = deque(maxlen=300)
_STARTED_AT = int(time.time())


def _is_true(value: str, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if raw in {"", "none"}:
        return default
    return raw not in {"0", "false", "no", "off", "disable", "disabled"}


def _file_logging_enabled() -> bool:
    explicit = (os.getenv("KABOOTAR_FILE_LOG_ENABLED", "") or "").strip()
    if explicit:
        return _is_true(explicit, default=False)

    platform = (os.getenv("KABOOTAR_PLATFORM", "") or "").strip().lower()
    if platform == "android":
        debug_enabled = (os.getenv("KABOOTAR_DEBUG_ENABLED", "") or "").strip()
        if debug_enabled:
            return _is_true(debug_enabled, default=False)
        return False
    return True


def resolve_database_path() -> Path:
    raw = (os.getenv("DATABASE_URL", "") or "").strip()
    if raw.startswith("sqlite:///"):
        path_text = unquote(raw[len("sqlite:///") :])
        path = Path(path_text)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path
    return (Path.cwd() / "data" / "app.db").resolve()


def resolve_runtime_dir() -> Path:
    override = (os.getenv("KABOOTAR_RUNTIME_DIR", "") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return resolve_database_path().parent


def resolve_log_path() -> Path:
    return resolve_runtime_dir() / "logs" / "client.log"


def setup_logging() -> Path:
    global _SETUP_DONE
    with _SETUP_LOCK:
        log_path = resolve_log_path()
        if _SETUP_DONE:
            return log_path

        root = logging.getLogger()
        root.setLevel(logging.INFO)

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_logging_enabled = _file_logging_enabled()
        if not file_logging_enabled:
            for handler in list(root.handlers):
                if isinstance(handler, RotatingFileHandler):
                    root.removeHandler(handler)
                    try:
                        handler.close()
                    except Exception:
                        pass
            _SETUP_DONE = True
            logging.getLogger("kabootar.runtime").info(
                "runtime logging ready (file logging disabled) platform=%s",
                (os.getenv("KABOOTAR_PLATFORM", "") or "").strip().lower(),
            )
            return log_path

        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler_exists = False
        for handler in root.handlers:
            if isinstance(handler, RotatingFileHandler) and Path(getattr(handler, "baseFilename", "")) == log_path:
                file_handler_exists = True
                handler.setFormatter(formatter)
                handler.setLevel(logging.INFO)
                break

        if not file_handler_exists:
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

        _SETUP_DONE = True
        logging.getLogger("kabootar.runtime").info("runtime logging ready file=%s", log_path)
        return log_path


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def record_event(kind: str, level: str = "info", **payload: object) -> dict[str, object]:
    event = {
        "ts": int(time.time()),
        "kind": kind,
        "level": level,
        **{str(k): _jsonable(v) for k, v in payload.items()},
    }
    with _EVENT_LOCK:
        _EVENTS.append(event)

    setup_logging()
    logger = logging.getLogger("kabootar.events")
    message = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    if level == "error":
        logger.error(message)
    elif level == "warning":
        logger.warning(message)
    else:
        logger.info(message)
    return event


def snapshot_events(limit: int = 80) -> list[dict[str, object]]:
    with _EVENT_LOCK:
        data = list(_EVENTS)
    if limit > 0:
        data = data[-limit:]
    return list(reversed(data))


def tail_log_lines(limit: int = 200) -> list[str]:
    path = resolve_log_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except Exception as exc:
        return [f"log_read_error: {exc}"]
    return [line.rstrip("\r\n") for line in lines[-max(1, limit) :]]


def runtime_summary() -> dict[str, object]:
    log_path = resolve_log_path()
    db_path = resolve_database_path()
    meta = app_meta()
    return {
        "started_at": _STARTED_AT,
        "database_path": str(db_path),
        "runtime_dir": str(resolve_runtime_dir()),
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
        "log_size": (log_path.stat().st_size if log_path.exists() else 0),
        "app": meta.as_dict(),
    }
