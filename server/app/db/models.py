from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)

