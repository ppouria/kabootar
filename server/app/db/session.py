from sqlalchemy import create_engine, text

from app.config import ensure_data_dir, settings

from .base import Base

ensure_data_dir()

engine = create_engine(settings.database_url, future=True)


def ensure_schema() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(app_settings)")).mappings().all()
        key_row = next((row for row in rows if str(row.get("name") or "") == "key"), None)
        if key_row and not int(key_row.get("notnull") or 0):
            conn.execute(text("ALTER TABLE app_settings RENAME TO app_settings__legacy"))
            conn.execute(
                text(
                    """
                    CREATE TABLE app_settings (
                      key TEXT NOT NULL PRIMARY KEY,
                      value TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(text("INSERT INTO app_settings(key, value) SELECT key, value FROM app_settings__legacy"))
            conn.execute(text("DROP TABLE app_settings__legacy"))

