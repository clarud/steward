from pathlib import Path
import sqlite3
import pytest
from datetime import UTC, datetime

from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.workspaces import WorkspaceRepository, WorkspaceService
from steward.action_proposals import ActionProposalRepository


def test_workspace_creation_and_listing(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    service = WorkspaceService(WorkspaceRepository(database_path))

    workspace = service.create("Compiler Project")

    assert WorkspaceRepository(database_path).list_all() == [workspace]


def test_workspace_source_link_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    service = WorkspaceService(WorkspaceRepository(database_path))
    workspace = service.create("Steward")

    timestamp = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database_path).add(Source(None, tmp_path / "note.md", "a" * 64, SourceType.MARKDOWN, 0, timestamp, timestamp, timestamp))
    service.add_source(workspace.id or 0, source.id or 0)
    service.add_source(workspace.id or 0, source.id or 0)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace_sources").fetchone()[0] == 1


def test_link_review_rolls_back_audit_failure_and_retries_once(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    workspaces = WorkspaceRepository(database)
    workspace = workspaces.create("School")
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "notes.md", "a" * 64,
                                                  SourceType.MARKDOWN, 0, now, now, now))
    proposals = ActionProposalRepository(database)
    proposal = proposals.add("link_source_to_workspace", {"workspace_id": str(workspace.id), "source_id": str(source.id)})
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TRIGGER fail_link_audit BEFORE INSERT ON activity_events
                            WHEN NEW.event_type = 'action_accepted'
                            BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        workspaces.review_link_proposal(proposal.id, "accepted")
    assert workspaces.list_source_ids(workspace.id) == ()
    assert proposals.get(proposal.id).status == "pending"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_link_audit")
    workspaces.review_link_proposal(proposal.id, "accepted")
    workspaces.review_link_proposal(proposal.id, "accepted")
    assert workspaces.list_source_ids(workspace.id) == (source.id,)
    assert proposals.get(proposal.id).status == "accepted"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0] == 2
    with pytest.raises(ValueError, match="already accepted"):
        workspaces.review_link_proposal(proposal.id, "rejected")

    rejected = proposals.add("link_source_to_workspace", {"workspace_id": str(workspace.id), "source_id": str(source.id)})
    workspaces.review_link_proposal(rejected.id, "rejected")
    with pytest.raises(ValueError, match="already rejected"):
        workspaces.review_link_proposal(rejected.id, "accepted")
