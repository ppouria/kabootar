from sqlalchemy import select

from app.db.models import Channel


def get_by_source_url(db, source_url: str) -> Channel | None:
    return db.scalar(select(Channel).where(Channel.source_url == source_url))


def get_by_username(db, username: str) -> Channel | None:
    return db.scalar(select(Channel).where(Channel.username == username))


def upsert_channel(
    db,
    *,
    source_url: str,
    username: str,
    title: str = "",
    avatar_url: str = "",
    avatar_mime: str = "",
    avatar_b64: str = "",
) -> Channel:
    row = get_by_source_url(db, source_url)
    if not row:
        row = Channel(
            source_url=source_url,
            username=username,
            title=title,
            avatar_url=avatar_url,
            avatar_mime=avatar_mime,
            avatar_b64=avatar_b64,
        )
        db.add(row)
        db.flush()
        return row

    row.username = username or row.username
    row.title = title or row.title
    row.avatar_url = avatar_url or row.avatar_url
    row.avatar_mime = avatar_mime or row.avatar_mime
    row.avatar_b64 = avatar_b64 or row.avatar_b64
    db.flush()
    return row

