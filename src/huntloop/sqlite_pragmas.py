"""Connect-time SQLite PRAGMAs, installed once process-wide."""

from sqlalchemy import event
from sqlalchemy.engine import Engine

_INSTALLED = False


def install_sqlite_pragmas() -> None:
    """Register a global Engine 'connect' listener that enables WAL + FK enforcement.

    Idempotent: safe to call from multiple modules at import time.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        # This listener fires for EVERY Engine in the process, including a
        # Postgres engine during the OPS-04 portability test. Guard on the
        # driver module so we never send PRAGMA to Postgres.
        if not dbapi_connection.__class__.__module__.startswith("sqlite3"):
            return
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    _INSTALLED = True
