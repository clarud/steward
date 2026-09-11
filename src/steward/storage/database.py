"""SQLite database initialization and schema-version tracking."""

from __future__ import annotations

import sqlite3
from contextlib import closing
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
WARRANTY_RECORDS_SCHEMA_VERSION = 28
WARRANTY_RECORD_EVIDENCE_SCHEMA_VERSION = 29
KNOWLEDGE_ENRICHMENT_PROPOSALS_SCHEMA_VERSION = 30
TELEGRAM_CALLBACKS_SCHEMA_VERSION = 31
PROVISIONAL_INTAKES_SCHEMA_VERSION = 32
PROVISIONAL_INTAKE_REVISIONS_SCHEMA_VERSION = 33
PROVISIONAL_INTAKE_CATEGORY_SCHEMA_VERSION = 34
SOURCE_ROOTS_SCHEMA_VERSION = 35
SOURCE_ROOT_EXCLUSIONS_SCHEMA_VERSION = 36
TASKS_SCHEMA_VERSION = 37
TELEGRAM_DELIVERY_RECOVERIES_SCHEMA_VERSION = 38
TASK_DUE_AT_SCHEMA_VERSION = 39
CALENDAR_TASK_EVENT_LINKS_SCHEMA_VERSION = 40
ORGANIZATION_PROPOSAL_WORKSPACE_NAME_SCHEMA_VERSION = 41
PROVISIONAL_INTAKE_ANALYSIS_MODE_SCHEMA_VERSION = 42
TASK_REMINDERS_SCHEMA_VERSION = 43
TELEGRAM_REVIEW_CONTEXT_SCHEMA_VERSION = 44
TASK_REMINDER_CLAIM_SCHEMA_VERSION = 45
KNOWLEDGE_REVIEW_SNAPSHOT_SCHEMA_VERSION = 46

MIGRATIONS: tuple[tuple[int, str | tuple[str, ...]], ...] = (
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
    (
        WARRANTY_RECORDS_SCHEMA_VERSION,
        """CREATE TABLE warranty_records (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            product_name TEXT, provider TEXT, warranty_number TEXT, coverage_ends_at TEXT)""",
    ),
    (
        WARRANTY_RECORD_EVIDENCE_SCHEMA_VERSION,
        """CREATE TABLE warranty_record_evidence (warranty_record_id INTEGER NOT NULL REFERENCES warranty_records(id) ON DELETE CASCADE,
            field_name TEXT NOT NULL, fragment_id INTEGER NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE,
            PRIMARY KEY (warranty_record_id, field_name))""",
    ),
    (
        KNOWLEDGE_ENRICHMENT_PROPOSALS_SCHEMA_VERSION,
        """
        CREATE TABLE knowledge_enrichment_proposals (
            id INTEGER PRIMARY KEY,
            claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            fragment_id INTEGER NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE,
            operation TEXT NOT NULL CHECK (operation IN ('confirm', 'extend', 'refine', 'qualify', 'contradict')),
            rationale TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            UNIQUE (claim_id, fragment_id, operation, rationale)
        )
        """,
    ),
    (
        TELEGRAM_CALLBACKS_SCHEMA_VERSION,
        """
        CREATE TABLE telegram_callbacks (
            token TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            command TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ),
    (
        PROVISIONAL_INTAKES_SCHEMA_VERSION,
        """
        CREATE TABLE provisional_intakes (
            id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('file', 'text')),
            staged_path TEXT NOT NULL,
            original_name TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'discarded')),
            created_at TEXT NOT NULL,
            decided_at TEXT
        )
        """,
    ),
    (
        PROVISIONAL_INTAKE_REVISIONS_SCHEMA_VERSION,
        """
        CREATE TABLE provisional_intake_revisions (
            id INTEGER PRIMARY KEY,
            intake_id INTEGER NOT NULL REFERENCES provisional_intakes(id) ON DELETE CASCADE,
            guidance TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ),
    (
        PROVISIONAL_INTAKE_CATEGORY_SCHEMA_VERSION,
        """
        ALTER TABLE provisional_intakes
        ADD COLUMN category TEXT NOT NULL DEFAULT 'uncertain'
        """,
    ),
    (
        SOURCE_ROOTS_SCHEMA_VERSION,
        """
        CREATE TABLE source_roots (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL
        )
        """,
    ),
    (
        SOURCE_ROOT_EXCLUSIONS_SCHEMA_VERSION,
        """
        ALTER TABLE source_roots
        ADD COLUMN exclusions TEXT NOT NULL DEFAULT '[]'
        """,
    ),
    (
        TASKS_SCHEMA_VERSION,
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            due_hint TEXT,
            status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """,
    ),
    (
        TELEGRAM_DELIVERY_RECOVERIES_SCHEMA_VERSION,
        """
        CREATE TABLE telegram_delivery_recoveries (
            id INTEGER PRIMARY KEY,
            update_id TEXT NOT NULL,
            recovered_at TEXT NOT NULL
        )
        """,
    ),
    (
        TASK_DUE_AT_SCHEMA_VERSION,
        "ALTER TABLE tasks ADD COLUMN due_at TEXT",
    ),
    (
        CALENDAR_TASK_EVENT_LINKS_SCHEMA_VERSION,
        """
        CREATE TABLE calendar_task_event_links (
            idempotency_key TEXT PRIMARY KEY,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            external_event_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ),
    (
        ORGANIZATION_PROPOSAL_WORKSPACE_NAME_SCHEMA_VERSION,
        "ALTER TABLE organization_proposals ADD COLUMN workspace_name TEXT",
    ),
    (
        PROVISIONAL_INTAKE_ANALYSIS_MODE_SCHEMA_VERSION,
        """
        ALTER TABLE provisional_intakes
        ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'none'
        CHECK (analysis_mode IN ('external', 'local', 'none'))
        """,
    ),
    (
        TASK_REMINDERS_SCHEMA_VERSION,
        """
        CREATE TABLE task_reminders (
            task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            claimed_at TEXT,
            reminded_at TEXT
        )
        """,
    ),
    (
        TELEGRAM_REVIEW_CONTEXT_SCHEMA_VERSION,
        """
        CREATE TABLE telegram_review_context (
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            review_kind TEXT NOT NULL,
            review_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (platform, chat_id)
        )
        """,
    ),
    (
        TASK_REMINDER_CLAIM_SCHEMA_VERSION,
        "ALTER TABLE task_reminders ADD COLUMN claim_token TEXT",
    ),
    (KNOWLEDGE_REVIEW_SNAPSHOT_SCHEMA_VERSION, (
        """CREATE TABLE knowledge_enrichment_versions (
            id INTEGER PRIMARY KEY,
            claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            fragment_id INTEGER NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE,
            operation TEXT NOT NULL CHECK (operation IN ('confirm', 'extend', 'refine', 'qualify', 'contradict')),
            rationale TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL, reviewed_at TEXT, evidence_snapshot TEXT,
            UNIQUE (claim_id, fragment_id, operation, rationale, evidence_snapshot)
        )""",
        "INSERT INTO knowledge_enrichment_versions SELECT *, NULL FROM knowledge_enrichment_proposals",
        "DROP TABLE knowledge_enrichment_proposals",
        "ALTER TABLE knowledge_enrichment_versions RENAME TO knowledge_enrichment_proposals",
    )),
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

            for sql in ((statement,) if isinstance(statement, str) else statement):
                connection.execute(sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )


def snapshot_database(source_path: Path, destination_path: Path) -> Path:
    """Create a consistent SQLite copy without overwriting an existing backup.

    SQLite's backup API works while the application has the source database
    open, unlike a filesystem copy which can miss WAL-backed changes. The
    destination is deliberately write-once: restore is a separate, explicit
    local operation rather than an implicit replacement of active state.
    """
    source = source_path.resolve()
    destination = destination_path.resolve()
    if not source.is_file():
        raise ValueError(f"Database to snapshot was not found: {source}")
    if source == destination:
        raise ValueError("Database snapshot destination must differ from its source.")
    if destination.exists():
        raise ValueError(f"Database snapshot already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb"):
            pass
    except FileExistsError as error:
        raise ValueError(f"Database snapshot already exists: {destination}") from error
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as source_connection:
            with closing(sqlite3.connect(destination)) as destination_connection:
                source_connection.backup(destination_connection)
    except sqlite3.Error:
        if destination.exists():
            destination.unlink()
        raise
    return destination


def _database_role(connection: sqlite3.Connection) -> str | None:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    operational = {"schema_migrations", "sources"} <= tables
    checkpoints = {"checkpoints", "writes"} <= tables
    if operational and not checkpoints:
        return "operational"
    if checkpoints and not operational:
        return "checkpoints"
    return None


def restore_database(snapshot_path: Path, destination_path: Path, safety_backup_path: Path) -> Path:
    """Restore a SQLite snapshot after first creating a write-once safety copy.

    Callers must obtain explicit user confirmation and stop Steward processes
    before calling this function. The active database is never replaced unless
    its current state has been backed up to a new, caller-selected path.
    """

    snapshot = snapshot_path.resolve()
    destination = destination_path.resolve()
    safety_backup = safety_backup_path.resolve()
    if not snapshot.is_file():
        raise ValueError(f"Database snapshot was not found: {snapshot}")
    if not destination.is_file():
        raise ValueError(f"Database to restore was not found: {destination}")
    if snapshot == destination:
        raise ValueError("Database snapshot must differ from its restore destination.")
    if safety_backup in {snapshot, destination}:
        raise ValueError("Safety backup must differ from both snapshot and restore destination.")
    # Read-only mode prevents SQLite from creating or initializing the input.
    # quick_check alone accepts a zero-byte file as an empty database, so also
    # require an application table before any destination/safety-copy writes.
    try:
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as candidate:
            integrity = candidate.execute("PRAGMA quick_check").fetchall()
            table = candidate.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if integrity != [("ok",)] or table is None:
                raise ValueError("Restore snapshot is empty or failed its SQLite integrity check. No restore was performed.")
            candidate_role = _database_role(candidate)
    except sqlite3.Error as error:
        raise ValueError("Restore snapshot could not be validated as a readable SQLite database. No restore was performed.") from error
    try:
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as current:
            destination_role = _database_role(current)
    except sqlite3.Error:
        # A damaged destination may need recovery; it cannot establish a role.
        destination_role = None
    if destination_role is not None and candidate_role != destination_role:
        raise ValueError("Restore snapshot has a different or unrecognized database role. No restore was performed.")
    snapshot_database(destination, safety_backup)
    try:
        with sqlite3.connect(snapshot) as source_connection, sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)
    except sqlite3.Error:
        with sqlite3.connect(safety_backup) as safety_connection, sqlite3.connect(destination) as destination_connection:
            safety_connection.backup(destination_connection)
        raise
    return safety_backup
