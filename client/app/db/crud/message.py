from sqlalchemy import select

from app.db.models import Message


def get_by_channel_message_id(db, channel_id: int, message_id: int) -> Message | None:
    return db.scalar(select(Message).where(Message.channel_id == channel_id, Message.message_id == message_id))


def upsert_message(db, *, channel_id: int, message_id: int, defaults: dict[str, object]) -> Message:
    row = get_by_channel_message_id(db, channel_id, message_id)
    if not row:
        row = Message(channel_id=channel_id, message_id=message_id)
        db.add(row)

    for key, value in defaults.items():
        if hasattr(row, key):
            setattr(row, key, value)
    db.flush()
    return row

