from sqlalchemy import delete, select

from app.db.models import AppSetting


def get_value(db, key: str) -> str | None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return None if row is None else str(row.value or "")


def set_value(db, key: str, value: str) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def delete_key(db, key: str) -> None:
    db.execute(delete(AppSetting).where(AppSetting.key == key))

