from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import ensure_data_dir, settings

ensure_data_dir()


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, future=True)
