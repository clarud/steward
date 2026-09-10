"""Process-lifetime coordination for local Steward services."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3


class RuntimeAlreadyRunningError(RuntimeError):
    """Another process owns this data directory's Telegram runtime."""


@contextmanager
def telegram_runtime_lock(data_dir: Path):
    """Hold an OS-backed SQLite lock, released even after process termination.

    The separate coordination database contains no application data. Its file
    may remain after shutdown; ownership comes from the open transaction, not
    the file's presence or a potentially stale PID.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / "telegram-runtime.db", timeout=0)
    try:
        try:
            connection.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise RuntimeAlreadyRunningError(
                    "Telegram is already running for this Steward data directory. Stop that instance before starting another."
                ) from error
            raise
        yield
    finally:
        connection.close()
