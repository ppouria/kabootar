#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys

from app.versioning import app_meta
from sqlalchemy import inspect, text


def _split_multi(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        for part in re.split(r"[,;\n\r،]+", str(raw or "")):
            token = part.strip()
            if token:
                out.append(token)
    return out


def _normalize_domains(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        domain = (value or "").strip().lower().rstrip(".")
        if not domain or domain in seen:
            continue
        seen.add(domain)
        out.append(domain)
    return out


def _apply_dns_bridge_overrides(args: argparse.Namespace) -> dict[str, str]:
    from app.settings_store import set_setting

    applied: dict[str, str] = {}

    domains = _normalize_domains(_split_multi(args.domain))
    if domains:
        set_setting("dns_domain", domains[0])
        set_setting("dns_domains", ",".join(domains[1:]))
        applied["dns_domain"] = domains[0]
        applied["dns_domains"] = ",".join(domains[1:])

    channels = _split_multi(args.channels)
    if channels:
        set_setting("telegram_channels", ",".join(channels))
        applied["telegram_channels"] = ",".join(channels)

    proxies = _split_multi(args.proxies)
    if proxies:
        set_setting("telegram_proxies", ",".join(proxies))
        applied["telegram_proxies"] = ",".join(proxies)

    if args.port is not None:
        set_setting("dns_port", args.port)
        applied["dns_port"] = str(args.port)
    if args.bind is not None:
        set_setting("dns_bind_address", args.bind.strip())
        applied["dns_bind_address"] = args.bind.strip()
    if args.ttl is not None:
        set_setting("dns_ttl", args.ttl)
        applied["dns_ttl"] = str(args.ttl)
    if args.refresh_seconds is not None:
        set_setting("dns_refresh_seconds", args.refresh_seconds)
        applied["dns_refresh_seconds"] = str(args.refresh_seconds)
    if args.recent_per_channel is not None:
        set_setting("dns_recent_per_channel", args.recent_per_channel)
        applied["dns_recent_per_channel"] = str(args.recent_per_channel)
    if args.access_mode is not None:
        set_setting("dns_access_mode", args.access_mode)
        applied["dns_access_mode"] = args.access_mode
    if args.password is not None:
        set_setting("dns_password", args.password)
        applied["dns_password"] = "<set>" if args.password else "<empty>"
    if args.session_ttl is not None:
        set_setting("dns_session_ttl_seconds", args.session_ttl)
        applied["dns_session_ttl_seconds"] = str(args.session_ttl)
    if args.fallback_host is not None:
        set_setting("dns_fallback_host", args.fallback_host.strip())
        applied["dns_fallback_host"] = args.fallback_host.strip()
    if args.fallback_port is not None:
        set_setting("dns_fallback_port", args.fallback_port)
        applied["dns_fallback_port"] = str(args.fallback_port)

    return applied


def cmd_dns_bridge_server(args: argparse.Namespace):
    applied = _apply_dns_bridge_overrides(args)
    if applied:
        printable = ", ".join(f"{k}={v}" for k, v in applied.items())
        print(f"[dns-bridge] applied cli overrides: {printable}")

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
    p = argparse.ArgumentParser(
        description=(
            "Kabootar server manager. "
            "If command is omitted, dns-bridge-server is used."
        )
    )
    p.add_argument(
        "command",
        nargs="?",
        default="dns-bridge-server",
        choices=["dns-bridge-server", "migrate", "check-migrations", "version"],
    )
    p.add_argument("-domain", "--domain", "--domains", nargs="+", help="One or more bridge domains. First value is primary.")
    p.add_argument("-channels", "--channels", nargs="+", help="One or more Telegram channels to serve.")
    p.add_argument("-proxies", "--proxies", nargs="+", help="One or more Telegram proxies.")
    p.add_argument("-port", "--port", type=int, help="DNS listen port.")
    p.add_argument("-bind", "--bind", help="DNS listen address.")
    p.add_argument("--ttl", type=int, help="TXT response TTL seconds.")
    p.add_argument("--refresh-seconds", type=int, help="Background refresh interval seconds.")
    p.add_argument("--recent-per-channel", type=int, help="Recent Telegram messages per channel.")
    p.add_argument("--access-mode", choices=["free", "fixed"], help="Bridge access mode.")
    p.add_argument("--password", help="Bridge password. Use empty string to clear.")
    p.add_argument("--session-ttl", type=int, help="Session TTL seconds when password is enabled.")
    p.add_argument("--fallback-host", help="Fallback upstream resolver host.")
    p.add_argument("--fallback-port", type=int, help="Fallback upstream resolver port.")
    args, _ = p.parse_known_args()

    if args.command == "dns-bridge-server":
        cmd_dns_bridge_server(args)
    elif args.command == "migrate":
        cmd_migrate()
    elif args.command == "check-migrations":
        cmd_check_migrations()
    elif args.command == "version":
        cmd_version()


if __name__ == "__main__":
    main()
