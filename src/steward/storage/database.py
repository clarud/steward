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
ACTIVITY_SCHEMA_VERSION = 9
KNOWLEDGE_SCHEMA_VERSION = 10
CONCEPT_ALIAS_SCHEMA_VERSION = 11
CLAIMS_SCHEMA_VERSION = 12
CLAIM_EVIDENCE_SCHEMA_VERSION = 13
TRAVEL_RECORDS_SCHEMA_VERSION = 14
TRAVEL_EVIDENCE_SCHEMA_VERSION = 15
ORGANIZATION_PROPOSAL_DETAILS_SCHEMA_VERSION = 16
ORGANIZATION_PROPOSAL_CONFIDENCE_SCHEMA_VERSION = 17
CALENDAR_EVENT_LINKS_SCHEMA_VERSION = 18
SOURCE_PRIVACY_SCHEMA_VERSION = 19
ORGANIZATION_APPROVAL_THREADS_SCHEMA_VERSION = 20
ACTION_PROPOSALS_SCHEMA_VERSION = 21
TELEGRAM_UPDATE_DELIVERIES_SCHEMA_VERSION = 22
TRAVEL_RECORD_REFERENCES_SCHEMA_VERSION = 23
TELEGRAM_DELIVERY_HISTORY_SCHEMA_VERSION = 24
TELEGRAM_DELIVERY_DEAD_LETTER_SCHEMA_VERSION = 25
RECEIPT_RECORDS_SCHEMA_VERSION = 26
RECEIPT_RECORD_EVIDENCE_SCHEMA_VERSION = 27

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
        WORKSPACE_SOURCES_SCHEMA_VERSION,
        """
        CREATE TABLE workspace_sources (workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            PRIMARY KEY (workspace_id, source_id))
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
        ACTIVITY_SCHEMA_VERSION,
        """CREATE TABLE activity_events (id INTEGER PRIMARY KEY, event_type TEXT NOT NULL,
            object_id TEXT, details TEXT NOT NULL, occurred_at TEXT NOT NULL)""",
    ),
    (
        KNOWLEDGE_SCHEMA_VERSION,
        """CREATE TABLE concepts (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""",
    ),
    (
        CONCEPT_ALIAS_SCHEMA_VERSION,
        """
        CREATE TABLE concept_aliases (concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            alias TEXT NOT NULL UNIQUE, PRIMARY KEY (concept_id, alias))""",
    ),
    (
        CLAIMS_SCHEMA_VERSION,
        """CREATE TABLE claims (id INTEGER PRIMARY KEY, concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE, text TEXT NOT NULL, created_at TEXT NOT NULL)""",
    ),
    (
        CLAIM_EVIDENCE_SCHEMA_VERSION,
        """CREATE TABLE claim_evidence (claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE, fragment_id INTEGER NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE, PRIMARY KEY (claim_id, fragment_id))""",
    ),
    (
        TRAVEL_RECORDS_SCHEMA_VERSION,
        """CREATE TABLE travel_records (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            flight_number TEXT, departure TEXT, arrival TEXT, departure_time TEXT, arrival_time TEXT, booking_reference TEXT)""",
    ),
    (
        TRAVEL_EVIDENCE_SCHEMA_VERSION,
        """CREATE TABLE travel_record_evidence (travel_record_id INTEGER NOT NULL REFERENCES travel_records(id) ON DELETE CASCADE,
            field_name TEXT NOT NULL, fragment_id INTEGER NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE,
            PRIMARY KEY (travel_record_id, field_name))""",
    ),
    (
        ORGANIZATION_PROPOSAL_DETAILS_SCHEMA_VERSION,
        """
        ALTER TABLE organization_proposals
        ADD COLUMN proposal_type TEXT NOT NULL DEFAULT 'keep_in_inbox'
        """,
    ),
    (
        ORGANIZATION_PROPOSAL_CONFIDENCE_SCHEMA_VERSION,
        """
        ALTER TABLE organization_proposals
        ADD COLUMN confidence REAL NOT NULL DEFAULT 0.0
        """,
    ),
    (
        CALENDAR_EVENT_LINKS_SCHEMA_VERSION,
        """
        CREATE TABLE calendar_event_links (
            idempotency_key TEXT PRIMARY KEY,
            travel_record_id INTEGER NOT NULL REFERENCES travel_records(id) ON DELETE CASCADE,
            external_event_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """,
    ),
    (
        SOURCE_PRIVACY_SCHEMA_VERSION,
        """CREATE TABLE source_privacy_policies (source_id INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE, rule TEXT NOT NULL)""",
    ),
    (
        ORGANIZATION_APPROVAL_THREADS_SCHEMA_VERSION,
        """
        CREATE TABLE organization_approval_threads (
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            proposal_id INTEGER NOT NULL UNIQUE REFERENCES organization_proposals(id) ON DELETE CASCADE,
            thread_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (platform, chat_id)
        )
        """,
    ),
    (
        ACTION_PROPOSALS_SCHEMA_VERSION,
        """
        CREATE TABLE action_proposals (
            id INTEGER PRIMARY KEY,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        )
        """,
    ),
    (
        TELEGRAM_UPDATE_DELIVERIES_SCHEMA_VERSION,
        """
        CREATE TABLE telegram_update_deliveries (
            update_id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('processing', 'delivered')),
            claimed_at TEXT NOT NULL,
            delivered_at TEXT
        )
        """,
    ),
    (
        TRAVEL_RECORD_REFERENCES_SCHEMA_VERSION,
        """
        CREATE TABLE travel_record_references (
            id INTEGER PRIMARY KEY,
            travel_record_id INTEGER NOT NULL REFERENCES travel_records(id) ON DELETE CASCADE,
            reference_type TEXT NOT NULL,
            value TEXT NOT NULL,
            fragment_id INTEGER NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE,
            UNIQUE (travel_record_id, reference_type, value)
        )
        """,
    ),
    (
        TELEGRAM_DELIVERY_HISTORY_SCHEMA_VERSION,
        """
        CREATE TABLE telegram_delivery_history (
            id INTEGER PRIMARY KEY,
            update_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('claimed', 'reclaimed', 'released', 'delivered')),
            occurred_at TEXT NOT NULL
        )
        """,
    ),
    (
        TELEGRAM_DELIVERY_DEAD_LETTER_SCHEMA_VERSION,
        """
        CREATE TABLE telegram_delivery_dead_letters (
            update_id TEXT PRIMARY KEY,
            attempts INTEGER NOT NULL CHECK (attempts > 0),
            failed_at TEXT NOT NULL
        )
        """,
    ),
    (
        RECEIPT_RECORDS_SCHEMA_VERSION,
        """CREATE TABLE receipt_records (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            merchant TEXT, total_cents INTEGER, currency TEXT, purchased_at TEXT, receipt_number TEXT)""",
    ),
    (
        RECEIPT_RECORD_EVIDENCE_SCHEMA_VERSION,
        """CREATE TABLE receipt_record_evidence (receipt_record_id INTEGER NOT NULL REFERENCES receipt_records(id) ON DELETE CASCADE,
            field_name TEXT NOT NULL, fragment_id INTEGER NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE,
            PRIMARY KEY (receipt_record_id, field_name))""",
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
