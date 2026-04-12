#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

from app.runtime_debug import record_event, setup_logging
from app.versioning import app_meta
from sqlalchemy import inspect, text


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


def cmd_migrate():
    _bootstrap_legacy_alembic()
    _run_alembic("upgrade", "head")


def cmd_check_migrations():
    _bootstrap_legacy_alembic()
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


def cmd_version():
    meta = app_meta()
    print(f"{meta.app_name} client {meta.version_name} ({meta.version_code}) [{meta.release_channel}]")


def cmd_dns_scan(argv: list[str]) -> None:
    from app.dns_bridge import parse_dns_resolvers_text, scan_dns_resolvers
    from app.settings_store import get_setting

    parser = argparse.ArgumentParser(prog="manage.py dns-scan")
    parser.add_argument("--domain", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--deep", action="store_true", help="Scan configured resolvers + public resolver pool")
    parser.add_argument("--no-e2e", action="store_true", help="Skip E2E checks")
    parser.add_argument("--no-auto-apply", action="store_true", help="Do not auto-apply selected resolvers")
    parser.add_argument("--timeout-ms", type=int, default=1800)
    parser.add_argument("--concurrency", type=int, default=96)
    parser.add_argument("--query-size", type=int, default=220)
    parser.add_argument("--e2e-threshold", type=int, default=4)
    parser.add_argument("--e2e-max", type=int, default=48)
    parser.add_argument("--e2e-concurrency", type=int, default=8)
    parser.add_argument("--resolvers-file", default="", help="Optional text file with one resolver per line")
    args = parser.parse_args(argv)

    resolver_raw = (get_setting("dns_resolvers", "") or "").strip()
    if args.resolvers_file:
        with open(args.resolvers_file, "r", encoding="utf-8") as f:
            resolver_raw = f.read()
    resolver_targets = parse_dns_resolvers_text(resolver_raw, use_system=False)

    progress_state = {
        "transparent": None,
        "scanned": 0,
        "total": 0,
        "working": 0,
        "e2e_tested": 0,
        "e2e_total": 0,
        "e2e_passed": 0,
    }

    def on_progress(event: dict) -> None:
        kind = str(event.get("kind") or "").strip().lower()
        if kind == "transparent_proxy":
            progress_state["transparent"] = bool(event.get("detected"))
            state = "DETECTED" if progress_state["transparent"] else "not detected"
            print(f"  Checking for transparent DNS proxy... {state}")
            print()
            return
        if kind == "scan_progress":
            progress_state["scanned"] = int(event.get("scanned", 0) or 0)
            progress_state["total"] = int(event.get("total", 0) or 0)
            progress_state["working"] = int(event.get("working", 0) or 0)
            print(
                f"\r  Scanning... {progress_state['scanned']}/{progress_state['total']}  (working: {progress_state['working']})",
                end="",
                flush=True,
            )
            return
        if kind == "e2e_start":
            progress_state["e2e_total"] = int(event.get("total", 0) or 0)
            if progress_state["e2e_total"] > 0:
                print()
            return
        if kind == "e2e_progress":
            progress_state["e2e_tested"] = int(event.get("tested", 0) or 0)
            progress_state["e2e_passed"] = int(event.get("passed", 0) or 0)
            total = int(event.get("total", progress_state["e2e_total"]) or progress_state["e2e_total"] or 0)
            progress_state["e2e_total"] = total
            print(
                f"\r  Scanning... {progress_state['scanned']}/{progress_state['total']}  (working: {progress_state['working']})"
                f"  |  E2E: {progress_state['e2e_passed']}/{progress_state['e2e_tested']} passed",
                end="",
                flush=True,
            )
            return

    print()
    print("====================================================")
    print("            Kabootar DNS Scanner")
    print("====================================================")
    print()

    result = scan_dns_resolvers(
        domain=args.domain,
        password=args.password,
        resolvers=resolver_targets,
        include_public_pool=bool(args.deep),
        timeout_ms=args.timeout_ms,
        concurrency=args.concurrency,
        query_size=args.query_size,
        e2e_enabled=not args.no_e2e,
        e2e_threshold=args.e2e_threshold,
        e2e_max_candidates=args.e2e_max,
        e2e_concurrency=args.e2e_concurrency,
        auto_apply_best=not args.no_auto_apply,
        progress=on_progress,
    )
    print()
    print()
    print("  -- Results --------------------------------------")
    print()
    print(
        f"  Total: {int(result.get('scanned', 0) or 0)} | Working: {int(result.get('working', 0) or 0)}"
        f" | Timeout: {int(result.get('timeout', 0) or 0)} | Error: {int(result.get('error', 0) or 0)}"
    )
    print(f"  Elapsed: {int(result.get('elapsed_ms', 0) or 0)}ms")
    print()

    compatible = list(result.get("compatible") or [])
    if compatible:
        print(f"  Compatible resolvers ({len(compatible)}):")
        print()
        print("  RESOLVER           SCORE     MS  DETAILS")
        print("  ----------------   -----  -----  ------------------------------")
        for item in compatible[:48]:
            resolver = str(item.get("resolver") or "-")
            score = int(item.get("score", 0) or 0)
            latency = int(item.get("latency_ms", 0) or 0)
            details = str(item.get("details") or "")
            marker = "*" if score >= 6 else " "
            print(f" {marker}{resolver:<18} {score}/6    {latency:>4}ms  {details}")
        print()
    else:
        print("  No compatible resolvers found.")
        print()

    e2e = result.get("e2e") if isinstance(result.get("e2e"), dict) else {}
    if e2e:
        tested = int(e2e.get("tested", 0) or 0)
        passed = int(e2e.get("passed", 0) or 0)
        if tested > 0:
            print(f"  E2E: {passed}/{tested} passed")
            print()

    selected = str(result.get("selected_resolver") or "")
    if selected:
        print(f"  Selected resolver: {selected}")
    if bool(result.get("auto_applied")):
        print("  Auto apply: enabled")
    print("  Credit: Inspired by SlipNet DNS Scanner.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["migrate", "check-migrations", "sync", "web", "version", "dns-scan"])
    args, _ = p.parse_known_args()

    if args.command == "migrate":
        cmd_migrate()
    elif args.command == "check-migrations":
        cmd_check_migrations()
    elif args.command == "sync":
        cmd_sync()
    elif args.command == "web":
        cmd_web()
    elif args.command == "version":
        cmd_version()
    elif args.command == "dns-scan":
        cmd_dns_scan(sys.argv[2:])


if __name__ == "__main__":
    main()
