"""Compatibility shim for older imports.

Prefer importing from ``app.db.models``.
"""

from app.db.models import Channel, Message

__all__ = ["Channel", "Message"]

