#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

from app.versioning import app_meta
from sqlalchemy import inspect, text


def cmd_dns_bridge_server():
    from app.dns_bridge import run_dns_bridge_server

    run_dns_bridge_server()


def _run_alembic(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    subprocess.check_call([sys.executable, "-m", "alembic", *args], env=env)


def _bootstrap_legacy_alembic() -> None:
    from app.config import settings
    from app.db import engine, ensure_schema

    if not settings.database_url.startswith("sqlite:///"):
        return

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    legacy_tables = tables - {"alembic_version"}
    if not legacy_tables:
        return

    version_rows = 0
    if "alembic_version" in tables:
        with engine.begin() as conn:
            version_rows = int(conn.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar() or 0)
    if version_rows > 0:
        return

    ensure_schema()
    _run_alembic("stamp", "head")


def _normalize_existing_schema() -> None:
    from app.db import engine, ensure_schema

    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    if tables:
        ensure_schema()


def cmd_migrate():
    _normalize_existing_schema()
    _bootstrap_legacy_alembic()
    _run_alembic("upgrade", "head")


def cmd_check_migrations():
    _normalize_existing_schema()
    _bootstrap_legacy_alembic()
    _run_alembic("check")


def cmd_version():
    meta = app_meta()
    print(f"{meta.app_name} server {meta.version_name} ({meta.version_code}) [{meta.release_channel}]")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["dns-bridge-server", "migrate", "check-migrations", "version"])
    args, _ = p.parse_known_args()

    if args.command == "dns-bridge-server":
        cmd_dns_bridge_server()
    elif args.command == "migrate":
        cmd_migrate()
    elif args.command == "check-migrations":
        cmd_check_migrations()
    elif args.command == "version":
        cmd_version()


if __name__ == "__main__":
    main()
