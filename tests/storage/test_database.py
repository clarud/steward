import sqlite3
from pathlib import Path

from steward.storage import INITIAL_SCHEMA_VERSION, initialize_database
from steward.storage.database import (
    FRAGMENTS_SCHEMA_VERSION,
    LEXICAL_SEARCH_SCHEMA_VERSION,
    SOURCES_SCHEMA_VERSION,
)


def test_initialize_database_creates_database_and_migration_ledger(tmp_path: Path) -> None:
    database_path = tmp_path / ".steward" / "steward.db"

    initialize_database(database_path)

    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        migrations = connection.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        source_columns = connection.execute("PRAGMA table_info(sources)").fetchall()

    assert [migration[0] for migration in migrations] == [
        INITIAL_SCHEMA_VERSION,
        SOURCES_SCHEMA_VERSION,
        FRAGMENTS_SCHEMA_VERSION,
        LEXICAL_SEARCH_SCHEMA_VERSION,
    ]
    assert all(migration[1] for migration in migrations)
    assert [column[1] for column in source_columns] == [
        "id",
        "path",
        "content_hash",
        "source_type",
        "size_bytes",
        "modified_at",
        "first_seen_at",
        "last_seen_at",
        "status",
    ]


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"

    initialize_database(database_path)
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]

    assert migration_count == 4
