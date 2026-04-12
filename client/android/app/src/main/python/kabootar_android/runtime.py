from __future__ import annotations

import os
import shutil
import socket
import threading
import time
import urllib.request
from importlib.resources import files
from pathlib import Path

from werkzeug.serving import make_server

_LOCK = threading.Lock()
_SERVER = None
_THREAD = None
_URL = ""


def _copy_tree(resource_root, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for child in resource_root.iterdir():
        dest = target_dir / child.name
        if child.is_dir():
            _copy_tree(child, dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with child.open("rb") as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def _prepare_frontend(runtime_root: Path) -> Path:
    frontend_root = runtime_root / "frontend"
    templates_dir = frontend_root / "templates"
    static_dir = frontend_root / "static"
    if templates_dir.exists() and static_dir.exists():
        return frontend_root

    package_root = files("kabootar_android_assets").joinpath("frontend")
    _copy_tree(package_root, frontend_root)
    return frontend_root


def _wait_ready(url: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.time() + timeout_seconds
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/app/meta", timeout=2.5) as resp:
                if 200 <= int(getattr(resp, "status", 0) or 0) < 500:
                    return
        except Exception as exc:  # pragma: no cover - platform-specific timing
            last_exc = exc
            time.sleep(0.35)
    raise RuntimeError(f"embedded_backend_not_ready:{last_exc}")


def _purge_runtime_logs(runtime_data_root: Path) -> None:
    log_dir = runtime_data_root / "logs"
    if not log_dir.exists():
        return
    for path in log_dir.glob("client.log*"):
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            # Ignore startup cleanup failures on restricted filesystems.
            pass


def _configure_environment(
    files_dir: str,
    cache_dir: str,
    port: int,
    app_name: str,
    version_name: str,
    version_code: int,
    release_channel: str,
    debug_enabled: bool = False,
) -> tuple[Path, Path, str]:
    files_root = Path(files_dir).expanduser().resolve()
    cache_root = Path(cache_dir).expanduser().resolve()
    runtime_root = files_root / "kabootar"
    data_root = runtime_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    frontend_root = _prepare_frontend(runtime_root)
    database_path = (data_root / "app.db").resolve()
    encoder_path = (runtime_root / "persian_encoder" / "lexicon.db").resolve()
    encoder_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["KABOOTAR_PLATFORM"] = "android"
    os.environ["APP_HOST"] = "127.0.0.1"
    os.environ["APP_PORT"] = str(port)
    os.environ["APP_SECRET_KEY"] = "kabootar-android-local"
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    os.environ["KABOOTAR_RUNTIME_DIR"] = str(data_root)
    os.environ["KABOOTAR_FRONTEND_ROOT"] = str(frontend_root)
    os.environ["KABOOTAR_PERSIAN_ENCODER_DB"] = str(encoder_path)
    os.environ["KABOOTAR_APP_NAME"] = app_name
    os.environ["KABOOTAR_VERSION_NAME"] = version_name
    os.environ["KABOOTAR_VERSION_CODE"] = str(version_code)
    os.environ["KABOOTAR_RELEASE_CHANNEL"] = release_channel
    os.environ["KABOOTAR_DEBUG_ENABLED"] = "1" if bool(debug_enabled) else "0"
    os.environ["KABOOTAR_FILE_LOG_ENABLED"] = "1" if bool(debug_enabled) else "0"

    url = f"http://127.0.0.1:{port}"
    return runtime_root, data_root, url


def start_backend(
    files_dir: str,
    cache_dir: str,
    port: int = 18765,
    app_name: str = "Kabootar",
    version_name: str = "0.7.2",
    version_code: int = 11,
    release_channel: str = "stable",
    debug_enabled: bool = False,
) -> str:
    global _SERVER, _THREAD, _URL

    with _LOCK:
        runtime_root, data_root, url = _configure_environment(
            files_dir=files_dir,
            cache_dir=cache_dir,
            port=int(port),
            app_name=app_name,
            version_name=version_name,
            version_code=int(version_code),
            release_channel=release_channel,
            debug_enabled=bool(debug_enabled),
        )
        os.chdir(runtime_root)

        if _THREAD is not None and _THREAD.is_alive() and _URL:
            _wait_ready(_URL, timeout_seconds=6.0)
            return _URL

        # Always start from a clean log file on Android fresh backend start.
        _purge_runtime_logs(data_root)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", int(port))) == 0:
                _URL = url
                _wait_ready(_URL, timeout_seconds=6.0)
                return _URL
        finally:
            sock.close()

        from app.web import create_app

        app = create_app()
        _SERVER = make_server("127.0.0.1", int(port), app, threaded=True)
        _THREAD = threading.Thread(target=_SERVER.serve_forever, name="kabootar-android-backend", daemon=True)
        _THREAD.start()
        _URL = url

    _wait_ready(url)
    return url
