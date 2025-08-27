"""
Database connection helpers for compatibility with tests and legacy imports.
"""
from ..core.config import get_config


def get_database_url() -> str:
    """Return SQLAlchemy database URL from unified settings.
    Compatible with tests expecting `bili_curator.app.database.connection.get_database_url`.
    """
    return get_config().get_database_url()
