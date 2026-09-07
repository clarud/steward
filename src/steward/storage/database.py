"""SQLite database initialization and schema-version tracking."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

INITIAL_SCHEMA_VERSION = 1
SOURCES_SCHEMA_VERSION = 2
FRAGMENTS_SCHEMA_VERSION = 3
LEXICAL_SEARCH_SCHEMA_VERSION = 4
SEMANTIC_SEARCH_SCHEMA_VERSION = 5
WORKSPACES_SCHEMA_VERSION = 6
WORKSPACE_SOURCES_SCHEMA_VERSION = 7
ORGANIZATION_SCHEMA_VERSION = 8

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        SOURCES_SCHEMA_VERSION,
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            source_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            modified_at TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """,
    ),
    (
        FRAGMENTS_SCHEMA_VERSION,
        """
        CREATE TABLE source_fragments (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            heading TEXT,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            text TEXT NOT NULL,
            location TEXT NOT NULL,
            UNIQUE (source_id, ordinal)
        )
        """,
    ),
    (
        LEXICAL_SEARCH_SCHEMA_VERSION,
        """
        CREATE VIRTUAL TABLE source_fragments_fts USING fts5(
            fragment_id UNINDEXED,
            source_id UNINDEXED,
            heading,
            text,
            tokenize = 'unicode61'
        )
        """,
    ),
    (
        SEMANTIC_SEARCH_SCHEMA_VERSION,
        """
        CREATE TABLE source_fragment_embeddings (
            fragment_id INTEGER PRIMARY KEY
                REFERENCES source_fragments(id) ON DELETE CASCADE,
            model_name TEXT NOT NULL,
            dimension INTEGER NOT NULL CHECK (dimension > 0),
            vector_json TEXT NOT NULL
        )
        """,
    ),
    (
        WORKSPACES_SCHEMA_VERSION,
        """
        CREATE TABLE workspaces (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL, created_at TEXT NOT NULL)
        """,
    ),
    (
        ORGANIZATION_SCHEMA_VERSION,
        """
        CREATE TABLE organization_proposals (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL,
            suggested_path TEXT, rationale TEXT NOT NULL, score REAL NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL)
        """,
    ),
    (
        WORKSPACE_SOURCES_SCHEMA_VERSION,
        """
        CREATE TABLE workspace_sources (workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            PRIMARY KEY (workspace_id, source_id))
        """,
    ),
)


def initialize_database(database_path: Path) -> None:
    """Create Steward's local database and record its initial schema version.

    This is safe to call repeatedly. It creates the database directory, SQLite
    file, migration ledger, and every unapplied schema migration.
    """
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            """,
            (INITIAL_SCHEMA_VERSION, datetime.now(UTC).isoformat()),
        )

        for version, statement in MIGRATIONS:
            migration_applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if migration_applied is not None:
                continue

            connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
