from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.utils import deserialize_photo_items


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    source_url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(255), default="")
    avatar_url: Mapped[str] = mapped_column(String(1024), default="")
    avatar_mime: Mapped[str] = mapped_column(String(64), default="")
    avatar_b64: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="channel", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", name="uq_channel_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)

    message_id: Mapped[int] = mapped_column(Integer, index=True)
    published_at: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    media_kind: Mapped[str] = mapped_column(String(32), default="")
    photo_mime: Mapped[str] = mapped_column(String(64), default="")
    photo_b64: Mapped[str] = mapped_column(Text, default="")
    photos_json: Mapped[str] = mapped_column(Text, default="")
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    reply_author: Mapped[str] = mapped_column(String(255), default="")
    reply_text: Mapped[str] = mapped_column(Text, default="")
    forward_source: Mapped[str] = mapped_column(String(255), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    channel: Mapped[Channel] = relationship(back_populates="messages")

    @property
    def photo_items(self) -> list[dict[str, str]]:
        return deserialize_photo_items(self.photos_json, fallback_mime=self.photo_mime, fallback_b64=self.photo_b64)
