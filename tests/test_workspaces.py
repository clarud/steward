from pathlib import Path
import sqlite3
from datetime import UTC, datetime

from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.workspaces import WorkspaceRepository, WorkspaceService


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
