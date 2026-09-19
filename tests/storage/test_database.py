import sqlite3
from pathlib import Path

import pytest

from steward.storage import INITIAL_SCHEMA_VERSION, initialize_database, restore_database, snapshot_database
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
    TASK_DUE_AT_SCHEMA_VERSION,
    CALENDAR_TASK_EVENT_LINKS_SCHEMA_VERSION,
    ORGANIZATION_PROPOSAL_WORKSPACE_NAME_SCHEMA_VERSION,
    PROVISIONAL_INTAKE_ANALYSIS_MODE_SCHEMA_VERSION,
    TASK_REMINDERS_SCHEMA_VERSION,
    TELEGRAM_REVIEW_CONTEXT_SCHEMA_VERSION,
    TASK_REMINDER_CLAIM_SCHEMA_VERSION,
    KNOWLEDGE_REVIEW_SNAPSHOT_SCHEMA_VERSION,
    TELEGRAM_MESSAGE_REFERENCES_SCHEMA_VERSION,
    KNOWLEDGE_CONFLICT_RESOLUTION_SCHEMA_VERSION,
    CLAIM_REVISIONS_SCHEMA_VERSION,
    PROVISIONAL_INTAKE_DIAGNOSTICS_SCHEMA_VERSION,
    ORGANIZATION_PROPOSAL_GUIDANCE_SCHEMA_VERSION,
    EPHEMERAL_RESEARCH_CARDS_SCHEMA_VERSION,
    TASK_CALENDAR_ASSOCIATIONS_SCHEMA_VERSION,
    TRAVEL_RECORD_PASSENGER_SCHEMA_VERSION,
    KNOWLEDGE_ENRICHMENT_CHAT_SCHEMA_VERSION,
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
        message_reference_columns = connection.execute("PRAGMA table_info(telegram_message_references)").fetchall()
        enrichment_columns = connection.execute("PRAGMA table_info(knowledge_enrichment_proposals)").fetchall()
        claim_revision_columns = connection.execute("PRAGMA table_info(claim_revisions)").fetchall()
        intake_columns = connection.execute("PRAGMA table_info(provisional_intakes)").fetchall()
        organization_columns = connection.execute("PRAGMA table_info(organization_proposals)").fetchall()
        travel_columns = connection.execute("PRAGMA table_info(travel_records)").fetchall()

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
            TASK_DUE_AT_SCHEMA_VERSION,
                CALENDAR_TASK_EVENT_LINKS_SCHEMA_VERSION,
                ORGANIZATION_PROPOSAL_WORKSPACE_NAME_SCHEMA_VERSION,
                PROVISIONAL_INTAKE_ANALYSIS_MODE_SCHEMA_VERSION,
                TASK_REMINDERS_SCHEMA_VERSION,
                TELEGRAM_REVIEW_CONTEXT_SCHEMA_VERSION,
                TASK_REMINDER_CLAIM_SCHEMA_VERSION,
                KNOWLEDGE_REVIEW_SNAPSHOT_SCHEMA_VERSION,
                TELEGRAM_MESSAGE_REFERENCES_SCHEMA_VERSION,
                KNOWLEDGE_CONFLICT_RESOLUTION_SCHEMA_VERSION,
                CLAIM_REVISIONS_SCHEMA_VERSION,
                PROVISIONAL_INTAKE_DIAGNOSTICS_SCHEMA_VERSION,
            ORGANIZATION_PROPOSAL_GUIDANCE_SCHEMA_VERSION,
            EPHEMERAL_RESEARCH_CARDS_SCHEMA_VERSION,
            TASK_CALENDAR_ASSOCIATIONS_SCHEMA_VERSION,
            TRAVEL_RECORD_PASSENGER_SCHEMA_VERSION,
            KNOWLEDGE_ENRICHMENT_CHAT_SCHEMA_VERSION,
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
    assert [column[1] for column in message_reference_columns] == [
        "platform", "chat_id", "message_id", "reference_kind", "reference_id", "created_at",
    ]
    assert [column[1] for column in enrichment_columns][-3:] == [
        "conflict_resolution", "conflict_resolved_at", "chat_id",
    ]
    assert [column[1] for column in claim_revision_columns] == [
        "action_proposal_id", "conflict_proposal_id", "original_claim_id",
        "replacement_claim_id", "created_at",
    ]
    assert intake_columns[-1][1] == "diagnostic"
    assert organization_columns[-1][1] == "user_guidance"
    assert travel_columns[-1][1] == "passenger"


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"

    initialize_database(database_path)
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]

    assert migration_count == KNOWLEDGE_ENRICHMENT_CHAT_SCHEMA_VERSION


def test_snapshot_database_copies_consistent_data_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "steward.db"; initialize_database(source)
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)", ("source_captured", "1", "test", "2026-09-10T00:00:00+00:00"))

    snapshot = snapshot_database(source, tmp_path / "backups" / "steward.db")

    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT details FROM activity_events").fetchone() == ("test",)
    with pytest.raises(ValueError, match="already exists"):
        snapshot_database(source, snapshot)


def test_restore_database_replaces_active_state_only_after_a_safety_snapshot(tmp_path: Path) -> None:
    active = tmp_path / "steward.db"; initialize_database(active)
    with sqlite3.connect(active) as connection:
        connection.execute("INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)", ("source_captured", "1", "before", "2026-09-10T00:00:00+00:00"))
    snapshot = snapshot_database(active, tmp_path / "snapshot.db")
    with sqlite3.connect(active) as connection:
        connection.execute("INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)", ("source_captured", "2", "after", "2026-09-10T00:01:00+00:00"))

    safety_backup = restore_database(snapshot, active, tmp_path / "safety.db")

    with sqlite3.connect(active) as connection:
        assert connection.execute("SELECT details FROM activity_events ORDER BY id").fetchall() == [("before",)]
    with sqlite3.connect(safety_backup) as connection:
        assert connection.execute("SELECT details FROM activity_events ORDER BY id").fetchall() == [("before",), ("after",)]


def test_snapshot_preserves_destination_created_after_initial_check(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.db"
    initialize_database(source)
    destination = tmp_path / "backup.db"
    original_open = Path.open

    def competing_open(path, mode="r", *args, **kwargs):
        if path == destination and mode == "xb":
            with original_open(path, "wb") as competing:
                competing.write(b"owned by another backup operation")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", competing_open)
    with pytest.raises(ValueError, match="already exists"):
        snapshot_database(source, destination)
    assert destination.read_bytes() == b"owned by another backup operation"


def test_failed_snapshot_cleans_up_only_its_reserved_destination(tmp_path: Path) -> None:
    source = tmp_path / "corrupt.db"
    source.write_bytes(b"not SQLite")
    destination = tmp_path / "backup.db"
    with pytest.raises(sqlite3.DatabaseError):
        snapshot_database(source, destination)
    assert not destination.exists()
    assert source.read_bytes() == b"not SQLite"


@pytest.mark.parametrize("reverse", [False, True])
def test_restore_refuses_swapped_operational_and_checkpoint_databases(tmp_path: Path, reverse: bool) -> None:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from contextlib import closing
    operational = tmp_path / "first.db"
    checkpoints = tmp_path / "second.db"
    initialize_database(operational)
    with closing(sqlite3.connect(checkpoints)) as connection:
        SqliteSaver(connection).setup()
    snapshot, destination = (operational, checkpoints) if reverse else (checkpoints, operational)
    original = destination.read_bytes()
    with pytest.raises(ValueError, match="database role"):
        restore_database(snapshot, destination, tmp_path / "safety.db")
    assert destination.read_bytes() == original
    assert not (tmp_path / "safety.db").exists()


@pytest.mark.parametrize("contents", [b"", b"not a SQLite database", b"SQLite format 3\x00" + b"\x00" * 90], ids=["empty", "not-sqlite", "truncated"])
def test_restore_refuses_invalid_snapshot_without_touching_active_state(tmp_path: Path, contents: bytes) -> None:
    active = tmp_path / "active.db"
    initialize_database(active)
    with sqlite3.connect(active) as connection:
        connection.execute("INSERT INTO activity_events (event_type, details, occurred_at) VALUES ('source_captured', 'preserve me', '2026-09-10T00:00:00+00:00')")
    original = active.read_bytes()
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(contents)
    safety = tmp_path / "safety.db"
    with pytest.raises(ValueError, match="No restore was performed"):
        restore_database(invalid, active, safety)
    assert active.read_bytes() == original
    assert invalid.read_bytes() == contents
    assert not safety.exists()
    with sqlite3.connect(active) as connection:
        assert connection.execute("SELECT details FROM activity_events").fetchall() == [("preserve me",)]
