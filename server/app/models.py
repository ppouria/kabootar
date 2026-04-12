"""Compatibility shim for older imports.

Prefer importing from ``app.db.models``.
"""

from app.db.models import AppSetting

__all__ = ["AppSetting"]

