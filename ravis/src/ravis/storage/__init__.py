"""SQLite persistence and forward-only migrations.

SQLite because RAVIS is a local-first single-process service (RAVIS.md §17) and a
database server would be infrastructure bought for a problem that does not exist.
"""

from ravis.storage.database import Database, prepare_database

__all__ = ["Database", "prepare_database"]
