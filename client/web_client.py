#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlparse


def _normalize_domain(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if "://" in token:
        parsed = urlparse(token)
        token = (parsed.hostname or "").strip()
    token = token.strip().rstrip(".").lower()
    token = re.sub(r"\s+", "", token)
    return token


def _normalize_resolver(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    token = token.replace("dns://", "").strip()
    return token


def _prompt_if_missing(current: str, message: str, default: str = "") -> str:
    if current:
        return current
    hint = f" [{default}]" if default else ""
    entered = input(f"{message}{hint}: ").strip()
    if entered:
        return entered
    return default


def _resolve_database_url(database_url: str, db_path: str) -> tuple[str, str]:
    direct = (database_url or "").strip()
    if direct:
        return direct, db_path

    chosen = (db_path or "").strip()
    if not chosen:
        chosen = "./data/app.db"
    path = Path(chosen).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}", str(path)


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Kabootar client in web/server mode. "
            "It configures DNS bridge domain + resolver, then starts Flask web app."
        )
    )
    parser.add_argument("--domain", help="DNS bridge domain (for example: v.example.com)")
    parser.add_argument("--resolver", help="DNS resolver address (ip or ip:port)")
    parser.add_argument("--app-host", default="0.0.0.0", help="Flask bind host (default: 0.0.0.0)")
    parser.add_argument("--app-port", type=int, help="Flask bind port")
    parser.add_argument("--db-path", help="SQLite DB file path (default: ./data/app.db)")
    parser.add_argument("--database-url", help="Full SQLAlchemy database URL (overrides --db-path)")
    parser.add_argument("--dns-password", default="", help="Optional DNS domain password")
    parser.add_argument("--no-prompt", action="store_true", help="Do not ask interactive questions")
    return parser.parse_args()


def main() -> None:
    args = _build_args()

    domain = _normalize_domain(args.domain or "")
    resolver = _normalize_resolver(args.resolver or "")
    app_host = (args.app_host or "0.0.0.0").strip() or "0.0.0.0"
    app_port = int(args.app_port or 8090)
    db_path = (args.db_path or "").strip()
    database_url = (args.database_url or "").strip()
    dns_password = (args.dns_password or "").strip()

    if not args.no_prompt:
        domain = _normalize_domain(_prompt_if_missing(domain, "DNS bridge domain", "v.example.com"))
        resolver = _normalize_resolver(_prompt_if_missing(resolver, "DNS resolver (ip or ip:port)", "1.1.1.1"))
        app_host = _prompt_if_missing(app_host, "App host", "0.0.0.0")
        port_raw = _prompt_if_missing(str(app_port), "App port", "8090")
        try:
            app_port = int(port_raw)
        except Exception:
            app_port = 8090
        if not database_url:
            db_path = _prompt_if_missing(db_path, "Database path", "./data/app.db")

    if not domain or not resolver:
        raise SystemExit("Missing required values. Provide --domain and --resolver (or run without --no-prompt).")

    app_port = max(1, min(65535, int(app_port)))
    database_url, resolved_db_path = _resolve_database_url(database_url, db_path)

    os.environ["APP_HOST"] = app_host
    os.environ["APP_PORT"] = str(app_port)
    os.environ["DATABASE_URL"] = database_url

    from app.runtime_debug import record_event, setup_logging
    from app.settings_store import set_settings_bulk
    from app.versioning import app_meta
    from app.web import create_app

    setup_logging()
    set_settings_bulk(
        {
            "source_mode": "dns",
            "dns_domains": f"{domain}|{dns_password}" if dns_password else domain,
            "dns_resolvers": resolver,
            "dns_use_system_resolver": "0",
        }
    )

    meta = app_meta()
    record_event(
        "web_client_start",
        mode="web",
        domain=domain,
        resolver=resolver,
        app_host=app_host,
        app_port=app_port,
        database_url=database_url,
    )

    print(f"[kabootar-web] {meta.app_name} client {meta.version_name} ({meta.version_code})")
    print(f"[kabootar-web] domain={domain} resolver={resolver}")
    print(f"[kabootar-web] app=http://{app_host}:{app_port} db={resolved_db_path or database_url}")

    app = create_app()
    app.run(host=app_host, port=app_port, debug=False)


if __name__ == "__main__":
    main()

