from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import ensure_data_dir, settings

ensure_data_dir()


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def ensure_schema() -> None:
    # Import lazily to avoid circular import issues.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    insp = inspect(engine)
    if "channels" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("channels")}
    with engine.begin() as conn:
        if "title" not in cols:
            conn.execute(text("ALTER TABLE channels ADD COLUMN title VARCHAR(255) NOT NULL DEFAULT ''"))
        if "avatar_url" not in cols:
            conn.execute(text("ALTER TABLE channels ADD COLUMN avatar_url VARCHAR(1024) NOT NULL DEFAULT ''"))
        if "avatar_mime" not in cols:
            conn.execute(text("ALTER TABLE channels ADD COLUMN avatar_mime VARCHAR(64) NOT NULL DEFAULT ''"))
        if "avatar_b64" not in cols:
            conn.execute(text("ALTER TABLE channels ADD COLUMN avatar_b64 TEXT NOT NULL DEFAULT ''"))

    if "messages" in insp.get_table_names():
        msg_cols = {c["name"] for c in insp.get_columns("messages")}
        with engine.begin() as conn:
            if "media_kind" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN media_kind VARCHAR(32) NOT NULL DEFAULT ''"))
            if "photo_mime" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN photo_mime VARCHAR(64) NOT NULL DEFAULT ''"))
            if "photo_b64" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN photo_b64 TEXT NOT NULL DEFAULT ''"))
            if "photos_json" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN photos_json TEXT NOT NULL DEFAULT ''"))
            if "reply_to_message_id" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN reply_to_message_id INTEGER"))
            if "reply_author" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN reply_author VARCHAR(255) NOT NULL DEFAULT ''"))
            if "reply_text" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN reply_text TEXT NOT NULL DEFAULT ''"))
            if "forward_source" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN forward_source VARCHAR(255) NOT NULL DEFAULT ''"))
