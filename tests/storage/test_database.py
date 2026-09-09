import sqlite3
from pathlib import Path

import pytest

from steward.storage import INITIAL_SCHEMA_VERSION, initialize_database, snapshot_database
from steward.storage.database import (
    FRAGMENTS_SCHEMA_VERSION,
    LEXICAL_SEARCH_SCHEMA_VERSION,
    SEMANTIC_SEARCH_SCHEMA_VERSION,
    WORKSPACE_SOURCES_SCHEMA_VERSION,
    WORKSPACES_SCHEMA_VERSION,
    ORGANIZATION_SCHEMA_VERSION,
    ACTIVITY_SCHEMA_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    CONCEPT_ALIAS_SCHEMA_VERSION,
    CLAIMS_SCHEMA_VERSION,
    CLAIM_EVIDENCE_SCHEMA_VERSION,
    TRAVEL_RECORDS_SCHEMA_VERSION,
    TRAVEL_EVIDENCE_SCHEMA_VERSION,
    ORGANIZATION_PROPOSAL_DETAILS_SCHEMA_VERSION,
    ORGANIZATION_PROPOSAL_CONFIDENCE_SCHEMA_VERSION,
    CALENDAR_EVENT_LINKS_SCHEMA_VERSION,
    ORGANIZATION_APPROVAL_THREADS_SCHEMA_VERSION,
    ACTION_PROPOSALS_SCHEMA_VERSION,
    TELEGRAM_UPDATE_DELIVERIES_SCHEMA_VERSION,
    TELEGRAM_DELIVERY_HISTORY_SCHEMA_VERSION,
    TELEGRAM_DELIVERY_DEAD_LETTER_SCHEMA_VERSION,
    TELEGRAM_CALLBACKS_SCHEMA_VERSION,
    PROVISIONAL_INTAKES_SCHEMA_VERSION,
    PROVISIONAL_INTAKE_REVISIONS_SCHEMA_VERSION,
    PROVISIONAL_INTAKE_CATEGORY_SCHEMA_VERSION,
    SOURCE_ROOTS_SCHEMA_VERSION,
    SOURCE_ROOT_EXCLUSIONS_SCHEMA_VERSION,
    TASKS_SCHEMA_VERSION,
    TELEGRAM_DELIVERY_RECOVERIES_SCHEMA_VERSION,
    RECEIPT_RECORDS_SCHEMA_VERSION,
    RECEIPT_RECORD_EVIDENCE_SCHEMA_VERSION,
    WARRANTY_RECORDS_SCHEMA_VERSION,
    WARRANTY_RECORD_EVIDENCE_SCHEMA_VERSION,
    KNOWLEDGE_ENRICHMENT_PROPOSALS_SCHEMA_VERSION,
    TRAVEL_RECORD_REFERENCES_SCHEMA_VERSION,
    SOURCE_PRIVACY_SCHEMA_VERSION,
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
            SEMANTIC_SEARCH_SCHEMA_VERSION,
            WORKSPACES_SCHEMA_VERSION,
            WORKSPACE_SOURCES_SCHEMA_VERSION,
            ORGANIZATION_SCHEMA_VERSION,
            ACTIVITY_SCHEMA_VERSION,
            KNOWLEDGE_SCHEMA_VERSION,
            CONCEPT_ALIAS_SCHEMA_VERSION,
            CLAIMS_SCHEMA_VERSION,
            CLAIM_EVIDENCE_SCHEMA_VERSION,
            TRAVEL_RECORDS_SCHEMA_VERSION,
            TRAVEL_EVIDENCE_SCHEMA_VERSION,
            ORGANIZATION_PROPOSAL_DETAILS_SCHEMA_VERSION,
            ORGANIZATION_PROPOSAL_CONFIDENCE_SCHEMA_VERSION,
            CALENDAR_EVENT_LINKS_SCHEMA_VERSION,
            SOURCE_PRIVACY_SCHEMA_VERSION,
            ORGANIZATION_APPROVAL_THREADS_SCHEMA_VERSION,
            ACTION_PROPOSALS_SCHEMA_VERSION,
            TELEGRAM_UPDATE_DELIVERIES_SCHEMA_VERSION,
            TRAVEL_RECORD_REFERENCES_SCHEMA_VERSION,
            TELEGRAM_DELIVERY_HISTORY_SCHEMA_VERSION,
            TELEGRAM_DELIVERY_DEAD_LETTER_SCHEMA_VERSION,
            RECEIPT_RECORDS_SCHEMA_VERSION,
            RECEIPT_RECORD_EVIDENCE_SCHEMA_VERSION,
            WARRANTY_RECORDS_SCHEMA_VERSION,
            WARRANTY_RECORD_EVIDENCE_SCHEMA_VERSION,
            KNOWLEDGE_ENRICHMENT_PROPOSALS_SCHEMA_VERSION,
            TELEGRAM_CALLBACKS_SCHEMA_VERSION,
            PROVISIONAL_INTAKES_SCHEMA_VERSION,
            PROVISIONAL_INTAKE_REVISIONS_SCHEMA_VERSION,
            PROVISIONAL_INTAKE_CATEGORY_SCHEMA_VERSION,
            SOURCE_ROOTS_SCHEMA_VERSION,
            SOURCE_ROOT_EXCLUSIONS_SCHEMA_VERSION,
            TASKS_SCHEMA_VERSION,
            TELEGRAM_DELIVERY_RECOVERIES_SCHEMA_VERSION,
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

    assert migration_count == 38


def test_snapshot_database_copies_consistent_data_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "steward.db"; initialize_database(source)
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)", ("source_captured", "1", "test", "2026-09-10T00:00:00+00:00"))

    snapshot = snapshot_database(source, tmp_path / "backups" / "steward.db")

    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT details FROM activity_events").fetchone() == ("test",)
    with pytest.raises(ValueError, match="already exists"):
        snapshot_database(source, snapshot)
