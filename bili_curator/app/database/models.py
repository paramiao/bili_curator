"""
Database models shim.
Expose SQLAlchemy Base for compatibility: tests import
`from bili_curator.app.database.models import Base`.
We reuse the project's canonical Base from `bili_curator.app.models`.
"""
from ..models import Base  # re-export

__all__ = ["Base"]
