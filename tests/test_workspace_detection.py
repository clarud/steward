from datetime import UTC, datetime
from pathlib import Path

from steward.sources import Source, SourceType
from steward.workspace_detection import WorkspaceDetectionService
from steward.workspaces import Workspace


def source(identifier: int, path: Path) -> Source:
    now = datetime(2026, 9, 8, tzinfo=UTC)
    return Source(identifier, path, "a" * 64, SourceType.MARKDOWN, 0, now, now, now)


def test_inbox_review_proposes_a_shared_new_workspace_without_creating_one(tmp_path: Path) -> None:
    proposals = WorkspaceDetectionService().propose(
        [source(1, tmp_path / "inbox" / "compiler-parsing.md"), source(2, tmp_path / "inbox" / "compiler-notes.pdf")],
        [],
    )

    compiler = next(item for item in proposals if item.proposed_name == "Compiler")
    assert compiler.source_ids == (1, 2)
    assert compiler.status == "pending"


def test_existing_workspace_and_non_inbox_files_are_not_reproposed(tmp_path: Path) -> None:
    proposals = WorkspaceDetectionService().propose(
        [source(1, tmp_path / "inbox" / "steward-notes.md"), source(2, tmp_path / "projects" / "steward-plan.md")],
        [Workspace(1, "Steward", "active", datetime(2026, 9, 8, tzinfo=UTC))],
    )

    assert proposals == ()
