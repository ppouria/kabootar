#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

from app.runtime_debug import record_event, setup_logging


def _run_alembic(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    subprocess.check_call([sys.executable, "-m", "alembic", *args], env=env)


def cmd_migrate():
    _run_alembic("upgrade", "head")


def cmd_check_migrations():
    _run_alembic("check")


def cmd_sync():
    from app.service import sync_once

    setup_logging()
    record_event("manage_sync_command")
    result = sync_once()
    print(result)


def cmd_web():
    from app.config import settings
    from app.web import create_app

    setup_logging()
    record_event("manage_web_command", host=settings.app_host, port=settings.app_port)
    app = create_app()
    app.run(host=settings.app_host, port=settings.app_port, debug=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["migrate", "check-migrations", "sync", "web"])
    args, _ = p.parse_known_args()

    if args.command == "migrate":
        cmd_migrate()
    elif args.command == "check-migrations":
        cmd_check_migrations()
    elif args.command == "sync":
        cmd_sync()
    elif args.command == "web":
        cmd_web()


if __name__ == "__main__":
    main()
