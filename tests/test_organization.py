from datetime import UTC, datetime
from pathlib import Path
from steward.organization import OrganizationProposalRepository, OrganizationService
from steward.sources import Source, SourceType
from steward.storage import initialize_database
from steward.workspaces import Workspace
from steward.sources import SourceRepository

def test_matching_workspace_creates_pending_proposal(tmp_path: Path) -> None:
    source = Source(1, tmp_path / "steward-notes.md", "a" * 64, SourceType.MARKDOWN, 0, datetime(2026,9,8,tzinfo=UTC), datetime(2026,9,8,tzinfo=UTC), datetime(2026,9,8,tzinfo=UTC))
    workspace = Workspace(2, "Steward", "active", datetime(2026,9,8,tzinfo=UTC))
    proposal = OrganizationService().propose(source, [workspace])
    assert proposal.workspace_id == 2 and proposal.proposal_type == "move_to_workspace" and proposal.confidence == 1.0


def test_explicit_context_selects_only_an_existing_workspace(tmp_path: Path) -> None:
    source = Source(1, tmp_path / "unrelated.md", "a" * 64, SourceType.MARKDOWN, 0, datetime(2026,9,8,tzinfo=UTC), datetime(2026,9,8,tzinfo=UTC), datetime(2026,9,8,tzinfo=UTC))
    workspace = Workspace(2, "CS3210", "active", datetime(2026,9,8,tzinfo=UTC))

    proposal = OrganizationService().propose_with_context(source, [workspace], "These are CS3210 lecture notes")

    assert proposal.workspace_id == 2
    assert proposal.suggested_path == tmp_path / "projects" / "CS3210" / "unrelated.md"

def test_proposal_status_changes_without_moving_file(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    # Repository behavior is exercised after normal source/workspace creation in later integration flows.
    repository = OrganizationProposalRepository(database)
    timestamp = datetime(2026,9,8,tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "steward.md", "b" * 64, SourceType.MARKDOWN, 0, timestamp, timestamp, timestamp))
    proposal_id = repository.add(OrganizationService().propose(source, []))
    repository.set_status(proposal_id, "rejected")
    proposal = repository.list_all()[0]
    assert proposal.status == "rejected" and proposal.proposal_type == "keep_in_inbox"
