#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys


def cmd_dns_bridge_server():
    from app.dns_bridge import run_dns_bridge_server

    run_dns_bridge_server()


def _run_alembic(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    subprocess.check_call([sys.executable, "-m", "alembic", *args], env=env)


def cmd_migrate():
    _run_alembic("upgrade", "head")


def cmd_check_migrations():
    _run_alembic("check")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["dns-bridge-server", "migrate", "check-migrations"])
    args, _ = p.parse_known_args()

    if args.command == "dns-bridge-server":
        cmd_dns_bridge_server()
    elif args.command == "migrate":
        cmd_migrate()
    elif args.command == "check-migrations":
        cmd_check_migrations()


if __name__ == "__main__":
    main()
