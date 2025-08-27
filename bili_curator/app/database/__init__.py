# Database package shim for backward compatibility with tests and older imports.
# Exposes SQLAlchemy Base and a helper to get DB URL via application settings.

from .models import Base  # re-export for convenience
from .connection import get_database_url  # re-export

__all__ = [
    "Base",
    "get_database_url",
]
