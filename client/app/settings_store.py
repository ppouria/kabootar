from __future__ import annotations

import os
import subprocess
from typing import Any

from sqlalchemy import text

from app.db import engine

DEFAULTS: dict[str, str] = {
    "source_mode": "dns",  # direct | dns
    "direct_channels": "",
    "direct_proxies": "",
    # DNS transport client config
    "dns_password": "",  # legacy/global fallback; per-domain password is in dns_channel_routes
    "dns_client_id": "",
    "dns_resolvers": "",  # lines: resolver or resolver:port
    "dns_domains": "",  # lines: domain|password(optional)
    "dns_query_size": "220",
    "dns_timeout_seconds": "3",
    "dns_query_retries": "4",
    "dns_meta_retries": "2",
    "dns_use_system_resolver": "1",
    "dns_channel_routes": "",  # lines: channel|domain|password(optional)
    "dns_client_channels": "",
    # Legacy keys kept for backward compatibility/migration of old data.
    "dns_domain": "",
    "dns_server": "",
    "dns_port": "5533",
    "dns_sources": "",
    "sync_interval_minutes": "1",
    "settings_password_hash": "",
    "app_password_hash": "",
    "app_auth_ttl_days": "7",
}


def ensure_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """
            )
        )
        for k, v in DEFAULTS.items():
            conn.execute(
                text("INSERT OR IGNORE INTO app_settings(key,value) VALUES (:k,:v)"),
                {"k": k, "v": v},
            )
        source_mode = (conn.execute(text("SELECT value FROM app_settings WHERE key='source_mode'")).scalar() or "").strip().lower()
        if source_mode == "direct":
            direct_channels = (conn.execute(text("SELECT value FROM app_settings WHERE key='direct_channels'")).scalar() or "").strip()
            direct_proxies = (conn.execute(text("SELECT value FROM app_settings WHERE key='direct_proxies'")).scalar() or "").strip()
            dns_domains = (conn.execute(text("SELECT value FROM app_settings WHERE key='dns_domains'")).scalar() or "").strip()
            dns_resolvers = (conn.execute(text("SELECT value FROM app_settings WHERE key='dns_resolvers'")).scalar() or "").strip()
            if not direct_channels and not direct_proxies and (dns_domains or dns_resolvers):
                conn.execute(text("UPDATE app_settings SET value='dns' WHERE key='source_mode'"))


def get_setting(key: str, default: str | None = None) -> str | None:
    ensure_table()
    with engine.begin() as conn:
        row = conn.execute(text("SELECT value FROM app_settings WHERE key=:k"), {"k": key}).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: Any) -> None:
    ensure_table()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO app_settings(key,value) VALUES (:k,:v)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """
            ),
            {"k": key, "v": str(value)},
        )


def all_settings() -> dict[str, str]:
    ensure_table()
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT key,value FROM app_settings")).fetchall()
    data = {k: v for k, v in rows}
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return data


def apply_sync_cron(minutes: int) -> tuple[bool, str]:
    minutes = max(1, min(59, int(minutes)))
    # Desktop Windows build runs its own in-process sync loop and does not use cron.
    if os.name == "nt" or (os.getenv("KABOOTAR_PLATFORM", "") or "").strip().lower() == "android":
        return True, ""

    try:
        root = os.path.dirname(os.path.dirname(__file__))
        script = os.path.join(root, "install_cron.sh")
        if not os.path.exists(script):
            return True, ""
        proc = subprocess.run(
            ["bash", script],
            cwd=root,
            env={**os.environ, "CRON_INTERVAL_MINUTES": str(minutes)},
            capture_output=True,
            text=True,
            check=True,
        )
        return True, (proc.stdout or "").strip()
    except Exception as exc:
        return False, str(exc)
