from __future__ import annotations

import os
from typing import Any

from sqlalchemy import text

from app.db import engine

DEFAULTS: dict[str, str] = {
    # Server behavior: free | fixed
    "dns_access_mode": os.getenv("DNS_ACCESS_MODE", "free"),
    # Optional password protection for DNS bridge
    "dns_password": os.getenv("DNS_PASSWORD", ""),
    # Session TTL in seconds when password mode is enabled
    "dns_session_ttl_seconds": os.getenv("DNS_SESSION_TTL_SECONDS", "3600"),
    # Channels/proxies used by DNS bridge to fetch Telegram data.
    "telegram_channels": os.getenv("TELEGRAM_CHANNELS", ""),
    "telegram_proxies": os.getenv("TELEGRAM_PROXIES", ""),
    "dns_domain": os.getenv("DNS_DOMAIN", "t.example.com"),
    "dns_port": os.getenv("DNS_PORT", "5533"),
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
